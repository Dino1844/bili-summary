"""S2 关键帧抽取：每个 segment 抽一张"末帧"高清图 + 去重后的幻灯片集."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .models import Segment


def _grab(video: str, t: float, out: Path, width: int, crop: tuple[int, int, int, int] | None = None) -> bool:
    vf = []
    if crop:
        x, y, w, h = crop
        vf.append(f"crop={w}:{h}:{x}:{y}")
    vf.append(f"scale={width}:-2")
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-ss",
        f"{t:.3f}",
        "-i",
        video,
        "-frames:v",
        "1",
        "-vf",
        ",".join(vf),
        "-q:v",
        "3",
        "-y",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0


def extract_keyframes(
    video: str,
    segments: list[Segment],
    outdir: str | Path,
    width: int = 1280,
    crop: tuple[int, int, int, int] | None = None,
    only_main: bool = True,
) -> dict[int, str]:
    """抽关键帧, 返回 {seg_id: path}。crop 为原始分辨率下的 (x, y, w, h)。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    mapping: dict[int, str] = {}
    for s in segments:
        if only_main and s.kind not in ("start", "flip"):
            continue
        out = outdir / f"seg_{s.seg_id:04d}.jpg"
        if _grab(video, s.keyframe_t, out, width, crop):
            mapping[s.seg_id] = str(out)
    return mapping


def export_unique_slides(
    mapping: dict[int, str], segments: list[Segment], outdir: str | Path
) -> dict[int, str]:
    """按 slide_id 去重导出: slides/slide_0001.jpg (+ 记录首次出现时间)."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    by_seg = {s.seg_id: s for s in segments}
    first: dict[int, int] = {}
    for seg_id, path in sorted(mapping.items()):
        sid = by_seg[seg_id].slide_id
        if sid is None or sid in first:
            continue
        first[sid] = seg_id
        shutil.copyfile(path, outdir / f"slide_{sid:04d}.jpg")
    return {sid: str(outdir / f"slide_{sid:04d}.jpg") for sid in first}
