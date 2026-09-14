"""S6 报告: notes.md + report.html(结构化参考: 逐页笔记 + 附录)。"""

from __future__ import annotations

from pathlib import Path

from ..workspace import Workspace
from . import html as H


def md_from_slides(slides: list[dict], title: str, video_url: str = "") -> str:
    L = [f"# {title}", ""]
    if video_url:
        L.append(f"原视频：{video_url}")
        L.append("")
    L.append("---")
    L.append("")
    for i, s in enumerate(slides, 1):
        mm, ss = divmod(int(s.get("t_start", 0)), 60)
        L.append(f"## {i}. {s.get('title', '(无标题)')}")
        L.append("")
        L.append(f"`[{mm:02d}:{ss:02d}]` · `{s.get('slide_type', '')}` · 段落 {s.get('seg_ids')}")
        L.append("")
        if s.get("key_points"):
            L.append("**幻灯片要点**")
            L += [f"- {p}" for p in s["key_points"]]
            L.append("")
        if s.get("speech_only"):
            L.append(f"**口头补充**：{s['speech_only']}")
            L.append("")
        if s.get("fused"):
            L.append(f"**讲解**：{s['fused']}")
            L.append("")
        for f in s.get("formulas") or []:
            L.append(f"- 公式 `{f.get('latex', '')}` — {f.get('meaning', '')}")
        if s.get("formulas"):
            L.append("")
        for c in s.get("code") or []:
            L.append(f"```{c.get('lang', '')}")
            L.append(c.get("snippet", ""))
            L.append("```")
            L.append("")
        if s.get("terms"):
            L.append("**术语**")
            L += [f"- **{t.get('term', '')}**：{t.get('def', '')}" for t in s["terms"]]
            L.append("")
        if s.get("open_questions"):
            L.append("**存疑**")
            L += [f"- {q}" for q in s["open_questions"]]
            L.append("")
        L.append("---")
        L.append("")
    return "\n".join(L)


def appendix_items(
    ws: Workspace, images: dict[int, str], skip: set[str] | None = None
) -> list[tuple[str, str]]:
    """把 final/ 下的 md 汇总成附录(不含 skip)。"""
    skip = skip or set()
    out: list[tuple[str, str]] = []
    for name, fn in (
        ("逐页笔记（结构化）", "notes.md"),
        ("概念体系（概念卡 + 依赖图）", "concepts.md"),
        ("学习目标与费曼提示", "learning_pack.md"),
        ("闪卡（主动回忆）", "flashcards.md"),
        ("测验（提取练习）", "quiz.md"),
        ("复习计划", "study_plan.md"),
        ("讲义完整性检查", "coverage.md"),
    ):
        if fn in skip:
            continue
        p = ws.final / fn
        if p.exists():
            out.append((name, H.md_to_html(p.read_text(encoding="utf-8"), images, ws)))
    mm = ws.mindmap_mmd
    if mm.exists() and "mindmap.mmd" not in skip:
        out.append(
            (
                "思维导图（Mermaid 源码）",
                f"<pre class='mermaid-src'>{H.esc(mm.read_text(encoding='utf-8'))}</pre>",
            )
        )
    return out


def build_report(workdir: str | Path, title: str = "", video_url: str = "", math: str = "inline") -> dict:
    ws = Workspace(workdir).ensure()
    slides, images, _ = H.load_slides_images(ws)
    title = title or ws.root.name
    ws.notes_md.write_text(md_from_slides(slides, title, video_url), encoding="utf-8")

    lead = f"{video_url} · {len(slides)} 页幻灯片 · 逐页结构化笔记（讲义见 lecture.html）"
    doc = H.render_shell(
        title, lead, H.page_cards(slides, images), appendix_items(ws, images, skip={"notes.md"}), math=math
    )
    ws.report_html.write_text(doc, encoding="utf-8")
    return {
        "slides": len(slides),
        "notes": str(ws.notes_md),
        "html": str(ws.report_html),
        "html_bytes": len(doc.encode()),
        "main": "逐页笔记",
    }
