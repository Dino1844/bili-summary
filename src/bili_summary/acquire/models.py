"""统一的转录数据结构: 无论来自平台字幕还是本地 ASR, 都归一化成这个格式。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class Word(BaseModel):
    w: str
    s: float
    e: float


class Cue(BaseModel):
    start: float
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)
    source: str = ""


class Transcript(BaseModel):
    video: str = ""
    source: str = ""  # bilibili:ai-zh | whisper:small | ...
    duration: float = 0.0
    cues: list[Cue] = Field(default_factory=list)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.model_dump_json(indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> Transcript:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def text_between(self, t0: float, t1: float) -> str:
        return " ".join(c.text for c in self.cues if c.end > t0 and c.start < t1).strip()

    def snap(self, t: float, max_shift: float = 2.0) -> float:
        """把边界吸附到最近的词间停顿(静音点), 避免把句子切两半。"""
        best, best_d = t, max_shift
        for c in self.cues:
            for w in c.words:
                for cand in (w.s, w.e):
                    d = abs(cand - t)
                    if d < best_d:
                        best, best_d = cand, d
        return best


def from_bilibili(path: str | Path, video: str = "") -> Transcript:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    cues = [
        Cue(start=s["start"], end=s["end"], text=s["text"], source=s.get("source", "bilibili"))
        for s in d.get("segments", [])
    ]
    return Transcript(
        video=video, source="bilibili_subtitle", duration=cues[-1].end if cues else 0.0, cues=cues
    )


def from_whisper(path: str | Path, video: str = "") -> Transcript:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    segs = d.get("segments", d if isinstance(d, list) else [])
    cues = []
    for s in segs:
        words = [Word(w=w["w"], s=w["s"], e=w["e"]) for w in s.get("words", [])]
        cues.append(
            Cue(
                start=s["start"], end=s["end"], text=s["text"], words=words, source=d.get("source", "whisper")
            )
        )
    return Transcript(
        video=video, source=d.get("source", "whisper"), duration=cues[-1].end if cues else 0.0, cues=cues
    )


def load_any(path: str | Path, video: str = "") -> Transcript:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(d, dict) and "cues" in d:
        return Transcript.model_validate(d)
    src = d.get("source", "") if isinstance(d, dict) else ""
    if "bilibili" in str(src) or (isinstance(d, dict) and "bvid" in d):
        return from_bilibili(path, video)
    return from_whisper(path, video)
