"""ReAct 工具定义与执行路由。

工具清单：
  look_at_camera — 视觉链路（qwen-vl-max，按需，零闲聊成本）
  get_datetime   — 本地时间，无网络开销
  calculate      — 安全 AST 计算，防 LLM 算错
  get_weather    — Open-Meteo 免费天气
  web_search     — Tavily（TAVILY_API_KEY 已配置时）或 DuckDuckGo 降级
"""

from agent.builtin_tools import calculate, get_datetime, get_weather, web_search

LOOK_AT_CAMERA = {
    "type": "function",
    "function": {
        "name": "look_at_camera",
        "description": (
            "查看用户摄像头当前画面。当用户提到画面中的内容"
            "（如'这是什么''你看我手里的东西'）时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "希望从画面中了解什么，用于定向描述",
                }
            },
            "required": ["question"],
        },
    },
}

GET_DATETIME = {
    "type": "function",
    "function": {
        "name": "get_datetime",
        "description": "获取当前日期和时间。用户问时间、日期、星期几时调用。",
        "parameters": {"type": "object", "properties": {}},
    },
}

CALCULATE = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": "精确计算数学表达式，避免 LLM 算错。用户要求计算或换算时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "合法数学表达式，如 '3.14 * 5 ** 2'",
                }
            },
            "required": ["expression"],
        },
    },
}

GET_WEATHER = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询城市实时天气。用户询问天气、是否要带伞时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名，如 '北京'、'上海'、'Tokyo'",
                }
            },
            "required": ["city"],
        },
    },
}

WEB_SEARCH = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "搜索互联网获取最新信息。用户问新闻、实时资讯、"
            "价格、近期事件等需要最新数据的问题时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                }
            },
            "required": ["query"],
        },
    },
}

TOOLS = [LOOK_AT_CAMERA, GET_DATETIME, CALCULATE, GET_WEATHER, WEB_SEARCH]


async def execute_tool(name: str, arguments: dict, session) -> str:
    if name == "look_at_camera":
        question = arguments.get("question", "画面里有什么")
        frame_b64 = await session.frames.request_frame(session.ws, session._ws_lock)
        if frame_b64 is None:
            return "摄像头画面暂时不可用，请确认摄像头已开启。"
        return await session.frames.describe(frame_b64, question, session=session)

    if name == "get_datetime":
        return get_datetime()

    if name == "calculate":
        return calculate(arguments.get("expression", ""))

    if name == "get_weather":
        return await get_weather(arguments.get("city", ""))

    if name == "web_search":
        return await web_search(arguments.get("query", ""))

    return f"未知工具：{name}"
