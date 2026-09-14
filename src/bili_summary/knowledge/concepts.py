"""概念体系: 把"逐页知识点"抽成"去重后的概念卡 + 依赖关系"。

这是把产物从「页的集合」升级为「可学习的知识结构」的关键一步：
学生复习时想的是"这个概念是什么、为什么需要它、学它之前要先懂什么"，
而不是"第 12 页讲了什么"。
"""

from __future__ import annotations

import json
from pathlib import Path

from ..describe.m3 import M3
from ..workspace import Workspace

SYSTEM = (
    "你是资深课程讲师兼知识工程师。你擅长把散落在各页的零散知识点，"
    "整理成结构化、有依赖关系的概念体系。你的读者要靠它自学，因此必须准确、不空泛。"
)

PROMPT = """\
下面是《{title}》逐页笔记的标题与要点（共 {n} 页）：

{digest}

请抽取这门课的**概念体系**，输出 JSON：

{{
 "concepts": [
   {{"name": "概念名（中文，统一用词）",
     "en": "English term（没有就留空）",
     "one_line": "一句话定义（不要'是指……'这种同义反复，要说清它是什么）",
     "why": "为什么需要它 / 它解决了什么问题（这是最容易缺失、也最有价值的部分）",
     "prereq": ["学它之前必须先懂的本课概念名", "..."],
     "related": ["相关或容易混淆的本课概念名", "..."],
     "pitfalls": ["常见误解或易错点", "..."],
     "pages": [页号, ...]}}
 ],
 "graph": "一段 Mermaid 依赖图（以 'graph LR' 开头），节点用概念名，箭头表示前置依赖"
}}

规则：
1. **合并同义概念**：同一个东西的不同叫法只保留一条（用最标准的写法）。
2. 概念数量控制在 **8-25 个**，只收"值得单独成卡"的：核心概念、关键机制、重要参数。
   不要收录一次性的细节数值。
3. `prereq` / `related` **只能填本课程里出现过的概念名**（必须与 concepts 里的 name 完全一致）。
   没有前置就留空数组。
4. `why` 必须回答"如果不这样会怎样"，不要写空话。
5. `pitfalls` 尽量多写：优先采集笔记里"存疑/易错/讲者反复强调"的内容。
6. Mermaid 语法必须正确：`graph LR`，节点名不要含括号、逗号、冒号等特殊字符。
"""


def _digest(slides: list[dict], per_page_chars: int = 260) -> str:
    rows = []
    for i, s in enumerate(slides, 1):
        pts = "；".join(str(p) for p in (s.get("key_points") or [])[:4])
        terms = "、".join(str(t.get("term", "")) for t in (s.get("terms") or [])[:4])
        rows.append(f"[页{i}] {s.get('title', '')}\n   要点: {pts[:per_page_chars]}\n   术语: {terms}")
    return "\n".join(rows)


def build_concepts(workdir: str | Path, title: str = "") -> dict:
    ws = Workspace(workdir).ensure()
    slides = json.loads(ws.slides_json.read_text(encoding="utf-8"))
    if not slides:
        raise RuntimeError("slides.json 为空")
    title = title or ws.root.name
    client = M3()
    data = client.complete_json(
        PROMPT.format(title=title, n=len(slides), digest=_digest(slides)), system=SYSTEM, max_tokens=16000
    )
    concepts = data.get("concepts", [])
    graph = str(data.get("graph", "")).strip()
    ws.concepts_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    L = [
        f"# {title} · 概念体系",
        "",
        f"> 从 {len(slides)} 页笔记中抽出 **{len(concepts)} 个核心概念**，包含定义、动机、前置依赖与易错点。",
        "",
    ]
    if graph:
        L += [
            "## 概念依赖图",
            "",
            "```mermaid",
            graph,
            "```",
            "",
            "> 箭头表示「前置依赖」：A → B 读作「学 B 之前需要先懂 A」。",
            "",
        ]
    L += ["## 概念卡", ""]
    for k, c in enumerate(concepts, 1):
        L.append(f"### {k}. {c.get('name', '')}" + (f"（{c['en']}）" if c.get("en") else ""))
        L.append("")
        L.append(f"- **是什么**：{c.get('one_line', '')}")
        if c.get("why"):
            L.append(f"- **为什么需要**：{c['why']}")
        if c.get("prereq"):
            L.append(f"- **前置**：{'、'.join(c['prereq'])}")
        if c.get("related"):
            L.append(f"- **相关/易混**：{'、'.join(c['related'])}")
        if c.get("pitfalls"):
            L.append("- **易错点**：")
            L += [f"  - {p}" for p in c["pitfalls"]]
        if c.get("pages"):
            L.append(f"- **出处**：页 {'、'.join(str(p) for p in c['pages'][:15])}")
        L.append("")
    ws.concepts_md.write_text("\n".join(L), encoding="utf-8")
    print(client.stats(), flush=True)
    return {"concepts": len(concepts), "graph": bool(graph)}
