# 语音链路 —— 对照 deep-research 的复盘

> 输入：`docs/deep-research-report.md`（ChatGPT deep research）。
> 目的：把报告的建议**按是否适用于本项目**过滤一遍，沉淀出可执行的改进项。
> 现状基线：级联架构 + DashScope（paraformer / qwen-plus / cosyvoice）+ 单条 WebSocket，详见 `voice-pipeline.md`。

## 一句话结论

报告是 **OpenAI Realtime / LiveKit 生态视角**写的，本项目已选**级联 + DashScope + WS** 路线，
报告大半内容不适用。真正值得落地的只有 **3 个缺口**，其余应主动跳过以免过度工程。

## 不适用 / 主动跳过（避免过度工程）

| 报告建议 | 为何跳过 |
|---|---|
| WebRTC 优于 WebSocket 传音频 | 已用 WS 二进制送 PCM，MVP 够用；AEC 靠浏览器 `echoCancellation`。改造无收益。 |
| 真 full-duplex / speech-to-speech 模型（Moshi 等） | 报告自己也列为比赛反例。 |
| 外部 semantic turn detector 与 Realtime 模型冲突、需额外接 STT | 未用 OpenAI Realtime，不存在双重 endpointing 问题。 |
| on-device / 端侧 streaming ASR | 报告承认强结果集中在英语；本项目中文为主、云端 paraformer 是正确选择。 |
| 纯语音回合的「快/慢路径」双模型 | qwen-plus 直接流式答已足够快；双路径的价值在视觉/工具边界（见缺口 3），而非纯语音。 |

## 三个值得落地的缺口

### 缺口 1：barge-in 误触发（优先级最高，纯收益）

- **报告依据**：adaptive interruption —— 不该「一听到人声/新文本就打断」，需区分 backchannel
  （"嗯""对""啊对对对"）与真正抢话。
- **现状**：`session.py:90-96` 中 `_on_asr` 只要出现任意新识别文本即 `_cancel_reply`。
  与 `voice-pipeline.md` 自列 TODO「可加最小字数阈值」一致。
- **设计取舍**：单纯「最小字数」太糙——中文单字也可能是真打断。倾向用
  **partial 文本长度 + 是否在短时间窗内持续增长** 作为判断，而非裸字数阈值。
- **状态**：可立即实施，不依赖外部信息。

### 缺口 2：turn closure（结束判定）延迟 —— 报告称「最常见的大头」

- **报告依据**：延迟预算表把 turn detector/endpointing 列为最易拖慢感知的环节；
  VAD-only 在用户停顿（"嗯…这个嘛…"）时易抢答。
- **现状**：完全依赖 paraformer 的 VAD `sentence_end`，且关闭 `semantic_punctuation`
  （`asr.py:58`）换低延迟——副作用即上述抢答风险。
- **关键动作**：先查 DashScope paraformer-realtime-v2 是否暴露 endpointing/静音判定
  参数（如 `max_end_silence` 一类）。这是延迟与抢答率的主旋钮，比换模型重要。
- **状态**：待查 DashScope 文档，确认可调面后再调参。

### 缺口 3：preamble —— 本质是语音 × 视觉的边界问题

- **报告依据**：工具调用/慢操作前先播一句短 preamble（"我看一下啊"），描述动作而非内部推理，
  避免「静默卡顿」；结果 ready 后再接完整回答。
- **现状**：单路径。LLM 决定调 `look_at_camera`（一次 VL 0.3~2s）时，TTS 无内容可播，
  回合会在工具调用处静默卡住。视觉链路接上后**立即**会遇到。
- **设计动作**：现在就在 `session.py` 的回复编排里留好钩子——工具调用触发时先发一句
  占位回应给 TTS，VL 结果回来再续；被打断时占位与后续都要可取消。
- **状态**：随视觉链路一起实施，但架构口子现在留。

## 落地优先级

1. **缺口 1（barge-in 阈值）**：现在做，纯收益、无外部依赖。
2. **缺口 2（endpointing 调参）**：先查 DashScope 可调参数 → 再调。
3. **缺口 3（preamble 钩子）**：随视觉链路落地，架构提前留口。

## 延迟埋点与落库（已实现）

调参的前提是先有实测数字。已加服务端埋点，每回合一行落库 + 推前端调试面板。

- **采集**：`server/session.py` 在各节点打单调时钟时间戳（`_now()`），
  回合完成或被打断时 `_flush_metrics()` 折算成毫秒。
- **落库**：`server/metrics.py`，stdlib `sqlite3`，库文件 `server/metrics.db`（零新依赖）。
  表 `turns` 一回合一行；被打断的回合也落，`interrupted=1`、`total_ms` 留空。
- **前端**：`{type:"metrics", ...}` 推到页面右下角调试面板（`web/main.js` `renderMetrics`），
  最新在上、保留 8 行，中断回合标黄。

指标字段（对照 deep-research 延迟预算表）：

| 字段 | 含义 | 报告理想值 |
|---|---|---|
| `turn_closure_ms` | 最后一次 ASR partial → sentence_end | 200–400ms |
| `ttft_ms` | sentence_end → LLM 首个增量 | 80–200ms |
| `tts_first_ms` | LLM 首增量 → TTS 首帧音频 | 75–150ms |
| `e2e_first_audio_ms` | sentence_end → TTS 首帧（说完→听到第一个字） | <800ms |
| `total_ms` | sentence_end → 本回合 done（中断为空） | — |

> 注：`barge-in 停播` 与端到端「感知」延迟含网络往返，服务端数字偏乐观；
> 真正的用户感知项需前端插桩才准（见缺口 2 后续）。当前先用服务端基线。

### 查询示例

```bash
# 看最近 20 回合
sqlite3 server/metrics.db "select turn_index, turn_closure_ms, ttft_ms, e2e_first_audio_ms, total_ms, interrupted from turns order by id desc limit 20;"
# 各指标 p50/p90（粗略，SQLite 无原生分位，用平均先看趋势）
sqlite3 server/metrics.db "select avg(turn_closure_ms), avg(ttft_ms), avg(e2e_first_audio_ms) from turns where interrupted=0;"
```
