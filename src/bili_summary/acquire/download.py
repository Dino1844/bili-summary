"""S1 下载: 用 yt-dlp 抓视频/音频并落 info.json。"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from ..config import sessdata

BILI_RE = re.compile(r"(bilibili\.com|bilivideo\.com)", re.I)


def _write_cookies(path: Path, sessdata_val: str) -> None:
    """写 Netscape 格式 cookies 文件。

    比 `--add-headers "Cookie: ..."` 可靠得多 —— 后者已被 yt-dlp 标记废弃,
    且会按"下载 URL 的域名"做作用域限制, 常常**传不到 CDN 域名**,
    导致 CDN 侧按匿名请求限速/断流。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\t{sessdata_val}\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text).strip("_")
    return s[:n] or "video"


def download(
    url: str,
    outdir: str | Path,
    max_height: int = 720,
    use_cookie: bool = True,
    cookie_file: str | Path | None = None,
) -> dict:
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

    is_bili = bool(BILI_RE.search(url))
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--write-info-json",
        # 网络健壮性: B站 CDN 会中途断流, 分块下载让每次只重试一小块而不是整个文件
        "--retries",
        "20",
        "--fragment-retries",
        "20",
        "--retry-sleep",
        "exp=1:20",
        "--socket-timeout",
        "30",
        "--http-chunk-size",
        "10M",
        "--force-ipv4",
        "-f",
        f"bv*[height<={max_height}]+ba/b[height<={max_height}]",
        "--merge-output-format",
        "mp4",
        "-o",
        str(outdir / "video.%(ext)s"),
        url,
    ]
    if is_bili:
        # B站是国内站点: 走本机代理会多一跳且更容易被掐断
        cmd[1:1] = ["--proxy", ""]
    if use_cookie:
        sess = sessdata()
        if sess:
            ck = Path(cookie_file) if cookie_file else (outdir.parent / ".bili_cookies.txt")
            _write_cookies(ck, sess)
            cmd[1:1] = ["--cookies", str(ck)]

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
