"""记忆的 SQLite FTS5 落库与全文检索。

复用 metrics.py 的并发套路：单连接 + 线程锁串行化访问，写入经 asyncio.to_thread
放线程池避免卡事件循环。库文件 server/memory.db（已 gitignore）。

FTS5 单虚表存两类记忆，靠 kind 区分：
  episode  原始回合文本（用户 + 助手），保留对话语料底料，供日后全文召回
  fact     LLM 从回合里抽取的可长期复用的事实（用户身份/偏好/在办事项等）
content 为被索引列；kind/session_id/created_at 为 UNINDEXED（只存不参与匹配）。

分词器用 trigram 而非默认 unicode61：unicode61 把一整串汉字当单个 token，
中文按词/子串检索几乎全失效。trigram 切成重叠三字组，支持中文子串匹配。
已知限制（实测 SQLite 3.43）：trigram 要求查询 >= 3 字符，2 字中文词（如「天气」
「咖啡」）查不到。recall 在 slice 2 才接入，届时查询多为整句或 >=3 字关键词短语，
影响可控；若召回质量不足，再考虑对短查询补 per-char 分词。
"""

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "memory.db"

# 与 metrics.py 同款：sqlite3 连接非线程安全，所有访问经此锁串行化；
# check_same_thread=False 允许 asyncio.to_thread 的工作线程访问。
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory USING fts5(
    content,
    kind UNINDEXED,
    session_id UNINDEXED,
    created_at UNINDEXED,
    tokenize='trigram'
);
"""


def init_db() -> None:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.execute(_SCHEMA)
            _conn.commit()


def _insert(kind: str, content: str, session_id: str) -> None:
    init_db()
    assert _conn is not None
    with _lock:
        _conn.execute(
            "INSERT INTO memory (content, kind, session_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (content, kind, session_id, time.time()),
        )
        _conn.commit()


def _build_match(text: str) -> str:
    """把检索文本构造成 FTS5 MATCH 表达式：重叠 3 字窗各成短语，OR 起来。

    为什么不是「整句当一个短语」：那等于要求事实是问句的子串，自然问句
    （「你还记得我喜欢喝什么」）永远匹配不到事实（「用户喜欢喝咖啡」）——方向反了。
    改成「任一 3 字片段命中即算相关」，靠 FTS rank(bm25) 把重叠多的事实排前，
    自然问句即可召回共享片段的事实。

    已知边界（trigram 固有）：只能匹配 >=3 字连续重叠；1-2 字的语义关键词
    （「猫」「项目」）抓不到。更强的语义召回需 embeddings，留作后续。
    每个 3 字窗用双引号包成短语并转义内部引号，规避 FTS5 语法字符注入/报错。
    """
    compact = "".join(text.split())
    if len(compact) < 3:
        return ""  # 不足 3 字，trigram 无从匹配
    grams = {compact[i : i + 3] for i in range(len(compact) - 2)}
    return " OR ".join('"' + g.replace('"', '""') + '"' for g in grams)


def _delete_by_id(rowid: int) -> int:
    """按 rowid 删除 fact 行，返回删除条数（记忆自愈用）。

    限定 kind='fact'：correct_memory 只允许删 fact，绝不误删原始对话 episode。
    """
    init_db()
    assert _conn is not None
    with _lock:
        cur = _conn.execute(
            "DELETE FROM memory WHERE rowid = ? AND kind = 'fact'", (rowid,)
        )
        _conn.commit()
        return cur.rowcount


def _query(text: str, limit: int, kind: str | None) -> list[tuple[int, str]]:
    init_db()
    assert _conn is not None
    match = _build_match(text)
    if not match:
        return []
    # 取 rowid 作为对模型公开的稳定记忆编号，供 correct_memory 按 id 精确删除，
    # 免去让模型逐字复述事实原文（改写措辞就删不掉）的脆弱性。
    sql = "SELECT rowid, content FROM memory WHERE memory MATCH ?"
    params: list = [match]
    if kind is not None:
        # kind 是 UNINDEXED 列，可在 WHERE 里与 MATCH 并用做过滤。
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    with _lock:
        cur = _conn.execute(sql, params)
        return [(row[0], row[1]) for row in cur.fetchall()]


async def add(kind: str, content: str, session_id: str) -> None:
    """写一条记忆（阻塞 sqlite 调用放线程池）。"""
    await asyncio.to_thread(_insert, kind, content, session_id)


async def delete_by_id(rowid: int) -> int:
    """按 rowid 删除 fact 行，返回删除条数。"""
    return await asyncio.to_thread(_delete_by_id, rowid)


async def search(
    text: str, limit: int = 5, kind: str | None = None
) -> list[tuple[int, str]]:
    """按全文相关度召回 (rowid, content)。kind 非空时只召回该类（'fact' / 'episode'）。"""
    if not text.strip():
        return []
    return await asyncio.to_thread(_query, text, limit, kind)
