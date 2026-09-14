from __future__ import annotations

from pathlib import Path

import numpy as np
import typer
from rich import print as rprint
from rich.table import Table

from ..workspace import Workspace
from . import detector, evaluate, keyframes, sampler
from .models import SegmentationResult, SegmentConfig
from .sampler import Metrics

app = typer.Typer(help="S2: 按 PPT 翻页切分课程视频", no_args_is_help=True)


def _load_metrics(
    video: str,
    cfg: SegmentConfig,
    reuse_gray: str | None,
    gray_size: tuple[int, int] | None,
    metrics: str | None = None,
) -> Metrics:
    if metrics and Path(metrics).exists():
        rprint(f"[cyan]metrics cache[/] {metrics}")
        return sampler.load_metrics(metrics)
    if reuse_gray:
        gw, gh = gray_size or (320, 180)
        rprint(f"[cyan]reuse gray[/] {reuse_gray} ({gw}x{gh}) — 无颜色, 无法剔除红笔批注")
        sig = sampler.load_gray_raw(reuse_gray, gw, gh, (cfg.sample_w, cfg.sample_h))
        m = sampler.metrics_from_gray(sig, block=cfg.metric_block, px_thresh=cfg.px_thresh)
    else:
        rprint(f"[cyan]sampling RGB[/] {video} @{cfg.fps}fps -> {cfg.sample_w}x{cfg.sample_h}")
        m = sampler.sample_metrics(
            video,
            cfg.fps,
            (cfg.sample_w, cfg.sample_h),
            cfg.crop,
            block=cfg.metric_block,
            px_diff=cfg.px_diff,
            ink_margin=cfg.ink_margin,
        )
    if metrics:
        sampler.encode_metrics(m, metrics)
        rprint(f"[cyan]metrics saved[/] {metrics}")
    return m


@app.command()
def run(
    video: str = typer.Argument(..., help="视频文件"),
    workdir: str = typer.Option(..., "--workdir", "-o", help="输出目录"),
    reuse_gray: str = typer.Option(None, "--reuse-gray", help="复用已有 raw gray(灰度兜底, 无颜色)"),
    gray_size: str = typer.Option(None, "--gray-size", help="raw gray 分辨率, 如 320x180"),
    metrics: str = typer.Option(None, "--metrics", help="度量缓存 npz(存在则直接复用)"),
    fps: float = typer.Option(1.0, "--fps"),
    width: int = typer.Option(1280, "--width", help="关键帧导出宽度"),
    no_keyframes: bool = typer.Option(False, "--no-keyframes"),
    dedup_hamming: int = typer.Option(6, "--dedup-hamming"),
    merge_page_diff: float = typer.Option(0.02, "--merge-page-diff"),
    t_low: float = typer.Option(2.5, "--t-low"),
    t_switch: float = typer.Option(25.0, "--t-switch"),
    area_min: float = typer.Option(0.03, "--area-min"),
    ink_margin: float = typer.Option(25.0, "--ink-margin"),
    px_diff: float = typer.Option(20.0, "--px-diff"),
) -> None:
    """一次跑完: 采样 -> 切分 -> 去重 -> 关键帧."""
    cfg = SegmentConfig(
        fps=fps,
        dedup_hamming=dedup_hamming,
        t_low=t_low,
        t_switch=t_switch,
        area_min=area_min,
        ink_margin=ink_margin,
        px_diff=px_diff,
        merge_page_diff=merge_page_diff,
    )
    gs = None
    if gray_size:
        gw, gh = gray_size.lower().split("x")
        gs = (int(gw), int(gh))

    ws = Workspace(workdir).ensure()

    m = _load_metrics(video, cfg, reuse_gray, gs, metrics or str(ws.metrics))
    rprint(f"[cyan]frames[/] {m.n}  (ink 总量 {m.ink.sum():.0f})")

    roi = sampler.static_box(m.lum)
    roi = tuple(int(v) * cfg.metric_block for v in roi)  # 换算到采样坐标, 供 S3 裁剪
    sampler.save_roi_heatmap(m.lum, ws.roi_heatmap)

    res = detector.detect(m, cfg, video=video)
    res.roi = roi

    mapping: dict[int, str] = {}
    if not no_keyframes:
        rprint("[cyan]extracting keyframes...[/]")
        mapping = keyframes.extract_keyframes(video, res.segments, ws.keyframes, width=width)
        keyframes.export_unique_slides(mapping, res.segments, ws.final / "slides")

    ws.segments_json.write_text(res.model_dump_json(indent=1), encoding="utf-8")

    t = Table(title="S2 切分结果", show_header=False)
    t.add_row("duration", f"{res.duration / 60:.1f} min ({res.n_frames} frames)")
    t.add_row("segments", str(res.stats["n_segments"]))
    t.add_row("main slides", str(res.stats["n_main_slides"]))
    t.add_row("unique slides", f"{res.stats['n_unique_slides']}  (revisit {res.stats['n_revisits']})")
    t.add_row("slides/hour", str(res.stats["slides_per_hour"]))
    t.add_row("mean dwell", f"{res.stats['mean_dwell_s']} s")
    t.add_row("kinds", str(res.stats["kinds"]))
    t.add_row("total ink", str(res.stats["total_ink"]))
    t.add_row("crop box (320x180 坐标)", str(roi))
    t.add_row("keyframes", str(len(mapping)))
    rprint(t)
    rprint(f"[green]wrote[/] {ws.segments_json}")


@app.command()
def stats(
    video: str = typer.Argument(..., help="视频文件"),
    reuse_gray: str = typer.Option(None, "--reuse-gray"),
    gray_size: str = typer.Option("320x180", "--gray-size"),
    metrics: str = typer.Option(None, "--metrics"),
    metric_block: int = typer.Option(4, "--metric-block"),
    ink_margin: float = typer.Option(25.0, "--ink-margin"),
) -> None:
    """阈值敏感性诊断: 用分布定阈值, 不靠猜."""
    cfg = SegmentConfig(metric_block=metric_block, ink_margin=ink_margin)
    gw, gh = (int(v) for v in gray_size.lower().split("x"))
    m = _load_metrics(video, cfg, reuse_gray, (gw, gh), metrics)

    rprint("[bold]mad 分位数[/]")
    ps = [50, 75, 90, 95, 96, 97, 98, 99]
    rprint("  " + "  ".join(f"p{p}={np.percentile(m.mad, p):.2f}" for p in ps) + f"  max={m.mad.max():.1f}")
    rprint("[bold]结构性 area 分位数[/]")
    rprint("  " + "  ".join(f"p{p}={np.percentile(m.area, p):.3f}" for p in ps) + f"  max={m.area.max():.3f}")
    rprint(f"[bold]ink(笔迹) 总量[/] {m.ink.sum():.0f}")

    t = Table(title="阈值 -> 段数")
    for c in ("t_low", "area_min", "events", "segments/hour"):
        t.add_column(c)
    n = m.n
    for thr in [1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        for am in [0.01, 0.03, 0.06]:
            cfg2 = cfg.model_copy(update={"t_low": thr, "area_min": am})
            kinds = detector.classify(m, cfg2)
            trans = detector._merge_transitions(kinds, m.mad, m.area, cfg2)
            ev = detector._events(trans, cfg2)
            t.add_row(f"{thr}", f"{am}", str(len(ev)), f"{len(ev) / (n / 3600):.0f}")
    rprint(t)


@app.command("eval")
def eval_(
    video: str = typer.Option(..., "--video"),
    workdir: str = typer.Option(..., "--workdir"),
    n: int = typer.Option(16, "--n"),
    width: int = typer.Option(640, "--width"),
) -> None:
    """生成切点对照图 cut_sheet.png, 人工核验准/漏/误切."""
    ws = Workspace(workdir).ensure()
    res = SegmentationResult.model_validate_json(ws.segments_json.read_text())
    rprint(evaluate.precision_report(res.segments))
    out = evaluate.build_cut_sheet(video, res.segments, ws.cuts_dir, n=n, width=width)
    rprint(f"[green]cut sheet[/] {out}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
