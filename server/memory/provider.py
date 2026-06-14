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
    async def recall(self, query: str, limit: int = 5) -> list[str]:
        """按相关度召回记忆（slice 2 接入回复链路做 prefetch 回灌）。"""


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

    async def recall(self, query: str, limit: int = 5) -> list[str]:
        return await sqlite_fts.search(query, limit)
