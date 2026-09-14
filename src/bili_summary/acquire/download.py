"""S1 下载: 用 yt-dlp 抓视频/音频并落 info.json。"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..config import sessdata


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text).strip("_")
    return s[:n] or "video"


def download(url: str, outdir: str | Path, max_height: int = 720, use_cookie: bool = True) -> dict:
    """下载视频(合并音轨) + 写 info.json。返回 {video, info, title, duration, bvid}。"""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    video = outdir / "video.mp4"
    info_path = outdir / "info.json"
    legacy = [p for p in (outdir / "video.info.json", outdir / "meta.json") if p.exists()]
    if video.exists() and (info_path.exists() or legacy):
        src = info_path if info_path.exists() else legacy[0]
        info = json.loads(src.read_text(encoding="utf-8"))
        return {
            "video": str(video),
            "info": str(src),
            "title": info.get("title", ""),
            "duration": info.get("duration"),
            "bvid": info.get("id"),
            "cached": True,
        }

    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--write-info-json",
        "-f",
        f"bv*[height<={max_height}]+ba/b[height<={max_height}]",
        "--merge-output-format",
        "mp4",
        "-o",
        str(outdir / "video.%(ext)s"),
        url,
    ]
    if use_cookie:
        sess = sessdata()
        if sess:
            cmd[1:1] = [
                "--add-headers",
                f"Cookie: SESSDATA={sess}",
                "--add-headers",
                "Referer: https://www.bilibili.com/",
            ]
    subprocess.run(cmd, check=True)
    # yt-dlp 写的是 video.info.json
    produced = outdir / "video.info.json"
    if produced.exists():
        produced.replace(info_path)
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    if not video.exists():
        cands = [p for p in outdir.glob("video.*") if p.suffix in (".mp4", ".mkv", ".webm")]
        if not cands:
            raise RuntimeError("download produced no video file")
        video = cands[0]
    return {
        "video": str(video),
        "info": str(info_path),
        "title": info.get("title", ""),
        "duration": info.get("duration"),
        "bvid": info.get("id"),
        "cached": False,
    }


def is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def bv_id(url: str) -> str | None:
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    return m.group(1) if m else None


def page_of(url: str) -> int | None:
    m = re.search(r"[?&]p=(\d+)", url)
    return int(m.group(1)) if m else None
