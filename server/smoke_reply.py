"""LLM→TTS 冒烟测试：一句最小对话，统计合成音频字节数。开发期工具。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from agent.llm import stream_chat
from config import SYSTEM_PROMPT
from voice.tts import StreamingTts


async def main() -> None:
    total = 0

    def on_audio(pcm: bytes) -> None:
        nonlocal total
        total += len(pcm)

    tts = StreamingTts(on_audio)
    text = []
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "用一句话介绍你自己"},
    ]
    async for delta in stream_chat(messages):
        text.append(delta)
        tts.feed(delta)
    await tts.finish()
    print("LLM:", "".join(text))
    print(f"TTS audio: {total} bytes (~{total / 2 / 22050:.1f}s)")


asyncio.run(main())
