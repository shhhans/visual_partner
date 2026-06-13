"""ReAct 工具定义与执行。视觉按需化是核心成本策略：闲聊时零视觉开销。"""

LOOK_AT_CAMERA = {
    "type": "function",
    "function": {
        "name": "look_at_camera",
        "description": "查看用户摄像头当前画面。当用户提到画面中的内容（如'这是什么''你看我手里的东西'）时调用。",
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

TOOLS = [LOOK_AT_CAMERA]


async def execute_tool(name: str, arguments: dict, session) -> str:
    if name == "look_at_camera":
        question = arguments.get("question", "画面里有什么")
        frame_b64 = await session.frames.request_frame(session.ws, session._ws_lock)
        if frame_b64 is None:
            return "摄像头画面暂时不可用，请确认摄像头已开启。"
        return await session.frames.describe(frame_b64, question)
    return f"未知工具：{name}"
