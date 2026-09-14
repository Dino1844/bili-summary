"""S2 核心：三层差分分类 + 稳定段切分 + 幻灯片去重."""

from __future__ import annotations

import numpy as np

from .models import Segment, SegmentationResult, SegmentConfig
from .sampler import Metrics


def dhash(img: np.ndarray, hash_size: int = 8) -> np.ndarray:
    """64-bit difference hash of a 2D image."""
    from PIL import Image

    im = Image.fromarray(img).resize((hash_size + 1, hash_size), Image.LANCZOS)
    a = np.asarray(im, dtype=np.int16)
    return (a[:, 1:] > a[:, :-1]).flatten()


def _bits_to_int(bits: np.ndarray) -> int:
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v


def hamming(a: np.ndarray, b: np.ndarray) -> int:
    return int(np.count_nonzero(a != b))


def classify(metrics: Metrics, cfg: SegmentConfig) -> np.ndarray:
    """0=无, 1=翻页, 2=切应用. 判据以 mad 为主, 结构性 area 为过滤。"""
    mad, area = metrics.mad, metrics.area
    kinds = np.zeros(len(mad), dtype=np.int8)
    flip = (mad >= cfg.t_low) & (mad < cfg.t_switch) & (area >= cfg.area_min)
    kinds[flip] = 1
    kinds[mad >= cfg.t_switch] = 2
    return kinds


def _merge_transitions(
    kinds: np.ndarray, mad: np.ndarray, area: np.ndarray, cfg: SegmentConfig
) -> list[dict]:
    trans: list[dict] = []
    for t in range(len(kinds)):
        if kinds[t] == 0:
            continue
        if trans and t - trans[-1]["t"] <= cfg.min_gap:
            if mad[t] > trans[-1]["score"]:
                trans[-1].update(t=t, kind=int(kinds[t]), score=float(mad[t]), area=float(area[t]))
        else:
            trans.append({"t": t, "kind": int(kinds[t]), "score": float(mad[t]), "area": float(area[t])})
    return trans


def _events(trans: list[dict], cfg: SegmentConfig) -> list[dict]:
    """把密集变化区聚成 dynamic 事件, 其余单点作为 flip/switch 事件."""
    events: list[dict] = []
    i = 0
    while i < len(trans):
        j = i
        while j + 1 < len(trans) and trans[j + 1]["t"] - trans[j]["t"] <= cfg.dynamic_gap:
            j += 1
        cluster = trans[i : j + 1]
        if len(cluster) >= cfg.dynamic_count:
            events.append(
                {
                    "kind": "dynamic",
                    "t0": cluster[0]["t"],
                    "t1": cluster[-1]["t"],
                    "score": max(c["score"] for c in cluster),
                    "area": max(c["area"] for c in cluster),
                }
            )
            i = j + 1
        else:
            for c in cluster:
                events.append(
                    {
                        "kind": "switch" if c["kind"] == 2 else "flip",
                        "t0": c["t"],
                        "t1": c["t"],
                        "score": c["score"],
                        "area": c["area"],
                    }
                )
            i = j + 1
    return events


def _pick_keyframe(metrics: Metrics, t0: int, t1: int) -> int:
    """在段末 40% 内挑"内容最丰富且非黑屏"的稳定帧, 避开淡入淡出/黑屏。"""
    lo = t0 + int((t1 - t0) * 0.6)
    best, best_score = None, -1.0
    for i in range(max(lo, t0), t1):
        f = metrics.lum[i].astype(np.float32)
        if f.mean() < 40:  # 黑屏 / 淡入
            continue
        sc = float(f.std())
        if sc > best_score:
            best_score, best = sc, i
    return best if best is not None else max(t0, t1 - 1)


def page_diff(a: np.ndarray, b: np.ndarray, px: int = 10) -> float:
    """两张缩略图的像素差异占比(0-1)。"""
    return float((np.abs(a.astype(np.int16) - b.astype(np.int16)) > px).mean())


def _merge_adjacent(metrics: Metrics, segments: list[Segment], cfg: SegmentConfig) -> list[Segment]:
    """合并相邻的近似页: 分步展开(build)/批注会造成同一页被反复切开。"""
    out: list[Segment] = []
    for s in segments:
        if out and s.kind == "flip" and out[-1].kind in ("start", "flip"):
            a = metrics.lum[min(int(out[-1].keyframe_t), metrics.n - 1)]
            b = metrics.lum[min(int(s.keyframe_t), metrics.n - 1)]
            if page_diff(a, b) <= cfg.merge_page_diff:
                out[-1].t_end = s.t_end
                out[-1].duration = out[-1].t_end - out[-1].t_start
                out[-1].keyframe_t = s.keyframe_t
                out[-1].change_area = max(out[-1].change_area, s.change_area)
                continue
        out.append(s)
    for i, s in enumerate(out):
        s.seg_id = i
    return out


def detect(metrics: Metrics, cfg: SegmentConfig, video: str = "") -> SegmentationResult:
    kinds = classify(metrics, cfg)
    trans = _merge_transitions(kinds, metrics.mad, metrics.area, cfg)
    events = _events(trans, cfg)
    n = metrics.n

    segments: list[Segment] = []

    def add(t0: int, t1: int, kind: str, score: float, area_v: float) -> None:
        if t1 <= t0:
            return
        key = _pick_keyframe(metrics, t0, t1)  # 段末内容最丰富的稳定帧(避开黑屏/淡入)
        segments.append(
            Segment(
                seg_id=len(segments),
                t_start=float(t0),
                t_end=float(t1),
                keyframe_t=float(key),
                kind=kind,
                change_score=round(score, 2),
                change_area=round(area_v, 4),
                duration=float(t1 - t0),
            )
        )

    cur = 0
    pending_kind, pending_score, pending_area = "start", 0.0, 0.0
    for ev in events:
        if ev["kind"] == "dynamic":
            if ev["t0"] - cur >= cfg.min_dwell:
                add(cur, ev["t0"], pending_kind, pending_score, pending_area)
            add(ev["t0"], ev["t1"] + 1, "dynamic", ev["score"], ev["area"])
            cur = ev["t1"] + 1
            pending_kind, pending_score, pending_area = "flip", 0.0, 0.0
            continue
        if ev["t0"] - cur >= cfg.min_dwell:
            add(cur, ev["t0"], pending_kind, pending_score, pending_area)
            cur = ev["t0"]
            pending_kind, pending_score, pending_area = ev["kind"], ev["score"], ev["area"]
        else:
            # 太短 -> 噪声边界, 合并进当前段; switch 级别提升段类型
            if ev["kind"] == "switch":
                pending_kind = "switch"
            pending_score = max(pending_score, ev["score"])
            pending_area = max(pending_area, ev["area"])
    if n - cur > 0:
        add(cur, n, pending_kind, pending_score, pending_area)

    segments = _merge_adjacent(metrics, segments, cfg)
    _dedup_slides(metrics, segments, cfg)

    kinds_count: dict[str, int] = {}
    for s in segments:
        kinds_count[s.kind] = kinds_count.get(s.kind, 0) + 1
    main = [s for s in segments if s.kind in ("start", "flip")]
    unique = len({s.slide_id for s in main if s.slide_id is not None})
    dur_h = max(n / 3600.0, 1e-9)
    stats = {
        "n_segments": len(segments),
        "n_main_slides": len(main),
        "n_unique_slides": unique,
        "n_revisits": sum(1 for s in main if s.is_revisit),
        "kinds": kinds_count,
        "segments_per_hour": round(len(segments) / dur_h, 1),
        "slides_per_hour": round(len(main) / dur_h, 1),
        "mean_dwell_s": round(float(np.mean([s.duration for s in segments])), 1) if segments else 0.0,
        "total_ink": round(float(metrics.ink.sum()), 1),
    }
    return SegmentationResult(
        video=video,
        duration=float(n),
        n_frames=int(n),
        fps=cfg.fps,
        config=cfg,
        segments=segments,
        stats=stats,
    )


def _dedup_slides(metrics: Metrics, segments: list[Segment], cfg: SegmentConfig) -> None:
    """对 start/flip 段计算 dhash; 页面相同则共享 slide_id 并标记 revisit."""
    reps: list[tuple[np.ndarray, int]] = []
    next_id = 0
    for s in segments:
        if s.kind not in ("start", "flip"):
            continue
        idx = min(round(s.keyframe_t), metrics.n - 1)
        bits = dhash(metrics.lum[idx])
        s.slide_hash = f"{_bits_to_int(bits):016x}"
        hit = None
        for h, sid in reps:
            if hamming(h, bits) <= cfg.dedup_hamming:
                hit = sid
                break
        if hit is None:
            s.slide_id = next_id
            s.is_revisit = False
            reps.append((bits, next_id))
            next_id += 1
        else:
            s.slide_id = hit
            s.is_revisit = True
