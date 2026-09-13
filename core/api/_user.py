"""astrbot_plugin_neteasemusic API —— （自 core/api.py 拆分；公开符号经本包 __init__ 再导出）。"""

from __future__ import annotations

import json

from ._core import ERR_MESSAGES, ApiError, _num, _opt_int, request
from ._normalize import _collect, _duration_text, _normalize_playlist, _normalize_song
from ._song import song_detail

# ──────────── 推荐 / 用户（需登录） ────────────


async def recommend_playlists(limit: int = 15, user_key: str = "") -> list:
    body = await request("/personalized", {"limit": limit}, "get", user_key)
    lst = (body or {}).get("result") or []
    return _collect(lst, _normalize_playlist)
async def daily_songs(user_key: str = "") -> list:
    body = await request("/recommend/songs", {}, "get", user_key)
    songs = (((body or {}).get("data") or {}).get("dailySongs")) or []
    return _collect(songs, _normalize_song)
async def recommend_newsong(limit: int = 15, user_key: str = "") -> list:
    body = await request("/personalized/newsong", {"limit": limit}, "get", user_key)
    lst = (body or {}).get("result") or []
    out = []
    for i, item in enumerate(lst):
        if not isinstance(item, dict):
            continue
        s = item.get("song") if isinstance(item.get("song"), dict) else None
        norm = _normalize_song(s or item, i)
        if norm:
            out.append(norm)
    return out
async def personal_fm(user_key: str = "") -> list:
    # 注意：api-enhanced 该路由是 /personal_fm（下划线特例，见 server.js）
    body = await request("/personal_fm", {}, "get", user_key)
    songs = (body or {}).get("data") or []
    return _collect(songs, _normalize_song)
async def likelist(uid, limit: int = 30, user_key: str = "") -> list:
    body = await request("/likelist", {"uid": uid}, "get", user_key)
    ids = (body or {}).get("ids") or []
    if not ids:
        return []
    return await song_detail(ids[:limit], user_key=user_key)
async def user_record(uid, type_: int = 1, limit: int = 30, user_key: str = "") -> list:
    body = await request("/user/record", {"uid": uid, "type": type_}, "get", user_key)
    key = "weekData" if type_ == 1 else "allData"
    data = (body or {}).get(key) or []
    out = []
    for i, item in enumerate(data[:limit]):
        if not isinstance(item, dict):
            continue
        s = item.get("song") if isinstance(item.get("song"), dict) else None
        norm = _normalize_song(s, i) if s else None
        if norm:
            norm["playCount"] = int(_num(item.get("playCount") or item.get("playedNum")))
            out.append(norm)
    return out
# ──────────── 签到 / 云盘 / 最近（需登录） ────────────


async def daily_signin(type_: int = 0, user_key: str = "") -> dict:

    return await request("/daily_signin", {"type": type_}, "get", user_key)
async def user_cloud(limit: int = 30, user_key: str = "") -> list:

    body = await request("/user/cloud", {"limit": limit, "offset": 0}, "get", user_key)
    # data 正常为 dict{songs:[...]}；未登录/异常时可能直接是 list（此前对其调 .get 抛
    # AttributeError: 'list' object has no attribute 'get'，导致指令报 ":("）
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict):
        songs = data.get("songs") or []
    elif isinstance(data, list):
        songs = data
    else:
        songs = []
    out = []
    for i, s in enumerate(songs):
        if not isinstance(s, dict):
            continue
        artist = s.get("artist") or ""
        if isinstance(artist, list):
            artist = " / ".join(a.get("name") or "" for a in artist if isinstance(a, dict))
        out.append(
            {
                "index": i + 1,
                "id": s.get("songId") or s.get("id") or 0,
                "name": s.get("songName") or s.get("name") or "",
                "artist": str(artist),
                "album": s.get("album") or "",
                "cover": s.get("cover") or s.get("albumPic") or "",
                "duration": _duration_text(_num(s.get("songTime") or s.get("duration"))),
                "dtMs": int(_num(s.get("songTime") or s.get("duration"))),
                "fee": 0,
                "payplay": False,
                "trial": False,
                "mvid": 0,
            }
        )
    return out
async def record_recent_song(limit: int = 30, user_key: str = "") -> list:

    body = await request("/record/recent/song", {"limit": limit}, "get", user_key)
    lst = (((body or {}).get("data") or {}).get("list")) or []
    out = []
    for i, item in enumerate(lst):
        if not isinstance(item, dict):
            continue
        # 实测（2026-08）：歌曲信息在 item.data（完整 song 对象，含 name/id/ar/al），
        # item.resource 字段已不存在；resourceId 为歌曲 id
        s = item.get("data") if isinstance(item.get("data"), dict) else item.get("resource")
        norm = _normalize_song(s, i) if isinstance(s, dict) else None
        if norm:
            # playTime 为最近播放时间戳（ms），可展示"X天前播放"
            norm["playTime"] = int(_num(item.get("playTime")))
            out.append(norm)
    return out
async def history_recommend_songs(user_key: str = "") -> list:

    body = await request("/history/recommend/songs", {}, "get", user_key)
    data = (body or {}).get("data") or {}
    # 实测（2026-08）：data 为 dict——dates 是可用日期列表（黑胶VIP 近 5 次），
    # songs 通常为 None；实际歌曲需再调 detail?date=YYYY-MM-DD 获取
    if not isinstance(data, dict):
        return []
    songs = data.get("songs") or []
    if not songs:
        dates = data.get("dates") or []
        if not dates:
            return []
        body2 = await request("/history/recommend/songs/detail", {"date": dates[0]}, "get", user_key)
        songs = ((body2 or {}).get("data") or {}).get("songs") or []
    return _collect(songs, _normalize_song)
async def user_playlist(uid, limit: int = 30, user_key: str = "") -> list:

    body = await request("/user/playlist", {"uid": uid, "limit": limit, "offset": 0}, "get", user_key)
    pls = (body or {}).get("playlist") or []
    return _collect(pls, _normalize_playlist)
# ──────────── 红心（需登录） ────────────


async def song_like_check(ids, user_key: str = "") -> dict:
    """查询这些歌曲是否已红心，返回 ``{歌曲 id: bool}``。

    两个坑（旧实现都踩了，导致「已红心」永远判为 False → 红心只能点、不能取消）：
    ① 上游 ``song_like_check.js`` 把 ``trackIds`` **原样**透传，必须是 JSON 数组字符串
       (``[186016]``），逗号分隔的 ``186016`` 会被判为参数错误；
    ② 响应里「已红心的 id 列表」在**顶层** ``ids``，不是 ``data``（``data`` 恒不存在）。
    这里同时兼容旧的 ``data`` 字典数组形态，避免上游版本差异导致再次失效。
    """
    id_list = [int(i) for i in ids]
    body = await request("/song/like/check", {"ids": json.dumps(id_list)}, "get", user_key)
    raw = (body or {}).get("ids")
    if raw is None:
        raw = (body or {}).get("data") or []
    liked_ids: set[str] = set()
    if isinstance(raw, dict):
        # {id: bool} 形态：只收真值的键，否则会把字典里所有 id 都当成已红心
        # （进而在 #ncm红心 里反向执行成「取消红心」）。
        for key, value in raw.items():
            if value:
                liked_ids.add(str(key))
    else:
        for item in raw:
            if isinstance(item, dict):
                if item.get("bool"):
                    liked_ids.add(str(item.get("id")))
            else:
                liked_ids.add(str(item))
    return {str(i): (str(i) in liked_ids) for i in id_list}
async def like(song_id, like_: bool = True, user_key: str = "") -> dict:
    """红心/取消红心；失败一律抛 ``ApiError``，不再「报成功但没写进去」。

    ``/like`` 失败时上游常返回 HTTP 200 + ``{"code": 400/301}``，而 ``_handle_response``
    只拦 HTTP≥400 与顶层 ``code=301``，不校验 body.code 就会谎报成功。
    """
    body = await request("/like", {"id": song_id, "like": "true" if like_ else "false"}, "get", user_key)
    code = _opt_int((body or {}).get("code"))
    if code is not None and code != 200:
        raise ApiError(ERR_MESSAGES.get(code) or f"操作失败（code={code}）", code=code, payload=body)
    return body
