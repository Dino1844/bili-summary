"""S4 Agent 描述: 把连续素材交给 M3, 同时做"同页合并"与"逐页融合笔记"。

设计要点(区别于所有开源实现):
- 一次调用处理一个窗口(默认 6 段), 让模型在**窗口内有全局视野**, 而不是逐帧孤立描述;
- 由模型判断哪些截图属于同一张幻灯片(分步展开/红笔批注), 输出 seg_ids 分组;
- 强制结构化 JSON 输出; speech_only 专门记录"讲稿有、幻灯片没有"的增量;
- **窗口之间完全独立 → 并行执行**(--jobs);
- 全局去重先用图像相似度找候选对, 再让 M3 看原图确认(比只给标题文本可靠)。
"""

from __future__ import annotations

import contextlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..enrich.materials import Material
from ..workspace import Workspace
from .m3 import M3, parse_json

SYSTEM = (
    "你是资深计算机体系结构讲师，正在为一门中文技术课程视频生成高质量的复习笔记。"
    "你会按时间顺序看到幻灯片截图，以及讲者在该时段的逐字讲稿。"
    "你的产出会被学生用来替代看视频，因此必须准确、完整、且包含幻灯片上没有的口头补充。"
)

INSTRUCTION = """\
下面是第 {i0}–{i1} 段素材（共 {total} 段），按时间顺序给出。

{materials}

请输出 JSON（不要输出任何其他文字）：
{{
 "slides": [
  {{
   "seg_ids": [整数, ...],
   "title": "幻灯片标题（看不清则给概括性标题）",
   "slide_type": "concept|definition|formula|code|diagram|table|example|summary|agenda",
   "key_points": ["幻灯片本身在讲什么"],
   "speech_only": "讲者口头补充、幻灯片上没有的内容（最有价值）",
   "fused": "融合讲解，面向复习，2-5 句",
   "formulas": [{{"latex": "...", "meaning": "..."}}],
   "code": [{{"lang": "...", "snippet": "..."}}],
   "terms": [{{"term": "...", "def": "..."}}],
   "open_questions": ["讲得含糊或看不清的地方"]
  }}
 ]
}}

规则：
1. 只有当画面确实是**同一张幻灯片**时才把多段合并进同一个 seg_ids（例如只增加了红笔批注、或分步展开新增了内容）；画面主体不同必须拆成不同 slide。相邻但不同的页不要合并。
2. `speech_only` 必须填：讲稿里讲了而幻灯片上没有的信息，这正是课程的增量。
3. `title` 尽量**照抄幻灯片上的原文**（英文缩写尤其不要臆造）。
4. 公式用 LaTeX；代码原样保留，不要改写。
5. 不要编造。看不清或没讲清楚的内容写进 open_questions，不要猜。
6. 如果讲稿里出现明显是语音识别错误的术语，在 open_questions 里指出你怀疑的正确术语。
"""

# ------------------------------------------------------------------ 基础工具


def _materials_block(mats: list[Material]) -> str:
    parts = []
    for k, m in enumerate(mats, 1):
        mm, ss = divmod(int(m.t_start), 60)
        txt = (m.text or "").strip() or "（此段没有可用讲稿）"
        parts.append(f"图{k}  seg{m.seg_id}  @{mm:02d}:{ss:02d}  时长{int(m.duration)}s\n讲稿：{txt}")
    return "\n\n".join(parts)


def _parse_with_repair(client: M3, raw: str, max_tokens: int = 12000):
    """解析 JSON; 失败则把坏文本回灌给模型让它重新输出合法 JSON。"""
    try:
        return parse_json(raw)
    except Exception as e:
        print(f"    json parse failed ({e}); asking model to repair", flush=True)
    fix = client.complete(
        "下面这段内容本应是 JSON，但解析失败了。请**只输出修正后的合法 JSON**，"
        "不要输出任何解释、不要用 markdown 代码围栏，保持原有内容不丢：\n\n" + raw[-14000:],
        max_tokens=max_tokens,
    )
    return parse_json(fix)


def _run_window(
    client: M3, wi: int, chunk: list[Material], out_path: Path, body: str, max_tokens: int
) -> dict:
    """处理一个窗口(带一次重试 + JSON 修复)。线程池调用。"""
    t0 = time.time()
    last: Exception | None = None
    for _ in range(2):
        try:
            raw = client.vision([m.image for m in chunk], body, system=SYSTEM, max_tokens=max_tokens)
            d = _parse_with_repair(client, raw, max_tokens=max_tokens)
            d["_window"] = {
                "wi": wi,
                "seg_ids": [m.seg_id for m in chunk],
                "elapsed": round(time.time() - t0, 1),
            }
            out_path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            return d
        except Exception as e:
            last = e
    raise RuntimeError(f"window {wi} failed: {type(last).__name__} {str(last)[:160]}")


def _merge_group(slides: list[dict], idxs: list[int]) -> dict:
    base = dict(slides[idxs[0]])
    segs = set(base.get("seg_ids") or [])
    for i in idxs[1:]:
        s = slides[i]
        segs |= set(s.get("seg_ids") or [])
        for k in ("key_points", "formulas", "code", "terms", "open_questions"):
            a = base.get(k) or []
            base[k] = a + [x for x in (s.get(k) or []) if x not in a]
        for k in ("speech_only", "fused", "title", "slide_type"):
            if len(str(s.get(k, ""))) > len(str(base.get(k, ""))):
                base[k] = s[k]
    base["seg_ids"] = sorted(segs)
    base["_merged_from"] = [slides[i].get("title", "") for i in idxs]
    return base


def _merge_across_windows(slides: list[dict]) -> list[dict]:
    """窗口之间有 overlap, 同一页可能落在相邻窗口 -> 合并。"""
    out: list[dict] = []
    for s in slides:
        ids = set(s.get("seg_ids") or [])
        if out and (ids & set(out[-1].get("seg_ids") or [])):
            hit = out[-1]
            hit["seg_ids"] = sorted(set(hit["seg_ids"]) | ids)
            for k in ("key_points", "formulas", "code", "terms", "open_questions"):
                a, b = hit.get(k) or [], s.get(k) or []
                hit[k] = a + [x for x in b if x not in a]
            for k in ("speech_only", "fused"):
                if len(str(s.get(k, ""))) > len(str(hit.get(k, ""))):
                    hit[k] = s[k]
        else:
            out.append(dict(s))
    return out


# ------------------------------------------------------------------ 全局去重


def _img_signature(path: str | Path, size: tuple[int, int] = (96, 54)):
    import numpy as np
    from PIL import Image

    im = Image.open(path).convert("L").resize(size, Image.BILINEAR)
    return np.asarray(im, dtype=np.int16)


def _img_diff(a, b, px: int = 22) -> float:
    import numpy as np

    return float((np.abs(a - b) > px).mean())


CONFIRM_SYS = (
    "你是课程视频编辑。学生按页复习，因此同一张幻灯片不应出现两次。"
    "请判断给出的每对图片是否**确实是同一张幻灯片**（可能一张有红笔批注/多显示了一行，"
    "也可能是同一页在不同时间被重复讲到）。"
)

CONFIRM_PROMPT = """\
下面是 {n} 对幻灯片截图。请逐对判断它们是否**确实是同一张幻灯片**。

{desc}

输出 JSON：{{"pairs": [{{"i": 序号, "same": true|false, "why": "一句话理由"}}]}}

判断标准：
- same=true 仅当两张图的**主体内容相同**（同样的标题与布局），差别只在于批注、动画步骤、或轻微缩放。
- 只要标题或主要版式不同，就必须 same=false。宁可判 false。
"""


def _confirm_batch(
    client: M3, slides: list[dict], chunk: list[tuple[int, int]], images: dict[int, str]
) -> list[tuple[int, int]]:
    imgs: list[str] = []
    desc = []
    for k, (i, j) in enumerate(chunk, 1):
        desc.append(
            f"对{k}: 页{i + 1}「{slides[i].get('title', '')}」 vs 页{j + 1}「{slides[j].get('title', '')}」"
        )
        imgs += [images[i], images[j]]
    raw = client.vision(
        imgs, CONFIRM_PROMPT.format(n=len(chunk), desc="\n".join(desc)), system=CONFIRM_SYS, max_tokens=2500
    )
    out: list[tuple[int, int]] = []
    try:
        for r in parse_json(raw).get("pairs", []):
            k = int(r.get("i")) - 1
            if r.get("same") and 0 <= k < len(chunk):
                out.append(chunk[k])
    except Exception:
        pass
    return out


def dedup_slides(
    workdir: str | Path,
    slides: list[dict] | None = None,
    sim_threshold: float = 0.08,
    jobs: int = 4,
    client: M3 | None = None,
) -> list[dict]:
    """**基于图像**的全局去重: 图像相似度找候选 -> M3 看原图确认并合并。

    比只给标题文本可靠得多 —— 标题是模型生成的, 同一页可能被起两个名字。
    """
    ws = Workspace(workdir).ensure()
    if slides is None:
        slides = json.loads(ws.slides_json.read_text(encoding="utf-8"))
    mats = {
        m["seg_id"]: str(ws.resolve(m["image"]))
        for m in json.loads(ws.materials_json.read_text(encoding="utf-8"))
    }

    images: dict[int, str] = {}
    for i, s in enumerate(slides):
        for g in s.get("seg_ids") or []:
            if g in mats:
                images[i] = mats[g]
                break
    idx = sorted(images)
    if len(idx) < 3:
        return slides

    sigs = {}
    for i in idx:
        with contextlib.suppress(Exception):
            sigs[i] = _img_signature(images[i])
    cands = [
        (a, b)
        for x, a in enumerate(idx)
        for b in idx[x + 1 :]
        if a in sigs and b in sigs and _img_diff(sigs[a], sigs[b]) <= sim_threshold
    ]
    print(f"  dedup: 图像候选对 {len(cands)} 组", flush=True)
    if not cands:
        return slides

    client = client or M3()
    batches = [cands[i : i + 6] for i in range(0, len(cands), 6)]
    confirmed: list[tuple[int, int]] = []
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = [ex.submit(_confirm_batch, client, slides, b, images) for b in batches]
        for f in as_completed(futs):
            try:
                confirmed += f.result()
            except Exception as e:
                print(f"    confirm batch failed: {type(e).__name__}", flush=True)

    parent = {i: i for i in idx}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in confirmed:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    groups: dict[int, list[int]] = {}
    for i in idx:
        groups.setdefault(find(i), []).append(i)

    out = []
    for root in sorted(groups):
        members = sorted(set(groups[root]))
        out.append(_merge_group(slides, members) if len(members) >= 2 else dict(slides[members[0]]))
    n_merged = sum(1 for m in groups.values() if len(m) >= 2)
    print(f"  dedup: {len(slides)} -> {len(out)} 页 (确认合并 {n_merged} 组)", flush=True)
    return out


# ------------------------------------------------------------------ 主流程


def fuse(
    workdir: str | Path,
    window: int = 6,
    overlap: int = 1,
    max_tokens: int = 12000,
    resume: bool = True,
    limit: int | None = None,
    jobs: int = 4,
) -> list[dict]:
    """逐窗口调用 M3 生成笔记; 窗口之间并行, 完成后做全局去重。"""
    ws = Workspace(workdir).ensure()
    mats = [Material.model_validate(x) for x in json.loads(ws.materials_json.read_text(encoding="utf-8"))]
    for m in mats:  # 记录里是相对路径, 这里解析成绝对路径
        m.image = str(ws.resolve(m.image))
    # 丢弃"没讲稿且极短"的碎片, 减少无效调用
    mats = [m for m in mats if m.n_chars > 0 or m.duration >= 20]
    if limit:
        mats = mats[:limit]

    outdir = ws.fuse_dir
    outdir.mkdir(parents=True, exist_ok=True)
    client = M3()
    step = max(1, window - overlap)

    tasks: list[tuple[int, list[Material], Path, str | None]] = []
    for wi, start in enumerate(range(0, len(mats), step)):
        chunk = mats[start : start + window]
        if len(chunk) < 2:
            continue
        out_path = outdir / f"w{wi:04d}.json"
        if resume and out_path.exists():
            tasks.append((wi, chunk, out_path, None))
        else:
            body = INSTRUCTION.format(
                i0=start + 1, i1=start + len(chunk), total=len(mats), materials=_materials_block(chunk)
            )
            tasks.append((wi, chunk, out_path, body))

    results: dict[int, dict] = {}
    pending = [t for t in tasks if t[3] is not None]
    for wi, _chunk, out_path, body in tasks:
        if body is None:
            results[wi] = json.loads(out_path.read_text(encoding="utf-8"))

    print(f"  windows: {len(tasks)} 总, {len(pending)} 待跑, jobs={jobs}", flush=True)
    failed: list[int] = []
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            futs = {
                ex.submit(_run_window, client, wi, chunk, out_path, body, max_tokens): wi
                for wi, chunk, out_path, body in pending
            }
            for f in as_completed(futs):
                wi = futs[f]
                try:
                    results[wi] = f.result()
                    r = results[wi]
                    print(
                        f"  window {wi + 1}/{len(tasks)} done "
                        f"({r['_window']['elapsed']}s, slides={len(r.get('slides', []))})",
                        flush=True,
                    )
                except Exception as e:
                    failed.append(wi)
                    print(f"  window {wi + 1} 失败: {e}", flush=True)
    if failed:
        print(
            f"  [warn] {len(failed)} 个窗口失败, 这些内容会缺失: {sorted(w + 1 for w in failed)}", flush=True
        )
        print(f"  [warn] 直接重跑 `bili-summary describe --workdir {ws.root}` 会只补这些窗口", flush=True)

    all_slides: list[dict] = []
    for wi in sorted(results):
        all_slides.extend(results[wi].get("slides", []))

    merged = _merge_across_windows(all_slides)
    print("  global dedup ...", flush=True)
    try:
        merged = dedup_slides(ws.root, merged, jobs=jobs, client=client)
    except Exception as e:
        print(f"  global dedup failed: {type(e).__name__} {str(e)[:150]}", flush=True)

    ws.slides_json.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    print(client.stats(), flush=True)
    return merged
