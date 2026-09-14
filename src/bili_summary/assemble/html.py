"""HTML 渲染公共设施: markdown -> 带内嵌图片的 HTML, 以及统一的文档外壳。

report.html 和 lecture.html 都用这里, 避免两份重复实现。
"""

from __future__ import annotations

import base64
import html as _html
import json
import re
from pathlib import Path

from ..config import PKG_ROOT
from ..workspace import Workspace

MATHJAX_JS = PKG_ROOT / "assets" / "mathjax-tex-svg.js"

_MATHJAX_CONFIG = r"""
window.MathJax = {
  tex: {
    /* 只认显式的 \(...\) / \[...\] —— 正文里的裸 $ 会被我们转义成 &#36;, 不参与公式解析 */
    inlineMath: [['\\(','\\)']],
    displayMath: [['\\[','\\]']],
    processEscapes: true
  },
  svg: { fontCache: 'local' },          /* SVG 输出, 不需要字体文件, 适合单文件离线 */
  options: {
    enableMenu: false,
    skipHtmlTags: ['script','noscript','style','textarea','pre','code']
  }
};
"""


def mathjax_block(mode: str = "inline") -> str:
    """生成数学渲染器的 <script> 片段。inline=把 2MB 的 MathJax 内嵌(离线可用)。

    mode: inline | cdn | none
    """
    if mode == "none":
        return ""
    cfg = f"<script>{_MATHJAX_CONFIG}</script>"
    if mode == "cdn":
        return (
            cfg + '<script id="MathJax-script" async '
            'src="https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-svg.js"></script>'
        )
    if MATHJAX_JS.exists():
        return cfg + f"<script>{MATHJAX_JS.read_text(encoding='utf-8')}</script>"
    # 兜底: 本地没有就退化成 CDN
    return (
        cfg + '<script id="MathJax-script" async '
        'src="https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-svg.js"></script>'
    )


# 数学片段保护: 先取出 LaTeX 占位, 走完 markdown 再放回, 避免 \sqrt / \{ / \_ 被转义。
# 之后再把所有"不属于公式"的裸 $ 转成 &#36; —— MathJax 只认 \(...\) / \[...\], 永不误判。
_CODE_SPLIT = re.compile(r"(```.*?```|~~~.*?~~~|`[^`\n]*`)", re.S)


def _protect_math(text: str) -> tuple[str, list[tuple[str, str]]]:
    parts = _CODE_SPLIT.split(text)
    store: list[tuple[str, str]] = []

    def add(kind: str, content: str) -> str:
        store.append((kind, content))
        return f"@@MATH{len(store) - 1}@@"

    for i in range(0, len(parts), 2):  # 偶数下标 = 代码之外
        s = parts[i]
        s = re.sub(r"\$\$(.+?)\$\$", lambda m: add("display", m.group(1)), s, flags=re.S)
        s = re.sub(r"\\\[(.+?)\\\]", lambda m: add("display", m.group(1)), s, flags=re.S)
        s = re.sub(r"(?<!\\)\$(?!\s)([^\n$]+?)(?<!\\)\$", lambda m: add("inline", m.group(1)), s)
        s = re.sub(r"\\\((.+?)\\\)", lambda m: add("inline", m.group(1)), s)
        s = s.replace("$", "&#36;")  # 剩余裸 $ 一律转义, 防止 MathJax 误判
        parts[i] = s
    return "".join(parts), store


def _restore_math(html: str, store: list[tuple[str, str]]) -> str:
    for i, (kind, content) in enumerate(store):
        wrap = (r"\[" + content + r"\]") if kind == "display" else (r"\(" + content + r"\)")
        html = html.replace(f"@@MATH{i}@@", wrap)
    return html


def esc(x) -> str:
    return _html.escape(str(x if x is not None else ""))


def mmss(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m}:{s:02d}"


def data_uri(path: str, ws: Workspace | None = None) -> str:
    p = path[6:] if path.startswith("IMAGE:") else path
    if ws is not None:
        p = str(ws.resolve(p))
    try:
        b = Path(p).read_bytes()
        return "data:image/jpeg;base64," + base64.b64encode(b).decode()
    except Exception:
        return ""


def md_to_html(text: str, images: dict[int, str] | None = None, ws: Workspace | None = None) -> str:
    r"""markdown -> html, 并把 ![](IMAGE:path) / [[SLIDE:n]] 解析成内嵌图片。

    LaTeX 会在送进 markdown 之前被抽出保护, 渲染完再放回,
    否则 \sqrt / \{ / \_ 这类反斜杠会被 markdown 的转义规则吃掉。
    """
    import markdown as mdlib

    images = images or {}

    def _slide(m):
        p = images.get(int(m.group(1)))
        return f"![]({p})" if p else ""

    text = re.sub(r"^[ \t]*\[\[SLIDE:页?(\d+)\]\][ \t]*$", _slide, text, flags=re.M)
    text, math_store = _protect_math(text)
    body = mdlib.markdown(text, extensions=["fenced_code", "tables", "toc", "sane_lists", "md_in_html"])

    def _fix(m):
        src = m.group(2)
        if src.startswith("data:"):
            return m.group(0)
        return f'{m.group(1)}="{data_uri(src, ws)}"'

    body = re.sub(r'(src)="([^"]+)"', _fix, body)
    body = re.sub(r"(src)='([^']+)'", _fix, body)
    return _restore_math(body, math_store)


def mathify(text: str) -> str:
    """把纯文本里的 $...$ / $$...$$ 转成 MathJax 的 \\(...\\) / \\[...\\], 其余裸 $ 转义。

    用于非 markdown 的字段(逐页笔记的要点/讲解/公式等)。
    """
    s = str(text or "")
    s = re.sub(r"\$\$(.+?)\$\$", lambda m: r"\[" + m.group(1) + r"\]", s, flags=re.S)
    s = re.sub(r"(?<!\\)\$(?!\s)([^\n$]+?)(?<!\\)\$", lambda m: r"\(" + m.group(1) + r"\)", s)
    return s.replace("$", "&#36;")


def render_body(s: dict) -> str:
    p = []
    if s.get("key_points"):
        p.append("<ul>" + "".join(f"<li>{mathify(esc(x))}</li>" for x in s["key_points"]) + "</ul>")
    if s.get("speech_only"):
        p.append(f"<p><span class='tag sp'>口头补充</span>{mathify(esc(s['speech_only']))}</p>")
    if s.get("fused"):
        p.append(f"<p class='fused'>{mathify(esc(s['fused']))}</p>")
    for f in s.get("formulas") or []:
        tex = str(f.get("latex", "")).strip()
        p.append(
            f"<p class='formula'><span class='tex'>\\({tex}\\)</span> "
            f"{mathify(esc(f.get('meaning', '')))}</p>"
        )
    for c in s.get("code") or []:
        p.append(f"<pre>{esc(c.get('snippet', ''))}</pre>")
    if s.get("terms"):
        p.append(
            "<dl>"
            + "".join(
                f"<dt>{esc(t.get('term', ''))}</dt><dd>{mathify(esc(t.get('def', '')))}</dd>"
                for t in s["terms"]
            )
            + "</dl>"
        )
    if s.get("open_questions"):
        p.append(
            "<p class='oq'><b>存疑：</b>" + "；".join(mathify(esc(q)) for q in s["open_questions"]) + "</p>"
        )
    return "\n".join(p)


def page_cards(slides: list[dict], images: dict[int, str]) -> str:
    out = []
    for i, s in enumerate(slides, 1):
        img = data_uri(images[i]) if images.get(i) else ""
        tag = f'<img src="{img}"/>' if img else ""
        out.append(f"""
<details class="pagecard">
  <summary><span class="idx">{i}</span> {esc(s.get("title", ""))}
    <span class="meta">{mmss(s.get("t_start", 0))} · {esc(s.get("slide_type", ""))}</span></summary>
  <div class="body">{render_body(s)}</div>{tag}
</details>""")
    return "\n".join(out)


CSS = """
:root{--ink:#1b2130;--mut:#6b7280;--acc:#2563eb;--bg:#f6f7f9;--card:#fff;--line:#e5e7eb}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);
color:var(--ink);line-height:1.9;font-size:16.5px}
.wrap{max-width:880px;margin:0 auto;padding:38px 22px 96px}
h1{font-size:1.8rem;margin:0 0 10px;letter-spacing:.3px}
h2{font-size:1.3rem;margin:40px 0 14px;padding-bottom:9px;border-bottom:2px solid var(--line)}
h3{font-size:1.08rem;margin:22px 0 8px}
p{margin:13px 0}
ul,ol{margin:12px 0 12px 24px} li{margin:5px 0}
code{background:#eef1f6;padding:1px 6px;border-radius:5px;font-size:.9em}
pre{background:#0f172a;color:#e2e8f0;padding:13px 15px;border-radius:10px;overflow-x:auto;
font-size:.84rem;margin:14px 0;line-height:1.65}
pre code{background:none;color:inherit;padding:0}
img{max-width:100%;display:block;margin:18px auto;border:1px solid var(--line);border-radius:10px;
box-shadow:0 1px 3px rgba(0,0,0,.05)}
blockquote{border-left:3px solid var(--acc);background:#f0f6ff;padding:9px 15px;margin:14px 0;color:#334155}
table{border-collapse:collapse;margin:14px 0;width:100%;font-size:.92rem}
th,td{border:1px solid var(--line);padding:7px 11px;text-align:left} th{background:#f2f4f8}
.lead{color:var(--mut);font-size:.9rem;margin-bottom:10px}
main{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:30px 34px}
details.appendix,details.pagecard{background:var(--card);border:1px solid var(--line);
border-radius:10px;margin:10px 0;padding:11px 15px}
details.appendix>summary{font-weight:700;cursor:pointer;font-size:1.02rem}
details.pagecard>summary{cursor:pointer}
.appbody{margin-top:14px;border-top:1px dashed var(--line);padding-top:12px}
.idx{background:var(--acc);color:#fff;border-radius:20px;font-size:.72rem;padding:1px 8px;margin-right:6px}
.meta{color:var(--mut);font-size:.75rem;font-family:ui-monospace,monospace;margin-left:6px}
.tag.sp{background:#fff7ed;color:#c2410c;border-radius:4px;padding:1px 7px;font-size:.72rem;
margin-right:6px;font-weight:700}
.fused{background:#f0f6ff;border-left:3px solid var(--acc);padding:8px 12px;border-radius:0 8px 8px 0}
.oq{color:#b45309;font-size:.88rem}
.formula{background:#eef6ff;border-radius:8px;padding:6px 12px}
.tex{padding:0 3px}
.formula .tex{font-size:1.05em}
dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;font-size:.9rem;margin:8px 0}
dt{font-weight:700}
.mermaid-src{white-space:pre-wrap;font-size:.8rem}
details.pagecard img{max-width:420px}
.tocbox{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 22px;margin:18px 0}
.tocbox ol{margin:6px 0 0 20px}
"""


def render_shell(
    title: str,
    lead: str,
    main_html: str,
    appendix: list[tuple[str, str]] | None = None,
    appendix_title: str = "附录",
    math: str = "inline",
) -> str:
    app = ""
    if appendix:
        app = f'<h2 style="margin-top:44px">{esc(appendix_title)}</h2>' + "\n".join(
            f'<details class="appendix"><summary>{esc(n)}</summary><div class="appbody">{c}</div></details>'
            for n, c in appendix
        )
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>{esc(title)}</h1>
<p class="lead">{esc(lead)}</p>
<main>{main_html}</main>
{app}
</div>
{mathjax_block(math)}
</body></html>"""


def load_slides_images(ws: Workspace):
    """返回 (slides, images{页号->绝对图片路径}, materials 原始 list)。"""
    slides = json.loads(ws.slides_json.read_text(encoding="utf-8"))
    materials = json.loads(ws.materials_json.read_text(encoding="utf-8"))
    mats = {m["seg_id"]: str(ws.resolve(m["image"])) for m in materials}
    tmap = {m["seg_id"]: m["t_start"] for m in materials}
    images: dict[int, str] = {}
    for i, s in enumerate(slides, 1):
        for g in s.get("seg_ids") or []:
            if g in mats:
                images[i] = mats[g]
                break
    for s in slides:
        ts = [tmap[i] for i in (s.get("seg_ids") or []) if i in tmap]
        s["t_start"] = min(ts) if ts else 0.0
    return slides, images, materials
