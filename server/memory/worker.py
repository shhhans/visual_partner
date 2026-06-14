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


@dataclass
class _Turn:
    user_text: str
    assistant_text: str
    session_id: str


class MemoryWorker:
    def __init__(self, provider: MemoryProvider) -> None:
        self._provider = provider
        self._queue: asyncio.Queue[_Turn] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._stopping = False  # stop() 排空后置位，拒绝晚到的 enqueue（否则无人消费）

    def ensure_started(self) -> None:
        """惰性启动消费者（须在运行中的事件循环内调用，如 WS 处理协程）。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def recall(self, query: str, limit: int = 5, kind: str | None = None) -> list[str]:
        """读侧入口：召回与 query 相关的记忆，供回复前回灌。

        只读、不经写队列，与后台串行写互不阻塞（底层 sqlite 访问由其自身锁串行化）。
        """
        return await self._provider.recall(query, limit, kind)

    def enqueue(self, user_text: str, assistant_text: str, session_id: str) -> None:
        """非阻塞入队一个已完成回合。空回合（两端都空）直接丢弃。"""
        if not user_text and not assistant_text:
            return
        if self._stopping:
            # 进程正在关停、队列已排空：此后到达的回合不再有消费者，记录后丢弃。
            log.warning("memory worker stopping, dropped turn (session=%s)", session_id)
            return
        self._queue.put_nowait(_Turn(user_text, assistant_text, session_id))

    async def _run(self) -> None:
        while True:
            turn = await self._queue.get()
            try:
                await self._provider.sync_turn(
                    turn.user_text, turn.assistant_text, turn.session_id
                )
            except Exception as exc:
                # 单条回合落库失败不能拖垮 worker，记录后继续下一条。
                log.warning("memory sync_turn failed (session=%s): %s", turn.session_id, exc)
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
