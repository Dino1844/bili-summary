"""编排: `bili-summary run <链接或本地文件>` —— 一条命令跑完全流程。

阶段: download -> transcript -> segment -> enrich -> describe -> teach -> lecture -> report
所有产物经 Workspace 归位到 work/<id>/{final,data,cache,source}/。
每个阶段都有产物落盘, 重复运行会跳过已完成的部分(按 `--from` 可强制从某阶段开始)。
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print as rprint
from rich.panel import Panel

from . import assemble as assemble_mod
from . import describe as describe_mod
from . import enrich as enrich_mod
from . import teach as teach_mod
from .acquire import bilibili
from .acquire import download as dl
from .acquire.asr import extract_audio, transcribe
from .config import PKG_ROOT
from .describe import m3
from .segment import detector, keyframes, sampler
from .segment.models import SegmentationResult, SegmentConfig
from .workspace import Workspace

app = typer.Typer(help="全流程编排", no_args_is_help=True)

STAGES = ["download", "transcript", "segment", "enrich", "describe", "teach", "lecture", "report"]


# ---------------------------------------------------------------- helpers


def default_workdir(source: str) -> Path:
    if dl.is_url(source):
        return PKG_ROOT / "work" / (dl.bv_id(source) or "video")
    p = Path(source).expanduser().resolve()
    return p.parent / f"{p.stem}_notes"


def run_segment(
    video: str,
    workdir: str | Path,
    t_low: float = 2.5,
    area_min: float = 0.04,
    width: int = 1280,
    metrics: str | None = None,
) -> SegmentationResult:
    """S2: 采样 -> 切分 -> 去重 -> 关键帧。"""
    ws = Workspace(workdir).ensure()
    cfg = SegmentConfig(t_low=t_low, area_min=area_min)
    mpath = Path(metrics) if metrics else ws.metrics
    if mpath.exists():
        m = sampler.load_metrics(mpath)
    else:
        m = sampler.sample_metrics(
            video,
            cfg.fps,
            (cfg.sample_w, cfg.sample_h),
            cfg.crop,
            block=cfg.metric_block,
            px_diff=cfg.px_diff,
            ink_margin=cfg.ink_margin,
        )
        sampler.encode_metrics(m, mpath)
    res = detector.detect(m, cfg, video=video)
    res.roi = tuple(int(v) * cfg.metric_block for v in sampler.static_box(m.lum))
    sampler.save_roi_heatmap(m.lum, ws.roi_heatmap)
    keyframes.extract_keyframes(video, res.segments, ws.keyframes, width=width)
    ws.segments_json.write_text(res.model_dump_json(indent=1), encoding="utf-8")
    return res


def build_transcript(
    source: str,
    workdir: str | Path,
    video: str,
    model: str = "small",
    device: str = "cuda",
    force: bool = False,
) -> Path:
    """S1: 平台官方字幕优先, 无字幕再走 ASR。"""
    ws = Workspace(workdir).ensure()
    out = ws.transcript_json
    if out.exists() and not force:
        rprint(f"  [dim]transcript 已存在, 跳过[/] {out.name}")
        return out
    if dl.is_url(source) and dl.bv_id(source):
        rprint("  [cyan]尝试 B站官方字幕...[/]")
        try:
            info = bilibili.fetch(dl.bv_id(source), out, p=dl.page_of(source))
            if info["n_segments"] > 0:
                rprint(f"  [green]官方字幕[/] {info['n_segments']} 条 (source=bilibili)")
                return out
            rprint("  [yellow]该视频没有官方字幕, 转 ASR[/]")
        except Exception as e:
            rprint(f"  [yellow]字幕接口失败({type(e).__name__}), 转 ASR[/]")
    if not ws.audio.exists():
        rprint("  [cyan]抽取音频...[/]")
        extract_audio(video, ws.audio)
    tr = transcribe(ws.audio, out, model=model, language="zh", device=device, video=video)
    rprint(f"  [green]ASR 完成[/] {len(tr.cues)} 段 (model={model})")
    return out


# ---------------------------------------------------------------- commands


@app.command()
def run(
    source: str = typer.Argument(..., help="B站/YouTube 链接, 或本地视频路径"),
    workdir: str = typer.Option(None, "--workdir", "-o", help="产物目录(默认自动推导)"),
    title: str = typer.Option(None, "--title"),
    window: int = typer.Option(6, "--window", help="S4 每个 M3 窗口包含的段数"),
    asr_model: str = typer.Option("small", "--asr-model"),
    device: str = typer.Option("cuda", "--device", help="ASR 设备: cuda|cpu"),
    max_height: int = typer.Option(720, "--max-height", help="下载分辨率上限"),
    crop: bool = typer.Option(False, "--crop/--no-crop", help="是否裁剪边框(默认不裁)"),
    limit: int = typer.Option(None, "--limit", help="S4 只跑前 N 段(快速试跑)"),
    jobs: int = typer.Option(4, "--jobs", help="M3 并行调用数(不同窗口相互独立)"),
    no_teach: bool = typer.Option(False, "--no-teach", help="跳过教学包与讲稿"),
    force_from: str = typer.Option(None, "--from", help=f"从某阶段强制重跑: {STAGES}"),
) -> None:
    """粘贴链接即可: 下载 -> 字幕/ASR -> 切分 -> 素材 -> M3 笔记 -> 教学包 -> 讲稿 -> 报告。"""
    ws = Workspace(Path(workdir).expanduser() if workdir else default_workdir(source)).ensure()
    start = STAGES.index(force_from) if force_from else 0
    is_url = dl.is_url(source)
    rprint(Panel.fit(f"[bold]output[/] {ws.root}", title="bili-summary run"))

    # --- download
    if is_url:
        if start <= 0:
            rprint("[bold cyan][1/8] 下载视频[/]")
        info = dl.download(source, ws.source, max_height=max_height)
        if start <= 0:
            rprint(f"  {info['title']}  ({info.get('duration')}s)")
        video = info["video"]
        if not title:
            title = info.get("title") or ws.root.name
    else:
        video = str(Path(source).expanduser().resolve())
        if not Path(video).exists():
            raise typer.BadParameter(f"文件不存在: {video}")
        ws.source.mkdir(parents=True, exist_ok=True)

    # --- transcript
    if start <= 1:
        rprint("[bold cyan][2/8] 获取字幕/转录[/]")
        build_transcript(
            source, ws.root, video, model=asr_model, device=device, force=(force_from == "transcript")
        )

    # --- segment
    if start <= 2:
        rprint("[bold cyan][3/8] 按 PPT 切分[/]")
        if ws.segments_json.exists() and force_from != "segment":
            res = SegmentationResult.model_validate_json(ws.segments_json.read_text())
        else:
            res = run_segment(video, ws.root)
        rprint(
            f"  段={res.stats['n_segments']} 候选页={res.stats['n_main_slides']} "
            f"唯一页={res.stats['n_unique_slides']} 平均停留={res.stats['mean_dwell_s']}s"
        )

    # --- enrich
    if start <= 3:
        rprint("[bold cyan][4/8] 素材装配[/]")
        mats = enrich_mod.build_materials(ws.root, crop=crop)
        rprint(f"  素材={len(mats)} 段 (有讲稿 {sum(1 for m in mats if m.n_chars > 0)})")

    # --- describe
    if start <= 4:
        rprint("[bold cyan][5/8] M3 逐页笔记[/]")
        slides = describe_mod.fuse(ws.root, window=window, limit=limit, jobs=jobs)
        rprint(f"  幻灯片={len(slides)} 页")
        m3.flush_usage(ws.root, "describe")

    # --- teach
    if start <= 5 and not no_teach:
        rprint("[bold cyan][6/8] 教学增强包[/]")
        t = teach_mod.build_teaching_pack(ws.root, title=title or ws.root.name, jobs=jobs)
        rprint(f"  学习目标={t['objectives']} 闪卡={t['flashcards']} 测验={t['quiz']} 题")
        m3.flush_usage(ws.root, "teach")

    # --- lecture
    if start <= 6 and not no_teach:
        rprint("[bold cyan][7/8] 生成讲稿[/]")
        lec = teach_mod.build_lecture(ws.root, title=title or ws.root.name, jobs=jobs)
        rprint(f"  讲稿 {lec['sections']} 章, {lec['chars']} 字")
        m3.flush_usage(ws.root, "lecture")

    # --- report
    if start <= 7:
        rprint("[bold cyan][8/8] 生成报告[/]")
        r = assemble_mod.build_report(
            ws.root, title=title or ws.root.name, video_url=source if is_url else ""
        )
        if (ws.lecture_md).exists():
            lh = assemble_mod.build_lecture_html(ws.root, title=title or ws.root.name)
            rprint(f"  lecture.html ({lh['html_bytes'] / 1e6:.1f} MB, 附 {lh['appendix']} 份资料)")
        rprint(f"  notes.md + report.html ({r['html_bytes'] / 1e6:.1f} MB)")

    index = ws.write_index(title or ws.root.name)
    table, total = m3.usage_table(ws.root)
    rprint(
        Panel.fit(
            table,
            title=f"M3 用量 (calls={total.get('calls', 0)}, "
            f"total={total.get('in', 0) + total.get('out', 0):,} tokens)",
        )
    )
    rprint(
        Panel.fit(
            f"[bold green]final/[/]   ⭐ 成品, 只看这里\n"
            f"  lecture.html  ← ⭐ 独立讲义（正文+全部资料+内嵌图，双击即开）\n"
            f"  report.html   结构化报告（逐页笔记）\n"
            f"  lecture.md  notes.md\n"
            f"  learning_pack.md  flashcards.md  quiz.md  mindmap.mmd  study_plan.md\n\n"
            f"[cyan]data/[/]    结构化 JSON      [cyan]cache/[/]  中间产物(可删)\n"
            f"[cyan]source/[/]  原始视频\n\n"
            f"[dim]{index}[/]",
            title=f"完成 -> {ws.root}",
        )
    )


@app.command()
def enrich(
    workdir: str = typer.Option(..., "--workdir"),
    transcript: str = typer.Option(None, "--transcript"),
    crop: bool = typer.Option(False, "--crop/--no-crop"),
    out_width: int = typer.Option(1100, "--out-width"),
) -> None:
    """S3: 每段素材包(裁剪可选 + 对齐讲稿)."""
    mats = enrich_mod.build_materials(workdir, transcript, crop=crop, out_width=out_width)
    rprint(f"[green]materials[/] {len(mats)} 段, 有讲稿 {sum(1 for m in mats if m.n_chars > 0)} 段")


@app.command()
def describe(
    workdir: str = typer.Option(..., "--workdir"),
    window: int = typer.Option(6, "--window"),
    jobs: int = typer.Option(4, "--jobs", help="并行窗口数"),
    limit: int = typer.Option(None, "--limit"),
) -> None:
    """S4: M3 逐窗口融合 + 全局去重(并行)."""
    slides = describe_mod.fuse(workdir, window=window, limit=limit, jobs=jobs)
    rprint(f"[green]slides[/] {len(slides)} 页")
    m3.flush_usage(workdir, "describe")
    rprint(m3.usage_table(workdir)[0])


@app.command()
def dedup(
    workdir: str = typer.Option(..., "--workdir"),
    threshold: float = typer.Option(0.08, "--threshold", help="图像相似度候选阈值(差异占比), 越小越严格"),
    jobs: int = typer.Option(4, "--jobs"),
) -> None:
    """对已有 slides.json 做基于图像的全局去重(M3 看原图确认)."""
    ws = Workspace(workdir).ensure()
    slides = json.loads(ws.slides_json.read_text(encoding="utf-8"))
    out = describe_mod.dedup_slides(ws.root, slides, sim_threshold=threshold, jobs=jobs)
    ws.slides_json.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    rprint(f"[green]dedup[/] {len(slides)} -> {len(out)} 页")
    m3.flush_usage(ws.root, "dedup")
    rprint(m3.usage_table(ws.root)[0])


@app.command()
def teach(
    workdir: str = typer.Option(..., "--workdir"),
    title: str = typer.Option("", "--title"),
    jobs: int = typer.Option(3, "--jobs"),
) -> None:
    """S6: 生成教学增强包(学习目标/闪卡/测验/思维导图/复习计划)."""
    t = teach_mod.build_teaching_pack(workdir, title=title, jobs=jobs)
    rprint(f"[green]teach[/] 目标={t['objectives']} 闪卡={t['flashcards']} 测验={t['quiz']}")
    m3.flush_usage(workdir, "teach")
    rprint(m3.usage_table(workdir)[0])


@app.command()
def lecture(
    workdir: str = typer.Option(..., "--workdir"),
    title: str = typer.Option("", "--title"),
    jobs: int = typer.Option(4, "--jobs"),
    sections: int = typer.Option(None, "--sections", help="章节数(默认自动)"),
) -> None:
    """S6.1: 把逐页笔记重写成一篇连贯讲稿(自动滤掉调试/闲聊等过程性内容), 并渲染成 HTML."""
    r = teach_mod.build_lecture(workdir, title=title, jobs=jobs, n_sections=sections)
    rprint(f"[green]lecture[/] {r['sections']} 章, {r['chars']} 字 -> {r['file']}")
    lh = assemble_mod.build_lecture_html(workdir, title=title)
    rprint(f"[green]lecture.html[/] {lh['html_bytes'] / 1e6:.1f} MB (附 {lh['appendix']} 份资料)")
    m3.flush_usage(workdir, "lecture")
    rprint(m3.usage_table(workdir)[0])


@app.command()
def report(
    workdir: str = typer.Option(..., "--workdir"),
    title: str = typer.Option("", "--title"),
    url: str = typer.Option("", "--url"),
) -> None:
    """S5: 生成 notes.md + report.html, 并刷新目录索引."""
    ws = Workspace(workdir).ensure()
    r = assemble_mod.build_report(ws.root, title=title, video_url=url)
    ws.write_index(title or ws.root.name)
    rprint(f"[green]report[/] {r['slides']} 页 -> {r['html']} ({r['html_bytes'] / 1e6:.1f} MB)")


@app.command()
def render(
    workdir: str = typer.Option(..., "--workdir"),
    title: str = typer.Option("", "--title"),
    url: str = typer.Option("", "--url"),
    math: str = typer.Option("inline", "--math", help="公式渲染: inline | cdn | none"),
) -> None:
    """**不调用 M3**: 从已有的 md/json 重新渲染 lecture.html 与 report.html。

    改样式、修公式渲染等都用它, 零 token 成本。
    """
    ws = Workspace(workdir).ensure()
    t = title or ws.root.name
    r = assemble_mod.build_report(ws.root, title=t, video_url=url, math=math)
    rprint(f"[green]report.html[/] {r['html_bytes'] / 1e6:.2f} MB")
    if ws.lecture_md.exists():
        lh = assemble_mod.build_lecture_html(ws.root, title=t, math=math)
        rprint(f"[green]lecture.html[/] {lh['html_bytes'] / 1e6:.2f} MB (附 {lh['appendix']} 份资料)")
    else:
        rprint("[yellow]没有 lecture.md, 跳过 lecture.html[/]")
    ws.write_index(t)


@app.command()
def usage(workdir: str = typer.Option(..., "--workdir")) -> None:
    """查看该视频的 M3 token 用量."""
    table, total = m3.usage_table(workdir)
    rprint(
        Panel.fit(
            table,
            title=f"M3 用量 (calls={total.get('calls', 0)}, "
            f"{total.get('in', 0) + total.get('out', 0):,} tokens)",
        )
    )


@app.command("sheet")
def sheet(
    workdir: str = typer.Option(..., "--workdir"),
    out: str = typer.Option(None, "--out"),
    cols: int = typer.Option(6, "--cols"),
    tile: int = typer.Option(300, "--tile"),
    source: str = typer.Option("materials", "--source", help="materials | keyframes | cuts"),
) -> None:
    """把幻灯片拼成一张总览图, 便于人工验收(输出到 final/)."""
    from PIL import Image, ImageDraw

    ws = Workspace(workdir).ensure()
    d = {"materials": ws.materials_dir, "keyframes": ws.keyframes, "cuts": ws.cuts_dir}.get(source)
    if d is None or not d.exists():
        raise typer.BadParameter(f"没有 {source}/ 目录")
    files = sorted(d.glob("*.jpg")) + sorted(d.glob("*.png"))
    if not files:
        raise typer.BadParameter(f"{d} 下没有图片")
    im0 = Image.open(files[0])
    th = int(tile * im0.height / im0.width)
    rows = (len(files) + cols - 1) // cols
    lab = 20
    S = Image.new("RGB", (cols * tile, rows * (th + lab)), (240, 240, 240))
    draw = ImageDraw.Draw(S)
    for i, f in enumerate(files):
        r, c = divmod(i, cols)
        x, y = c * tile, r * (th + lab)
        S.paste(Image.open(f).convert("RGB").resize((tile, th)), (x, y))
        draw.rectangle([x, y, x + tile - 1, y + th], outline=(150, 150, 150))
        draw.text((x + 3, y + th + 4), f.stem, fill=(0, 0, 0))
    outp = Path(out) if out else (ws.final / f"sheet_{source}.png")
    S.save(outp)
    rprint(f"[green]sheet[/] {len(files)} 张 -> {outp} {S.size}")


def main() -> None:
    app()
