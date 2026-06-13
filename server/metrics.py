"""每回合延迟指标的采集与落库。

落库用 stdlib sqlite3（零新依赖），库文件在 server/metrics.db。
时间戳全部用事件循环单调时钟（loop.time()）算时间差，避免墙钟回拨；
created_at 单独存墙钟 epoch，仅用于人读「这条记录是什么时候产生的」。

指标语义（均对应 docs/voice-pipeline-review.md 与 deep-research 延迟预算表）：
  turn_closure_ms     最后一次 ASR partial → sentence_end（断句结束判定耗时）
  ttft_ms             sentence_end → LLM 第一个文本增量
  tts_first_ms        LLM 第一个增量 → TTS 第一帧音频
  e2e_first_audio_ms  sentence_end → TTS 第一帧音频（用户「说完→听到第一个字」）
  total_ms            sentence_end → 本回合 done（被打断则为空）
"""

import asyncio
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "metrics.db"

# 单连接 + 锁：sqlite3 连接非线程安全，所有访问经此锁串行化；
# check_same_thread=False 是为了允许 asyncio.to_thread 的工作线程写入。
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT    NOT NULL,
    turn_index          INTEGER NOT NULL,
    created_at          REAL    NOT NULL,
    asr_text            TEXT,
    reply_chars         INTEGER,
    turn_closure_ms     REAL,
    ttft_ms             REAL,
    tts_first_ms        REAL,
    e2e_first_audio_ms  REAL,
    total_ms            REAL,
    interrupted         INTEGER NOT NULL DEFAULT 0
);
"""


def init_db() -> None:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.execute(_SCHEMA)
            _conn.commit()


def _insert(row: dict) -> None:
    init_db()
    assert _conn is not None
    with _lock:
        _conn.execute(
            """INSERT INTO turns
                (session_id, turn_index, created_at, asr_text, reply_chars,
                 turn_closure_ms, ttft_ms, tts_first_ms, e2e_first_audio_ms,
                 total_ms, interrupted)
               VALUES
                (:session_id, :turn_index, :created_at, :asr_text, :reply_chars,
                 :turn_closure_ms, :ttft_ms, :tts_first_ms, :e2e_first_audio_ms,
                 :total_ms, :interrupted)""",
            row,
        )
        _conn.commit()


async def save_turn(row: dict) -> None:
    # sqlite 写入是阻塞调用，放线程池避免卡事件循环。
    await asyncio.to_thread(_insert, row)


@dataclass
class TurnMetrics:
    """一个回合的时间戳累加器。时间戳由 session 在各节点写入，flush 时折算成毫秒。"""

    session_id: str
    turn_index: int
    created_at: float  # 墙钟 epoch，仅供人读
    t_final: float  # sentence_end 到达时刻（单调时钟）
    last_partial_ts: float | None = None  # 本句最后一次 partial 时刻
    asr_text: str = ""
    reply_chars: int = 0
    t_ttft: float | None = None  # LLM 首个增量时刻
    t_tts_first: float | None = None  # TTS 首帧音频时刻
    t_done: float | None = None  # 本回合 done 时刻（被打断则保持 None）
    interrupted: bool = False

    def to_row(self) -> dict:
        def ms(end: float | None, start: float | None) -> float | None:
            if end is None or start is None:
                return None
            return round((end - start) * 1000, 1)

        return {
            "session_id": self.session_id,
            "turn_index": self.turn_index,
            "created_at": self.created_at,
            "asr_text": self.asr_text,
            "reply_chars": self.reply_chars,
            "turn_closure_ms": ms(self.t_final, self.last_partial_ts),
            "ttft_ms": ms(self.t_ttft, self.t_final),
            "tts_first_ms": ms(self.t_tts_first, self.t_ttft),
            "e2e_first_audio_ms": ms(self.t_tts_first, self.t_final),
            "total_ms": ms(self.t_done, self.t_final),
            "interrupted": int(self.interrupted),
        }
