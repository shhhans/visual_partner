# ReAct 链路

> 状态：**骨架已留，LLM 流式直连已通（无工具版），工具循环待实现**

## 定位

`qwen-plus` 是对话大脑。视觉不是常驻输入，而是工具：模型在推理中自行决定何时调
`look_at_camera` 获取画面信息（ReAct: Reason → Act → Observe → 继续生成）。

```
用户语句(来自 ASR) → messages 历史 → qwen-plus(stream, tools=[look_at_camera,...])
   ├─ 直接文本 → 流式喂 TTS
   └─ tool_call → 执行(取帧→VL) → tool 结果回填 → 再次调用 → 文本 → TTS
```

## 架构决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 大脑模型 | `qwen-plus` | 性价比平衡点；工具调用可靠；比 max 便宜数倍 |
| 接入方式 | OpenAI 兼容接口（dashscope compatible-mode） | 工具调用/流式协议标准化，便于切换 MiniMax 做 A/B |
| 视觉=工具 | look_at_camera 由模型自主触发 | 见 vision-pipeline.md，核心成本策略 |
| 历史管理 | 滚动窗口（保留 system + 最近 N 轮），VL 描述以文本形式入史 | 控制上下文 token；图片本体绝不进历史 |
| 流式+工具 | 先流式收 token，检测到 tool_call 则暂停 TTS 喂入，执行后续轮 | 兼顾低延迟与工具能力 |
| system prompt | 口语化短句、禁 markdown、回答简短 | 输出是要被 TTS 念出来的，且短回答直接省输出 token |

## 代码位置

| 职责 | 文件 |
|---|---|
| LLM 流式调用封装 | `server/agent/llm.py` |
| 工具 schema 与执行 | `server/agent/tools.py`（骨架） |
| 对话历史/轮次编排 | `server/session.py` |

## TODO

- [ ] 工具循环：解析流式 tool_calls 增量、执行、二次调用
- [ ] 历史窗口裁剪 + 旧轮次摘要（可选）
- [ ] MiniMax 备选通道（abab / MiniMax-Text），配置切换
- [ ] 打断后的历史处理：被打断的回复以"(已被用户打断)"截断入史，保持上下文真实
