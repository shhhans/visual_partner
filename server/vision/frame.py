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

from config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, VL_MODEL

_vl_client = AsyncOpenAI(api_key=DASHSCOPE_API_KEY, base_url=DASHSCOPE_BASE_URL)

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

    async def describe(self, frame_b64: str, question: str) -> str:
        """调 qwen-vl-max，结合用户问题做定向描述。同帧短期内复用缓存结果。"""
        cache_key = frame_b64[:64]
        cached = self._vl_cache.get(cache_key)
        if cached is not None:
            result, ts = cached
            if time.monotonic() - ts < _VL_CACHE_TTL:
                return result

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
                        {
                            "type": "text",
                            "text": (
                                f"请用简短的中文描述画面内容，重点回答：{question}。"
                                "只描述画面事实，不要推测，不要问候语。"
                            ),
                        },
                    ],
                }
            ],
        )
        result = response.choices[0].message.content or "无法理解画面"
        self._vl_cache[cache_key] = (result, time.monotonic())
        return result
