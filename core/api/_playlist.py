"""astrbot_plugin_neteasemusic API —— （自 core/api.py 拆分；公开符号经本包 __init__ 再导出）。"""

from __future__ import annotations

from ._core import _num, request
from ._normalize import _collect, _duration_text, _normalize_playlist, _normalize_song

# ──────────── 歌单 / 榜单 ────────────


async def playlist_detail(playlist_id, user_key: str = "") -> dict | None:
    body = await request("/playlist/detail", {"id": playlist_id}, "get", user_key)
    pl = (body or {}).get("playlist") or {}
    if not isinstance(pl, dict) or not pl.get("name"):
        return None
    creator = pl.get("creator") if isinstance(pl.get("creator"), dict) else {}
    return {
        "id": pl.get("id") or playlist_id,
        "name": pl.get("name") or "",
        "cover": pl.get("coverImgUrl") or pl.get("picUrl") or "",
        "playCount": int(_num(pl.get("playCount"))),
        "trackCount": int(_num(pl.get("trackCount"))),
        "creator": creator.get("nickname") or "",
    }
async def playlist_tracks(playlist_id, limit: int = 1000, user_key: str = "") -> list:
    body = await request("/playlist/track/all", {"id": playlist_id, "limit": limit, "offset": 0}, "get", user_key)
    songs = (body or {}).get("songs") or []
    return _collect(songs, _normalize_song)
async def toplist(user_key: str = "") -> list:
    body = await request("/toplist", {}, "get", user_key)
    lst = (body or {}).get("list") or []
    out = []
    for i, t in enumerate(lst):
        if not isinstance(t, dict):
            continue
        out.append(
            {
                "index": i + 1,
                "id": t.get("id"),
                "name": t.get("name") or "",
                "updateFrequency": t.get("updateFrequency") or "",
                "cover": t.get("coverImgUrl") or "",
            }
        )
    return out
async def top_detail(chart_id, limit: int = 60, user_key: str = "") -> list:
    # 旧 /top/list 已废弃（v3.34.0 后不支持 idx）；榜单本质是歌单，用 track/all 拉全量
    body = await request("/playlist/track/all", {"id": chart_id, "limit": limit, "offset": 0}, "get", user_key)
    songs = (body or {}).get("songs") or []
    return _collect(songs, _normalize_song)
async def new_songs(area: int = 0, limit: int = 30, user_key: str = "") -> list:
    body = await request("/top/song", {"type": area, "limit": limit}, "get", user_key)
    songs = (body or {}).get("data") or []
    return _collect(songs, _normalize_song)
async def highquality_playlists(cat: str = "", limit: int = 20, user_key: str = "") -> list:
    params = {"limit": limit}
    if cat:
        params["cat"] = cat
    body = await request("/top/playlist/highquality", params, "get", user_key)
    pls = (body or {}).get("playlists") or []
    return _collect(pls, _normalize_playlist)
async def playlist_cats(user_key: str = "") -> list:
    body = await request("/playlist/catlist", {}, "get", user_key)
    subs = (body or {}).get("sub") or []
    out = []
    for s in subs:
        if not isinstance(s, dict) or not s.get("name"):
            continue
        out.append(
            {
                "name": s.get("name"),
                "count": int(_num(s.get("count"))),
            }
        )
    return out
async def banner(user_key: str = "") -> list:
    body = await request("/banner", {}, "get", user_key)
    banners = (body or {}).get("banners") or []
    out = []
    for i, b in enumerate(banners[:10]):
        if not isinstance(b, dict):
            continue
        out.append(
            {
                "index": i + 1,
                "title": b.get("title") or b.get("typeTitle") or "",
                "typeTitle": b.get("typeTitle") or "",
                "url": b.get("url") or "",
                "image": b.get("imageUrl") or b.get("pic") or "",
            }
        )
    return out
# ──────────── 相似歌单 / 榜单 / 新碟 / 电台 ────────────


async def simi_playlists(song_id, limit: int = 10, user_key: str = "") -> list:
    """相似歌单：/simi/playlist 的参数必须是**歌曲 id**，由 #ncm相似歌单 <歌曲id> 调用。"""
    body = await request("/simi/playlist", {"id": song_id, "limit": limit}, "get", user_key)
    pls = (body or {}).get("playlists") or []
    return _collect(pls[:limit], _normalize_playlist)
async def toplist_artist(user_key: str = "") -> list:

    body = await request("/toplist/artist", {}, "get", user_key)
    lst = (body or {}).get("list") or []
    if isinstance(lst, dict):
        lst = lst.get("artists") or []
    out = []
    for i, a in enumerate(lst[:20]):
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
async def album_newest(limit: int = 10, user_key: str = "") -> list:

    body = await request("/album/newest", {"limit": limit}, "get", user_key)
    albums = (body or {}).get("albums") or []
    out = []
    for i, a in enumerate(albums[:limit]):
        if not isinstance(a, dict):
            continue
        artist = a.get("artist") if isinstance(a.get("artist"), dict) else {}
        out.append(
            {
                "index": i + 1,
                "id": a.get("id") or 0,
                "name": a.get("name") or "",
                "cover": a.get("picUrl") or "",
                "artist": artist.get("name") or a.get("artistName") or "",
                "publishTime": int(_num(a.get("publishTime"))),
                "size": int(_num(a.get("size"))),
            }
        )
    return out
async def top_artists(limit: int = 20, user_key: str = "") -> list:
    body = await request("/top/artists", {"limit": limit}, "get", user_key)
    artists = (body or {}).get("artists") or []
    out = []
    for i, a in enumerate(artists[:limit]):
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
async def top_album(area: str = "ALL", limit: int = 10, user_key: str = "") -> list:

    body = await request("/top/album", {"area": area, "type": "new", "limit": limit}, "get", user_key)
    data = (body or {}).get("data") or (body or {}).get("weekData") or []
    out = []
    for i, a in enumerate(data[:limit]):
        if not isinstance(a, dict):
            continue
        artist = a.get("artist") if isinstance(a.get("artist"), dict) else {}
        out.append(
            {
                "index": i + 1,
                "id": a.get("id") or 0,
                "name": a.get("name") or "",
                "cover": a.get("picUrl") or a.get("cover") or "",
                "artist": artist.get("name") or a.get("artistName") or "",
                "publishTime": int(_num(a.get("publishTime"))),
            }
        )
    return out
async def top_mv(limit: int = 10, user_key: str = "") -> list:
    body = await request("/top/mv", {"limit": limit}, "get", user_key)
    data = (body or {}).get("data") or []
    out = []
    for i, m in enumerate(data[:limit]):
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
async def dj_recommend(limit: int = 10, user_key: str = "") -> list:

    body = await request("/dj/recommend", {"limit": limit}, "get", user_key)
    data = (body or {}).get("djRadios") or (body or {}).get("data") or []
    out = []
    for i, d in enumerate(data[:limit]):
        if not isinstance(d, dict):
            continue
        out.append(
            {
                "index": i + 1,
                "id": d.get("id") or 0,
                "name": d.get("name") or "",
                "cover": d.get("picUrl") or "",
                "desc": str(d.get("rcmdtext") or d.get("desc") or "")[:100],
                "subCount": int(_num(d.get("subCount"))),
            }
        )
    return out
async def top_playlists(cat: str = "全部", order: str = "hot", limit: int = 20, user_key: str = "") -> list:
    body = await request("/top/playlist", {"cat": cat, "order": order, "limit": limit}, "get", user_key)
    pls = (body or {}).get("playlists") or []
    return _collect(pls, _normalize_playlist)
async def playlist_hot_tags(user_key: str = "") -> list:

    body = await request("/playlist/hot", {}, "get", user_key)
    tags = (body or {}).get("tags") or []
    out = []
    for i, t in enumerate(tags[:20]):
        if not isinstance(t, dict):
            continue
        tag = t.get("playlistTag") if isinstance(t.get("playlistTag"), dict) else t
        out.append(
            {
                "index": i + 1,
                "name": tag.get("name") or "",
                "usedCount": int(_num(tag.get("usedCount") or t.get("usedCount"))),
            }
        )
    return out
