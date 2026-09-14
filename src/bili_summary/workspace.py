"""工作目录布局：把「最终成果」和「中间产物」彻底分开。

    work/<视频ID>/
    ├── README.md     自动生成的索引, 说明每个文件是什么
    ├── final/        ⭐ 最终交付物(人看的成品)
    ├── data/         结构化 JSON(机器可读, 也是各阶段的接口)
    ├── cache/        中间产物(可删除后重算)
    └── source/       原始媒体(下载的视频/音频/元数据)

所有路径都只在这里定义一次, 其它模块不许再手拼路径。
"""

from __future__ import annotations

import shutil
from pathlib import Path

# 旧版平铺布局 -> 新布局(自动迁移, 幂等)
_MIGRATE = {
    "transcript.json": "data",
    "segments.json": "data",
    "materials.json": "data",
    "slides.json": "data",
    "flashcards.json": "data",
    "quiz.json": "data",
    "usage.json": "data",
    "metrics.npz": "cache",
    "keyframes": "cache",
    "materials": "cache",
    "fuse": "cache",
    "cuts": "cache",
    "roi_heatmap.png": "cache",
    "slides": "cache",
    "video.mp4": "source",
    "video.mkv": "source",
    "video.webm": "source",
    "info.json": "source",
    "video.info.json": "source",
    "meta.json": "source",
    "audio.wav": "source",
    "audio.m4a": "source",
    "transcript.wav": "source",
    # 早期一次性脚本留下的产物
    "frames": "cache",
    "runs": "cache",
    "whisper_small": "cache",
    "audio_segments.json": "cache",
    "audio_transcript.txt": "cache",
    "notes.md": "final",
    "lecture.md": "final",
    "learning_pack.md": "final",
    "flashcards.md": "final",
    "quiz.md": "final",
    "mindmap.mmd": "final",
    "study_plan.md": "final",
    "report.html": "final",
    "lecture.html": "final",
}


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    # ---------------------------------------------------------- 目录
    @property
    def source(self) -> Path:
        return self.root / "source"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def final(self) -> Path:
        return self.root / "final"

    def ensure(self) -> Workspace:
        self.migrate()
        for d in (self.source, self.data, self.cache, self.final):
            d.mkdir(parents=True, exist_ok=True)
        return self

    # ---------------------------------------------------------- 输入媒体
    @property
    def info_json(self) -> Path:
        return self.source / "info.json"

    @property
    def audio(self) -> Path:
        return self.source / "audio.wav"

    def video(self) -> Path | None:
        for n in ("video.mp4", "video.mkv", "video.webm"):
            p = self.source / n
            if p.exists():
                return p
        return None

    # ---------------------------------------------------------- data/
    @property
    def transcript_json(self) -> Path:
        return self.data / "transcript.json"

    @property
    def segments_json(self) -> Path:
        return self.data / "segments.json"

    @property
    def materials_json(self) -> Path:
        return self.data / "materials.json"

    @property
    def slides_json(self) -> Path:
        return self.data / "slides.json"

    @property
    def flashcards_json(self) -> Path:
        return self.data / "flashcards.json"

    @property
    def quiz_json(self) -> Path:
        return self.data / "quiz.json"

    @property
    def usage_json(self) -> Path:
        return self.data / "usage.json"

    # ---------------------------------------------------------- cache/
    @property
    def metrics(self) -> Path:
        return self.cache / "metrics.npz"

    @property
    def keyframes(self) -> Path:
        return self.cache / "keyframes"

    @property
    def materials_dir(self) -> Path:
        return self.cache / "materials"

    @property
    def fuse_dir(self) -> Path:
        return self.cache / "fuse"

    @property
    def cuts_dir(self) -> Path:
        return self.cache / "cuts"

    @property
    def roi_heatmap(self) -> Path:
        return self.cache / "roi_heatmap.png"

    # ---------------------------------------------------------- final/
    @property
    def report_html(self) -> Path:
        return self.final / "report.html"

    @property
    def notes_md(self) -> Path:
        return self.final / "notes.md"

    @property
    def lecture_md(self) -> Path:
        return self.final / "lecture.md"

    @property
    def lecture_html(self) -> Path:
        return self.final / "lecture.html"

    @property
    def learning_pack_md(self) -> Path:
        return self.final / "learning_pack.md"

    @property
    def flashcards_md(self) -> Path:
        return self.final / "flashcards.md"

    @property
    def quiz_md(self) -> Path:
        return self.final / "quiz.md"

    @property
    def mindmap_mmd(self) -> Path:
        return self.final / "mindmap.mmd"

    @property
    def study_plan_md(self) -> Path:
        return self.final / "study_plan.md"

    # ---------------------------------------------------------- 路径解析
    def resolve(self, p: str | Path) -> Path:
        """把(可能是相对/可能是旧绝对路径的)记录解析成当前存在的绝对路径。

        兜底策略: 依次尝试 root/<原样>、root/<末尾 k 段>、以及按文件名全目录查找,
        这样即使工作目录被迁移/改名, 旧记录仍然能解析到。
        """
        p = Path(p)
        if p.is_absolute() and p.exists():
            return p
        cand = self.root / p
        if cand.exists():
            return cand
        parts = p.parts
        for k in range(1, min(6, len(parts)) + 1):
            c = self.root.joinpath(*parts[-k:])
            if c.exists():
                return c
        try:  # 最后按文件名兜底
            return next(self.root.rglob(p.name))
        except StopIteration:
            return cand

    def rel(self, p: str | Path) -> str:
        """记录成相对工作目录的路径(可搬迁)。"""
        try:
            return Path(p).resolve().relative_to(self.root.resolve()).as_posix()
        except Exception:
            return str(p)

    # ---------------------------------------------------------- 迁移 / 索引
    def migrate(self) -> list[str]:
        """把旧版平铺布局搬到新布局(幂等)。返回搬动的条目。"""
        moved = []
        if not self.root.exists():
            return moved
        for name, sub in _MIGRATE.items():
            src = self.root / name
            if not src.exists():
                continue
            dst = self.root / sub / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                # 目标已存在: 源若是目录就合并, 是文件就丢弃旧的
                if src.is_dir() and dst.is_dir():
                    for item in src.iterdir():
                        tgt = dst / item.name
                        if not tgt.exists():
                            shutil.move(str(item), str(tgt))
                    shutil.rmtree(src, ignore_errors=True)
                else:
                    src.unlink()
            else:
                shutil.move(str(src), str(dst))
            moved.append(f"{name} -> {sub}/")
        # 通配的核验图
        for pat, sub in (("sheet_*.png", "cache"),):
            for p in self.root.glob(pat):
                tgt = self.root / sub / p.name
                tgt.parent.mkdir(parents=True, exist_ok=True)
                if not tgt.exists():
                    shutil.move(str(p), str(tgt))
                    moved.append(f"{p.name} -> {sub}/")
        return moved

    def write_index(self, title: str = "") -> Path:
        """生成 README.md, 说明每个文件是什么。"""

        def rows(d: Path, desc: dict[str, str]) -> list[str]:
            if not d.exists():
                return []
            out = []
            for p in sorted(d.iterdir()):
                if p.name.startswith("."):
                    continue
                size = ""
                if p.is_file():
                    n = p.stat().st_size
                    size = f"{n / 1e6:.1f} MB" if n > 1e6 else f"{n / 1e3:.0f} KB"
                else:
                    size = f"{sum(1 for _ in p.rglob('*') if _.is_file())} 个文件"
                out.append(f"| `{p.name}` | {size} | {desc.get(p.name, '')} |")
            return out

        L = [
            f"# {title or self.root.name}",
            "",
            "> 本目录由 bili-summary 自动生成。**只需要看 `final/`**，其余可删可重算。",
            "",
            "## ⭐ final/ — 最终成果",
            "",
            "| 文件 | 大小 | 说明 |",
            "| --- | --- | --- |",
            "| `report.html` | | 结构化报告：逐页笔记（含幻灯片图） |",
            "| `lecture.html` | | **⭐ 独立讲义**：连贯文章 + 图片 + 全部资料附录，双击即开 |",
            "| `lecture.md` | | 讲义 Markdown 源（独立、完整、清晰的讲解） |",
            "| `notes.md` | | 逐页笔记（结构化，便于检索与引用） |",
            "| `learning_pack.md` | | 学习目标（布鲁姆分层）+ 费曼自解释提示 + 知识关联 |",
            "| `flashcards.md` | | 闪卡（主动回忆） |",
            "| `quiz.md` | | 提取练习测验（含答案与解析） |",
            "| `mindmap.mmd` | | 思维导图（Mermaid 源码） |",
            "| `study_plan.md` | | 间隔重复复习计划 |",
            "",
            "## data/ — 结构化数据（机器可读）",
            "",
            "| 文件 | 大小 | 说明 |",
            "| --- | --- | --- |",
        ]
        L += rows(
            self.data,
            {
                "transcript.json": "统一转录（平台字幕或 ASR，含词级时间戳）",
                "segments.json": "S2 切分结果（段、幻灯片去重、裁剪框）",
                "materials.json": "S3 每段素材（图片路径 + 对齐后的讲稿）",
                "slides.json": "S4 M3 输出（每页结构化笔记）",
                "flashcards.json": "闪卡数据",
                "quiz.json": "测验数据",
                "usage.json": "M3 token 用量统计",
            },
        )
        L += [
            "",
            "## cache/ — 中间产物（可删除，重跑会重建）",
            "",
            "| 文件 | 大小 | 说明 |",
            "| --- | --- | --- |",
        ]
        L += rows(
            self.cache,
            {
                "metrics.npz": "S2 采样度量缓存（避免重复解码视频）",
                "keyframes": "每个段的关键帧（未裁剪原图）",
                "materials": "送入 M3 的素材图",
                "fuse": "S4 每个窗口的原始 JSON（断点续跑用）",
                "cuts": "切点对照图（人工核验用）",
                "roi_heatmap.png": "ROI/静止边检测热力图",
            },
        )
        L += ["", "## source/ — 原始媒体", "", "| 文件 | 大小 | 说明 |", "| --- | --- | --- |"]
        L += rows(
            self.source,
            {
                "video.mp4": "下载的课程视频",
                "info.json": "yt-dlp 元数据",
                "audio.wav": "抽取的音频（ASR 输入）",
            },
        )
        L += [
            "",
            "---",
            "",
            "重新生成某个阶段：`bili-summary <stage> --workdir <本目录>`，",
            "或整条重跑：`bili-summary run <链接> --workdir <本目录> --from <阶段>`。",
            "",
        ]
        p = self.root / "README.md"
        p.write_text("\n".join(L), encoding="utf-8")
        return p
