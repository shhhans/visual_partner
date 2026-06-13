"""ReAct 工具定义（骨架）。实现计划见 docs/react-pipeline.md。"""

# 视觉按需化是核心成本策略：模型自行决定何时调用，闲聊时零视觉开销
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
    """TODO(vision): 取帧 → qwen-vl 定向描述 → 返回文本。见 server/vision/frame.py"""
    raise NotImplementedError(name)
