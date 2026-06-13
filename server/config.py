"""集中配置。模型与采样率改这里，不要散落在业务代码中。"""

import os

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")  # 可选；未配置时 web_search 降级 DuckDuckGo

# OpenAI 兼容接口（LLM / VL 走这里，便于后续切 MiniMax 做 A/B）
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 模型分层：大脑用 plus 而非 max（成本策略 C3，见 docs/design-doc.md）
LLM_MODEL = "qwen-plus"
VL_MODEL = "qwen-vl-max"

ASR_MODEL = "paraformer-realtime-v2"
ASR_SAMPLE_RATE = 16000  # 与 web/pcm-worklet.js 的输出采样率是数据契约，两边必须一致

TTS_MODEL = "cosyvoice-v2"
TTS_VOICE = "longxiaochun_v2"
TTS_SAMPLE_RATE = 22050  # 与 web/audio.js 播放上下文采样率是数据契约

SYSTEM_PROMPT = (
    "你是一个语音视觉助手，正通过摄像头和麦克风与用户实时交流。"
    "你的回答会被语音合成念出来，所以：用口语化短句，不用 markdown、列表、emoji；"
    "一般两三句话内说完，用户追问再展开。"
)
