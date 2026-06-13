"""FastAPI 入口：托管前端静态页 + /ws WebSocket。"""

from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from config import DASHSCOPE_API_KEY
from metrics import init_db
from session import Session

app = FastAPI(title="Visual Partner")

if not DASHSCOPE_API_KEY:
    raise RuntimeError("请设置环境变量 DASHSCOPE_API_KEY")

# 延迟指标库：进程启动即建表（详见 metrics.py）
init_db()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    await Session(ws).run()


WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
