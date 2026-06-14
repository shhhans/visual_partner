# Codex Review 发现（2026-06-14）

> 来源：Codex review 误把本未跟踪目录当成审查目标时产出的发现。与主动视觉无关，
> 但看起来是 capsule 前端自身的真实问题，先记录待修。未验证。

## [P1] PCM worklet 未接入活跃音频图 — `src/lib/audio.ts:54`

Electron/Chromium 下 `AudioWorkletNode.process()` 是 pull-driven 的；该 PCM 节点
没有连到 `ctx.destination` 或其他活跃输出，processor 可能保持不活跃，即使麦克风流已
打开，`onPcm` 也不会触发，麦克风上行链路因此断掉。修法：把 worklet 经一个静音/零增益
节点接入渲染图，或以其他方式让它留在活跃音频图中。

## [P2] 初始窗口位置用了错误的尺寸 — `electron/main.ts:16`

首次启动时 `mainWindow` 仍为 null，回退分支用**展开态**宽高计算 `x/y`，但窗口实际是按
`MINI_WINDOW` 创建的。迷你胶囊因此比右下锚点偏左约 180px，且随后的
`resizeCapsule(REST_WINDOW)` 因尺寸未变不会把它移回。修法：用 mini 尺寸算初始位置。

## [P2] 不应靠 CSS pointer-events 实现 OS 级点击穿透 — `src/components/Capsule.tsx:27`

胶囊可见区域以外透明时，`pointer-events-none` 只影响 BrowserWindow 内的 DOM 命中测试；
原生 Electron 窗口仍接收鼠标事件，底层应用收不到点击。若要让透明边距的点击穿透，需在主
进程切换 `setIgnoreMouseEvents(..., { forward: true })`，或把原生窗口 bounds 收缩/移动
到可交互区域。
