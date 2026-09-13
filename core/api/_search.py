"""astrbot_plugin_neteasemusic API —— （自 core/api.py 拆分；公开符号经本包 __init__ 再导出）。"""

from __future__ import annotations

from ._core import _num, request
from ._normalize import _collect, _normalize_playlist, _normalize_song

# ──────────── 搜索 ────────────


async def search(keyword: str, type_: int = 1, limit: int = 10, offset: int = 0, user_key: str = "") -> list:
    body = await request(
        "/cloudsearch", {"keywords": keyword, "type": type_, "limit": limit, "offset": offset}, "get", user_key
    )
    songs = (((body or {}).get("result") or {}).get("songs")) or []
    return _collect(songs, _normalize_song)
async def search_artists(keyword: str, limit: int = 5, user_key: str = "") -> list:
    body = await request("/cloudsearch", {"keywords": keyword, "type": 100, "limit": limit}, "get", user_key)
    artists = (((body or {}).get("result") or {}).get("artists")) or []
    out = []
    for i, a in enumerate(artists):
        if not isinstance(a, dict):
            continue
        out.append(
            {
                "index": i + 1,
                "id": a.get("id") or 0,
                "name": a.get("name") or "",
                "cover": a.get("img1v1Url") or "",
            }
        )
    return out
async def search_albums(keyword: str, limit: int = 5, user_key: str = "") -> list:
    body = await request("/cloudsearch", {"keywords": keyword, "type": 10, "limit": limit}, "get", user_key)
    albums = (((body or {}).get("result") or {}).get("albums")) or []
    out = []
    for i, a in enumerate(albums):
        if not isinstance(a, dict):
            continue
        artist = a.get("artist") if isinstance(a.get("artist"), dict) else {}
        out.append(
            {
                "index": i + 1,
                "id": a.get("id") or 0,
                "name": a.get("name") or "",
                "cover": a.get("picUrl") or "",
                "artist": artist.get("name") or "",
            }
        )
    return out
async def search_playlists(keyword: str, limit: int = 5, user_key: str = "") -> list:
    body = await request("/cloudsearch", {"keywords": keyword, "type": 1000, "limit": limit}, "get", user_key)
    pls = (((body or {}).get("result") or {}).get("playlists")) or []
    return _collect(pls, _normalize_playlist)
async def search_suggest(keyword: str, user_key: str = "") -> list:
    """搜索建议：歌手 / 专辑 / 歌名（按此优先级取前 10 条，去重保序）。

    注：``/search/suggest`` 返回 ``result.{songs,artists,albums,order}``，**没有**
    ``allMatch``；旧实现的主分支因此恒为空，实际只靠 songs 兜底。
    """
    body = await request("/search/suggest", {"keywords": keyword}, "get", user_key)
    result = (body or {}).get("result") or {}
    out: list[str] = []
    for key in ("artists", "albums", "songs"):
        for item in result.get(key) or []:
            if isinstance(item, dict) and item.get("name"):
                out.append(str(item["name"]))
    seen: set[str] = set()
    uniq = [x for x in out if not (x in seen or seen.add(x))]
    return uniq[:10]
async def hot_search(user_key: str = "") -> list:
    body = await request("/search/hot/detail", {}, "get", user_key)
    data = (body or {}).get("data") or []
    out = []
    for i, h in enumerate(data[:15]):
        if not isinstance(h, dict):
            continue
        out.append(
            {
                "index": i + 1,
                "word": h.get("searchWord") or "",
                "score": int(_num(h.get("score"))),
                "content": h.get("content") or "",
            }
        )
    return out
