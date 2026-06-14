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

from agent.llm import complete_chat, stream_chat
from config import PROACTIVE_DIRECTIVE, SYSTEM_PROMPT
from memory import get_worker
from metrics import TurnMetrics, save_turn
from vision.frame import FrameStore
from voice.asr import AsrEvent, StreamingAsr
from voice.tts import StreamingTts

# 主动视觉：两次场景触发之间的冷却，防 diff 抖动刷屏 + 控 VL 成本
_SCENE_COOLDOWN = 8.0


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
        # 本回合的用户语句，回合结束时连同助手回复一起送进长期记忆
        self._current_user_text = ""
        self._last_partial_ts: float | None = None  # 本句最后一次 ASR partial 时刻
        self._metrics: TurnMetrics | None = None  # 当前进行中回合的指标累加器
        # 主动视觉：场景变化触发的冷却与重入保护
        self._last_scene_ts: float = 0.0  # 上次主动视觉触发时刻（冷却用）
        self._scene_busy: bool = False    # 取帧+VL 处理中，防止并发场景触发重入
        # 进行中的调用追踪：trace id → 起始 loop 时刻。barge-in 取消时统一兜底结束，
        # 避免被中途取消的 LLM/VL 调用在前端面板留永久 pending 行。
        self._open_traces: dict[str, float] = {}

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    async def run(self) -> None:
        self.asr.start()
        # 惰性启动进程级记忆 worker（须在运行中的事件循环内，此处即 WS 处理协程）
        get_worker().ensure_started()
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
        elif data.get("type") == "scene_change":
            # 主动视觉：前端检测到画面剧变。fire-and-forget 异步处理，
            # 绝不阻塞 run() 接收循环——取帧依赖循环继续转动才能收到 frame 回传。
            if not self._scene_busy:
                self._fire(self._on_scene_change())

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
        self._current_user_text = event.text
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
            # 先快照本回合历史再 await 召回：召回期间 _on_scene_change 可能并发
            # 往 history 追加观察，若 await 后再切片会把注入插错位置。
            user_msg = self.history[-1]
            prefix = self.history[:-1]
            recalled = await self._recall_context()
            if recalled is not None:
                # 注入到本回合用户消息之前；用快照而非 live history，不写回 self.history
                messages = prefix + [recalled, user_msg]
            else:
                messages = self.history
            async for delta in stream_chat(messages, session=self):
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
            reply = "".join(spoken)
            self.history.append({"role": "assistant", "content": reply})
            self._remember(reply)
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
                # 被打断的回合同样照实入长期记忆（视为正常 message）
                self._remember("".join(spoken) + "（已被用户打断）")
            raise

    async def _recall_context(self) -> dict | None:
        """回复前用当前用户语句召回相关长期事实，返回待注入的 system 消息。

        纯本地 FTS 查询（亚毫秒级），同步在回复前做，能帮到当前这句，
        故不采用「只预热下一回合」。无命中或失败返回 None，绝不阻断回复。
        """
        if not self._current_user_text:
            return None
        try:
            facts = await get_worker().recall(self._current_user_text, limit=5, kind="fact")
        except Exception:
            return None
        if not facts:
            return None
        bullets = "\n".join(f"- {f}" for f in facts)
        return {
            "role": "system",
            "content": (
                "下面是你记得的、可能和当前对话相关的用户信息。"
                "自然地运用，不要生硬复述，也不要主动声称这是「记忆」：\n" + bullets
            ),
        }

    def _remember(self, assistant_text: str) -> None:
        """把本回合（用户语句 + 助手回复）送进长期记忆 worker。

        enqueue 是非阻塞的，落库与事实抽取都在后台 worker 串行进行，不占回复热路径。
        """
        get_worker().enqueue(self._current_user_text, assistant_text, self._session_id)

    # ---------- 主动视觉（场景变化 → 取帧 VL → 观察入史 →（空闲则主动发声）） ----------

    def _is_idle(self) -> bool:
        """是否处于可主动发声的空闲态：无回复在跑、无在途音频、用户没在说话。"""
        reply_running = self._reply_task is not None and not self._reply_task.done()
        user_speaking = self._last_partial_ts is not None  # 有未结句的 partial = 正在说
        return not reply_running and not self._audio_inflight and not user_speaking

    async def _on_scene_change(self) -> None:
        """前端上报画面剧变：取帧→VL→观察入史；空闲时再发起主动回合。

        观察无论如何都入史，这样即便当下忙碌，用户下一句正常对话时 LLM 也能
        自然带出「刚刚……」——主动视觉的记忆全靠这条入史的观察。
        """
        self._scene_busy = True
        try:
            now = self._now()
            if now - self._last_scene_ts < _SCENE_COOLDOWN:
                return  # 冷却期内，忽略（前端已防抖，这里再保一层并控成本）
            self._last_scene_ts = now

            frame_b64 = await self.frames.request_frame(self.ws, self._ws_lock)
            if frame_b64 is None:
                return  # 抓不到帧（摄像头关闭/超时），放弃本次
            description = await self.frames.describe_scene(frame_b64, session=self)
            # 观察入史：标注为系统检测，区别于用户语音
            self.history.append(
                {"role": "user", "content": f"[系统检测到画面变化] {description}"}
            )

            # 忙碌态：不抢麦，观察已入史，留待下一个自然回合消化
            if not self._is_idle():
                return
            # 空闲态：发起主动回合，纳入 _reply_task 生命周期以便用户随时 barge-in
            self._reply_task = asyncio.create_task(self._proactive_reply())
        finally:
            self._scene_busy = False

    async def _proactive_reply(self) -> None:
        """空闲态下的主动发声：先非流式决策是否值得开口（SKIP=沉默），值得才播报。

        注意：主动回合不调 _remember()——它没有对应的用户语句，若与过期的
        _current_user_text 配对会污染记忆。slice 1 有意不入记忆，留待后续按需处理。
        """
        try:
            directive = {"role": "system", "content": PROACTIVE_DIRECTIVE}
            text = (await complete_chat(
                self.history + [directive], session=self, purpose="proactive_decision"
            )).strip()
        except asyncio.CancelledError:
            raise  # 决策阶段被 barge-in：直接取消，无副作用
        except Exception:
            return  # 决策失败：静默放弃，不打扰用户
        if not text or text.upper().startswith("SKIP"):
            return  # 不值得说，保持沉默（观察已入史）

        # 决定开口：占用新代号，走 TTS，全程复用既有 barge-in 闸门
        self._reply_gen += 1
        gen = self._reply_gen
        self._audio_inflight = False
        self._tts = StreamingTts(
            on_audio=lambda pcm: self._on_tts_audio(gen, pcm),
            on_error=lambda msg: self._on_tts_error(gen, msg),
        )
        await self._send_json({"type": "start", "gen": gen})
        try:
            await self._send_json({"type": "delta", "text": text})
            self._tts.feed(text)
            await self._tts.finish()
            self._audio_inflight = False
            self.history.append({"role": "assistant", "content": text})
            await self._send_json({"type": "done"})
        except asyncio.CancelledError:
            # 主动发言被用户打断：已说部分如实入史，保持上下文真实
            self.history.append(
                {"role": "assistant", "content": text + "（已被用户打断）"}
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
        # 被取消的 LLM/VL 调用来不及自报 end，统一兜底结束，清掉面板的 pending 行
        await self._flush_open_traces()
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

    # ---------- 调用追踪（所有 LLM/VL 调用都推前端调试面板） ----------

    async def _trace_start(self, kind: str, purpose: str, model: str) -> str:
        """发一次调用的 start 事件，返回配对用 id。kind: 'llm'|'vl'。

        await 发送（而非 fire），确保 start 一定先于对应 end 到达前端。
        gen 携带当前代号，便于面板与对话流按轮次对齐。
        """
        tid = uuid.uuid4().hex[:8]
        self._open_traces[tid] = self._now()
        await self._send_json({
            "type": "trace", "phase": "start", "id": tid,
            "kind": kind, "purpose": purpose, "model": model, "gen": self._reply_gen,
        })
        return tid

    async def _trace_end(self, tid: str, ms: float, summary: str = "", ok: bool = True) -> None:
        self._open_traces.pop(tid, None)
        await self._send_json({
            "type": "trace", "phase": "end", "id": tid,
            "ms": round(ms), "summary": summary, "ok": ok,
        })

    async def _flush_open_traces(self) -> None:
        """兜底结束所有仍挂着的 trace（barge-in 取消的 LLM/VL 调用来不及自报 end）。

        这些调用都跑在 _reply_task 内，取消必经 _cancel_reply，故在那里统一调用。
        """
        if not self._open_traces:
            return
        now = self._now()
        pending = list(self._open_traces.items())
        self._open_traces.clear()
        for tid, t0 in pending:
            await self._send_json({
                "type": "trace", "phase": "end", "id": tid,
                "ms": round((now - t0) * 1000), "summary": "已中断", "ok": False,
            })

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
