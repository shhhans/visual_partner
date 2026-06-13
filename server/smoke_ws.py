"""WS 冒烟测试：连 /ws、发 2 秒静音 PCM，确认链路无异常。开发期工具，不属于产品代码。"""

import asyncio
import json

import websockets


async def main() -> None:
    async with websockets.connect("ws://127.0.0.1:8800/ws") as ws:
        silence = b"\x00" * 3200  # 100ms @ 16kHz PCM16
        for _ in range(20):
            await ws.send(silence)
            await asyncio.sleep(0.1)
        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=3)
                print("recv:", msg if isinstance(msg, str) else f"<{len(msg)} bytes audio>")
                if isinstance(msg, str) and json.loads(msg).get("type") == "error":
                    raise SystemExit(1)
        except asyncio.TimeoutError:
            print("no error events — ASR session OK")


asyncio.run(main())
