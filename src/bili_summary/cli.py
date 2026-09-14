"""bili-summary CLI 入口。

典型用法（粘贴链接即可）：
    bili-summary run https://www.bilibili.com/video/BV1g83w6DETm
"""

from __future__ import annotations

import typer

from . import pipeline
from .acquire.cli import app as acquire_app
from .config import ensure_cuda_env
from .segment.cli import app as segment_app

app = typer.Typer(
    help="课程视频 -> 按 PPT 切分 -> 逐页融合笔记 + 复习包",
    no_args_is_help=True,
)
app.add_typer(acquire_app, name="acquire", help="S1: 视频下载 / 字幕 / ASR")
app.add_typer(segment_app, name="segment", help="S2: 按 PPT 翻页切分")

# 全流程与各阶段命令直接挂在顶层
app.command("run", help="⭐ 粘贴视频链接, 全流程自动输出笔记与复习包")(pipeline.run)
app.command("enrich", help="S3: 装配每段素材")(pipeline.enrich)
app.command("describe", help="S4: M3 逐页笔记 + 全局去重")(pipeline.describe)
app.command("knowledge", help="知识层: 术语表 + 概念体系 + 完整性检查")(pipeline.knowledge)
app.command("dedup", help="按图像对已有 slides.json 做全局去重(M3 确认)")(pipeline.dedup)
app.command("report", help="S5: 生成 notes.md / report.html")(pipeline.report)
app.command("teach", help="S6: 生成教学增强包(闪卡/测验/思维导图/复习计划)")(pipeline.teach)
app.command("lecture", help="S6.1: 把逐页笔记重写成连贯讲义(自动滤掉过程性噪音)")(pipeline.lecture)
app.command("render", help="不调用 M3: 从已有产物重新渲染 HTML(改样式/修公式用)")(pipeline.render)
app.command("usage", help="查看该视频的 M3 token 用量")(pipeline.usage)
app.command("sheet", help="导出幻灯片总览图(人工验收)")(pipeline.sheet)


def main() -> None:
    ensure_cuda_env()  # 必要时带上 cuBLAS12/cuDNN9; 用户无需任何包装脚本
    app()


if __name__ == "__main__":
    main()
