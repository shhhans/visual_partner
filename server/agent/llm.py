"""LLM 流式调用。走 OpenAI 兼容接口，便于无改动切换 MiniMax。"""

from typing import AsyncIterator

from openai import AsyncOpenAI

from config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, LLM_MODEL

_client = AsyncOpenAI(api_key=DASHSCOPE_API_KEY, base_url=DASHSCOPE_BASE_URL)


async def stream_chat(messages: list[dict]) -> AsyncIterator[str]:
    """产出助手回复的文本增量。工具调用循环（ReAct）后续在此扩展。"""
    stream = await _client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        stream=True,
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
