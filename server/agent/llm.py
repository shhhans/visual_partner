"""LLM 流式调用，支持多轮 ReAct 工具调用循环。

流程：
  每轮流式读取 LLM 响应；
  若出现 finish_reason="tool_calls" → 执行工具 → 把结果以 tool 角色追加到 working_messages → 继续下一轮；
  直到 finish_reason="stop" 或达到 MAX_REACT_STEPS 上限为止。

延迟优化：content delta 在流式过程中即时 yield，tool_calls delta 在内存中拼装，
两者在同一轮 LLM 响应中互斥，不会同时出现。
"""

import asyncio
import json
import time
from typing import AsyncIterator

from openai import AsyncOpenAI

from agent.tools import TOOLS, execute_tool
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

_client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

MAX_REACT_STEPS = 5


async def complete_chat(
    messages: list[dict], session=None, purpose: str = "complete"
) -> str:
    """非流式获取完整回复，不带工具。

    用于主动视觉「是否值得主动开口」的决策：需要拿到完整文本才能判断 SKIP，
    且主动发言对首字延迟不敏感，故不走流式、不挂工具。
    """
    tid = await session._trace_start("llm", purpose, LLM_MODEL) if session else None
    t0 = time.monotonic()
    try:
        resp = await _client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            stream=False,
        )
    except Exception:
        if tid:
            await session._trace_end(tid, (time.monotonic() - t0) * 1000, "调用失败", ok=False)
        raise
    text = resp.choices[0].message.content or ""
    if tid:
        await session._trace_end(tid, (time.monotonic() - t0) * 1000, text[:40] or "(空)")
    return text


async def stream_chat(messages: list[dict], session=None) -> AsyncIterator[str]:
    """产出助手回复的文本增量，内部完成 ReAct 多轮工具调用。"""
    working = list(messages)

    for _step in range(MAX_REACT_STEPS):
        tid = await session._trace_start("llm", "reply", LLM_MODEL) if session else None
        t0 = time.monotonic()
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

        # 本轮 LLM 调用结束：推 trace（文本则摘要，工具则标注调用了哪些工具）
        if tid:
            if content_parts:
                summary = "".join(content_parts)[:40]
            elif tc_acc:
                summary = "调用工具 " + ",".join(a["name"] for a in tc_acc.values())
            else:
                summary = ""
            await session._trace_end(tid, (time.monotonic() - t0) * 1000, summary)

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

        # 先解析参数并通知前端"工具调用开始"（ReAct 可视化）。
        # 带 call_id（=tool_call_id）供前端精确关联 result；带 gen 供 barge-in 时清理。
        gen = getattr(session, "_reply_gen", 0)
        parsed: list[tuple[dict, dict]] = []
        for acc in tc_acc.values():
            try:
                args = json.loads(acc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            parsed.append((acc, args))
            await session._send_json({
                "type": "tool_call",
                "call_id": acc["id"],
                "name": acc["name"],
                "arguments": args,
                "gen": gen,
            })

        # 并发执行所有工具（本轮可能有多个 tool_call），结果回来即推前端
        async def _run(acc, args):
            result = await execute_tool(acc["name"], args, session)
            await session._send_json({
                "type": "tool_result",
                "call_id": acc["id"],
                "name": acc["name"],
                "content": result,
                "gen": gen,
            })
            return {"role": "tool", "tool_call_id": acc["id"], "content": result}

        tool_msgs = await asyncio.gather(*[_run(acc, args) for acc, args in parsed])
        working.extend(tool_msgs)

    # 到达最大步数上限，强制最终回答（不带工具）
    tid = await session._trace_start("llm", "reply_final", LLM_MODEL) if session else None
    t0 = time.monotonic()
    final_parts: list[str] = []
    stream = await _client.chat.completions.create(
        model=LLM_MODEL,
        messages=working,
        stream=True,
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            final_parts.append(chunk.choices[0].delta.content)
            yield chunk.choices[0].delta.content
    if tid:
        await session._trace_end(tid, (time.monotonic() - t0) * 1000, "".join(final_parts)[:40])
