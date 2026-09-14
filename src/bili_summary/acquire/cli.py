from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print as rprint

from . import bilibili
from .asr import extract_audio, transcribe

app = typer.Typer(help="S1: 采集 B站视频 / 官方字幕 / ASR", no_args_is_help=True)


@app.command()
def subs(
    bvid: str = typer.Argument(..., help="如 BV1LxM96eE43"),
    p: int = typer.Option(None, "--p", help="分P"),
    fetch_body: bool = typer.Option(False, "--fetch", help="真的抓取字幕正文"),
    out: str = typer.Option(None, "--out", "-o"),
) -> None:
    """探测(并可选抓取) B站官方字幕."""
    if fetch_body:
        if not out:
            raise typer.BadParameter("--fetch 需要 --out")
        rprint(bilibili.fetch(bvid, out, p=p))
    else:
        r = bilibili.probe(bvid, p=p)
        rprint(json.dumps(r, ensure_ascii=False, indent=1))


@app.command()
def asr(
    video: str = typer.Option(..., "--video", help="视频/音频文件"),
    out: str = typer.Option(..., "--out", "-o", help="转录 json 输出"),
    model: str = typer.Option("small", "--model"),
    language: str = typer.Option("zh", "--language"),
    device: str = typer.Option("auto", "--device"),
    audio: str = typer.Option(None, "--audio", help="复用已提取的 wav, 跳过抽音"),
) -> None:
    """本地 ASR 转录(faster-whisper), 带词级时间戳."""
    a = audio
    if not a:
        a = str(Path(out).with_suffix(".wav"))
        rprint(f"[cyan]extract audio[/] -> {a}")
        extract_audio(video, a)
    tr = transcribe(a, out, model=model, language=language, device=device, video=video)
    rprint(f"[green]wrote[/] {out}  cues={len(tr.cues)} source={tr.source} dur={tr.duration:.0f}s")


def main() -> None:
    app()
