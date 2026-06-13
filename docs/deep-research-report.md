# AI 视觉对话助手比赛 MVP 技术方案深度研究

## 核心结论摘要

最推荐的整体方案，不是“原生端到端全双工语音模型 + 每帧喂 VLM”，而是**可中断的级联式多模态架构**：前端用 WebRTC 打开摄像头和麦克风；端侧先做音频预处理、轻量 VAD、画面变化检测、目标检测/跟踪与 ROI 选择；云端做流式 ASR、主对话 LLM、按需 VLM、流式 TTS；中间用一个**状态管理器 + 成本控制器**统一协调。这样做的原因是：比赛 MVP 需要的是**可控、可解释、可调参、可展示**，而不是追求研究前沿的单模型极限。官方语音 Agent 文档也把架构明确分成两类：自然低延迟的 live audio speech-to-speech，以及更可控的 chained STT→Agent→TTS；比赛场景里，如果你还要接视觉、工具与演示 UI，级联链路通常更稳。citeturn37view0turn37view1turn24view1

这套方案的关键不是“一个模型更强”，而是**快路径**和**慢路径**协同。快路径只做一件事：尽快让用户感到“它在听，而且马上会答”。它吃的是 VAD、增量 ASR、最近的场景状态和低推理预算 LLM，优先产出三类东西：确认性回应、简短澄清、以及不依赖新视觉分析/工具调用的直接回答。慢路径则在后台继续做更贵的工作，例如新的视觉分析、OCR、工具调用、ReAct/Plan-Execute、多步推理，并在结果 ready 后接管后续话语。OpenAI 的实时语音提示指南已经把这种模式说得很清楚：**直接回答就快答，不要做扩展推理；需要工具、多步诊断或升级时再 reason；如果用户会感到“沉默”，先给一句极短 preamble，但不要暴露内部思维链。**citeturn22view1turn22view2turn22view4

对于比赛作品，我的总体建议是：**语音层做“准全双工体验”，视觉层做“事件驱动 + 缓存记忆”，推理层做“前台流畅回应 + 后台异步深挖”**。也就是说，不必上真正的研究型 full-duplex speech LLM；只要把**barge-in、增量理解、预启动生成、短 preamble、VLM 按需触发、可取消慢任务**做好，用户主观上就会觉得系统足够“自然”和“聪明”。研究系统如 Moshi 已经展示了约 200 ms 级别的全双工语音下限，但这些方案对数据、训练、工具调用可控性和工程调试都更苛刻；而 FireRedChat 这类 2025 年系统也说明：**模块化、可插拔的 cascaded / semi-cascaded 架构**依然是更现实的工业化路径。citeturn27view2turn29view0turn29view2

如果要把一句话写在答辩页上，我会写：**“先用廉价本地模块持续感知，再用昂贵云模型按需确认；先给出自然反应，再在后台做深度分析；所有昂贵链路都要可取消、可降级、可缓存。”** 这比“把所有数据实时喂给一个超大模型”更适合比赛，也更接近长期可用、低成本的产品形态。citeturn32view0turn21view10turn33view0turn29view6

## 端到端系统架构

推荐的 MVP 架构可以写成下面这个文字版架构图。这个分层设计的核心依据来自几类事实：浏览器实时音频最稳的传输层通常是 WebRTC；实时语音需要中断与 turn 管理；视觉视频默认采样往往非常稀疏，必须在本地先做选择；而长会话必须依赖缓存、截断和摘要，否则延迟和成本都会失控。citeturn24view1turn24view0turn33view0turn23view1turn32view0

```text
[前端 Web / Electron / Mobile]
  ├─ getUserMedia(Camera + Mic)
  ├─ WebRTC AEC / NS / AGC
  ├─ 本地轻量音频门控
  ├─ 本地视觉预处理
  │    ├─ 低分辨率预览流
  │    ├─ 场景变化检测
  │    ├─ 本地检测/跟踪/人脸关键点
  │    └─ ROI 裁剪与关键帧挑选
  └─ WebRTC / WebSocket 发送:
       音频流、局部视觉事件、关键帧/ROI、UI 控制事件

[会话网关 / Orchestrator]
  ├─ Session Router
  ├─ Interrupt Controller
  ├─ State Store
  │    ├─ 对话历史摘要
  │    ├─ 最近 ASR partial / final
  │    ├─ Scene state / object graph / visual memory
  │    ├─ Pending slow tasks
  │    └─ Cost budget / rate counters
  └─ Cost Controller
       ├─ prompt cache / truncation
       ├─ 模型路由 mini / full
       ├─ 图像 detail / fps / resolution 策略
       └─ VLM 调用限频与去重

[语音链路]
  ├─ VAD / endpointing / semantic turn detection
  ├─ Streaming ASR (partial + final)
  ├─ Fast Path Policy
  │    ├─ 直接答 / 澄清 / backchannel
  │    └─ 低推理预算 LLM
  ├─ Slow Path Agent
  │    ├─ Planner / Executor
  │    ├─ Tool calls
  │    ├─ VLM requests
  │    └─ 异步可取消任务
  └─ Streaming TTS

[视觉链路]
  ├─ Scene-change / motion / track delta gate
  ├─ 本地 detector / tracker / landmarker
  ├─ Visual cache / keyframe buffer
  ├─ ROI high-res escalation
  └─ On-demand VLM / OCR / dense analysis

[输出层]
  ├─ 首句快速回复
  ├─ 后续深度补充
  ├─ Barge-in 立刻停播
  └─ 字幕 / UI 状态 / 调试面板
```

在这套架构里，前端最重要的不是“算很多”，而是做**第一道分流**。浏览器或端侧负责的任务应该是：采集、AEC/降噪、非常快的讲话门控、简单视觉事件检测、目标/人脸/姿态之类的轻量连续跟踪，以及为云端准备“值不值得看”的候选证据。Google/MDN 路线下，WebRTC 直接把浏览器音频送到实时会话是标准做法；OpenAI 文档还明确建议，对浏览器端音频播放与上传，WebRTC 比 WebSocket 更稳健，尤其在网络条件不确定时更适合媒体传输。citeturn24view1turn24view0

中间层真正的“灵魂”是 **State Store**。如果没有它，系统就会不断遗忘当前场景、重复做昂贵视觉分析、把历史消息无节制重新喂给 LLM，最后同时输在延迟和成本上。State Store 至少要存四类内容：其一，语音状态，包括最近 partial transcript、已确认 final transcript、当前是否处于用户说话/模型说话/工具中；其二，视觉状态，包括最近的全局 scene caption、对象表、track id、ROI 快照和最后一次高置信视觉结论；其三，任务状态，包括慢路径是否正在跑、是否可取消、工具调用的返回；其四，成本状态，包括 prompt cache 命中前缀、上下文截断阈值、当前回合是否允许触发 VLM。官方 Prompt Caching 和 Realtime cost 文档都说明：前缀稳定时，缓存会明显降时延和降成本；但一旦频繁改动对话头部或发生高频 truncation，就会把缓存收益打掉。citeturn32view0turn23view1turn23view2

这也是为什么我推荐**语音、视觉、推理三条链路都不要直接彼此耦死**。VAD/ASR 不直接驱动 VLM；VLM 不直接驱动 TTS；而是都把“证据”和“建议动作”写入一个可观测的会话状态中，再由 fast path / slow path 协调器决定是否说、说什么、要不要继续深挖。这样你既能在演示里把系统结构讲清楚，也能在比赛现场对某一层做降级：比如 VLM 挂了时，仍可用本地 tracker + 旧 scene summary 说出一个不那么强但仍流畅的回答。citeturn21view16turn37view0turn29view6

## 三大专题技术分析

**语音：关键技术与方案判断。** 从用户感知角度看，语音助手里最影响延迟的通常不是 VAD 计算本身，而是**turn 结束判定、ASR 稳定化、LLM 首个有效 token、TTS 首段音频、以及往返网络**。Silero VAD 这类模块对 30 ms 音频块的处理在单线程 CPU 上可低于 1 ms，本身不是大头；真正经常拖垮体验的是过保守的 endpointing、等整句再理解、以及等所有工具/VLM 完成后才开始说。Google STT 的 voice activity events 能在转写结果之前就发出“speech start/end”，AssemblyAI 的 Universal-Streaming 把 turn detection 集成进 STT 并宣称约 300 ms 级不可变 transcript，这些都说明**尽早拿到稳定增量信号**比单纯追求更强识别模型更重要。citeturn8search0turn26view0turn27view0turn27view1

在 turn-taking 上，单纯 VAD 已经不够。LiveKit 的 turn detector 明确把“对话上下文”作为 VAD 之外的额外信号，并举例说明：如果用户说“我想一想”然后停顿一会儿，VAD-only 系统容易抢答，而语义化 turn detector 会等待。LiveKit 还给出很有工程味的建议：默认 `endpointing.min_delay` 是 0.5 秒、`max_delay` 是 3 秒；`interruption.mode` 更推荐 adaptive 而不是“只要听到人声就打断”；而 preemptive generation 可以在最终 transcript 一到就提前启动 LLM，甚至可选更激进的 preemptive TTS，只是会带来取消浪费。换句话说，**最好把 turn closure、barge-in、preemptive generation 当成一组联动参数，而不是单独调 VAD 阈值。**citeturn38view2turn25view2turn25view3turn25view0

对 barge-in 的工程建议也很明确：不应只要检测到用户发声就立刻把助手停掉。LiveKit 的 adaptive interruption 文档指出，模型会在 VAD 检出说话后，再用声学信号判断这是不是“真正打断”，因为这样比等到 transcript 出来更快，也更能避免把“嗯”“对”“啊对对对”这些 backchannel 误判成抢话。OpenAI 的实时会话文档则补上了播放控制侧：当服务端发来 `input_audio_buffer.speech_started` 时，客户端应立即停止当前模型音频播放，并把已播部分记下来；如果做 push-to-talk，还要显式 `response.cancel` / `output_audio_buffer.clear`。因此比赛 MVP 最现实的选择是**“可打断的半双工”或“准全双工”**，而不是真 full-duplex speech model。citeturn21view5turn25view5turn24view3

端侧与云端怎么分工？我的推荐是：**端侧做即时性信号，云端做高价值理解。** 具体地说，AEC/NS/AGC、轻量 VAD、TTS 播放取消、讲话检测、甚至本地小型 turn detector 都适合端侧或边缘侧；而强鲁棒流式 ASR、多语言/口音覆盖、主 LLM、流式 TTS 以及工具调用，通常放云端更稳。Microsoft 2026 的 on-device streaming ASR 研究证明，CPU 上确实可以做到 0.56 秒算法延迟、低于 1 GB 模型尺寸、并快于实时；Moonshine v2 也把“latency-critical speech applications”明确指向边缘设备。但这些成果目前更多集中在英语或受限部署环境，而比赛作品往往更在意**接入速度、可展示稳定性、多语言风险控制**，所以默认仍建议云端 streaming ASR，再用端侧模块去“提早做决策”。citeturn30view0turn30view2turn30view3turn31view0turn31view2

如果你问“听得准”和“回得快”怎么权衡，我的工程答案是：**把准确率拆成两层。** 第一层是快而保守：partial transcript 只用来做 backchannel、短确认、打断和路由，不直接产出高风险实体值；第二层是稳定而慢一点：final transcript 才驱动真正的事实回答、工具参数、落库动作。OpenAI 的实时提示指南也建议对 direct answers、simple lookups、short confirmations 快速回应，而把 multi-step tasks、tool decisions、troubleshooting 放到更高 reasoning effort；同时高精度标识符要在 tool call 前确认。对比赛 MVP，我建议优先选**带 semantic endpointing 的流式 ASR + streaming TTS + adaptive interruption** 的级联链路，而不是直接上端到端 speech-to-speech 研究模型。研究系统如 Moshi 能做到理论 160 ms、实测约 200 ms 的惊人延迟，但它们的优势更多在音频端对音频端的自然性，不一定等价于你要做的“视觉 + 工具 + 状态机 + 演示 UI”的产品工程。citeturn22view4turn22view3turn27view2turn29view0

**视觉：关键技术与方案判断。** 实时视觉对话的第一原则，是**不要把摄像头每一帧直接送进 VLM**。官方视频理解文档已经给出一个非常强的提示：Gemini 默认对视频只按 1 FPS 采样，而且文档明确说这种默认采样虽然适合多数内容，但会漏掉快速运动和快切镜头；技术细节里又给出了视频 token 预算——默认分辨率约 300 tokens/s，低分辨率约 100 tokens/s。也就是说，厂商自己都在强制做**稀疏采样与分辨率降级**，这从反面证明“全帧密集送模”在成本和延迟上都不现实。citeturn33view0turn33view1turn33view3

因此，比赛 MVP 应该把视觉拆成两层：**本地持续视觉层**和**云端重理解层**。本地层持续运行，但尽量便宜：低分辨率预览、场景切换检测、目标检测/跟踪、人脸关键点、姿态或手势、ROI 裁剪、简单文本区域检测。MediaPipe 明确支持对象检测、面部关键点和表情分析，并且适用于连续视频流；Ultralytics 的 tracking 模式则把目标检测和 persistent object ID 结合起来；ByteTrack 进一步强调，跟踪不应只看高分框，而应该把低分框也纳入关联，以降低遮挡和轨迹碎片化问题。对产品意义是：**对象有没有换、变了多少、用户指的“这个”到底是哪一个，很多时候本地 tracker 就能答，不必每次都叫 VLM。**citeturn27view6turn27view7turn21view12turn36view0

“什么时候需要调用 VLM？”我建议把触发条件做成一个显式门控器。应触发 VLM 的场景包括：用户明确发起视觉问题；本地模块置信度低或类别未知；画面发生语义上重要变化，比如新对象进入、目标消失、交互对象被更换；需要 OCR、计数、复杂关系判断、跨对象比较；或者当前对话中出现“这个/那个/左边那个/刚才那个”之类必须重新视觉对齐的指代。相反，如果只是检测“人还在不在”“杯子是否还在桌上”“用户是不是抬手了”“当前面部表情是否变化”，本地视觉模块通常就够了。PySceneDetect 的 `ContentDetector` 用相邻帧内容差异触发 scene cut，MediaPipe/YOLO/ByteTrack 可以提供 object delta，二者结合起来已经足够组成比赛级的“值得重新看”的判断器。citeturn21view11turn27view6turn21view12turn36view0

“如何保持当前场景理解，而不是每次重新看图？”这里我的建议非常明确：**做 scene state，不做长视频全文回放。** 具体做法是维护一份结构化视觉状态：`scene_summary`、`objects[]`、`track_id -> semantic label`、`salient_roi[]`、`last_vlm_caption`、`last_high_res_snapshot`。当本地检测到事件时，不是重送整个视频，而是更新这份状态并只在必要时送新的关键帧/ROI。这个设计一方面受到 SAM 2 的启发——SAM 2 在视频上依赖 per-session memory 来持续跟踪目标，即使目标暂时离开视野也能恢复；另一方面也得到 2025-2026 streaming video 研究的支持：ProVideLLM、VideoScan、StreamChat 都在做“短期视觉 + 长期压缩记忆”的折中；但更关键的是，SimpleStream 2026 又提醒我们：**复杂 memory 机制并不总是必要，短最近窗口 + 强基础 VLM 已经能打平甚至超过很多复杂流式方法。** 所以比赛 MVP 的正确策略不是堆 memory tricks，而是**最近 3-5 张关键帧 + 一份持续更新的文本化 scene state**。citeturn21view13turn35view3turn35view2turn35view4turn29view6

“如何在成本有限时提高视觉质量？”最有效的不是“更大 VLM”，而是**两阶段看图**。第一阶段，用低分辨率、低 detail、甚至全局图的低质量版，先判断这张图值不值得深入看。OpenAI 文档明确写到：`detail=low` 适合快速、低成本理解，并会把图像压到 512×512；对 GPT-4o/4.1 一类模型，低 detail 的基价只有 65 图像 tokens，而高保真还会额外增加成千上万 tokens。第二阶段，如果第一阶段发现“这里有文字”“用户在指一个小物体”“需要定位像素级区域”，再做**高分辨率 ROI crop** 或高 fidelity 复查。Anthropic 的视觉文档也展示了类似事实：视觉 token 成本随图像尺寸按 patch 增长，超大图会被缩放。对比赛来说，这意味着**低清全局 + 高清局部**几乎一定优于“整张图永远高分辨率”。citeturn32view1turn32view2turn32view3turn32view4

**LLM / ReAct：关键技术与方案判断。** 实时对话里最容易做错的一点，是把所有问题都扔进“会思考、会查工具、会看图”的慢大脑。这样系统会显得很聪明，但也会非常慢。OpenAI 的实时提示指南给出的原则非常适合这里：**direct answers、simple lookups、short confirmations 要快答，不要 reason；multi-step tasks、tool decisions、troubleshooting、escalation 才进入 deeper reasoning。** 我建议把这一原则硬编码成两条通道。快路径是“反应层”，只看最近 transcript、scene state 和少量会话摘要，推理预算设为 `minimal` 或 `low`；慢路径是“深度层”，负责 ReAct、视觉复核、工具调用和 planner-executor。这样你不会把所有轮次都拖进慢速代理循环。citeturn22view1turn22view4

哪些场景应该直接快速回答？我建议至少包括：寒暄、确认、简单命令、基于已缓存 scene state 的回答、纯记忆性对话、低风险澄清，以及用户刚说一半时的 backchannel。哪些场景才需要 ReAct 或 planner-executor？应当是：需要新工具输出、需要新的 VLM/OCR、需要多步约束求解、需要明确计划后再执行的任务。ReAct 原始论文证明了“Reason + Act”在需要外部信息和交互环境时有价值；而 2025-2026 的 agent 研究又进一步把 planner 和 executor 分离，强调显式规划对长任务更稳，甚至在 planner-executor benchmark 中，**弱 planner 对整体质量的伤害比弱 executor 更显著**。这意味着比赛 MVP 不必处处 ReAct，但应该在**复杂回合使用显式计划或至少显式工具路由**。citeturn20search0turn28search0turn28search19

ReAct 是否应该暴露给用户？我的结论是：**不要暴露“思维链”，只暴露“状态感”。** 也就是用户可以听到“我看一下画面”“我来查一下”“我先确认一下这个号码”，但不应该听到内部链式推理本身。OpenAI 的实时提示指南明确要求 preamble 要“描述动作，而不是内部推理”，并且要短，不要“Let me think...”这类 filler；同时 commentary / final 两个可见阶段也说明，用户可见的中间消息适合放简短更新，而不是完整 reasoning trace。比赛作品如果把 ReAct 轨迹直接念出来，既拖慢节奏，也会显得机械。citeturn22view0turn22view2

要让模型“先自然回应，同时后台继续视觉分析或工具调用”，关键是把一句回答拆成两段。第一段由快路径立即播出，通常是承诺型或限定型回应，例如“我先看一下你手里这个东西。”或者“好像是个杯子，我再确认一下细节。”第二段由慢路径在 0.5-2 秒后接上，例如“看清楚了，是一个红色保温杯，杯身上还有白色文字。”这种做法和 OpenAI 实时语音里的 preamble 机制完全一致：工具调用前先说一句短 preamble；需要时可以有 commentary phase；真正结果出来后再给完整 answer。若期间用户打断，则慢路径必须可取消，或者至少“静默完成但不再播报”。citeturn22view0turn22view2turn24view3

对比赛 MVP，要用最小复杂度实现“快速反应层 + 深度推理层”，最简单的做法不是多智能体大战，而是**一个显式路由器 + 两个模型档位**。路由器可以是规则加轻量分类器：如果是简单意图、短问答、缓存命中，就走 fast LLM；如果要视觉新鲜证据、工具或复杂约束，就走 slow LLM/VLM。你甚至可以只用**同一个模型的两种预算**：比如实时模型 `reasoning.effort=low` 做快路径，而慢路径才允许更高 effort 或显式工具链。对于 planner-executor，不必引入 3 个以上 agent；一个 planner 产出 2-4 步结构化计划、一个 executor 顺序执行，再把结果塞回 response merger，就足够展示“边说边查、边看边想”的产品体验。citeturn22view1turn22view4turn28search0

## 延迟预算表

下面这张表不是某家厂商的 SLA，而是**基于当前公开资料所做的工程预算**。它综合了：Silero / LiveKit 对 VAD 与 turn detector 的运行时间；Google / AssemblyAI 的流式 turn 信号与 partial transcript 时间特征；OpenAI 对实时语音回合、低推理预算和可打断链路的设计；ElevenLabs 的低延迟 TTS 指标；Microsoft、Moonshine、Moshi 对 on-device / end-to-end 语音下限的公开结果；以及 Gemini/视觉研究对视频帧采样和长视频 token 预算的限制。用它的正确姿势不是“精确承诺”，而是**检查你的每个模块有没有拖后腿**。citeturn8search0turn38view3turn26view0turn27view0turn27view11turn27view10turn30view0turn31view0turn27view2turn33view0

| 模块 | 理想值 | 可接受值 | 糟糕体验阈值 | 备注 |
|---|---:|---:|---:|---|
| 前端采集 + AEC/NS + 发送首包 | 10–30 ms | 30–60 ms | >100 ms | 浏览器/WebRTC 正常应很快，超过 100 ms 往往是设备或网络问题。 |
| VAD 讲话开始检测 | 20–60 ms | 60–120 ms | >150 ms | 由 10/20/30 ms 帧长和少量缓冲决定；计算本身一般不是瓶颈。 |
| Turn detector / endpointing 结束判定 | 200–400 ms | 400–800 ms | >1000 ms | 这是最常见的大头；默认值保守时会明显拖慢感知响应。 |
| ASR partial 首次稳定文本 | 150–300 ms | 300–600 ms | >800 ms | 足够支持 backchannel、路由和抢答预热。 |
| ASR final after turn close | 100–300 ms | 300–700 ms | >1000 ms | 超过 1 s 时，用户会感觉“它明明听完了还不答”。 |
| Fast-path LLM 首 token / 首短句 | 80–200 ms | 200–500 ms | >800 ms | 只适合直接答、澄清、状态回应。 |
| TTS first audio | 75–150 ms | 150–300 ms | >400 ms | 首段音频必须快，音色细腻可以放在后段。 |
| 播放侧 barge-in 停播 | <100 ms | 100–200 ms | >250 ms | 超过 250 ms，用户会明显感到“抢不过它”。 |
| 简单语音问答 end-to-first-audio | 400–800 ms | 800–1500 ms | >2000 ms | 比赛 MVP 的现实目标。 |
| 本地视觉变化检测 | 20–80 ms | 80–150 ms | >300 ms | 目标是便宜地给“是否值得再看”打分。 |
| 关键帧选择 + ROI 裁剪 | 20–80 ms | 80–200 ms | >400 ms | 如果慢，说明你在本地做了太重的视觉预处理。 |
| 单张图 VLM 低清复核 | 300–900 ms | 900–2000 ms | >3000 ms | 应隐藏在慢路径里，用 preamble 遮蔽。 |
| 视频片段 / 多图 VLM 分析 | 800–2000 ms | 2000–4000 ms | >5000 ms | 只应在“值得”的视觉事件上触发。 |

这一预算表背后的总目标是：**简单回合首音 1 秒内，复杂视觉/工具回合也要先在 1 秒左右给出自然占位回应，真正细节在 1-3 秒内补上。** 如果你的 simple turn 还需要 1.8-2.5 秒，通常不是模型太差，而是 turn closure 太慢、没有 preemptive generation、或者 TTS 没有流式首段输出。相反，如果你把 endpointing 压得极短，又没有 semantic turn detection，就会变成“经常抢话、频繁重说”。因此表中的理想值不是越小越好，而是要与误触发率一起看。citeturn25view2turn25view3turn27view0turn27view1turn27view11turn27view10

## 成本控制策略

真正有效的成本控制，绝不是笼统地说“减少调用次数”，而是要把**触发门、分辨率门、上下文门、模型门、缓存门**都做出来。最应该先做的，是把视觉调用分成**本地连续感知**和**云端按需确认**。Gemini 的视频理解文档已经告诉我们，默认就是 1 FPS 采样；`media_resolution=LOW` 时大约是 100 tokens/s，而默认分辨率大约 300 tokens/s；OpenAI 图像输入文档则告诉我们，`detail=low` 适用于便宜快速的理解，而更高 fidelity 会显著增加 token 成本。换句话说，最便宜的 VLM 不是“更便宜的模型”，而是**先别发、先发低清、只发 ROI、只发最近几帧**。citeturn33view0turn33view3turn32view1turn32view2

具体机制上，我建议做一个**视觉事件评分器**。分数来源至少有四类：画面级差异分数（如 scene cut / content diff）、对象图谱变化（新对象、消失对象、轨迹切换）、用户语义需求（“这是什么”“它现在在干嘛”）、以及本地模型不确定性。如果四类信号都低，就完全不触发 VLM，只更新本地 scene state；如果是中等事件，只发低清全局图；如果是高价值事件，例如需要识别手中的小件、读标签、数数量、看屏幕上的字，则升级到高分辨率 ROI 或短片段多帧分析。PySceneDetect、MediaPipe、YOLO tracking、ByteTrack 都可以作为这些信号的廉价来源。citeturn21view11turn27view6turn21view12turn36view0

第二个大头是**上下文与前缀缓存**。Prompt Caching 官方文档给出的收益非常夸张：静态前缀命中时，延迟最多可降到 20% 左右，输入成本最多可降到原来的 10%；Realtime cost 文档又提醒，频繁改 conversation 头部、改工具定义或高频截断，会把 cache 命中打烂。你的做法应该是：把系统提示、工具 schema、角色设定、视觉描述格式放到静态前缀；把用户当前 turn 和最新 scene delta 放在末尾；长会话不要“每轮重发所有历史”，而是定期把旧对话和旧视觉记忆压成摘要，再删除原消息。citeturn32view0turn23view1turn23view2

第三个成本策略是**模型按层路由，而不是统一大模型**。在语音上，快路径只需要低 effort 或 mini 档，甚至可以只是规则 + 小模型做 direct answer / clarification / slot capture；慢路径才调用高推理预算 LLM、VLM 或 planner-executor。OpenAI 实时提示指南明确建议从 `reasoning.effort=low` 开始，并只在复杂任务上调高；Realtime cost 文档也明确说明 mini 模型会更便宜，只是工具使用和指令遵循能力可能下降。比赛场景里，这是容易展示、又能真正省钱的策略：**把贵模型留给少数关键回合，而不是每一轮都烧。**citeturn22view1turn22view4turn23view4

第四个策略是**把连续音频中“无价值的部分”尽早截掉**。Google 的 voice activity 文档强调：开始/结束事件可以先于转写结果；而过大的音频 chunk 或过于粗暴的发送节奏会影响 timeout 精度。AssemblyAI 则把 turn detection 和 transcript 稳定化绑进 streaming STT 本身。工程上意味着，你应尽量发送**16 kHz mono、小 chunk、带本地门控的语音流**，不要把长时间静音、背景电视声、桌面噪声完整送去计费链路。对于演示版，如果场景允许，甚至可以提供 push-to-talk 模式作为降级选项。citeturn26view0turn26view2turn21view7

第五个策略是**TTS 只对“新信息”花钱**。真正需要神经 TTS 的，是当前生成的实质内容；而一些稳定的 UI 交互语音，比如“我看一下”“好的”“你可以继续说”，完全可以改成短 preamble、短 earcon、甚至静音字幕。ElevenLabs 的文档也说明了一个现实约束：低延迟模型之所以快，往往牺牲了部分文本规范化或高保真语音特性；这对比赛恰恰是好事，因为真正能拿分的是交互流畅，而不是每一句都像配音演员。建议把**高质量长回复**留给展示高潮，把大多数占位回应做短、轻、可打断。citeturn27view11turn27view10turn22view0

## MVP 技术路线建议

下面给出三个版本，它们不是“功能越多越好”的线性升级，而是三种不同的比赛取舍。共同前提是：都围绕**实时感、低成本、可解释演示**展开，而不是堆模型参数。相关建议的底层依据是前面几部分：WebRTC 更适合浏览器音频链路；级联 voice pipeline 在需要中间控制时更合适；VLM 要靠事件触发与分辨率控制；快/慢路径能把体感速度和深度分析分开。citeturn24view1turn37view0turn33view0turn22view4

| 版本 | 必做模块 | 可以省掉什么 | 为什么这样取舍 |
|---|---|---|---|
| 最小可行版本 | WebRTC 采集、浏览器 AEC/NS、流式 ASR、基础 endpointing、单一 LLM、流式 TTS、简单视觉事件门控、单张图按需 VLM、会话状态缓存 | 不做本地 detector/tracker；不做 planner-executor；不做多帧 VLM；不做高阶 visual memory | 这是最快上线版本。它已经能演示“边说边答、看图回答、用户打断即可停播”，足以形成完整闭环。 |
| 效果增强版本 | 加入端侧 detector/tracker、ROI crop、scene state、fast path / slow path、短 preamble、barge-in、自适应 interruption、视觉调用限频 | 可以暂不做真正的多智能体；不做长期视频记忆压缩论文级实现 | 这是最推荐的比赛版本。它的用户体感会明显优于最小版，因为系统不再每次都“重新看整张图”，也不会所有问题都等慢路径。 |
| 展示效果最好的版本 | 再加入语义 turn detector、preemptive generation、视觉多帧窗口、OCR 专项路径、planner-executor、异步可取消 agent loop、调试面板 | 可以不追求 true full-duplex 研究模型 | 这个版本最适合答辩：你能讲清楚快/慢路径、视觉 cache、工具调用、成本控制、以及为什么它看起来“像真的会边听边想边看”。 |

如果只允许我给一条**实际实现路线**，我会建议这样做。第一周做最小版：浏览器采集、流式 ASR、TTS、用户打断、单张图问答、状态 UI。第二周只加两个最值钱的模块：**fast path / slow path** 和 **视觉 scene state**。第三周再补演示增强项：对象检测/跟踪、ROI 放大、控制台展示每一轮是否触发了 VLM、每次用了多少 token、当前 slow task 是否被取消。这样不但结果更稳，而且答辩时非常容易体现“工程判断”而不是“API 拼装”。citeturn22view0turn23view2turn21view12turn29view6

从比赛收益角度，我最推的其实是“效果增强版本”。因为最小版太容易像普通语音聊天 + 看图 QA 拼起来的 demo；而展示版如果做得太野，比如真 full-duplex、复杂多 agent、持续视频理解，很容易把时间花在系统不稳定和延迟调不下来上。“效果增强版”最容易形成差异化：用户一说话它能立刻停，用户问“这是什么”时它不需要每次都重新看整张图，用户指着东西继续追问时它还能记得“这个”是哪个 track id 对应的物体。对评委来说，这些体验往往比“模型规模”更可感知。citeturn21view5turn21view12turn21view13

## 风险与反例

第一个最常见的误区，是**把 true full-duplex speech LLM 当成比赛 MVP 的默认选项**。Moshi、Freeze-Omni、FireRedChat 这类系统很先进，也确实代表未来方向；但它们解决的是“能不能自然重叠说话、保留副语言信息、缩短端到端链路”这类研究与平台问题。比赛项目往往还要同时面对视觉触发、UI 演示、工具集成、可观测性、可调参与故障兜底。对这种场景，模块化级联仍然是更稳的选择。真正不适合比赛的不是这些技术本身，而是**在缺乏调试和评测预算时，把它们当成默认工程底座。**citeturn27view2turn29view2turn29view0

第二个误区，是**把“视频流输入 VLM”误解为“持续把视频送给 VLM”**。官方视频理解接口已经默认做 1 FPS；研究界的高效 streaming video 论文也都在压视觉 token，而不是扩大输入。若你的设计是“每秒多次全帧上云”，那几乎注定会同时输掉成本、延迟和稳定性。真正该做的是先用本地模块决定“这次值不值得送”，再决定“送全局图还是 ROI，送 1 张还是最近 4 张，送低清还是高清”。citeturn33view0turn35view1turn35view2

第三个误区，是**所有轮次都走 ReAct**。ReAct 适合需要外部环境与工具的复杂回合，不适合“你好”“你还在吗”“这个杯子是不是还在桌上”这类低风险直接答。如果你让每个 turn 都先规划、再调用、再汇总，最后就会做出一个“看起来很 Agent，实际上处处卡顿”的系统。快路径的价值恰恰在于：很多回合本来就不需要复杂代理。citeturn20search0turn22view4

第四个误区，是**同时叠两套 turn detection 却没有想清楚冲突**。LiveKit 文档明确提醒：如果你用外部 turn detector 搭配 OpenAI Realtime，一定要关掉 Realtime 模型自己的 turn detection，并且因为外部 detector 需要 live STT，你还得额外接一个 STT 插件。这在比赛里非常容易引入“双重 endpointing、重复计费、调参相互打架”的问题。除非你真的需要更高级的语义 turn 检测，否则 MVP 更适合选择**一体化 streaming STT endpointing**，或者只保留一套外部 semantic turn detector。citeturn38view2turn38view3

第五个误区，是**把 memory 设计得像论文，而不是像产品**。2025-2026 的 streaming video 研究大量在做 memory bank、semantic carriers、文本化长记忆；但 SimpleStream 的结果反过来提醒我们：短最近窗口常常已经很强。比赛作品里，最危险的不是“记忆不够高级”，而是“记忆太复杂导致系统变慢、变脆、难解释”。真正合适的比赛 memory 应该是：最近 3-5 张关键帧、一份 scene summary、一个 object graph、若干 ROI 快照。citeturn35view3turn35view2turn35view4turn29view6

仍然存在的开放问题也要坦诚说清楚。第一，公开资料里关于**不同 VLM 在单张图/多帧图上的统一延迟 SLA** 很少，因此本文对 VLM 延迟预算主要是工程估计，而非厂商保证。第二，2026 年 on-device streaming ASR 的强结果目前公开证据主要集中在英语；如果你的比赛需要高质量中英混合甚至更复杂多语，建议优先选成熟云端 streaming ASR，再把端侧模块用作辅助。第三，视觉事件触发阈值在不同摄像头、帧率和场景下都要重新调，不存在一组“最优万能超参数”。这些不是这套方案的缺点，而是任何真实多模态实时系统都必须面对的边界。citeturn30view0turn31view0turn33view0

## 参考资料

**OpenAI Voice agents / Realtime docs。** 这组官方文档支持了本文关于“speech-to-speech vs chained pipeline 如何选”“WebRTC 作为浏览器音频链路”“speech_started 触发打断”“快路径低推理预算”“preamble 只暴露动作不暴露思维链”“Realtime 会话如何做缓存和截断”等核心判断。它们最强的地方是工程可落地性很高；局限是产品特定、并不代表所有厂商的延迟或接口形态。citeturn37view0turn24view1turn24view3turn22view0turn22view4turn23view1

**LiveKit turn detector / interruption / tuning docs。** 这些资料支持了本文关于“语义 turn detection 比纯 VAD 更适合对话”“adaptive interruption 优于一刀切 VAD 打断”“preemptive generation 可以显著减感知延迟”“外部 turn detector 与 Realtime 模型会冲突、需要额外 STT”这些结论。优点是非常贴近 voice agent 调参；局限是它针对 LiveKit 生态，默认参数不一定适用于你的链路。citeturn38view2turn25view2turn25view3turn25view0

**Google Cloud Speech-to-Text voice activity events。** 它支持了“speech start/end 事件可在转写结果之前到达”“不要把音频 chunk 发得太大，否则 timeout 不准”“VAD/endpointing 必须被当成独立信号处理”这些观点。优点是官方、清楚；局限是它描述的是 Google STT 的行为，不直接等价于所有 streaming ASR。citeturn26view0turn26view2

**AssemblyAI Universal-Streaming docs 与博客。** 它支持了“immutable transcript ~300 ms”“semantic + acoustic endpointing 内建于 STT”“用一体化 streaming STT 可以减少外置 turn detector 复杂度”的结论。优点是非常贴近 voice agent 用例；局限是供应商材料，性能表述带有产品宣传色彩。citeturn27view0turn27view1turn21view7

**Silero VAD 与 WebRTC VAD。** 这些资料支撑了“VAD 的计算耗时通常不是主延迟瓶颈”“10/20/30 ms 帧长是常见实时粒度”的判断。优点是工程事实直接；局限是它们只解决检测有没有说话，不解决语义何时说完。citeturn8search0turn8search1

**Moshi、Freeze-Omni、FireRedChat。** 这组 2024-2025 资料支持了“全双工 speech model 的研究下限已经很低”“semi-cascaded/full-duplex 可以提升自然度和副语言能力”“但模块化系统仍是现实路线”的判断。优点是代表最新研究方向；局限是离比赛可复制工程通常还有一段距离，且公开数据多为特定硬件或自建系统。citeturn27view2turn29view2turn29view0

**Microsoft 2026 on-device streaming ASR 与 Moonshine v2。** 它们支撑了“端侧/CPU 上确实可以做到亚秒级 streaming ASR”“边缘部署已进入可行区间”“但重点仍在低延迟英语场景”的判断。优点是对端云分工极有参考价值；局限是并不能直接代表多语和复杂噪声场景的比赛体验。citeturn30view0turn30view2turn30view3turn31view0turn31view2

**Gemini 视频理解与视频 metadata/fps/mediaResolution 文档。** 这组资料支撑了“默认 1 FPS 视频采样”“低分辨率 token 明显更省”“必要时可用 clip offsets 和 fps 精细控制视频片段”的结论。优点是直接给出 token 与 fps 参数；局限是 Google 模型特定，不是一般 VLM 的统一规律。citeturn33view0turn33view3

**OpenAI / Anthropic 视觉 token 文档。** 它们支持了“低清全局 + 高清 ROI”是必要的成本策略，因为图像 token 明确随 detail 与尺寸显著上升。优点是量化非常具体；局限是不同模型族的 token 规则不同，不能把某一公式简单套到所有 VLM。citeturn32view1turn32view2turn32view3turn32view4

**PySceneDetect。** 它支持了“画面变化检测完全可以先靠轻量内容差异，不必先调用 VLM”的设计。优点是简单、解释性强；局限是它主要感知镜头内容变化，不会直接理解语义重要性。citeturn21view11

**MediaPipe Object Detector / Face Landmarker。** 它支撑了“端侧持续视觉层可以做对象检测、人脸关键点、表情等连续轻量任务”的建议。优点是跨平台、贴近实时应用；局限是泛化能力和类别复杂度不能替代通用 VLM。citeturn27view6turn27view7

**Ultralytics Tracking 与 ByteTrack。** 这组资料支持了“对象持续 ID 对视觉对话很重要”“低分框也应被纳入关联以减少遮挡带来的记忆断裂”的判断。优点是对 ROI、指代解析、物体持续记忆很实用；局限是需要一定硬件预算，且场景泛化取决于 detector 本身。citeturn21view12turn36view0

**SAM 2。** 它支撑了“视频场景记忆可以由 per-session memory 持续维护，而不是每次重新理解整段视频”的设计启发。优点是说明视频 memory 的产品意义；局限是它是分割/跟踪模型，不是通用视觉语义解释器。citeturn21view13

**SimpleStream、VideoScan、ProVideLLM、StreamChat。** 这组 2025-2026 的 streaming video 研究支持了两个看似矛盾但都重要的判断：一方面，长视频确实需要压缩、选择和记忆；另一方面，复杂 memory 不一定比“最近几帧 + 强 VLM”更值。它们最适合支撑“比赛 MVP 不要过度论文化”的工程判断。局限是其中不少仍是 arXiv / ICLR 路线，离通用生产环境还有差距。citeturn29view6turn35view2turn35view3turn35view4

**ReAct、Plan-and-Act、PEAR 等 agent 研究。** 这组资料支撑了“不是所有回合都该 ReAct”“复杂任务应显式规划”“planner 质量往往比 executor 更关键”的结论。优点是能为快/慢路径和 planner-executor 的取舍提供理论背书；局限是多数 benchmark 与实时语音视觉对话的用户感知延迟并不完全一致。citeturn20search0turn28search0turn28search19

**ElevenLabs 低延迟 TTS 文档。** 它支撑了“流式 TTS 的首音时间已经足够低，真正要优化的是你何时开始合成”和“低延迟模型通常要牺牲部分高保真文本规范化”的结论。优点是指标明确；局限是供应商特定，网络地域和音色配置会显著影响实测。citeturn27view11turn27view10