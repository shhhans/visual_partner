"""从一个回合里抽取「值得长期记住的事实」。

跑在后台 worker 里（脱离回复热路径），用非流式 LLM 调用。借鉴主动视觉的 SKIP 模式：
绝大多数闲聊回合没有可长期复用的信息，此时模型回 SKIP，不产生任何事实，控成本。

只抽取关于用户的、跨会话仍成立的稳定信息（身份/偏好/长期目标/在办事项/重要关系），
不记一次性寒暄、不记助手自己说的话、不做推测。
"""

import asyncio
import logging

from agent.llm import complete_chat

log = logging.getLogger(__name__)

# 抽取走非流式 LLM 调用，须设超时：worker 是单消费者，LLM 挂起会永久阻塞队列。
# 超时即放弃本回合抽取（episode 已另行落库），不影响后续回合。
_EXTRACT_TIMEOUT = 30.0

_EXTRACT_SYSTEM = (
    "你是对话记忆抽取器。从下面这轮对话里，提取关于用户的、跨会话仍然成立的稳定事实，"
    "例如：身份与称呼、长期偏好、长期目标、正在进行的事项、重要关系。"
    "规则：\n"
    "- 只记用户的稳定信息，不记一次性寒暄、情绪、天气等易过期内容。\n"
    "- 不记助手说的话，不做推测，只记对话里明确出现的事实。\n"
    "- 每条事实一行，用简洁中文陈述句，不加序号、不加解释。\n"
    "- 若这轮没有任何值得长期记住的事实，只输出 SKIP，不要输出别的。"
)


async def extract_facts(user_text: str, assistant_text: str) -> list[str]:
    """返回本回合抽取到的事实列表；无可记则返回空列表。"""
    convo = f"用户: {user_text}\n助手: {assistant_text}"
    messages = [
        {"role": "system", "content": _EXTRACT_SYSTEM},
        {"role": "user", "content": convo},
    ]
    # session=None：抽取不进前端 trace 面板，也不计回合延迟指标。
    try:
        raw = await asyncio.wait_for(
            complete_chat(messages, session=None, purpose="memory_extract"),
            timeout=_EXTRACT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.warning("fact extraction timed out after %.0fs, skipping turn", _EXTRACT_TIMEOUT)
        return []
    text = raw.strip()
    if not text or text.upper().startswith("SKIP"):
        return []
    facts = [line.strip(" -·\t") for line in text.splitlines()]
    return [f for f in facts if f and f.upper() != "SKIP"]
