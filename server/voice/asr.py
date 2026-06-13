"""流式 ASR 封装：DashScope paraformer-realtime。

DashScope SDK 的回调在它自己的工作线程触发，这里统一用
call_soon_threadsafe 桥回 asyncio 事件循环，业务侧只消费 asyncio 队列。
"""

import asyncio
from dataclasses import dataclass

from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from config import ASR_MODEL, ASR_SAMPLE_RATE


@dataclass
class AsrEvent:
    text: str
    is_final: bool  # True = 一句话结束（sentence_end），驱动下游 LLM


class _Callback(RecognitionCallback):
    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue):
        self._loop = loop
        self._queue = queue

    def _put(self, event):
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def on_event(self, result: RecognitionResult) -> None:
        sentence = result.get_sentence()
        if not sentence or not sentence.get("text"):
            return
        self._put(AsrEvent(
            text=sentence["text"],
            is_final=RecognitionResult.is_sentence_end(sentence),
        ))

    def on_error(self, result) -> None:
        self._put(RuntimeError(f"ASR error: {result}"))

    def on_close(self) -> None:
        pass


class StreamingAsr:
    """用法：start() 后持续 send_audio()，从 events 队列读 AsrEvent。"""

    def __init__(self) -> None:
        self.events: asyncio.Queue = asyncio.Queue()
        self._recognition: Recognition | None = None

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._recognition = Recognition(
            model=ASR_MODEL,
            format="pcm",
            sample_rate=ASR_SAMPLE_RATE,
            semantic_punctuation_enabled=False,  # 用 VAD 断句而非语义断句，断句更快，对话延迟更低
            callback=_Callback(loop, self.events),
        )
        self._recognition.start()

    def send_audio(self, pcm: bytes) -> None:
        if self._recognition is not None:
            self._recognition.send_audio_frame(pcm)

    def stop(self) -> None:
        if self._recognition is not None:
            try:
                self._recognition.stop()
            except Exception:
                pass  # 连接已断时 SDK 会抛错，会话收尾不关心
            self._recognition = None
