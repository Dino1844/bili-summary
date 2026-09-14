"""完整性校验: 逐页核对"这一页的知识点有没有进讲义"。

定位是**兜底**：讲义是模型重写过的，可能漏掉某页的关键信息。
这里用轻量的文本覆盖判断找出可疑页，交给人工或后续 pass 补。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..workspace import Workspace

_STOP = set(
    "的了和与及是在对为用把被从到中上下个这那一二三四五六七八九十我们你们他们"
    "可以能够需要所以因为如果那么就都还很更最不没有会要能可能这时"
)


def _tokens(text: str) -> set[str]:
    """粗分词：英文按词、中文按 2-gram。"""
    t = str(text).lower()
    en = set(re.findall(r"[a-z][a-z0-9_\.]{2,}", t))
    zh = re.findall(r"[\u4e00-\u9fff]", t)
    grams = {"".join(zh[i : i + 2]) for i in range(len(zh) - 1)}
    return {x for x in (en | grams) if x not in _STOP}


def _ratio(needle: str, hay: set[str]) -> float:
    tk = _tokens(needle)
    if not tk:
        return 1.0
    return len(tk & hay) / len(tk)


def check_coverage(workdir: str | Path, lecture_name: str = "lecture.md", warn_below: float = 0.45) -> dict:
    ws = Workspace(workdir).ensure()
    slides = json.loads(ws.slides_json.read_text(encoding="utf-8"))
    lp = ws.final / lecture_name
    if not lp.exists():
        raise RuntimeError(f"{lecture_name} 不存在")
    lecture = lp.read_text(encoding="utf-8")
    hay = _tokens(lecture)

    weak_pages, rows = [], []
    for i, s in enumerate(slides, 1):
        items = list(s.get("key_points") or [])
        items += [t.get("term", "") for t in (s.get("terms") or [])]
        if not items:
            continue
        scores = [(_ratio(x, hay), x) for x in items if str(x).strip()]
        if not scores:
            continue
        avg = sum(v for v, _ in scores) / len(scores)
        missing = [x for v, x in scores if v < warn_below]
        rows.append({"page": i, "title": s.get("title", ""), "coverage": round(avg, 3), "missing": missing})
        if avg < warn_below or len(missing) >= 2:
            weak_pages.append(i)

    report = {
        "pages": len(slides),
        "checked": len(rows),
        "weak_pages": weak_pages,
        "mean_coverage": round(sum(r["coverage"] for r in rows) / max(len(rows), 1), 3),
        "rows": rows,
    }
    ws.coverage_json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    L = [
        "# 讲义完整性检查",
        "",
        f"- 页面数 {report['pages']}，已核对 {report['checked']}",
        f"- 平均覆盖度 **{report['mean_coverage']:.2f}**（1.0 = 每页要点都出现在讲义里）",
        f"- 可疑页（{len(weak_pages)} 个）：{'、'.join(map(str, weak_pages)) or '无'}",
        "",
        "| 页 | 标题 | 覆盖度 | 可能遗漏 |",
        "| --- | --- | --- | --- |",
    ]
    for r in sorted(rows, key=lambda x: x["coverage"])[:25]:
        miss = "；".join(str(x)[:40] for x in r["missing"][:3]) or ""
        L.append(f"| {r['page']} | {r['title'][:32]} | {r['coverage']:.2f} | {miss} |")
    L.append("")
    (ws.final / "coverage.md").write_text("\n".join(L), encoding="utf-8")
    return report
