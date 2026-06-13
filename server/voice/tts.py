"""流式 TTS 封装：CosyVoice v2。

一次助手回复 = 一个 StreamingTts 实例：LLM 文本增量 feed() 进来，
合成出的 PCM 通过 on_audio 回调（已桥回事件循环）交给会话层下发前端。
错误同样桥回事件循环，由会话层转发给前端，不再静默丢弃。
"""

import asyncio
from typing import Callable

from dashscope.audio.tts_v2 import AudioFormat, ResultCallback, SpeechSynthesizer

from config import TTS_MODEL, TTS_VOICE


class _Callback(ResultCallback):
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_audio: Callable[[bytes], None],
        on_error: Callable[[str], None],
    ):
        self._loop = loop
        self._on_audio = on_audio
        self._on_error = on_error

    def on_data(self, data: bytes) -> None:
        self._loop.call_soon_threadsafe(self._on_audio, data)

    def on_error(self, message: str) -> None:
        # 桥回事件循环，由会话层处理；不在此静默丢弃
        self._loop.call_soon_threadsafe(self._on_error, message)

    def on_event(self, message) -> None:
        pass

    def on_open(self) -> None:
        pass

    def on_complete(self) -> None:
        pass

    def on_close(self) -> None:
        pass


class StreamingTts:
    def __init__(
        self,
        on_audio: Callable[[bytes], None],
        on_error: Callable[[str], None],
    ) -> None:
        loop = asyncio.get_running_loop()
        self._synth = SpeechSynthesizer(
            model=TTS_MODEL,
            voice=TTS_VOICE,
            format=AudioFormat.PCM_22050HZ_MONO_16BIT,
            callback=_Callback(loop, on_audio, on_error),
        )

    def feed(self, text: str) -> None:
        if text:
            self._synth.streaming_call(text)

    async def finish(self) -> None:
        # streaming_complete 会阻塞等全部音频回完，放线程池避免卡事件循环。
        # 若外层 task 被取消，asyncio.to_thread 的 Future 会被取消，
        # 但底层线程仍会运行至 streaming_complete 返回；
        # 残余音频回调由会话层的代号闸门（_reply_gen）拦截丢弃。
        await asyncio.to_thread(self._synth.streaming_complete)

    def cancel(self) -> None:
        """barge-in 打断：丢弃未合成内容。后续音频回调可能仍到达，由会话层闸门拦截。"""
        try:
            self._synth.streaming_cancel()
        except Exception:
            pass
