"""S6.1 讲稿生成: 把逐页笔记重写成一篇连贯的讲义。

为什么需要它:
- 逐页笔记是"按幻灯片切块"的清单, 读起来是碎片化的;
- 更重要的是, 逐页笔记会**忠实保留讲者的过程性内容**(调试设备/切换窗口/调整放映设置/
  打招呼/闲聊), 因为 prompt 只是"如实描述这一页"。
- 一旦要求"写成一节连贯的讲稿", 模型必须自己决定什么值得讲 —— 过程性内容自然被过滤掉。
- 同时把 题目 / 知识点 / 闪卡 融进同一篇文档, 图片按位置引用。
"""

from __future__ import annotations

import contextlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..describe.m3 import M3
from ..workspace import Workspace

OUTLINE_SYS = "你是课程内容架构师，负责把逐页素材组织成有教学逻辑的章节。"

OUTLINE_PROMPT = """\
下面是一门课程的逐页笔记标题（共 {n} 页，编号从 1 开始）：

{listing}

请把它们划分成约 {k} 个逻辑章节，输出 JSON：
{{"sections": [
  {{"title": "有信息量的章节标题（不要'第一部分'这种空标题）",
    "pages": [页号, ...],
    "goal": "这一节要让学生学会什么（一句话）"}}
]}}

规则：
- 页号必须**连续、不重、不漏**地覆盖 1..{n}（按从小到大顺序）。
- 章节边界要落在主题切换处，不要在同一个知识点中间切开。
"""

SCRIPT_SYS = (
    "你是一位优秀的大学讲师兼技术作者。你要把一节课程视频的素材，重写成一篇**可以独立阅读的文章**，"
    "读者从头读到尾就能学会，不需要看视频。你写的是文章，不是笔记、不是要点清单、不是幻灯片说明。"
)

SCRIPT_PROMPT = """\
课程：《{title}》
你要写的这一章：**{stitle}**（教学目的：{goal}）

这一章覆盖的视频素材（仅供你取材，**不要**在文章里提到它的结构）：

{material}

请写出这一章的正文（Markdown 段落，**不要写章节标题**，我会自己加）。

写作要求（很重要，请逐条遵守）：

1. **这是一篇文章**。按视频的脉络推进：先提出要解决的问题，再讲清楚为什么这么做、怎么做、
   有什么代价。段落之间要有承接，读起来是一条线，不是若干条并列的要点。
2. **不要罗列**。凡是能用叙述讲清的内容，不要写成 `- ` 项目符号。项目符号只用于真正并列、
   需要逐条对照的清单（如参数表）。
3. **不要出现任何元叙述**：不要写"幻灯片上""这一页""本页""讲者补充""老师说""如图所示"。
   直接把知识讲出来；讲者讲的内容就是你自己的话。
4. **不要重复**。同一个点只说一次，不要换句话再说一遍。
5. **不要写与课程无关的内容**：调试设备、切换窗口、调整放映设置、共享屏幕、声音测试、
   打招呼闲聊、约定时间、软件故障、自嘲吐槽 —— 全部不要出现在文章里。
6. 讲者在口头表达里补充的**技术性**内容（数值上的修正、经验性提醒、为什么这样做）要**融进叙述**，
   这是课程最有价值的部分，但不许标注它的来源。
7. 公式用 LaTeX（行内 `$...$`，独立 `$$...$$`）；代码用 ``` 围栏并**原样保留**，正文里用一句话说明它在做什么。
8. 插入幻灯片图片：在合适的段落之后**单独一行**写 `[[SLIDE:页号]]`，页号只能取 {allowed}。
   每 2-4 段配一张，让图片出现在对应知识点附近。
9. 篇幅大约 {words} 字。
"""


def _page_material(s: dict, idx: int) -> str:
    """把一页的结构化笔记摊平成"取材原料"。

    故意不标注字段名(如"口头补充"), 避免模型把这些结构词汇抄进文章。
    """
    parts = [f"[页{idx}] {s.get('title', '')}"]
    if s.get("key_points"):
        parts.append("  幻灯片内容: " + "；".join(s["key_points"]))
    for k in ("speech_only", "fused"):
        v = str(s.get(k, "")).strip()
        if v:
            parts.append("  讲到的: " + v[:600])
    if s.get("formulas"):
        parts.append("  公式: " + "；".join(str(f.get("latex", "")) for f in s["formulas"]))
    if s.get("terms"):
        parts.append("  术语: " + "、".join(str(t.get("term", "")) for t in s["terms"]))
    if s.get("code"):
        parts.append("  代码: " + " ".join(str(c.get("snippet", ""))[:200] for c in s["code"]))
    return "\n".join(parts)


def _resolve_images(text: str, page_to_img: dict[int, str]) -> str:
    """把 [[SLIDE:n]] 换成 markdown 图片引用。"""
    import re

    def rep(m):
        n = int(m.group(1))
        img = page_to_img.get(n)
        return f"![](IMAGE:{img})" if img else ""

    return re.sub(r"^[ \t]*\[\[SLIDE:页?(\d+)\]\][ \t]*$", rep, text, flags=re.M)


def _section_script(
    client: M3, title: str, sec: dict, pages: list[int], slides: list[dict], words: int
) -> str:
    material = "\n\n".join(_page_material(slides[p - 1], p) for p in pages)
    prompt = SCRIPT_PROMPT.format(
        title=title,
        stitle=sec.get("title", ""),
        goal=sec.get("goal", ""),
        material=material,
        allowed=f"{min(pages)}-{max(pages)}",
        words=words,
    )
    return client.complete(prompt, system=SCRIPT_SYS, max_tokens=16000)


def build_lecture(
    workdir: str | Path,
    title: str = "",
    jobs: int = 4,
    n_sections: int | None = None,
    words_per_section: int = 900,
) -> dict:
    wd = Path(workdir)
    ws = Workspace(wd).ensure()
    slides = json.loads(ws.slides_json.read_text(encoding="utf-8"))
    if not slides:
        raise RuntimeError("slides.json 为空")
    title = title or wd.name
    mats_path = ws.materials_json
    mats = json.loads(mats_path.read_text(encoding="utf-8")) if mats_path.exists() else []
    seg2img = {m["seg_id"]: str(ws.resolve(m["image"])) for m in mats}

    page_to_img: dict[int, str] = {}
    for i, s in enumerate(slides, 1):
        for g in s.get("seg_ids") or []:
            if g in seg2img:
                page_to_img[i] = seg2img[g]
                break

    client = M3()
    n = len(slides)
    k = n_sections or max(3, min(8, round(n / 5)))

    listing = "\n".join(f"{i}. {s.get('title', '')}" for i, s in enumerate(slides, 1))
    outline = client.complete_json(
        OUTLINE_PROMPT.format(n=n, k=k, listing=listing), system=OUTLINE_SYS, max_tokens=4000
    )
    sections = outline.get("sections") or [{"title": title, "pages": list(range(1, n + 1)), "goal": ""}]
    # 容错: 补齐/去重页号
    seen, fixed = set(), []
    for sec in sections:
        pages = [p for p in sec.get("pages", []) if isinstance(p, int) and 1 <= p <= n and p not in seen]
        if not pages:
            continue
        seen |= set(pages)
        fixed.append({**sec, "pages": sorted(pages)})
    miss = [p for p in range(1, n + 1) if p not in seen]
    if miss:
        fixed.append({"title": "其它内容", "pages": miss, "goal": ""})
    sections = fixed or [{"title": title, "pages": list(range(1, n + 1)), "goal": ""}]
    print(f"  lecture: {n} 页 -> {len(sections)} 章", flush=True)

    # 并行写各章讲稿
    scripts: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = {
            ex.submit(_section_script, client, title, sec, sec["pages"], slides, words_per_section): i
            for i, sec in enumerate(sections)
        }
        for f in as_completed(futs):
            i = futs[f]
            try:
                scripts[i] = f.result()
                print(f"    章节 {i + 1}/{len(sections)} ok", flush=True)
            except Exception as e:
                print(f"    章节 {i + 1} FAILED: {type(e).__name__} {str(e)[:150]}", flush=True)

    # 闪卡按章节归位
    cards_by_page: dict[int, list[dict]] = {}
    fpath = ws.flashcards_json
    if fpath.exists():
        for c in json.loads(fpath.read_text(encoding="utf-8")):
            with contextlib.suppress(Exception):
                cards_by_page.setdefault(int(c.get("slide", 0)), []).append(c)

    # ---- 组装成一篇独立文章: 正文纯净, 自测放文末 ----

    def clean(body: str) -> str:
        """去掉模型自作主张加的标题，保持文章是连续正文。"""
        lines = []
        for ln in body.strip().splitlines():
            if re.match(r"^#{1,6}\s", ln):  # 正文里不允许出现标题
                continue
            lines.append(ln)
        return "\n".join(lines).strip()

    L = [
        f"# {title}",
        "",
        "> 这是一篇可以独立阅读的讲义：按视频脉络完整讲清这门课的内容，读完不需要再看视频。",
        "",
    ]
    toc = []
    for i, sec in enumerate(sections):
        toc.append(f"{i + 1}. {sec.get('title', '')}")
    L += ["**目录**", ""] + [f"- {t}" for t in toc] + ["", "---", ""]

    for i, sec in enumerate(sections):
        L.append(f"## {i + 1}. {sec.get('title', '')}")
        L.append("")
        body = clean(_resolve_images(scripts.get(i, "（本章生成失败）"), page_to_img))
        L.append(body)
        L.append("")

    # 文末统一放自测(闪卡), 不打断正文的连贯性
    if cards_by_page:
        L += [
            "---",
            "",
            "# 附：复习自测（闪卡）",
            "",
            "> 读完正文后合上讲义自答；答不出的按 `study_plan.md` 的间隔重复节奏复习。",
            "",
        ]
        for i, sec in enumerate(sections):
            cards = [c for p in sec["pages"] for c in cards_by_page.get(p, [])]
            if not cards:
                continue
            L.append(f"**第 {i + 1} 章 · {sec.get('title', '')}**")
            L.append("")
            for c in cards:
                L.append(f"- **{c.get('front', '')}**")
                L.append(f"  <br>　答：{c.get('back', '')}")
            L.append("")

    lecture = "\n".join(L)
    ws.lecture_md.write_text(lecture, encoding="utf-8")
    print(client.stats(), flush=True)
    return {"sections": len(sections), "chars": len(lecture), "file": str(ws.lecture_md)}
