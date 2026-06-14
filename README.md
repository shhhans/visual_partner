# Visual Partner — AI 视觉对话助手

打开摄像头与麦克风，AI 能看到画面、听到你说话，并实时语音回应。

## 架构总览

本项目采用**双线前端**：`clients/web` 是调试界面（后端直接托管的原生 JS 页面，便于联调与延迟观测），`clients/capsule` 是产品形态前端（Electron + React 桌面胶囊）。两者共用同一套 `server/` 后端与 `/ws` 协议。

```
clients/web      ─┐                     后端 (server/)                DashScope / MiniMax
 调试界面         │   WS 二进制 PCM16   ┌────────────────┐
clients/capsule  ─┤ ──────────────────→ │ 语音链路        │ → paraformer-realtime-v2 (ASR)
 产品形态(桌面胶囊)│   WS JSON(帧/事件)  │ ReAct 链路      │ → qwen-plus (LLM, 流式)
                  │ ←────────────────── │ 视觉链路        │ → qwen-vl-max (按需看图)
                 ─┘   WS 二进制 TTS音频  │ 记忆链路        │ → cosyvoice-v2 (TTS, 流式)
                                        └────────────────┘
```

四条链路在 `server/session.py` 里按回合编排：ASR 自动断句 → 召回长期记忆回灌 → ReAct 流式推理（按需触发视觉 / 工具）→ TTS 流式合成 → 回合落入记忆。

- 语音链路：`docs/voice-pipeline.md`
- 语音链路复盘（对照 deep-research，待办缺口 + 延迟埋点说明）：`docs/voice-pipeline-review.md`
- 视觉链路：`docs/vision-pipeline.md`
- ReAct 链路：`docs/react-pipeline.md`
- 设计文档（用户故事 / 成本控制）：`docs/design-doc.md`

## 能力一览

- **实时语音对话**：免按钮，ASR 自动断句驱动，支持插话打断（barge-in）即停 LLM 与 TTS。
- **按需视觉**：闲聊零视觉成本；LLM 用 `look_at_camera` 工具触发，按需取帧调 VL，同帧 8s 结果缓存。
- **主动视觉**：前端低帧率像素 diff 流水线检测画面变化 → VL 描述 → 空闲时主动开口（允许 SKIP 沉默）。
- **跨会话长期记忆**：回合落库 + LLM 抽取稳定事实，下次相关时自动回灌 prompt；agent 可用 `remember` 主动记、`correct_memory` 自愈纠错。
- **ReAct 工具**：`look_at_camera` / `get_datetime` / `calculate` / `get_weather` / `web_search`（承载类，回填续轮）+ `remember` / `correct_memory`（背景类，fire-and-forget）。
- **延迟观测**：每回合延迟埋点落库 + 前端实时显示。

## 运行

```bash
cd server
pip install -r requirements.txt
cp .env.example .env   # 然后填入 API key
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

浏览器打开 http://localhost:8000 （必须用 `localhost`，getUserMedia 要求安全上下文），授权摄像头与麦克风后即可对话——这是 `clients/web` 调试界面。

产品形态前端（桌面胶囊）单独运行：

```bash
cd clients/capsule
npm install
npm run dev      # 启动 Vite + Electron，连接同一个 :8000 后端
```

## 延迟观测

每个回合的延迟会落库并实时显示，方便对照 `docs/voice-pipeline-review.md` 里的延迟预算调参：

- **前端面板**：聊天区下方一行显示本回合 `turn_closure / ttft / tts_first / e2e_first_audio / total`（回合结束后出现）。
- **落库**：`server/metrics.db`（SQLite，进程启动自建，已 gitignore）。一回合一行，被打断的回合也记录。

```bash
sqlite3 server/metrics.db "select turn_index, turn_closure_ms, ttft_ms, e2e_first_audio_ms, total_ms, interrupted from turns order by id desc limit 10;"
```

> 注：尚未采集 token / 计费用量，面板「字数」为回复字符数而非 token。

## 长期记忆

跨会话记忆由进程级单写者后台 worker 串行落库，独立于任何单条连接的生命周期，全程不占回复热路径：

- **写入**：每个回合结束无条件 enqueue。worker 把原始回合存入 `episode`，并用一次非流式 LLM 抽取「关于用户的稳定事实」存入 `fact`（绝大多数闲聊回合抽取返回 SKIP，不产生事实，控成本）。
- **召回**：回复前用当前用户语句召回相关 `fact`，以临时 system 消息回灌上文；每条带 `[#id]` 编号。
- **自愈**：agent 用 `remember` 主动记下稳定信息，用 `correct_memory`（按 `[#id]` 删旧写新）纠正过时/错误事实。
- **存储**：`server/memory.db`（SQLite FTS5，进程启动自建，已 gitignore）。中文检索用 trigram 分词支持子串匹配，召回查询需 ≥3 字符。

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
  app.py               FastAPI 入口，托管静态页 + /ws WebSocket
  config.py            模型名、采样率、system prompt 等集中配置
  session.py           每连接的会话编排（语音/视觉/ReAct/记忆的粘合层）
  metrics.py           每回合延迟指标采集 + SQLite 落库（metrics.db）
  voice/asr.py         流式 ASR 封装（DashScope SDK 回调 → asyncio 队列）
  voice/tts.py         流式 TTS 封装（CosyVoice）
  agent/llm.py         LLM 流式调用 + co-emission 工具分流（OpenAI 兼容接口）
  agent/tools.py       ReAct 工具定义与执行路由
  agent/builtin_tools.py  内置工具实现（datetime/calculate/weather/web_search）
  vision/frame.py      帧缓存与 VL 调用
  memory/worker.py     进程级单写者后台记忆 worker（enqueue/recall）
  memory/provider.py   记忆后端契约 + SQLite 实现
  memory/extractor.py  从回合 LLM 抽取可长期复用的事实
  memory/sqlite_fts.py SQLite FTS5 落库与全文检索（memory.db）
clients/
  web/                 调试界面（后端托管的原生 JS 单页）
    index.html / main.js / audio.js / camera.js / style.css
    pcm-worklet.js     麦克风 AudioWorklet（Float32 → Int16 PCM）
  capsule/             产品形态前端（Electron + React 桌面胶囊）
    electron/          主进程 / preload
    src/               React UI（Capsule 组件、useVisualPartner 链路 hook、lib/ 音视频封装）
```
