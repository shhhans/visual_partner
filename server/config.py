"""集中配置。模型、供应商、采样率改这里或 .env，不要散落在业务代码中。

供应商通过 .env 配置（OpenAI 兼容接口）。LLM 与 VL 默认共用同一供应商，
也可用 LLM_* / VL_* 变量分别覆盖到不同供应商，便于做 A/B 或混合部署。
ASR/TTS 仍走 DashScope 原生 SDK——OpenAI 接口没有对应的实时流式语音能力。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 读取 server/.env（与本文件同目录，不入库见 .gitignore）。显式定位，不依赖运行 cwd。
# override=False：已存在的系统环境变量优先，.env 仅作补充。
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

# ---------- OpenAI 兼容供应商（LLM / VL） ----------
# 默认供应商：所有 OpenAI 兼容调用的基准 base_url 与 key。
# 默认指向阿里 DashScope 兼容端点，保持改造前的行为不变。
OPENAI_BASE_URL = os.environ.get(
    "OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
# key 缺省回落到 DASHSCOPE_API_KEY，让仅配了 DashScope 的旧环境无需改动即可运行。
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("DASHSCOPE_API_KEY", "")

# LLM：可单独覆盖供应商，缺省回落到默认供应商。
LLM_BASE_URL = os.environ.get("LLM_BASE_URL") or OPENAI_BASE_URL
LLM_API_KEY = os.environ.get("LLM_API_KEY") or OPENAI_API_KEY
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen-plus")

# VL：可单独覆盖供应商，缺省回落到默认供应商。
VL_BASE_URL = os.environ.get("VL_BASE_URL") or OPENAI_BASE_URL
VL_API_KEY = os.environ.get("VL_API_KEY") or OPENAI_API_KEY
VL_MODEL = os.environ.get("VL_MODEL", "qwen-vl-max")

# ---------- DashScope 原生 SDK（ASR / TTS） ----------
# DashScope SDK 从环境变量 DASHSCOPE_API_KEY 读取凭证（load_dotenv 后已就绪）。
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")

ASR_MODEL = os.environ.get("ASR_MODEL", "paraformer-realtime-v2")
ASR_SAMPLE_RATE = 16000  # 与 web/pcm-worklet.js 的输出采样率是数据契约，两边必须一致

TTS_MODEL = os.environ.get("TTS_MODEL", "cosyvoice-v2")
TTS_VOICE = os.environ.get("TTS_VOICE", "longxiaochun_v2")
TTS_SAMPLE_RATE = 22050  # 与 web/audio.js 播放上下文采样率是数据契约

# ---------- 其他 ----------
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")  # 可选；未配置时 web_search 降级 DuckDuckGo

SYSTEM_PROMPT = (
    "你是一个语音视觉助手，正通过摄像头和麦克风与用户实时交流。"
    "你的回答会被语音合成念出来，所以：用口语化短句，不用 markdown、列表、emoji；"
    "一般两三句话内说完，用户追问再展开。"
)
