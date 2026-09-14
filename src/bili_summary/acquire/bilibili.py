"""S1 采集: B站官方字幕(优先) — 含 wbi 签名."""

from __future__ import annotations

import hashlib
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from ..config import sessdata as _sessdata

API = "https://api.bilibili.com"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_MIXIN_TAB = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
]


def load_sessdata(env_path: str | Path | None = None) -> str | None:
    """B站登录态 SESSDATA（来自 .env 或环境变量）。env_path 参数已废弃。"""
    return _sessdata()


def _client(sessdata: str | None) -> httpx.Client:
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    if sessdata:
        headers["Cookie"] = f"SESSDATA={sessdata}"
    # trust_env=False: 绕开 HTTPS_PROXY(境外出口), B站需直连
    return httpx.Client(headers=headers, timeout=30, follow_redirects=True, trust_env=False)


def _mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in _MIXIN_TAB)[:32]


def _wbi_keys(cli: httpx.Client) -> tuple[str, str]:
    d = cli.get(f"{API}/x/web-interface/nav").json()
    wbi = d.get("data", {}).get("wbi_img", {})
    img = wbi.get("img_url", "").rsplit("/", 1)[-1].split(".")[0]
    sub = wbi.get("sub_url", "").rsplit("/", 1)[-1].split(".")[0]
    return img, sub


def _sign(params: dict[str, Any], img: str, sub: str) -> dict[str, Any]:
    params = dict(params)
    params["wts"] = int(time.time())
    params = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    params = dict(sorted(params.items()))
    query = urllib.parse.urlencode(params)
    params["w_rid"] = hashlib.md5((query + _mixin_key(img + sub)).encode()).hexdigest()
    return params


def video_info(bvid: str, cli: httpx.Client) -> dict:
    r = cli.get(f"{API}/x/web-interface/view", params={"bvid": bvid})
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"view failed: code={d.get('code')} msg={d.get('message')}")
    return d["data"]


def list_subtitles(bvid: str, cid: int, aid: int, cli: httpx.Client, page: int = 0) -> dict:
    """优先 wbi/v2, 失败回退 v2."""
    try:
        img, sub = _wbi_keys(cli)
        p = _sign({"aid": aid, "cid": cid, "bvid": bvid}, img, sub)
        r = cli.get(f"{API}/x/player/wbi/v2", params=p)
        d = r.json()
    except Exception as e:
        d = {"code": -1, "message": f"wbi error {e}"}
    if d.get("code") != 0:
        r = cli.get(f"{API}/x/player/v2", params={"bvid": bvid, "cid": cid})
        d = r.json()
    data = d.get("data", {}) or {}
    return {
        "code": d.get("code"),
        "message": d.get("message"),
        "subtitles": (data.get("subtitle") or {}).get("subtitles") or [],
        "has_subtitle": bool((data.get("subtitle") or {}).get("subtitles")),
    }


def fetch_subtitle_body(url: str, cli: httpx.Client) -> list[dict]:
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    d = cli.get(url).json()
    out = []
    for b in d.get("body", []):
        out.append(
            {
                "start": round(float(b["from"]), 2),
                "end": round(float(b["to"]), 2),
                "text": (b.get("content") or "").strip(),
            }
        )
    return out


def probe(bvid: str, sessdata: str | None = None, p: int | None = None) -> dict:
    """查询某 BV 号(及分P)的字幕可用性, 返回摘要。"""
    sessdata = sessdata or load_sessdata()
    with _client(sessdata) as cli:
        info = video_info(bvid, cli)
        pages = info.get("pages") or []
        result = {
            "bvid": bvid,
            "title": info.get("title"),
            "aid": info.get("aid"),
            "logged_in": bool(sessdata),
            "n_pages": len(pages),
            "pages": [],
        }
        targets = pages if not p else [pages[p - 1]]
        for pg in targets:
            sub = list_subtitles(bvid, pg["cid"], info["aid"], cli, pg.get("page", 0) - 1)
            result["pages"].append(
                {
                    "page": pg.get("page"),
                    "cid": pg["cid"],
                    "part": pg.get("part"),
                    "duration": pg.get("duration"),
                    "code": sub["code"],
                    "message": sub["message"],
                    "subtitles": [
                        {
                            "lan": s.get("lan"),
                            "lan_doc": s.get("lan_doc"),
                            "ai": bool(s.get("ai_status") and s.get("ai_status") == 2),
                            "url": s.get("subtitle_url"),
                        }
                        for s in sub["subtitles"]
                    ],
                }
            )
        return result


def fetch(bvid: str, out_path: str | Path, sessdata: str | None = None, p: int | None = None) -> dict:
    """抓取指定 BV(及分P)的字幕, 写成统一 transcript.json 格式。"""
    sessdata = sessdata or load_sessdata()
    with _client(sessdata) as cli:
        info = video_info(bvid, cli)
        pages = info.get("pages") or []
        targets = pages if not p else [pages[p - 1]]
        segs: list[dict] = []
        meta_pages = []
        for pg in targets:
            sub = list_subtitles(bvid, pg["cid"], info["aid"], cli, pg.get("page", 0) - 1)
            picked = None
            for s in sub["subtitles"]:
                if picked is None:
                    picked = s
                if (s.get("lan") or "").startswith("zh"):
                    picked = s
                    break
            meta_pages.append(
                {
                    "page": pg.get("page"),
                    "cid": pg["cid"],
                    "part": pg.get("part"),
                    "n_subtitles": len(sub["subtitles"]),
                    "picked": picked and picked.get("lan"),
                }
            )
            if not picked:
                continue
            body = fetch_subtitle_body(picked["subtitle_url"], cli)
            for b in body:
                b["source"] = f"bilibili:{picked.get('lan')}"
                segs.append(b)
        payload = {
            "bvid": bvid,
            "title": info.get("title"),
            "aid": info.get("aid"),
            "pages": meta_pages,
            "source": "bilibili_subtitle",
            "segments": segs,
        }
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(
            __import__("json").dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return {"n_segments": len(segs), "pages": meta_pages, "out": str(out_path)}
