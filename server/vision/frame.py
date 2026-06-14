"""视觉链路实现。设计见 docs/vision-pipeline.md。

取帧协议：
  后端发 {type:"capture", id: <uuid>} → 前端用 captureFrame() 抓帧 →
  前端回传 {type:"frame", id: <uuid>, data: <base64 JPEG>} → receive_frame() resolve future
"""

import asyncio
import json
import time
import uuid

from openai import AsyncOpenAI

from config import SCENE_VL_PROMPT, VL_API_KEY, VL_BASE_URL, VL_MODEL

_vl_client = AsyncOpenAI(api_key=VL_API_KEY, base_url=VL_BASE_URL)

_VL_CACHE_TTL = 8.0   # 同一帧 N 秒内复用 VL 结果（成本策略 C8）
_FRAME_TIMEOUT = 5.0  # 前端抓帧最长等待时间


class FrameStore:
    def __init__(self) -> None:
        self.latest_b64: str | None = None
        self.latest_ts: float = 0.0
        self._pending: dict[str, asyncio.Future] = {}
        # VL 结果缓存：{frame_b64_前64字节: (result_str, ts)}
        self._vl_cache: dict[str, tuple[str, float]] = {}

    def receive_frame(self, request_id: str, b64: str) -> None:
        """session._on_json 收到 frame 消息后调用，resolve 等待中的 future。"""
        self.latest_b64 = b64
        self.latest_ts = time.monotonic()
        fut = self._pending.pop(request_id, None)
        if fut is not None and not fut.done():
            fut.set_result(b64)

    async def request_frame(self, ws, ws_lock) -> str | None:
        """向前端索取最新一帧。超时则降级返回最近缓存帧（可能为 None）。"""
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending[request_id] = fut

        msg = json.dumps({"type": "capture", "id": request_id}, ensure_ascii=False)
        try:
            async with ws_lock:
                await ws.send_text(msg)
        except Exception:
            self._pending.pop(request_id, None)
            return self.latest_b64  # WS 异常，降级用缓存帧

        try:
            return await asyncio.wait_for(fut, timeout=_FRAME_TIMEOUT)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            return self.latest_b64  # 超时降级

    async def _call_vl(
        self, frame_b64: str, prompt: str, session=None, purpose: str = "vision"
    ) -> str:
        """单次 VL 调用：一帧图 + 一段文字 prompt，返回描述文本。

        传入 session 时，调用前后推 trace 事件到调试面板（成本/行为可视化）。
        """
        tid = await session._trace_start("vl", purpose, VL_MODEL) if session else None
        t0 = time.monotonic()
        try:
            response = await _vl_client.chat.completions.create(
                model=VL_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"},
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
        except Exception:
            if tid:
                await session._trace_end(tid, (time.monotonic() - t0) * 1000, "调用失败", ok=False)
            raise
        result = response.choices[0].message.content or "无法理解画面"
        if tid:
            await session._trace_end(tid, (time.monotonic() - t0) * 1000, result[:40])
        return result

    async def describe(self, frame_b64: str, question: str, session=None) -> str:
        """被动视觉：结合用户问题做定向描述。同帧短期内复用缓存结果。"""
        cache_key = frame_b64[:64]
        cached = self._vl_cache.get(cache_key)
        if cached is not None:
            result, ts = cached
            if time.monotonic() - ts < _VL_CACHE_TTL:
                return result  # 缓存命中：无真实 VL 调用，不计入 trace

        prompt = (
            f"请用简短的中文描述画面内容，重点回答：{question}。"
            "只描述画面事实，不要推测，不要问候语。"
            # 穿搭增强：仍只报事实（建议由 LLM 给），但把建议用得上的维度如实捕捉，
            # 让下游 LLM 有据可依——颜色、单品款式、版型是否合身、场合是否得体。
            "若问题涉及穿着搭配，请如实说明各件单品的颜色、款式，"
            "以及版型是否合身、整体是否适合相应场合。"
        )
        result = await self._call_vl(frame_b64, prompt, session, "vision_passive")
        self._vl_cache[cache_key] = (result, time.monotonic())
        return result

    async def describe_scene(self, frame_b64: str, session=None) -> str:
        """主动视觉：画面变化后的定向描述，侧重人是否在场。

        不走缓存——每次变化都要对「当前画面」做新鲜判断，复用旧结果会错判状态。
        """
        return await self._call_vl(frame_b64, SCENE_VL_PROMPT, session, "vision_active")
