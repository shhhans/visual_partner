"""LLM 流式调用，支持单轮工具调用（ReAct look_at_camera）。"""

import json
from typing import AsyncIterator

from openai import AsyncOpenAI

from agent.tools import TOOLS, execute_tool
from config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, LLM_MODEL

_client = AsyncOpenAI(api_key=DASHSCOPE_API_KEY, base_url=DASHSCOPE_BASE_URL)


async def stream_chat(messages: list[dict], session=None) -> AsyncIterator[str]:
    """产出助手回复的文本增量。若 LLM 触发工具调用，先执行工具再继续流式输出。"""
    # 第一轮：允许工具调用，非流式（tool_calls 不能在 streaming 中可靠拼装）
    first = await _client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        stream=False,
    )
    choice = first.choices[0]

    if choice.finish_reason == "tool_calls" and session is not None:
        tool_calls = choice.message.tool_calls
        # 把助手的工具调用意图入历史
        messages = messages + [choice.message]

        tool_results = []
        for tc in tool_calls:
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            result = await execute_tool(tc.function.name, arguments, session)
            tool_results.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

        # VL 结果以文本入历史，图片本身不进历史（成本策略 C5）
        messages = messages + tool_results

        # 第二轮：有了工具结果，流式输出最终回答
        stream = await _client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
        return

    # 无工具调用：直接流式输出（走流式避免 non-stream 路径的延迟）
    if choice.message.content:
        yield choice.message.content
        return

    # 极少数情况：first call 没有内容也没有 tool_calls，再走一次流式保底
    stream = await _client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        stream=True,
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
