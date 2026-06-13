"""视觉链路（骨架）。设计见 docs/vision-pipeline.md。

TODO:
- request_frame(): 通过 WS 发 {type:"capture"}，await 前端回传的 base64 JPEG（request_id 关联）
- describe(frame_b64, question): 调 qwen-vl-max，结合用户问题做定向描述
- 最近帧 + VL 结果的短期缓存（成本策略 C8）
"""


class FrameStore:
    def __init__(self) -> None:
        self.latest_b64: str | None = None
        self.latest_ts: float = 0.0
