"""astrbot_plugin_neteasemusic API —— （自 core/api.py 拆分；公开符号经本包 __init__ 再导出）。"""

from __future__ import annotations

from ._core import _num

# ──────────── 归一化 ────────────


def _collect(items, normalizer) -> list:
    """把上游列表逐项交给 ``normalizer``（签名 ``(item, index)``），跳过空结果。

    本模块有十几处「遍历列表 → 归一化 → 丢弃 None → 收集」的相同循环，
    统一收敛到这里：逐处手写容易漏掉索引、漏判 None（历史上都出现过）。
    """
    out = []
    for i, item in enumerate(items or []):
        norm = normalizer(item, i)
        if norm:
            out.append(norm)
    return out
def _norm_artists(item: dict) -> str:
    arr = item.get("artists") or item.get("ar") or []
    if isinstance(arr, list):
        names = [a.get("name") or "" for a in arr if isinstance(a, dict)]
        return " / ".join(n for n in names if n)
    singer = item.get("singer") or item.get("singers") or ""
    return str(singer) if singer else ""
def _norm_album(item: dict) -> tuple[str, str]:
    album = item.get("album") or item.get("al") or {}
    if not isinstance(album, dict):
        return "", ""
    return album.get("name") or "", album.get("picUrl") or ""
def _norm_fee(item: dict) -> tuple[int, bool, bool]:
    fee = int(_num(item.get("fee")))
    priv = item.get("privilege") if isinstance(item.get("privilege"), dict) else {}
    if priv.get("fee") is not None:
        fee = int(_num(priv.get("fee")))
    return fee, fee in (1, 4), fee == 8
def _duration_text(dt_ms: float) -> str:
    sec = int(dt_ms / 1000)
    if sec <= 0:
        return ""
    return f"{sec // 60:02d}:{sec % 60:02d}"
def _normalize_song(item: dict, idx: int = 0) -> dict | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name") or item.get("title") or ""
    if not name:
        return None
    album_name, cover = _norm_album(item)
    dt = _num(item.get("dt") or item.get("duration") or 0)
    fee, payplay, trial = _norm_fee(item)
    # MV id：/cloudsearch 与 /song/detail 给的是 ``mv``（0 表示无 MV），
    # 只有少数接口给 ``mvid``；只读 mvid 会恒为 0，导致直连 MV 路径死掉、
    # 每次都退回按歌名搜索（可能搜到同名不同歌的 MV）。
    mvid = item.get("mvid") or item.get("mv") or 0
    return {
        "index": idx + 1,
        "id": item.get("id") or 0,
        "name": name,
        "artist": _norm_artists(item),
        "album": album_name,
        "cover": cover or "",
        "duration": _duration_text(dt),
        "dtMs": int(dt),
        "fee": fee,
        "payplay": payplay,
        "trial": trial,
        "mvid": int(_num(mvid)),
    }
def _normalize_playlist(item: dict, idx: int = 0) -> dict | None:
    if not isinstance(item, dict):
        return None
    name = item.get("name") or ""
    if not name:
        return None
    creator = item.get("creator") if isinstance(item.get("creator"), dict) else {}
    return {
        "index": idx + 1,
        "id": item.get("id"),
        "name": name,
        "cover": item.get("picUrl") or item.get("coverImgUrl") or "",
        "playCount": int(_num(item.get("playCount"))),
        "trackCount": int(_num(item.get("trackCount"))),
        "creator": creator.get("nickname") or "",
    }
