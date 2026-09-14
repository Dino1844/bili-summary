"""S2 切分：数据结构与阈值配置."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["start", "flip", "switch", "dynamic"]


class SegmentConfig(BaseModel):
    """切分阈值。默认值来自真实课程视频(2h40m)的实测标定。

    三层差分分类(单位: 每秒灰度块平均绝对差):
        mad < t_low            -> 动画 / 鼠标 / 高亮      (同一页)
        t_low <= mad < t_switch-> 幻灯片翻页
        mad >= t_switch        -> 切换应用 / 播放视频 / 换窗口
    """

    fps: float = 1.0
    sample_w: int = 320
    sample_h: int = 180

    # 三层阈值
    t_low: float = 3.0
    t_switch: float = 25.0
    px_thresh: int = 25
    area_min: float = 0.03
    # 彩色笔迹(红笔批注)剔除参数: 逐像素 RGB 差 > px_diff 视为变化;
    # 变化像素中"饱和度增量 > ink_margin"的判为笔迹, 从结构性变化中剔除
    px_diff: float = 20.0
    ink_margin: float = 25.0
    # 差分度量前先做 metric_block x metric_block 块均值:
    # 直接在全分辨率上算会被大量未变化像素稀释, 导致翻页幅度小一个量级
    metric_block: int = 4

    # 边界合并与最短页时长
    min_gap: int = 2
    min_dwell: int = 3

    # 动态内容(演示/滚动/放视频)判定
    dynamic_gap: int = 6
    dynamic_count: int = 8

    # 幻灯片去重(64-bit dhash 的汉明距离)
    dedup_hamming: int = 6
    # 相邻段合并: 两段关键帧缩略图的"像素差异占比" <= 此值 -> 判为同一页的
    # 分步展开/批注, 合并。(dhash 在白底稀疏页上区分度不足, 故不用它)
    merge_page_diff: float = 0.02

    # 可选: 手动 ROI (x, y, w, h), 作用于采样分辨率
    crop: tuple[int, int, int, int] | None = None


class Segment(BaseModel):
    seg_id: int
    t_start: float
    t_end: float
    keyframe_t: float
    kind: Kind
    change_score: float
    change_area: float
    slide_id: int | None = None
    is_revisit: bool = False
    slide_hash: str | None = None
    duration: float = 0.0


class SegmentationResult(BaseModel):
    video: str
    duration: float
    n_frames: int
    fps: float
    config: SegmentConfig
    segments: list[Segment] = Field(default_factory=list)
    roi: tuple[int, int, int, int] | None = None
    stats: dict = Field(default_factory=dict)
