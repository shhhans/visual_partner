"""FastAPI 入口：托管前端静态页 + /ws WebSocket。"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from config import DASHSCOPE_API_KEY, LLM_API_KEY
from memory import get_worker
from metrics import init_db
from session import Session


@asynccontextmanager
async def lifespan(app: FastAPI):
    # worker 由首个 WS 连接惰性启动；退出时排空，尽量不丢未落库的回合记忆。
    yield
    await get_worker().stop()


app = FastAPI(title="Visual Partner", lifespan=lifespan)

# LLM/VL 走 OpenAI 兼容供应商，必须有 key（默认回落 DASHSCOPE_API_KEY）
if not LLM_API_KEY:
    raise RuntimeError("请在 .env 配置 OPENAI_API_KEY（或 LLM_API_KEY）")
# ASR/TTS 走 DashScope 原生 SDK，单独需要 DASHSCOPE_API_KEY
if not DASHSCOPE_API_KEY:
    raise RuntimeError("ASR/TTS 走 DashScope，请在 .env 配置 DASHSCOPE_API_KEY")

# 延迟指标库：进程启动即建表（详见 metrics.py）
init_db()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    await Session(ws).run()


# 调试前端（clients/web）由后端直接托管；产品形态前端 clients/capsule 独立打包运行
WEB_DIR = Path(__file__).resolve().parent.parent / "clients" / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
