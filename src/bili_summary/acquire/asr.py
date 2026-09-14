"""S1 ASR: faster-whisper 本地转录(带词级时间戳, 供 S3 边界吸附)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from .models import Cue, Transcript, Word


def extract_audio(video: str | Path, out: str | Path, sr: int = 16000) -> str:
    out = str(out)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sr),
            "-c:a",
            "pcm_s16le",
            out,
        ],
        check=True,
    )
    return out


def transcribe(
    audio: str | Path,
    out: str | Path,
    model: str = "small",
    language: str = "zh",
    device: str = "auto",
    compute_type: str | None = None,
    initial_prompt: str | None = (
        "以下是普通话的简体中文转录，内容为 GPU 与 CUDA 编程的技术讲座。"
        "术语：CUDA、kernel、grid、block、thread、warp、SIMT、thread block、"
        "shared memory、global memory、register、occupancy、coalescing、"
        "Tensor Core、mma.sync、tcgen05、PTX、latency hiding、stream。"
    ),
    video: str = "",
) -> Transcript:
    from faster_whisper import WhisperModel

    attempts = []
    if device in ("auto", "cuda"):
        attempts.append(("cuda", compute_type or "float16"))
        attempts.append(("cuda", "int8_float16"))
    attempts.append(("cpu", compute_type or "int8"))

    last_err = None
    m = None
    used = None
    for dev, ct in attempts:
        try:
            m = WhisperModel(model, device=dev, compute_type=ct)
            used = (dev, ct)
            break
        except Exception as e:  # cuDNN/cuBLAS 缺失等
            last_err = e
    if m is None:
        raise RuntimeError(f"cannot load whisper model: {last_err}")

    t0 = time.time()
    segments, info = m.transcribe(
        str(audio),
        language=language,
        initial_prompt=initial_prompt,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        word_timestamps=True,
        beam_size=5,
    )
    cues: list[Cue] = []
    last_log = 0.0
    for seg in segments:
        words = [Word(w=w.word, s=round(w.start, 2), e=round(w.end, 2)) for w in (seg.words or [])]
        cues.append(
            Cue(
                start=round(seg.start, 2),
                end=round(seg.end, 2),
                text=seg.text.strip(),
                words=words,
                source=f"whisper:{model}",
            )
        )
        if seg.end - last_log > 300:
            last_log = seg.end
            print(f"  asr {seg.end:.0f}s/{info.duration:.0f}s elapsed {time.time() - t0:.0f}s", flush=True)

    tr = Transcript(
        video=video,
        source=f"whisper:{model}",
        duration=float(info.duration or (cues[-1].end if cues else 0)),
        cues=cues,
    )
    tr.save(out)
    print(f"asr done: {len(cues)} cues, device={used}, {time.time() - t0:.0f}s", flush=True)
    return tr
