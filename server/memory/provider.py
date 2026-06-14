"""记忆 provider：定义接口契约 + SQLite/FTS5 实现。

接口形状借鉴 Hermes Agent 的 MemoryProvider（prefetch 回合前取、sync_turn 回合后落），
留出抽象层是为日后接其它后端（如向量检索、用户画像服务）做准备，而非现在就需要多实现。
"""

import logging
from abc import ABC, abstractmethod

from memory import sqlite_fts
from memory.extractor import extract_facts

log = logging.getLogger(__name__)


class MemoryProvider(ABC):
    """记忆后端契约。当前只有 SqliteMemoryProvider 一个实现。"""

    @abstractmethod
    async def sync_turn(self, user_text: str, assistant_text: str, session_id: str) -> None:
        """把一个已完成回合落入长期记忆（回合结束后由后台 worker 调用）。"""

    @abstractmethod
    async def remember_fact(self, content: str, session_id: str) -> None:
        """直接写入一条 agent 主动认定的长期事实（不经 LLM 抽取）。"""

    @abstractmethod
    async def correct_fact(self, fact_id: int, right: str, session_id: str) -> None:
        """记忆自愈：按 id 删除过时/错误事实，写入更正后的事实（right 为空则仅删除）。"""

    @abstractmethod
    async def recall(
        self, query: str, limit: int = 5, kind: str | None = None
    ) -> list[tuple[int, str]]:
        """按相关度召回 (id, content)，回复前注入 prompt。kind 限定类别。"""


class SqliteMemoryProvider(MemoryProvider):
    """SQLite FTS5 实现：原始回合入 episode，LLM 抽取的事实入 fact。"""

    async def sync_turn(self, user_text: str, assistant_text: str, session_id: str) -> None:
        # 原始回合无条件入库，作为日后全文召回的语料底料（被打断的回合同样照实记）。
        episode = f"用户: {user_text}\n助手: {assistant_text}"
        await sqlite_fts.add("episode", episode, session_id)
        # 事实抽取失败不应影响 episode 已落库的结果，单独兜底。
        try:
            facts = await extract_facts(user_text, assistant_text)
        except Exception as exc:
            log.warning("fact extraction failed: %s", exc)
            facts = []
        for fact in facts:
            await sqlite_fts.add("fact", fact, session_id)

    async def remember_fact(self, content: str, session_id: str) -> None:
        await sqlite_fts.add("fact", content, session_id)

    async def correct_fact(self, fact_id: int, right: str, session_id: str) -> None:
        # 按 id 删旧（agent 传回灌时给的 [#id] 编号），再写新。
        await sqlite_fts.delete_by_id(fact_id)
        if right.strip():
            await sqlite_fts.add("fact", right, session_id)

    async def recall(
        self, query: str, limit: int = 5, kind: str | None = None
    ) -> list[tuple[int, str]]:
        return await sqlite_fts.search(query, limit, kind)
