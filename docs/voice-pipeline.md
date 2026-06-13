# 语音链路

> 状态：**已实现（第一版）**

## 链路全景

```
麦克风 → AudioWorklet(16kHz PCM16) → WS 二进制 → ASR(paraformer-realtime-v2)
  → sentence_end 触发 LLM(qwen-plus 流式) → 文本增量喂 TTS(cosyvoice-v2 流式)
  → PCM 二进制 ← WS ← 浏览器播放队列(22.05kHz)
```

全链路流式：ASR 边说边出字、LLM 边生成边喂 TTS、TTS 边合成边回传播放。
目标是把"用户说完 → 听到第一个字"的延迟压到 1~2 秒内。

## 架构决策

| 决策 | 选择 | 理由 |
|---|---|---|
| ASR | DashScope `paraformer-realtime-v2` 流式 | 中文识别强、按时长计费便宜、SDK 提供 sentence_end 断句，省去自建 VAD |
| TTS | DashScope `cosyvoice-v2` 流式 | 支持 streaming_call 增量喂文本，可与 LLM 流式输出衔接 |
| 端云分工 | 采集/播放在端，ASR/TTS 在云 | 浏览器 Web Speech API 在国内不可靠；云端 SDK 质量稳定 |
| 传输 | 单条 WebSocket，二进制帧=音频、文本帧=JSON 事件 | 避免多连接管理；二进制免 base64 开销 |
| 断句驱动 | ASR sentence_end 作为"用户说完一句"的信号 | 不必等用户手动停止，对话更自然 |
| barge-in | ASR 出现新的部分识别文本时，打断进行中的回复 | 用户插话时立刻停 LLM + TTS + 清空前端播放队列，体验自然且省 token |
| 线程模型 | DashScope SDK 回调线程 → `call_soon_threadsafe` → asyncio 队列 | SDK 回调在自己的线程触发，必须桥接回事件循环 |

## 代码位置

| 职责 | 文件 |
|---|---|
| ASR 封装（回调转 asyncio 事件流） | `server/voice/asr.py` |
| TTS 封装（增量喂文本、取消） | `server/voice/tts.py` |
| 会话编排（断句→LLM→TTS、barge-in） | `server/session.py` |
| 麦克风采集 worklet | `web/pcm-worklet.js` |
| 播放队列 / 打断清空 | `web/audio.js` |

## WS 协议

- client→server 二进制：16kHz mono PCM16 麦克风音频
- server→client 二进制：22.05kHz mono PCM16 TTS 音频
- server→client JSON：`{type: "asr", text, final}` / `{type: "delta", text}` / `{type: "done"}` / `{type: "interrupt"}`
- client→server JSON：`{type: "frame", data: <base64 jpeg>}`（视觉链路用）

## 已知取舍 / TODO

- LLM 文本增量直接喂 `streaming_call`，未做按标点聚句；CosyVoice 内部有缓冲，实测可接受，若韵律差再改。
- barge-in 目前以"出现任何新识别文本"为触发，嘈杂环境可能误打断；可加最小字数阈值。
- 未做回声消除依赖浏览器 `echoCancellation: true`，外放大音量场景可能自激。
