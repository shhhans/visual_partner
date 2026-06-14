# 视觉链路

> 状态：**被动视觉、主动视觉均已实现并端到端打通**。

## 设计原则：按需看，不持续看

不采用 omni 类实时视频流模型（按秒计费，成本不可控）。摄像头画面常驻在**前端**，
后端只在 ReAct 链路决定"需要看"时才索取一帧，调一次 VL 模型。这是本项目最核心的成本控制手段。

```
摄像头(常开,仅本地预览) → 前端定时缓存最新帧(canvas→JPEG)
ReAct 调 look_at_camera 工具 → 后端向前端要帧(或取最近缓存) → qwen-vl-max 单次调用 → 描述文本回填对话
```

## 架构决策

| 决策 | 选择 | 理由 |
|---|---|---|
| VL 模型 | `qwen-vl-max`（备选 `qwen-vl-plus` 降本） | 中文场景理解准确；plus 便宜约一半，可按场景降级 |
| 取帧时机 | LLM 工具调用驱动（lazy），而非定时上传 | 闲聊时零视觉成本；只有"这是什么""你看我手里"才触发 |
| 帧预处理 | 前端 canvas 缩到长边 ≤768px、JPEG q≈0.7 | qwen-vl 按图块计 token，缩图直接省钱且不损语义 |
| 帧传输 | 前端收到 `{type:"capture"}` 后回传 base64 JPEG | 复用既有 WS，免新通道 |
| 帧缓存 | 后端保留最近一帧 + 时间戳 | 连续追问同一画面时免重复传输/重复理解（可选缓存 VL 结果） |

## 代码位置

| 职责 | 文件 |
|---|---|
| 帧缓存、capture 请求-响应、VL 调用与结果缓存 | `server/vision/frame.py`（`FrameStore`） |
| 前端抓帧/缩图/回传 | `clients/web/camera.js`（`captureFrame`） |
| 前端收 capture→抓帧、收 frame 回传 | `clients/web/main.js`（`case 'capture'`） |
| 后端收 frame→resolve future | `server/session.py`（`_on_json` 中 `type=="frame"`） |
| look_at_camera 工具注册与执行 | `server/agent/tools.py`（`LOOK_AT_CAMERA` / `execute_tool`） |

## 已实现

- [x] capture 请求-响应：WS 上以 `request_id` 关联，前端抓帧后回传 `{type:"frame", id, data}`
- [x] 取帧降级：前端超时（5s）或 WS 异常时，退回后端最近一帧缓存
- [x] 接 qwen-vl-max（OpenAI 兼容接口），prompt 结合当前用户问题做定向描述而非泛泛 caption
- [x] VL 结果短期缓存：同一帧 8s（`_VL_CACHE_TTL`）内复用描述，覆盖"连续追问同一画面"（U6）
- [x] VL 描述文本作为 tool 结果回填 ReAct 对话历史，不传图片入史（成本策略 C5）

## 主动视觉（已实现）

被动视觉是"你问我才看"；主动视觉是"画面变了我自己注意到并提一句"。触发源不是
用户语句，而是画面变化本身。检测全在**前端本地**完成，不调云，直到判定"画面真的变了"。

### 前端流水线（不调云）

```
帧采样(~3fps,64×64灰度) → 帧差(SAD) → EMA平滑 → hysteresis运动门 → 静止确认 → 关键帧对比
                                                                              │
                              终态帧 vs 上一稳态帧差异 ≥ 阈值 → 上行 {type:"scene_change"}
```

- **EMA 平滑**：压制"低头贴近镜头"这类单帧尖峰，避免误触发。
- **hysteresis 运动门**：进 `MOTION_ENTER`(8) / 出 `MOTION_EXIT`(4) 双阈值 + 连续静止帧确认，防阈值附近抖动反复触发。
- **关键帧对比**：只比两个**时间相近的稳态**（终态帧 vs 上一稳态帧），甩掉光照漂移。`KEYFRAME_THRESHOLD`=25。
- 这套设计让"低头坐回""背景路人走过"（终态与原状相同）自然被过滤，只有真正的状态切换（人离开/回来）才上行。常量集中在 `clients/web/main.js` 顶部。

> 设计取舍：diff 只当"门控信号"，运动中的中间态被丢弃——presence 的语义判断交给 VL，
> 不让 diff 背"区分低头 vs 离开"的锅。识别"动作/事件"（挥手、摔倒）才需要时序模型，不在本期。

### 后端处理

```
收 scene_change → 冷却+空闲判定 → request_frame 取帧 → describe_scene(VL) → 观察入史
                                                                            │
                                              空闲态 → 主动回合(complete_chat 决策) → 值得说则 TTS 播报
```

- **观察永远入史**（`[系统检测到画面变化] <VL描述>`）：这是"刚刚干嘛去了"有记忆的根基。即便当下忙碌不发声，用户下一句正常对话时 LLM 也能自然带出。
- **忙碌不抢麦**：`_is_idle()`（无回复在跑 + 无在途音频 + 用户没在说）才主动发声，否则只入史。
- **允许沉默**：`_proactive_reply` 先非流式问 LLM 是否值得开口，回 `SKIP` 则闭嘴（观察仍入史）。对空房间不自言自语。
- **可被打断**：主动回合注册为 `_reply_task`、走 `_reply_gen` 闸门，用户一开口照常 barge-in。
- **防抖控成本**：`_scene_busy` 防重入 + `_SCENE_COOLDOWN`(8s) 冷却。

### 代码位置（主动视觉）

| 职责 | 文件 |
|---|---|
| 前端 diff 流水线、scene_change 上行 | `clients/web/camera.js`（`grabScenePixels`/`pixelDiff`）、`clients/web/main.js`（`sceneTick`/`onSceneSettled`） |
| 后端取帧→VL→入史→主动回合 | `server/session.py`（`_on_scene_change`/`_is_idle`/`_proactive_reply`） |
| 面向画面变化的 VL 描述 | `server/vision/frame.py`（`describe_scene`） |
| 主动决策（SKIP）非流式 LLM | `server/agent/llm.py`（`complete_chat`） |
| VL prompt / 主动决策指令 | `server/config.py`（`SCENE_VL_PROMPT`/`PROACTIVE_DIRECTIVE`） |

## 调用追踪（调试可视化）

所有 LLM/VL 调用（主回复每个 ReAct step、被动 VL、主动 VL、主动决策）在发起前后推
`{type:"trace", phase:"start"/"end"}`，前端"LLM / VL 调用追踪"面板按发生顺序列出，
每条带 `#gen` 与对话流轮次对齐。被动 VL 命中 8s 缓存时不计（无真实调用）。
插桩在 `frame._call_vl` / `llm.complete_chat` / `llm.stream_chat`，`session._trace_start/_end` 发送。

## TODO

- [ ] 主动视觉 VL 的 context 增强：加「上一关键帧 VL 输出 + 对话上下文」，让 VL 描述"相比之前的变化"。
      对话上下文优先用最近 N 轮原文（零额外 LLM）；待做 history 压缩时升级为 Summary Buffer 短期记忆层（详见 react-pipeline.md）。
