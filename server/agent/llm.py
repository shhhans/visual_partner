"""LLM 流式调用，支持多轮 ReAct 工具调用循环。

流程：
  每轮流式读取 LLM 响应；
  若出现 finish_reason="tool_calls" → 执行工具 → 把结果以 tool 角色追加到 working_messages → 继续下一轮；
  直到 finish_reason="stop" 或达到 MAX_REACT_STEPS 上限为止。

延迟优化：content delta 在流式过程中即时 yield，tool_calls delta 在内存中拼装，
两者在同一轮 LLM 响应中互斥，不会同时出现。
"""

import json
from typing import AsyncIterator

from openai import AsyncOpenAI

from agent.tools import TOOLS, execute_tool
from config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, LLM_MODEL

_client = AsyncOpenAI(api_key=DASHSCOPE_API_KEY, base_url=DASHSCOPE_BASE_URL)

MAX_REACT_STEPS = 5


async def stream_chat(messages: list[dict], session=None) -> AsyncIterator[str]:
    """产出助手回复的文本增量，内部完成 ReAct 多轮工具调用。"""
    working = list(messages)

    for _step in range(MAX_REACT_STEPS):
        stream = await _client.chat.completions.create(
            model=LLM_MODEL,
            messages=working,
            tools=TOOLS,
            tool_choice="auto",
            stream=True,
        )

        # 拼装本轮响应
        content_parts: list[str] = []
        tc_acc: dict[int, dict] = {}   # index → {id, name, arguments}
        finish_reason: str | None = None

        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta

            # content delta：即时 yield（零额外延迟）
            if delta.content:
                yield delta.content
                content_parts.append(delta.content)

            # tool_calls delta：在内存中拼装，本轮有 content 时不会出现
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    acc = tc_acc.setdefault(
                        tc.index, {"id": "", "name": "", "arguments": ""}
                    )
                    if tc.id:
                        acc["id"] += tc.id
                    if tc.function:
                        if tc.function.name:
                            acc["name"] += tc.function.name
                        if tc.function.arguments:
                            acc["arguments"] += tc.function.arguments

        # 无工具调用 → 本次回复已完整输出，结束
        if finish_reason != "tool_calls" or not tc_acc or session is None:
            return

        # 把助手的工具调用意图写入 working_messages（历史完整性）
        working.append({
            "role": "assistant",
            "content": "".join(content_parts) or None,
            "tool_calls": [
                {
                    "id": acc["id"],
                    "type": "function",
                    "function": {"name": acc["name"], "arguments": acc["arguments"]},
                }
                for acc in tc_acc.values()
            ],
        })

        # 并发执行所有工具（本轮可能有多个 tool_call）
        import asyncio
        async def _run(acc):
            try:
                args = json.loads(acc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            result = await execute_tool(acc["name"], args, session)
            return {"role": "tool", "tool_call_id": acc["id"], "content": result}

        tool_msgs = await asyncio.gather(*[_run(acc) for acc in tc_acc.values()])
        working.extend(tool_msgs)

    # 到达最大步数上限，强制最终回答（不带工具）
    stream = await _client.chat.completions.create(
        model=LLM_MODEL,
        messages=working,
        stream=True,
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
