"""单写者后台记忆 worker（进程级单例）。

为什么是单例 + 单消费者：对齐 Hermes「单 daemon worker 串行写」的纪律——
所有回合经同一队列、同一消费者串行落库，既保证写入顺序，又避免并发抽取打架。
它独立于任何单条 WebSocket/Session 的生命周期：Session 只管 enqueue，
barge-in 取消的是回复链路（_reply_task），绝不取消已入队的记忆写入。

热路径零成本：enqueue 是非阻塞 put_nowait，真正的落库 + LLM 抽取都在消费者协程里跑。
"""

import asyncio
import logging
from dataclasses import dataclass

from memory.provider import MemoryProvider, SqliteMemoryProvider

log = logging.getLogger(__name__)


# 队列任务三态：完成回合（走 LLM 抽取）、直接写事实、自愈更正。
# 全部经同一单消费者串行处理，保写入顺序。
@dataclass
class _Turn:
    user_text: str
    assistant_text: str
    session_id: str


@dataclass
class _Fact:
    content: str
    session_id: str


@dataclass
class _Correction:
    fact_id: int
    right: str
    session_id: str


class MemoryWorker:
    def __init__(self, provider: MemoryProvider) -> None:
        self._provider = provider
        self._queue: asyncio.Queue = asyncio.Queue()  # _Turn | _Fact | _Correction
        self._task: asyncio.Task | None = None
        self._stopping = False  # stop() 排空后置位，拒绝晚到的 enqueue（否则无人消费）

    def ensure_started(self) -> None:
        """惰性启动消费者（须在运行中的事件循环内调用，如 WS 处理协程）。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def recall(
        self, query: str, limit: int = 5, kind: str | None = None
    ) -> list[tuple[int, str]]:
        """读侧入口：召回与 query 相关的 (id, content)，供回复前回灌。

        只读、不经写队列，与后台串行写互不阻塞（底层 sqlite 访问由其自身锁串行化）。
        """
        return await self._provider.recall(query, limit, kind)

    def _put(self, item, session_id: str) -> None:
        """统一入队闸门：关停后拒绝并记录，否则非阻塞入队。"""
        if self._stopping:
            # 进程正在关停、队列已排空：此后到达的写入不再有消费者，记录后丢弃。
            log.warning("memory worker stopping, dropped %s (session=%s)",
                        type(item).__name__, session_id)
            return
        self._queue.put_nowait(item)

    def enqueue(self, user_text: str, assistant_text: str, session_id: str) -> None:
        """非阻塞入队一个已完成回合（走 LLM 抽取）。空回合（两端都空）直接丢弃。"""
        if not user_text and not assistant_text:
            return
        self._put(_Turn(user_text, assistant_text, session_id), session_id)

    def remember(self, content: str, session_id: str) -> None:
        """非阻塞入队一条 agent 主动认定的长期事实（不经抽取）。"""
        if not content.strip():
            return
        self._put(_Fact(content, session_id), session_id)

    def correct(self, fact_id: int, right: str, session_id: str) -> None:
        """非阻塞入队一次记忆自愈：按 id 删旧事实、写更正。"""
        self._put(_Correction(fact_id, right, session_id), session_id)

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if isinstance(item, _Fact):
                    await self._provider.remember_fact(item.content, item.session_id)
                elif isinstance(item, _Correction):
                    await self._provider.correct_fact(item.fact_id, item.right, item.session_id)
                else:  # _Turn
                    await self._provider.sync_turn(
                        item.user_text, item.assistant_text, item.session_id
                    )
            except Exception as exc:
                # 单条写入失败不能拖垮 worker，记录后继续下一条。
                log.warning("memory write failed (%s, session=%s): %s",
                            type(item).__name__, getattr(item, "session_id", "?"), exc)
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        """进程退出时排空队列再停（尽量不丢未落库的回合）。"""
        if self._task is None:
            return
        self._stopping = True  # 先关闸：拒绝排空之后才到达的回合，避免其永远无人消费
        try:
            await asyncio.wait_for(self._queue.join(), timeout=10.0)
        except asyncio.TimeoutError:
            # 排空超时：放弃剩余，避免卡死关停。记录丢失条数便于排查。
            log.warning("memory worker drain timed out, %d turn(s) lost", self._queue.qsize())
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None


# 进程级单例：所有 Session 共享同一个写者，保证全局写入顺序。
_worker: MemoryWorker | None = None


def get_worker() -> MemoryWorker:
    global _worker
    if _worker is None:
        _worker = MemoryWorker(SqliteMemoryProvider())
    return _worker
