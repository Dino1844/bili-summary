"""MiniMax M3 客户端 (Anthropic 兼容协议, 官方推荐)."""

from __future__ import annotations

import base64
import json
import os
import random
import re
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

# 会被重试的瞬时网络错误(代理掐断 / 连接被重置 / 超时)
_TRANSIENT = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)


def load_env(path: str | Path | None = None) -> None:
    p = Path(path or os.path.expanduser("~/bili_summary/.env"))
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


def parse_json(text: str) -> Any:
    """从模型回复里抠出 JSON（容忍 ```json 围栏与前后废话）。"""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    start = t.find("{")
    end = t.rfind("}")
    if start >= 0 and end > start:
        return json.loads(t[start : end + 1])
    raise ValueError("no JSON found in response")


# ---------------------------------------------------------------- token 用量

_USAGE = {"calls": 0, "in": 0, "out": 0}
_USAGE_LOCK = threading.Lock()
_last_flush = dict(_USAGE)


def usage_snapshot() -> dict:
    with _USAGE_LOCK:
        return dict(_USAGE)


def _usage_add(in_tokens: int, out_tokens: int) -> None:
    with _USAGE_LOCK:
        _USAGE["calls"] += 1
        _USAGE["in"] += int(in_tokens or 0)
        _USAGE["out"] += int(out_tokens or 0)


def flush_usage(workdir: str | Path, stage: str) -> dict:
    """把自上次 flush 以来的用量增量记到 data/usage.json, 按阶段累计。"""
    from ..workspace import Workspace

    with _USAGE_LOCK:
        snap = dict(_USAGE)
    delta = {k: snap[k] - _last_flush[k] for k in snap}
    _last_flush.update(snap)
    ws = Workspace(workdir)
    ws.data.mkdir(parents=True, exist_ok=True)
    path = ws.usage_json
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    s = data.setdefault(stage, {"calls": 0, "in": 0, "out": 0})
    for k in delta:
        s[k] += delta[k]
    data["total"] = {
        k: sum(v.get(k, 0) for kk, v in data.items() if kk != "total") for k in ("calls", "in", "out")
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return delta


def usage_table(workdir: str | Path) -> tuple[str, dict]:
    """返回可打印的用量表 + total。"""
    from ..workspace import Workspace

    path = Workspace(workdir).usage_json
    if not path.exists():
        return "(无用量记录)", {"calls": 0, "in": 0, "out": 0}
    data = json.loads(path.read_text(encoding="utf-8"))
    lines = [f"{'阶段':<12}{'调用':>6}{'输入token':>12}{'输出token':>12}"]
    for k, v in data.items():
        if k == "total":
            continue
        lines.append(f"{k:<12}{v.get('calls', 0):>8}{v.get('in', 0):>12}{v.get('out', 0):>12}")
    t = data.get("total", {})
    lines.append("-" * 42)
    lines.append(f"{'合计':<12}{t.get('calls', 0):>8}{t.get('in', 0):>12}{t.get('out', 0):>12}")
    return "\n".join(lines), t


class M3:
    def __init__(self, env_path: str | Path | None = None, timeout: float = 900.0) -> None:
        load_env(env_path)
        self.key = os.environ["MINIMAX_API_KEY"]
        self.base = os.environ.get("MINIMAX_ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")
        self.model = os.environ.get("MINIMAX_MODEL", "MiniMax-M3")
        self.timeout = timeout
        # MiniMax 是国内端点, 默认直连; 走本机代理(如 127.0.0.1:7891)会在并发时被掐断连接。
        # 需要走代理时设 MINIMAX_USE_PROXY=1。
        self.trust_env = os.environ.get("MINIMAX_USE_PROXY", "0") == "1"
        self.n_calls = 0
        self.in_tokens = 0
        self.out_tokens = 0
        self._lock = threading.Lock()

    def _timeout(self) -> httpx.Timeout:
        # 连接超时设小一些, 避免被掐断时一直挂着
        return httpx.Timeout(connect=20.0, read=self.timeout, write=60.0, pool=30.0)

    def _post(self, body: dict, retries: int = 6) -> dict:
        url = f"{self.base}/v1/messages"
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        last: str = ""
        for attempt in range(retries):
            try:
                # 每次新建 client: 不复用可能已被对端关闭的连接
                with httpx.Client(timeout=self._timeout(), trust_env=self.trust_env) as c:
                    r = c.post(url, json=body, headers=headers)
                if r.status_code == 200:
                    d = r.json()
                    u = d.get("usage", {})
                    with self._lock:
                        self.in_tokens += u.get("input_tokens", 0)
                        self.out_tokens += u.get("output_tokens", 0)
                        self.n_calls += 1
                    _usage_add(u.get("input_tokens", 0), u.get("output_tokens", 0))
                    return d
                # 5xx / 429 也当瞬时错误重试
                if r.status_code >= 500 or r.status_code == 429:
                    last = f"HTTP {r.status_code}"
                    transient = True
                else:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    transient = False
                    print(f"  m3 {last}", flush=True)
            except _TRANSIENT as e:
                last = f"{type(e).__name__}: {e}"
                transient = True
                print(f"  m3 瞬时错误(重试 {attempt + 1}/{retries}): {type(e).__name__}", flush=True)
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                transient = False
                print(f"  m3 {last}", flush=True)
            if not transient:
                break
            time.sleep(min(30.0, (2**attempt) * (0.5 + random.random())))
        raise RuntimeError(f"m3 failed after {retries} attempts: {last}")

    def complete(
        self, text: str, system: str | None = None, max_tokens: int = 8000, thinking: str = "disabled"
    ) -> str:
        body: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": text}],
        }
        if system:
            body["system"] = system
        if thinking:
            body["thinking"] = {"type": thinking}
        d = self._post(body)
        return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")

    def complete_json(
        self, prompt: str, system: str | None = None, max_tokens: int = 12000, attempts: int = 2
    ) -> Any:
        """要求模型输出 JSON, 失败自动回灌修复。"""
        last: Exception | None = None
        for _ in range(attempts):
            raw = self.complete(prompt, system=system, max_tokens=max_tokens)
            try:
                return parse_json(raw)
            except Exception as e:
                last = e
                raw = self.complete(
                    "下面内容本应是 JSON，但解析失败了。请**只输出修正后的合法 JSON**，"
                    "不要任何解释、不要 markdown 围栏，内容不要丢：\n\n" + raw[-14000:],
                    system=system,
                    max_tokens=max_tokens,
                )
                try:
                    return parse_json(raw)
                except Exception as e2:
                    last = e2
        raise ValueError(f"complete_json failed: {last}")

    def vision(
        self,
        images: Sequence[str | Path],
        text: str,
        system: str | None = None,
        max_tokens: int = 16000,
        thinking: str = "disabled",
    ) -> str:
        content: list[dict] = []
        for p in images:
            p = Path(p)
            b64 = base64.b64encode(p.read_bytes()).decode()
            media = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}})
        content.append({"type": "text", "text": text})
        body: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            body["system"] = system
        if thinking:
            body["thinking"] = {"type": thinking}
        d = self._post(body)
        return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")

    def stats(self) -> str:
        return f"m3 calls={self.n_calls} in={self.in_tokens} out={self.out_tokens}"
