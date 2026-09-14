"""S2 分页切分."""

from .detector import classify, detect, dhash, hamming
from .models import Segment, SegmentationResult, SegmentConfig
from .sampler import (
    Metrics,
    encode_metrics,
    load_gray_raw,
    load_metrics,
    metrics_from_gray,
    probe_video,
    sample_metrics,
    static_box,
)

__all__ = [
    "Metrics",
    "Segment",
    "SegmentConfig",
    "SegmentationResult",
    "classify",
    "detect",
    "dhash",
    "encode_metrics",
    "hamming",
    "load_gray_raw",
    "load_metrics",
    "metrics_from_gray",
    "probe_video",
    "sample_metrics",
    "static_box",
]
