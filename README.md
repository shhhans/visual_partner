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
set DASHSCOPE_API_KEY=sk-xxx   # PowerShell: $env:DASHSCOPE_API_KEY="sk-xxx"
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

浏览器打开 http://localhost:8000 ，授权摄像头与麦克风后即可对话。

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
