# Visual Partner — AI 视觉对话助手

打开摄像头与麦克风，AI 能看到画面、听到你说话，并实时语音回应。

## 架构总览

```
浏览器 (web/)                          后端 (server/)                DashScope / MiniMax
┌──────────────┐   WS 二进制 PCM16    ┌────────────────┐
│ 麦克风采集    │ ──────────────────→ │ 语音链路        │ → paraformer-realtime-v2 (ASR)
│ 摄像头抓帧    │   WS JSON(帧/事件)  │ ReAct 链路      │ → qwen-plus (LLM, 流式)
│ 音频播放队列  │ ←────────────────── │ 视觉链路        │ → qwen-vl-max (按需看图)
└──────────────┘   WS 二进制 TTS音频  └────────────────┘ → cosyvoice-v2 (TTS, 流式)
```

- 语音链路：`docs/voice-pipeline.md`
- 视觉链路：`docs/vision-pipeline.md`
- ReAct 链路：`docs/react-pipeline.md`
- 设计文档（用户故事 / 成本控制）：`docs/design-doc.md`

## 运行

```bash
cd server
pip install -r requirements.txt
cp .env.example .env   # 然后填入 API key
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

浏览器打开 http://localhost:8000 ，授权摄像头与麦克风后即可对话。

## 配置供应商（.env）

LLM 与 VL 走 **OpenAI 兼容接口**，供应商在 `server/.env` 里配置，可整体切换或对 LLM/VL 分别覆盖；ASR/TTS 仍走 DashScope 原生 SDK。

| 变量 | 说明 | 默认 |
|---|---|---|
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` | LLM+VL 默认供应商；key 留空则回落 `DASHSCOPE_API_KEY` | DashScope 兼容端点 |
| `LLM_MODEL` / `VL_MODEL` | 模型名 | `qwen-plus` / `qwen-vl-max` |
| `LLM_BASE_URL/API_KEY`、`VL_BASE_URL/API_KEY` | 可选，把 LLM 或 VL 单独切到别的供应商 | 继承 `OPENAI_*` |
| `DASHSCOPE_API_KEY` | ASR/TTS 必填（MiniMax 无 ASR） | — |

例：把 LLM+VL 切到 MiniMax（`.env` 中设 `OPENAI_BASE_URL=https://api.minimax.io/v1`、`OPENAI_API_KEY=...`、`LLM_MODEL=VL_MODEL=MiniMax-M3`），ASR/TTS 继续用 DashScope。完整示例见 `server/.env.example`。

## 目录结构

```
server/
  app.py            FastAPI 入口，托管静态页 + /ws WebSocket
  config.py         模型名、采样率等集中配置
  session.py        每连接的会话编排（语音/视觉/ReAct 的粘合层）
  voice/asr.py      流式 ASR 封装（DashScope SDK 回调 → asyncio 队列）
  voice/tts.py      流式 TTS 封装（CosyVoice）
  agent/llm.py      LLM 流式调用（OpenAI 兼容接口）
  agent/tools.py    ReAct 工具定义（look_at_camera 等）
  vision/frame.py   帧缓存与 VL 调用
web/
  index.html / main.js / audio.js / camera.js / style.css
  pcm-worklet.js    麦克风 AudioWorklet（Float32 → Int16 PCM）
```
