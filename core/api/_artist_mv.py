"""astrbot_plugin_neteasemusic API —— （自 core/api.py 拆分；公开符号经本包 __init__ 再导出）。"""

from __future__ import annotations

from ._core import _num, request
from ._normalize import _collect, _duration_text, _normalize_song

# ──────────── 歌手 / 专辑 / MV ────────────


async def artist_top_songs(artist_id, limit: int = 30, user_key: str = "") -> list:
    body = await request("/artist/top/song", {"id": artist_id, "limit": limit}, "get", user_key)
    songs = (body or {}).get("songs") or []
    return _collect(songs, _normalize_song)
async def album_detail(album_id, user_key: str = "") -> tuple[dict | None, list]:
    body = await request("/album", {"id": album_id}, "get", user_key)
    album = (body or {}).get("album") or {}
    album_info = None
    if isinstance(album, dict) and album.get("name"):
        artist = album.get("artist") if isinstance(album.get("artist"), dict) else {}
        album_info = {
            "id": album.get("id") or 0,
            "name": album.get("name") or "",
            "cover": album.get("picUrl") or "",
            "artist": artist.get("name") or "",
            "publishTime": int(_num(album.get("publishTime"))),
        }
    songs = (body or {}).get("songs") or []
    return album_info, _collect(songs, _normalize_song)
# ──────────── MV ────────────


async def search_mv(keyword: str, limit: int = 5, user_key: str = "") -> list:
    body = await request("/cloudsearch", {"keywords": keyword, "type": 1004, "limit": limit}, "get", user_key)
    mvs = (((body or {}).get("result") or {}).get("mvs")) or []
    out = []
    for i, m in enumerate(mvs):
        if not isinstance(m, dict):
            continue
        out.append(
            {
                "index": i + 1,
                "id": m.get("id") or 0,
                "name": m.get("name") or "",
                "artist": m.get("artistName") or "",
                "cover": m.get("cover") or "",
                "duration": _duration_text(_num(m.get("duration"))),
                "playCount": int(_num(m.get("playCount"))),
            }
        )
    return out
async def mv_detail(mvid, user_key: str = "") -> dict:

    body = await request("/mv/detail", {"mvid": mvid}, "get", user_key)
    mp = body.get("data") if isinstance(body.get("data"), dict) else None
    if not mp:
        mp = body.get("mp") if isinstance(body.get("mp"), dict) else None
    if not mp:
        return {}
    return {
        "id": mp.get("id") or mvid,
        "name": mp.get("name") or "",
        "artist": mp.get("artistName") or "",
        "cover": mp.get("cover") or mp.get("imgurl") or "",
        "duration": _duration_text(_num(mp.get("duration"))),
        "playCount": int(_num(mp.get("playCount"))),
        "desc": (mp.get("briefDesc") or mp.get("desc") or "")[:200],
    }
async def mv_url(mvid, r: int = 1080, user_key: str = "") -> str:
    body = await request("/mv/url", {"id": mvid, "r": r}, "get", user_key)
    data = (body or {}).get("data") or {}
    return data.get("url") if isinstance(data, dict) else ""
