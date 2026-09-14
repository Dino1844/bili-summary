"""S2 采样：流式解码出切分所需的度量(保留颜色, 用于区分"红笔批注"与"翻页")."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class Metrics:
    """逐秒度量。数组长度 = 帧数(索引即秒)。"""

    lum: np.ndarray  # (T, mh, mw) uint8  —— 块均值亮度, 用于 dhash 去重
    mad: np.ndarray  # (T,) float32 —— 块均值绝对差(强度)
    area: np.ndarray  # (T,) float32 —— **结构性**变化占比(排除彩色笔迹)
    ink: np.ndarray  # (T,) float32 —— 彩色笔迹(批注)变化占比

    @property
    def n(self) -> int:
        return int(self.mad.shape[0])


def probe_video(path: str | Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    d = json.loads(out.stdout)
    st = d["streams"][0]
    return {
        "width": int(st["width"]),
        "height": int(st["height"]),
        "duration": float(d["format"]["duration"]),
        "fps_src": st.get("r_frame_rate", "0/1"),
    }


def _block_mean(a: np.ndarray, k: int) -> np.ndarray:
    """(h,w[,c]) -> 块均值。"""
    h, w = a.shape[:2]
    if k <= 1:
        return a
    if a.ndim == 2:
        return a.reshape(h // k, k, w // k, k).mean(axis=(1, 3), dtype=np.float32)
    return a.reshape(h // k, k, w // k, k, a.shape[2]).mean(axis=(1, 3), dtype=np.float32)


def sample_metrics(
    video: str | Path,
    fps: float = 1.0,
    size: tuple[int, int] = (320, 180),
    crop: tuple[int, int, int, int] | None = None,
    block: int = 4,
    px_diff: float = 20.0,
    ink_margin: float = 25.0,
) -> Metrics:
    """流式解码 RGB, 逐秒计算: 亮度差分 / 结构性变化 / 彩色笔迹变化。

    关键点: 中文课程讲者常用红笔在 PPT 上批注, 批注会持续改变画面。
    批注是"新增彩色像素", 而翻页是"中性色(黑字白底)结构变化", 因此用
    饱和度增量把笔迹从结构性变化中剔除。
    """
    w, h = size
    vf = []
    if crop:
        x, y, cw, ch = crop
        vf.append(f"crop={cw}:{ch}:{x}:{y}")
    vf += [f"fps={fps}", f"scale={w}:{h}", "format=rgb24"]
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(video),
        "-vf",
        ",".join(vf),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    fb = w * h * 3

    thumbs: list[np.ndarray] = []
    mad: list[float] = []
    area: list[float] = []
    ink: list[float] = []

    prev = None
    while True:
        buf = proc.stdout.read(fb)
        if len(buf) < fb:
            break
        fr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
        lum = 0.299 * fr[:, :, 0] + 0.587 * fr[:, :, 1] + 0.114 * fr[:, :, 2]
        sat = fr.max(axis=2).astype(np.int16) - fr.min(axis=2).astype(np.int16)
        thumbs.append(_block_mean(lum.astype(np.float32), block).astype(np.uint8))
        if prev is None:
            mad.append(0.0)
            area.append(0.0)
            ink.append(0.0)
        else:
            pl, ps, pf = prev
            d = np.abs(fr.astype(np.int16) - pf.astype(np.int16)).mean(axis=2)
            changed = d > px_diff
            ink_mask = changed & ((sat - ps) > ink_margin)
            struct = changed & ~ink_mask
            mad.append(float(_block_mean(np.abs(lum - pl), block).mean()))
            area.append(float(_block_mean(struct.astype(np.float32), block).mean()))
            ink.append(float(_block_mean(ink_mask.astype(np.float32), block).mean()))
        prev = (lum, sat, fr)

    err = proc.stderr.read().decode() if proc.stderr else ""
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err[:400]}")
    if not thumbs:
        raise RuntimeError("no frames decoded")
    return Metrics(
        lum=np.stack(thumbs),
        mad=np.asarray(mad, dtype=np.float32),
        area=np.asarray(area, dtype=np.float32),
        ink=np.asarray(ink, dtype=np.float32),
    )


def load_gray_raw(path: str | Path, w: int, h: int, out_size: tuple[int, int]) -> np.ndarray:
    """复用已有 raw gray 文件(如 .probe/frames.gray), 块平均降采样到 out_size。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    n = len(data) // (w * h)
    sig = data[: n * w * h].reshape(n, h, w)
    ow, oh = out_size
    if (w, h) != (ow, oh):
        if w % ow or h % oh:
            raise ValueError(f"cannot block-downsample {w}x{h} -> {ow}x{oh}")
        out = np.empty((n, oh, ow), dtype=np.uint8)
        step = max(1, 2_000_000 // (w * h))
        for i in range(0, n, step):
            blk = sig[i : i + step].reshape(-1, oh, h // oh, ow, w // ow)
            out[i : i + step] = blk.mean(axis=(2, 4), dtype=np.float32).astype(np.uint8)
        sig = out
    return sig


def metrics_from_gray(sig: np.ndarray, block: int = 4, px_thresh: int = 25) -> Metrics:
    """灰度兜底路径: 无颜色信息, 无法剔除笔迹 -> ink 恒为 0。"""
    n = sig.shape[0]
    thumb = np.stack([_block_mean(sig[i].astype(np.float32), block).astype(np.uint8) for i in range(n)])
    mad = np.zeros(n, dtype=np.float32)
    area = np.zeros(n, dtype=np.float32)
    prev = sig[0].astype(np.float32)
    for t in range(1, n):
        cur = sig[t].astype(np.float32)
        d = np.abs(cur - prev)
        mad[t] = float(_block_mean(d, block).mean())
        area[t] = float(_block_mean((d > px_thresh).astype(np.float32), block).mean())
        prev = cur
    return Metrics(lum=thumb, mad=mad, area=area, ink=np.zeros(n, dtype=np.float32))


def _trim_static(active: np.ndarray, max_trim: int) -> tuple[int, int]:
    n = len(active)
    a = 0
    while a < max_trim and a < n and not active[a]:
        a += 1
    b = n
    while (n - b) < max_trim and b > a and not active[b - 1]:
        b -= 1
    return a, b


def static_box(lum: np.ndarray, eps: float = 1.5, max_frac: float = 0.22) -> tuple[int, int, int, int]:
    """只裁掉"整行/整列完全静止"的边框(菜单栏/任务栏/黑边), 绝不裁进有内容变化的区域。

    与"内容包围盒"方案的区别: 后者会把幻灯片本身的空白边裁掉, 造成画面被切。
    这里逐行/逐列判断: 只要该行/列在整段视频里有过变化, 就保留。
    返回 lum 坐标下的 (x, y, w, h)。
    """
    h, w = lum.shape[1:]
    std = lum.astype(np.float32).std(axis=0)  # (h, w)
    col_active = std.max(axis=0) > eps  # (w,)
    row_active = std.max(axis=1) > eps  # (h,)
    x0, x1 = _trim_static(col_active, int(w * max_frac))
    y0, y1 = _trim_static(row_active, int(h * max_frac))
    if x1 - x0 < w * 0.5 or y1 - y0 < h * 0.5:  # 异常结果 -> 不裁
        return (0, 0, w, h)
    return (x0, y0, x1 - x0, y1 - y0)


def encode_metrics(m: Metrics, path: str | Path) -> None:
    """缓存度量, 避免反复解码长视频。"""
    np.savez_compressed(str(path), lum=m.lum, mad=m.mad, area=m.area, ink=m.ink)


def load_metrics(path: str | Path) -> Metrics:
    z = np.load(str(path))
    return Metrics(lum=z["lum"], mad=z["mad"], area=z["area"], ink=z["ink"])


def save_roi_heatmap(lum: np.ndarray, out_path: str | Path) -> None:
    from PIL import Image

    std = lum.astype(np.float32).std(axis=0)
    img = (std / std.max() * 255).astype(np.uint8) if std.max() > 0 else std.astype(np.uint8)
    Image.fromarray(img).resize((img.shape[1] * 4, img.shape[0] * 4), Image.NEAREST).save(out_path)
