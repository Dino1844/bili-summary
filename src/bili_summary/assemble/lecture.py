"""S6.1 讲稿 HTML: 把 lecture.md 渲染成一篇独立的、好看的 HTML 文章,
并把其它已生成的 md(逐页笔记 / 学习目标 / 闪卡 / 测验 / 复习计划 / 思维导图)作为附录收进来。
"""

from __future__ import annotations

from pathlib import Path

from ..workspace import Workspace
from . import html as H
from .report import appendix_items


def build_lecture_html(
    workdir: str | Path, title: str = "", with_appendix: bool = True, math: str = "inline"
) -> dict:
    ws = Workspace(workdir).ensure()
    title = title or ws.root.name
    slides, images, _ = H.load_slides_images(ws)

    if not ws.lecture_md.exists():
        raise RuntimeError("lecture.md 不存在, 先跑 `bili-summary lecture`")
    article = H.md_to_html(ws.lecture_md.read_text(encoding="utf-8"), images, ws)

    lead = f"独立讲义 · 共 {len(slides)} 页幻灯片整理 · 读完即可，不需要再看视频"
    appendix = appendix_items(ws, images, skip={"lecture.md"}) if with_appendix else []
    doc = H.render_shell(title, lead, article, appendix, appendix_title="附：完整资料", math=math)
    ws.lecture_html.write_text(doc, encoding="utf-8")
    return {"html": str(ws.lecture_html), "html_bytes": len(doc.encode()), "appendix": len(appendix)}
