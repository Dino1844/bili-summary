"""S1 采集: 下载 / 平台字幕 / 本地 ASR。"""

from .bilibili import fetch, list_subtitles, load_sessdata, probe, video_info

# 注意: 不要在这里绑定名为 `download` 的名字 —— 否则会遮蔽子模块,
# 导致 `from .acquire import download as dl` 拿到函数而不是模块。
from .download import bv_id, is_url, page_of
from .download import download as download_video
from .models import Cue, Transcript, Word, from_bilibili, from_whisper, load_any

__all__ = [
    "Cue",
    "Transcript",
    "Word",
    "bv_id",
    "download_video",
    "fetch",
    "from_bilibili",
    "from_whisper",
    "is_url",
    "list_subtitles",
    "load_any",
    "load_sessdata",
    "page_of",
    "probe",
    "video_info",
]
