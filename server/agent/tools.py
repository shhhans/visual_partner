"""ReAct 工具定义与执行路由。

工具分两类（co-emission 分流，见 llm.stream_chat）：
  承载类 blocking — 结果决定答案，须回填 LLM 并续轮：
    look_at_camera / get_datetime / calculate / get_weather / web_search
  背景类 background — 不决定答案、不喂回 LLM、fire-and-forget：
    remember / correct_memory（agent 自主记忆，写入异步经单写者 worker）
背景类工具名登记在 BACKGROUND_TOOLS，stream_chat 据此分流。
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

REMEMBER = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": (
            "当用户透露了值得长期记住的稳定信息时调用，把它存入长期记忆，"
            "下次对话仍能记得。适用：身份与称呼、长期偏好、长期目标、正在进行的事项、"
            "重要关系。可与正常回复在同一轮一起调用，不影响你开口说话。"
            "不要记一次性寒暄、情绪、天气等易过期信息。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要记住的事实，简洁中文陈述句，如「用户喜欢喝美式咖啡」",
                }
            },
            "required": ["content"],
        },
    },
}

CORRECT_MEMORY = {
    "type": "function",
    "function": {
        "name": "correct_memory",
        "description": (
            "当你发现长期记忆里某条事实已经过时或错误时调用：删掉旧事实、写入更正后的。"
            "fact_id 是被回灌进上下文的旧事实前面 [#数字] 里的那个数字编号——"
            "按编号删除，不用复述原文。只能更正被回灌出来、带编号的事实。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fact_id": {
                    "type": "integer",
                    "description": "要删除的旧事实的编号，即回灌时该事实前 [#数字] 的数字",
                },
                "right": {
                    "type": "string",
                    "description": "更正后的新事实；若只是删除错误信息可留空",
                },
            },
            "required": ["fact_id", "right"],
        },
    },
}

TOOLS = [
    LOOK_AT_CAMERA, GET_DATETIME, CALCULATE, GET_WEATHER, WEB_SEARCH,
    REMEMBER, CORRECT_MEMORY,
]

# 背景类工具名：stream_chat 据此把这些 tool_call 分流为 fire-and-forget。
BACKGROUND_TOOLS = {"remember", "correct_memory"}


async def execute_tool(name: str, arguments: dict, session) -> str:
    # 记忆工具：写入经单写者 worker 异步落库，调用本身瞬时返回。
    # 延迟 import 规避 tools→memory→extractor→agent.llm→agent.tools 的循环依赖。
    if name == "remember":
        from memory import get_worker

        get_worker().remember(arguments.get("content", ""), session._session_id)
        return "已记录"

    if name == "correct_memory":
        from memory import get_worker

        # fact_id 可能被模型当字符串传来，统一强转 int；非法则不动记忆，回报错误。
        try:
            fact_id = int(arguments.get("fact_id"))
        except (TypeError, ValueError):
            return "无法识别要更正的记忆编号"
        get_worker().correct(fact_id, arguments.get("right", ""), session._session_id)
        return "已更正"

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
