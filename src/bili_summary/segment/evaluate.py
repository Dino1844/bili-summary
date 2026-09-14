"""S2 核验：把检出的切点做成"翻页前 / 翻页后"对照图, 供人眼判定准/漏/误切."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .keyframes import _grab
from .models import Segment


def _spread(segs: list[Segment], n: int) -> list[Segment]:
    if len(segs) <= n:
        return segs
    step = len(segs) / n
    return [segs[min(int(i * step), len(segs) - 1)] for i in range(n)]


def build_cut_sheet(
    video: str,
    segments: list[Segment],
    outdir: str | Path,
    n: int = 16,
    width: int = 640,
    pad: float = 2.0,
    tile_w: int = 480,
) -> str:
    """抽取 n 个 flip 切点的前后帧, 拼成对照图。返回图片路径。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    flips = [s for s in segments if s.kind in ("flip", "switch")]
    picked = _spread(flips, n)

    rows: list[tuple[Image.Image, Image.Image, Segment]] = []
    for s in picked:
        b = outdir / f"cut_{s.seg_id:04d}_before.jpg"
        a = outdir / f"cut_{s.seg_id:04d}_after.jpg"
        _grab(video, max(0.0, s.t_start - pad), b, width)
        _grab(video, s.t_start + pad, a, width)
        if b.exists() and a.exists():
            rows.append((Image.open(b).convert("RGB"), Image.open(a).convert("RGB"), s))

    if not rows:
        raise RuntimeError("no cut frames extracted")

    ratio = rows[0][0].height / rows[0][0].width
    th = int(tile_w * ratio)
    label_h = 26
    cols = 2
    rows_per_img = (len(rows) + cols - 1) // cols
    cell_w, cell_h = tile_w, th + label_h
    sheet = Image.new("RGB", (cols * cell_w, rows_per_img * cell_h), (250, 250, 250))
    draw = ImageDraw.Draw(sheet)

    for i, (bimg, aimg, s) in enumerate(rows):
        r, c = divmod(i, cols)
        for j, (im, _tag) in enumerate(((bimg, "BEFORE"), (aimg, "AFTER"))):
            x = c * cell_w + j * (cell_w // 2)
            y = r * cell_h
            im = im.resize((cell_w // 2, th))
            sheet.paste(im, (x, y))
            draw.rectangle([x, y, x + cell_w // 2 - 1, y + th], outline=(180, 180, 180))
        mm, ss = divmod(int(s.t_start), 60)
        draw.text(
            (c * cell_w + 4, r * cell_h + th + 4),
            f"seg{s.seg_id:03d} {mm:02d}:{ss:02d} {s.kind} score={s.change_score:.1f} area={s.change_area:.2f}",
            fill=(20, 20, 20),
        )

    out = outdir / "cut_sheet.png"
    sheet.save(out)
    return str(out)


def precision_report(segments: list[Segment]) -> str:
    """粗略统计: 切点密度、动态段占比、最短/最长页。"""
    from collections import Counter

    kinds = Counter(s.kind for s in segments)
    durs = sorted(s.duration for s in segments)
    lines = [
        f"segments      : {len(segments)}",
        f"kinds         : {dict(kinds)}",
        f"duration min/med/max : {durs[0]:.0f} / {durs[len(durs) // 2]:.0f} / {durs[-1]:.0f} s",
    ]
    return "\n".join(lines)
