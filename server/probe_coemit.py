"""探针：测 qwen（dashscope 兼容端点）是否支持 co-emission——
即同一轮 assistant 响应里 content 与 tool_calls 能否同时出现。

结论决定记忆架构走向：
  - 若支持：shallow(念话) 与 deep(工具/记忆) 可由一次推理原生表达，
    content 边到边喂 TTS，tool_calls 收尾后甩给后台 deep。
  - 若不支持：退回「content 内嵌结构化 tag」方案，不依赖原生 co-emission。

用法（在 server/ 下，.env 已配 key）：
  python probe_coemit.py            # 默认型号列表
  python probe_coemit.py qwen-max   # 指定一个或多个型号
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from openai import AsyncOpenAI

from config import LLM_API_KEY, LLM_BASE_URL

_client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

# 默认轮询的型号；命令行可覆盖。部分型号可能不存在 → 捕获并跳过。
DEFAULT_MODELS = ["qwen-plus", "qwen-plus-latest", "qwen-max", "qwen3-max"]

# 一个明显需要工具才能回答的问题 + 明确要求「先说一句过渡语再调工具」，
# 给模型最大的 co-emit 机会（auto 路径，贴近生产用法）。
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询某城市实时天气。",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "城市名"}},
                "required": ["city"],
            },
        },
    }
]
MESSAGES = [
    {
        "role": "system",
        "content": (
            "你是语音助手。在调用任何工具之前，必须先用一句自然的口语告诉用户"
            "你正要做什么（例如「我帮你查一下啊」），然后再调用工具。"
        ),
    },
    {"role": "user", "content": "北京现在天气怎么样，要不要带伞？"},
]


async def probe_nonstream(model: str) -> str:
    resp = await _client.chat.completions.create(
        model=model, messages=MESSAGES, tools=TOOLS, tool_choice="auto", stream=False
    )
    msg = resp.choices[0].message
    has_content = bool(msg.content and msg.content.strip())
    has_tools = bool(msg.tool_calls)
    verdict = "✅ CO-EMIT" if (has_content and has_tools) else "— 互斥"
    content_preview = (msg.content or "").strip()[:50]
    tools_preview = ",".join(tc.function.name for tc in (msg.tool_calls or []))
    return (
        f"  [非流式] content={has_content} tool_calls={has_tools}  {verdict}\n"
        f"           content: {content_preview!r}\n"
        f"           tools:   {tools_preview or '(无)'}"
    )


async def probe_stream(model: str) -> str:
    stream = await _client.chat.completions.create(
        model=model, messages=MESSAGES, tools=TOOLS, tool_choice="auto", stream=True
    )
    saw_content = False
    saw_tool = False
    order: list[str] = []  # 记录首次出现顺序，判断 content 是否先于 tool_calls
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            if not saw_content:
                order.append("content")
            saw_content = True
        if delta.tool_calls:
            if not saw_tool:
                order.append("tool_calls")
            saw_tool = True
    if saw_content and saw_tool:
        verdict = f"✅ CO-EMIT（首现顺序: {' → '.join(order)}）"
    elif saw_tool:
        verdict = "— 只有 tool_calls（互斥）"
    elif saw_content:
        verdict = "— 只有 content（没触发工具，换更刚需的问题再试）"
    else:
        verdict = "?? 空响应"
    return f"  [流式]   content={saw_content} tool_calls={saw_tool}  {verdict}"


async def main() -> None:
    models = sys.argv[1:] or DEFAULT_MODELS
    print(f"端点: {LLM_BASE_URL}")
    print(f"测试型号: {', '.join(models)}\n")
    for model in models:
        print(f"== {model} ==")
        for probe in (probe_nonstream, probe_stream):
            try:
                print(await probe(model))
            except Exception as exc:  # 型号不存在 / 调用失败：记录后继续下一个
                print(f"  [{probe.__name__}] 失败: {type(exc).__name__}: {exc}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
