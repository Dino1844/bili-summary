"""统一配置: 密钥 / 路径 / CUDA 运行环境。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = Path(os.environ.get("BILI_SUMMARY_ENV", PKG_ROOT / ".env"))
LIBS_DIR = PKG_ROOT / "libs"
_REEXEC_FLAG = "BILI_SUMMARY_CUDA_ENV"


def load_env(path: str | Path | None = None) -> None:
    """读取 .env, 不覆盖已存在的环境变量。"""
    p = Path(path) if path else ENV_FILE
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip())


def ensure_cuda_env() -> None:
    """把 libs/(cuBLAS 12 + cuDNN 9) 加入 LD_LIBRARY_PATH。

    ctranslate2 链接的是 cuBLAS 12, 而系统 CUDA 13 只提供 13, 因此需要自带。
    glibc 只在进程启动时读取 LD_LIBRARY_PATH, 所以必要时重新 exec 自身 ——
    这样用户直接敲 `bili-summary ...` 即可, 不需要任何包装脚本或 module load。
    """
    if os.environ.get(_REEXEC_FLAG) == "1" or not LIBS_DIR.is_dir():
        return
    cur = os.environ.get("LD_LIBRARY_PATH", "")
    if str(LIBS_DIR) in cur.split(":"):
        os.environ[_REEXEC_FLAG] = "1"
        return
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = f"{LIBS_DIR}:{cur}" if cur else str(LIBS_DIR)
    env[_REEXEC_FLAG] = "1"
    os.execvpe(sys.executable, [sys.executable, "-m", "bili_summary", *sys.argv[1:]], env)


def sessdata() -> str | None:
    load_env()
    return os.environ.get("BILI_SESSDATA")


def m3_settings() -> tuple[str, str, str]:
    load_env()
    return (
        os.environ["MINIMAX_API_KEY"],
        os.environ.get("MINIMAX_ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic"),
        os.environ.get("MINIMAX_MODEL", "MiniMax-M3"),
    )
