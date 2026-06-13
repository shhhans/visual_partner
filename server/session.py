"""每个 WebSocket 连接一个 Session：语音链路的编排核心。

数据流：
  WS 二进制(麦克风 PCM) → ASR → sentence_end → _reply():
    LLM 流式增量 ──┬→ WS JSON {type:"delta"} 字幕
                   └→ TTS feed → on_audio → WS 二进制(前4字节=代号 uint32LE + TTS PCM)
barge-in：回复进行中或音频已发但客户端尚未播完时收到新 ASR 文本 →
  取消 _reply task、cancel TTS、通知前端 {type:"interrupt", gen:N} 清播放队列。
新回复开始时发送 {type:"start", gen:N}，客户端据此冲掉上一轮残余音频。
"""

import asyncio
import json
import struct
import time
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from agent.llm import stream_chat
from config import SYSTEM_PROMPT
from metrics import TurnMetrics, save_turn
from vision.frame import FrameStore
from voice.asr import AsrEvent, StreamingAsr
from voice.tts import StreamingTts


class Session:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.frames = FrameStore()
        self.asr = StreamingAsr()
        self._reply_task: asyncio.Task | None = None
        self._tts: StreamingTts | None = None
        # 代号闸门：取消后 SDK 线程仍可能回调，比对代号丢弃过期数据
        self._reply_gen = 0
        # 音频已发出但 done 尚未确认，此窗口内仍允许 barge-in（覆盖 reply_task.done() 的盲区）
        self._audio_inflight = False
        # 单写者锁：序列化所有 WS 帧，防止二进制帧与 JSON 帧并发写入导致帧乱序
        self._ws_lock = asyncio.Lock()
        # 追踪所有 fire-and-forget 发送 task，断连时统一取消，防止孤儿 task 持有引用
        self._send_tasks: set[asyncio.Task] = set()
        # 延迟指标采集（详见 metrics.py）
        self._session_id = uuid.uuid4().hex[:8]
        self._turn_index = 0
        self._last_partial_ts: float | None = None  # 本句最后一次 ASR partial 时刻
        self._metrics: TurnMetrics | None = None  # 当前进行中回合的指标累加器

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    async def run(self) -> None:
        self.asr.start()
        consumer = asyncio.create_task(self._consume_asr())
        try:
            while True:
                msg = await self.ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    self.asr.send_audio(msg["bytes"])
                elif msg.get("text"):
                    try:
                        await self._on_json(json.loads(msg["text"]))
                    except json.JSONDecodeError as exc:
                        # 畸形 JSON：回传 error，不中断接收循环
                        await self._send_json({"type": "error", "message": f"bad json: {exc}"})
        except WebSocketDisconnect:
            pass
        finally:
            consumer.cancel()
            try:
                await consumer
            except (asyncio.CancelledError, Exception):
                pass
            await self._cancel_reply(notify=False)
            # 取消并收集所有残余发送 task，释放 WS 引用
            if self._send_tasks:
                for t in list(self._send_tasks):
                    t.cancel()
                await asyncio.gather(*self._send_tasks, return_exceptions=True)
            self.asr.stop()

    async def _on_json(self, data: dict) -> None:
        if data.get("type") == "frame":
            # 视觉链路：前端回传的抓帧，resolve 等待中的 future 并更新缓存
            self.frames.receive_frame(data.get("id", ""), data.get("data", ""))

    # ---------- ASR 事件 ----------

    async def _consume_asr(self) -> None:
        while True:
            event = await self.asr.events.get()
            if isinstance(event, Exception):
                await self._send_json({"type": "error", "message": str(event)})
                continue
            await self._on_asr(event)

    async def _on_asr(self, event: AsrEvent) -> None:
        now = self._now()
        # barge-in 条件：reply task 仍在运行，或音频已发但 done 尚未到达
        should_interrupt = (
            self._reply_task is not None and not self._reply_task.done()
        ) or self._audio_inflight
        if should_interrupt:
            await self._cancel_reply(notify=True)
        await self._send_json({"type": "asr", "text": event.text, "final": event.is_final})
        if not event.is_final:
            # 记录最后一次 partial 时刻，用于计算 turn_closure（partial→sentence_end）
            self._last_partial_ts = now
            return
        self.history.append({"role": "user", "content": event.text})
        self._metrics = TurnMetrics(
            session_id=self._session_id,
            turn_index=self._turn_index,
            created_at=time.time(),
            t_final=now,
            last_partial_ts=self._last_partial_ts,
            asr_text=event.text,
        )
        self._turn_index += 1
        self._last_partial_ts = None
        self._reply_task = asyncio.create_task(self._reply())

    # ---------- 回复（LLM → TTS） ----------

    async def _reply(self) -> None:
        self._reply_gen += 1
        gen = self._reply_gen
        self._audio_inflight = False
        self._tts = StreamingTts(
            on_audio=lambda pcm: self._on_tts_audio(gen, pcm),
            on_error=lambda msg: self._on_tts_error(gen, msg),
        )
        spoken: list[str] = []
        # 通知客户端新回复开始，客户端应冲掉上一轮残余音频
        await self._send_json({"type": "start", "gen": gen})
        try:
            async for delta in stream_chat(self.history, session=self):
                if self._metrics is not None:
                    if self._metrics.t_ttft is None:
                        self._metrics.t_ttft = self._now()
                    self._metrics.reply_chars += len(delta)
                spoken.append(delta)
                self._tts.feed(delta)
                await self._send_json({"type": "delta", "text": delta})
            await self._tts.finish()
            # finish() 返回后所有音频帧均已提交发送，清除 inflight 标志
            self._audio_inflight = False
            self.history.append({"role": "assistant", "content": "".join(spoken)})
            if self._metrics is not None:
                self._metrics.t_done = self._now()
                await self._flush_metrics(interrupted=False)
            await self._send_json({"type": "done"})
        except asyncio.CancelledError:
            # 被打断的部分如实入史，后续对话上下文才不会错位
            if spoken:
                self.history.append(
                    {"role": "assistant", "content": "".join(spoken) + "（已被用户打断）"}
                )
            raise

    def _on_tts_audio(self, gen: int, pcm: bytes) -> None:
        # 由 call_soon_threadsafe 调度，运行在事件循环中，可安全访问实例状态
        if gen != self._reply_gen:
            return  # 已被打断的旧回复音频，丢弃
        if self._metrics is not None and self._metrics.t_tts_first is None:
            self._metrics.t_tts_first = self._now()
        self._audio_inflight = True
        # 前4字节为代号（小端 uint32），客户端据此丢弃在 interrupt/start 之后到达的过期帧
        frame = struct.pack("<I", gen) + pcm
        self._fire(self._send_bytes_checked(gen, frame))

    def _on_tts_error(self, gen: int, message: str) -> None:
        # 由 call_soon_threadsafe 调度，运行在事件循环中
        if gen != self._reply_gen:
            return
        self._fire(self._send_json({"type": "error", "message": f"TTS: {message}"}))

    def _fire(self, coro) -> None:
        """创建 fire-and-forget task 并纳入追踪集，断连时统一取消。"""
        task = asyncio.ensure_future(coro)
        self._send_tasks.add(task)
        task.add_done_callback(self._send_tasks.discard)

    async def _cancel_reply(self, notify: bool) -> None:
        self._reply_gen += 1  # 先关闸门，再取消，确保不漏旧音频
        self._audio_inflight = False
        if self._tts is not None:
            self._tts.cancel()
            self._tts = None
        if self._reply_task is not None and not self._reply_task.done():
            self._reply_task.cancel()
            try:
                await self._reply_task
            except asyncio.CancelledError:
                pass
        self._reply_task = None
        # 进行中回合被打断：如实落库（total_ms 留空，e2e/ttft 等已测部分仍有效）
        await self._flush_metrics(interrupted=True)
        if notify:
            # 携带新代号：客户端据此丢弃代号小于 gen 的残余音频帧
            await self._send_json({"type": "interrupt", "gen": self._reply_gen})

    async def _flush_metrics(self, interrupted: bool) -> None:
        """折算当前回合指标，推前端调试面板 + 落库。每回合只 flush 一次。"""
        m = self._metrics
        if m is None:
            return
        self._metrics = None  # 先置空，防止 done 与 barge-in 路径重复 flush
        m.interrupted = interrupted
        row = m.to_row()
        await self._send_json({"type": "metrics", **row})
        try:
            await save_turn(row)
        except Exception:
            pass  # 落库失败不应影响对话主流程

    # ---------- 发送（连接断开时静默吞掉，由 run() 统一收尾） ----------

    async def _send_json(self, data: dict) -> None:
        try:
            async with self._ws_lock:
                await self.ws.send_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass

    async def _send_bytes_checked(self, gen: int, data: bytes) -> None:
        # 重新校验代号：_on_tts_audio 到此协程真正执行之间，interrupt 可能已经发生
        if gen != self._reply_gen:
            return
        try:
            async with self._ws_lock:
                await self.ws.send_bytes(data)
        except Exception:
            pass
