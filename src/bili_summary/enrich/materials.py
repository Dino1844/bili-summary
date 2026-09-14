"""S3 素材装配: 把 S2 的切分 + 转录对齐成"每段一份素材包"。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from ..acquire.models import Transcript, load_any
from ..segment.models import SegmentationResult
from ..workspace import Workspace


class Material(BaseModel):
    seg_id: int
    slide_id: int | None = None
    t_start: float
    t_end: float
    duration: float
    kind: str
    image: str
    text: str
    n_chars: int = 0
    is_revisit: bool = False


def _crop_roi(
    src: Path,
    dst: Path,
    roi: tuple[int, int, int, int] | None,
    sample: tuple[int, int] = (320, 180),
    out_width: int = 1100,
    quality: int = 84,
) -> None:
    """按 roi(采样坐标) 裁剪关键帧。带安全阀: 裁完必须保留 >=60% 画面, 否则不裁。"""
    from PIL import Image

    im = Image.open(src).convert("RGB")
    if roi:
        x, y, w, h = roi
        sw, sh = sample
        sx, sy = im.width / sw, im.height / sh
        box = (
            max(0, int(x * sx)),
            max(0, int(y * sy)),
            min(im.width, int((x + w) * sx)),
            min(im.height, int((y + h) * sy)),
        )
        if (box[2] - box[0] >= im.width * 0.6) and (box[3] - box[1] >= im.height * 0.6):
            im = im.crop(box)
    if im.width > out_width:
        im = im.resize((out_width, max(1, int(im.height * out_width / im.width))), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, quality=quality)


def build_materials(
    workdir: str | Path,
    transcript_path: str | Path | None = None,
    roi: tuple[int, int, int, int] | None = None,
    crop: bool = False,
    out_width: int = 1100,
    pad: float = 1.0,
) -> list[Material]:
    """默认不裁剪 —— 任何自动裁剪都有切掉幻灯片内容的风险, 而 chrome 不影响理解。
    需要裁剪时显式传 crop=True(此时用 S2 的 static_box, 并带 60% 安全阀)。"""
    wd = Path(workdir)
    ws = Workspace(wd).ensure()
    seg_path = ws.segments_json
    res = SegmentationResult.model_validate_json(seg_path.read_text(encoding="utf-8"))
    if roi is None:
        roi = res.roi
    if not crop:
        roi = None

    tpath = Path(transcript_path) if transcript_path else ws.transcript_json
    tr = load_any(tpath) if tpath.exists() else Transcript()
    # 平台字幕/ASR 的时长可能与视频对齐, 用转录时长兜底
    if tr.duration and res.duration and abs(tr.duration - res.duration) > 60:
        print(f"  warn: transcript {tr.duration:.0f}s vs video {res.duration:.0f}s")

    mat_dir = ws.materials_dir
    mats: list[Material] = []
    for s in res.segments:
        if s.kind not in ("start", "flip"):
            continue
        kf = ws.keyframes / f"seg_{s.seg_id:04d}.jpg"
        if not kf.exists():
            continue
        t0 = max(0.0, s.t_start + pad)
        t1 = max(t0, s.t_end - pad)
        text = tr.text_between(t0, t1)
        img = mat_dir / f"seg_{s.seg_id:04d}.jpg"
        _crop_roi(kf, img, roi, sample=(res.config.sample_w, res.config.sample_h), out_width=out_width)
        mats.append(
            Material(
                seg_id=s.seg_id,
                slide_id=s.slide_id,
                t_start=s.t_start,
                t_end=s.t_end,
                duration=s.duration,
                kind=s.kind,
                image=ws.rel(img),
                text=text,
                n_chars=len(text),
                is_revisit=s.is_revisit,
            )
        )
    ws.materials_json.write_text(
        json.dumps([m.model_dump() for m in mats], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return mats
