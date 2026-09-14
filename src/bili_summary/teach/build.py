"""S6 教学增强: 把"一份笔记"升级成"一套可执行的复习方案"。

借鉴的学习科学方法:
- **主动回忆 (active recall)**: 闪卡优于重读
- **间隔重复 (spaced repetition)**: 1/3/7/16/35 天复习节奏
- **提取练习 (retrieval practice)**: 测验比划线有效
- **费曼技巧 (self-explanation)**: 用一句话讲给外行
- **布鲁姆分类 (Bloom)**: 目标分层, 避免只停留在"记忆"
- **双重编码 (dual coding)**: 思维导图 + 文字
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..describe.m3 import M3
from ..workspace import Workspace

SYSTEM = (
    "你是资深教学设计师，精通学习科学（主动回忆、间隔重复、提取练习、费曼技巧、布鲁姆分类）。"
    "你的产出会直接给学生使用，必须准确、可执行、不空泛。"
)


def _digest(slides: list[dict], max_points: int = 4) -> str:
    lines = []
    for i, s in enumerate(slides, 1):
        pts = "；".join((s.get("key_points") or [])[:max_points])
        terms = "、".join(t.get("term", "") for t in (s.get("terms") or [])[:5])
        lines.append(f"{i}. {s.get('title', '')}\n   要点: {pts}\n   术语: {terms}")
    return "\n".join(lines)


def _objectives_prompt(digest: str, n: int) -> str:
    return f"""\
下面是一门技术课程逐页笔记的摘要（共 {n} 页）：

{digest}

请设计教学材料，输出 JSON：
{{
 "objectives": [
   {{"topic": "主题", "bloom": "remember|understand|apply|analyze|evaluate|create",
     "statement": "以学生为第一人称的可检验学习目标，如'我能解释……'",
     "check": "如何自测是否达成（一句话，可操作）"}}
 ],
 "feynman": [
   {{"concept": "概念名", "prompt": "费曼式自解释提示，如'用一句话向没学过 GPU 的人解释 X'",
     "pitfall": "初学者最容易错/混的地方"}}
 ],
 "prerequisites": ["学这门课前需要的前置知识"],
 "connections": [{{"from": "本课概念A", "to": "本课/外部概念B", "relation": "关系说明"}}],
 "mindmap": "一段 Mermaid mindmap 语法（以 mindmap 开头），概括全课结构"
}}

规则：
- objectives 覆盖全课主干，8-16 条；bloom 分布要合理（不要全是 remember）。
- feynman 选 4-8 个最核心/最易误解的概念。
- connections 至少 5 条，帮助建立知识网络。
- mindmap 必须语法正确（缩进用空格，节点不含括号等特殊字符）。
"""


def _flashcards_prompt(digest: str, n: int) -> str:
    return f"""\
下面是课程逐页笔记摘要（共 {n} 页）：

{digest}

请生成用于**主动回忆**的闪卡，输出 JSON：
{{"cards": [
  {{"front": "正面（一个问题，必须能独立回答，不要'这页讲了什么'这类空问题）",
    "back": "背面（准确、简洁的答案）",
    "hint": "提示（在想不起来时看）",
    "slide": 页号,
    "difficulty": "easy|medium|hard"}}
]}}

规则：
- 数量 20-40 张，覆盖全课；每页至少 1 张。
- 一卡只考一个知识点；正反面都不含"如上页所示"这类依赖上下文的说法。
- 优先考查**原理、因果、易错点**，而不是名词罗列。
"""


def _quiz_prompt(digest: str, n: int) -> str:
    return f"""\
下面是课程逐页笔记摘要（共 {n} 页）：

{digest}

请设计**提取练习**测验，输出 JSON：
{{"questions": [
  {{"type": "mcq|short",
    "question": "题干",
    "options": ["A...", "B...", "C...", "D..."],
    "answer": "mcq 填正确选项原文；short 填参考答案",
    "explanation": "为什么，以及错误选项错在哪",
    "slide": 页号,
    "bloom": "remember|understand|apply|analyze"}}
]}}

规则：
- 12-20 题，mcq 与 short 混合（mcq 至少 8 题）。
- 干扰项要有迷惑性（来自常见误解），不能一眼排除。
- 至少 3 题是 apply/analyze 层次（给一个小场景让学生判断）。
- 每题都要给 explanation。
"""


def _render_pack(d: dict, title: str) -> str:
    L = [f"# {title} · 学习包", ""]
    L.append("## 一、学习目标（按布鲁姆分类）")
    L.append("")
    L.append("| 层级 | 目标 | 自测方法 |")
    L.append("| --- | --- | --- |")
    for o in d.get("objectives", []):
        L.append(f"| {o.get('bloom', '')} | {o.get('statement', '')} | {o.get('check', '')} |")
    L.append("")
    L.append("## 二、费曼式自解释（讲给外行听）")
    L.append("")
    for f in d.get("feynman", []):
        L.append(f"### {f.get('concept', '')}")
        L.append(f"- 提示：{f.get('prompt', '')}")
        L.append(f"- ⚠️ 易错：{f.get('pitfall', '')}")
        L.append("")
    if d.get("prerequisites"):
        L.append("## 三、前置知识")
        L.append("")
        L += [f"- {p}" for p in d["prerequisites"]]
        L.append("")
    if d.get("connections"):
        L.append("## 四、知识关联")
        L.append("")
        L.append("| 从 | 到 | 关系 |")
        L.append("| --- | --- | --- |")
        for c in d["connections"]:
            L.append(f"| {c.get('from', '')} | {c.get('to', '')} | {c.get('relation', '')} |")
        L.append("")
    return "\n".join(L)


def _render_flashcards_md(cards: list[dict]) -> str:
    L = [
        "# 闪卡（主动回忆）",
        "",
        "> 用法：先看正面自答，再看背面。答不出的卡放入「今天必刷」堆，",
        "> 答对的按 `study_plan.md` 的间隔重复节奏复习。",
        "",
    ]
    for i, c in enumerate(cards, 1):
        L.append(f"**{i}. {c.get('front', '')}**")
        L.append("")
        L.append("<details><summary>答案</summary>")
        L.append("")
        L.append(f"{c.get('back', '')}")
        if c.get("hint"):
            L.append("")
            L.append(f"提示：{c['hint']}")
        L.append("")
        L.append("</details>")
        L.append("")
    return "\n".join(L)


def _render_quiz_md(qs: list[dict]) -> str:
    L = ["# 提取练习测验", "", "> 先独立做完，再看文末答案。", ""]
    for i, q in enumerate(qs, 1):
        L.append(f"**{i}. {q.get('question', '')}**  `{q.get('bloom', '')}`")
        L.append("")
        for o in q.get("options") or []:
            L.append(f"- {o}")
        if q.get("options"):
            L.append("")
    L.append("---")
    L.append("")
    L.append("## 答案与解析")
    L.append("")
    for i, q in enumerate(qs, 1):
        L.append(f"**{i}. {q.get('answer', '')}**  ")
        L.append(f"{q.get('explanation', '')}")
        L.append("")
    return "\n".join(L)


STUDY_PLAN = """\
# 复习计划（间隔重复）

基于 Ebbinghaus 遗忘曲线，用"间隔重复 + 主动回忆"把短期记忆转成长期记忆。

| 轮次 | 时间 | 做什么 | 目标 |
| --- | --- | --- | --- |
| 第 0 轮 | 看完课当天 | 读 `notes.md` 一遍，然后**合上**做 `quiz.md` | 暴露盲区 |
| 第 1 轮 | +1 天 | 只刷 `flashcards.md` 中答错的卡 | 修补盲区 |
| 第 2 轮 | +3 天 | 全部闪卡 + 重做测验错题 | 巩固 |
| 第 3 轮 | +7 天 | 看 `mindmap.mmd` 复述全课结构，讲给别人听 | 结构化 |
| 第 4 轮 | +16 天 | 只做 `quiz.md`；错的回闪卡 | 长期化 |
| 第 5 轮 | +35 天 | 快速过一遍 `learning_pack.md` 的学习目标自评 | 保持 |

**每次复习的自检问题**
1. 我能不能不看笔记，用 3 句话讲清这节课在解决什么问题？
2. `learning_pack.md` 里每个学习目标，我能不能举一个自己的例子？
3. 测验做错的题，属于"记忆不牢"还是"理解错了"？后者必须回原页重读。

**为什么这样做**
- 主动回忆（合上书自答）比反复阅读的记忆留存率高得多；
- 间隔重复把复习安排在"即将忘记"的时刻，效率最高；
- 提取练习（测验）会暴露"以为懂了"的错觉；
- 费曼技巧（讲给外行）逼你补上逻辑链里被跳过的一环。
"""


def build_teaching_pack(workdir: str | Path, title: str = "", jobs: int = 3) -> dict:
    wd = Path(workdir)
    ws = Workspace(wd).ensure()
    slides = json.loads(ws.slides_json.read_text(encoding="utf-8"))
    if not slides:
        raise RuntimeError("slides.json 为空, 先跑 describe")
    title = title or wd.name
    digest = _digest(slides)
    client = M3()

    # 三个产出互相独立 -> 并行
    jobs_spec = [
        ("objectives", _objectives_prompt(digest, len(slides)), 12000),
        ("flashcards", _flashcards_prompt(digest, len(slides)), 16000),
        ("quiz", _quiz_prompt(digest, len(slides)), 16000),
    ]
    results: dict[str, dict] = {}
    print(f"  teach: 并行生成 objectives/flashcards/quiz (jobs={jobs}) ...", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        futs = {ex.submit(client.complete_json, prompt, SYSTEM, mt): name for name, prompt, mt in jobs_spec}
        for f in as_completed(futs):
            name = futs[f]
            try:
                results[name] = f.result()
                print(f"    {name} ok", flush=True)
            except Exception as e:
                print(f"    {name} FAILED: {type(e).__name__} {str(e)[:150]}", flush=True)
                results[name] = {}

    d1, d2, d3 = results.get("objectives", {}), results.get("flashcards", {}), results.get("quiz", {})
    ws.learning_pack_md.write_text(_render_pack(d1, title), encoding="utf-8")
    ws.mindmap_mmd.write_text(str(d1.get("mindmap", "")).strip() + "\n", encoding="utf-8")

    cards = d2.get("cards", [])
    ws.flashcards_json.write_text(json.dumps(cards, ensure_ascii=False, indent=1), encoding="utf-8")
    ws.flashcards_md.write_text(_render_flashcards_md(cards), encoding="utf-8")

    qs = d3.get("questions", [])
    ws.quiz_json.write_text(json.dumps(qs, ensure_ascii=False, indent=1), encoding="utf-8")
    ws.quiz_md.write_text(_render_quiz_md(qs), encoding="utf-8")

    ws.study_plan_md.write_text(STUDY_PLAN, encoding="utf-8")

    print(client.stats(), flush=True)
    return {
        "objectives": len(d1.get("objectives", [])),
        "flashcards": len(cards),
        "quiz": len(qs),
        "files": [
            "learning_pack.md",
            "flashcards.md",
            "quiz.md",
            "mindmap.mmd",
            "study_plan.md",
            "flashcards.json",
            "quiz.json",
        ],
    }
