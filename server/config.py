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
ASR_SAMPLE_RATE = 16000  # 与前端 PCM worklet 输出采样率是数据契约，两端前端必须一致
# （clients/web/pcm-worklet.js、clients/capsule/src/lib/pcm-worklet.js）

TTS_MODEL = os.environ.get("TTS_MODEL", "cosyvoice-v2")
TTS_VOICE = os.environ.get("TTS_VOICE", "longxiaochun_v2")
TTS_SAMPLE_RATE = 22050  # 与前端播放上下文采样率是数据契约
# （clients/web/audio.js、clients/capsule/src/lib/audio.ts）

# ---------- 其他 ----------
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")  # 可选；未配置时 web_search 降级 DuckDuckGo

SYSTEM_PROMPT = (
    "你是一个语音视觉助手，正通过摄像头和麦克风与用户实时交流。"
    "你的回答会被语音合成念出来，所以：用口语化短句，不用 markdown、列表、emoji；"
    "一般两三句话内说完，用户追问再展开。"
)

# 主动视觉：画面变化后给 VL 的定向描述 prompt（侧重人是否在场、在做什么）
SCENE_VL_PROMPT = (
    "画面刚刚发生了明显变化。请用一句简短中文描述现在画面里有什么，"
    "特别说明是否有人在场、在做什么。只描述事实，不要推测，不要问候语。"
)

# 主动视觉：让 LLM 就这次画面变化决定是否值得主动开口（SKIP = 保持沉默）
PROACTIVE_DIRECTIVE = (
    "你通过摄像头注意到画面发生了变化（见最近一条「[系统检测到画面变化]」的描述）。"
    "请判断是否值得主动开口：若变化无关紧要、或画面中没有人可交流，只回复 SKIP，不要解释；"
    "若值得，就用一句口语化的短话主动和用户搭话，可以结合画面变化和之前的对话。"
)
