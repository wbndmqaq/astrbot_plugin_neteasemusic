"""astrbot_plugin_neteasemusic API —— （自 core/api.py 拆分；公开符号经本包 __init__ 再导出）。"""

from __future__ import annotations

from ..quality import QUALITY_LABEL, quality_candidates
from ._account import has_high_quality_privilege
from ._core import ERR_MESSAGES, ApiError, _opt_int, request
from ._normalize import _collect, _normalize_playlist, _normalize_song

# ──────────── 歌曲 ────────────


async def song_url_v1(song_id, level: str = "lossless", *, unblock: bool = False, user_key: str = "") -> dict:
    params = {"id": song_id, "level": level}
    if unblock:
        params["unblock"] = "true"
    body = await request("/song/url/v1", params, "get", user_key)
    data = (body or {}).get("data") or []
    d0 = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    return {
        "url": d0.get("url") or "",
        "level": d0.get("level") or level,
        "fee": d0.get("fee"),
        "proxyUrl": d0.get("proxyUrl") or "",
        "code": d0.get("code"),
        "raw": d0,
    }
async def song_url_best(song_id, level: str = "auto", *, user_key: str = "", unblock_fallback: bool = True) -> dict:
    # auto 下按账号实际权限决定起试档位：会员才值得先试母带/环绕/Hi-Res，
    # 否则匿名/非会员每首歌都会白打 4 次必然失败的请求。
    q = (level or "auto").lower()
    full_ladder = q in ("auto", "adaptive", "best") and await has_high_quality_privilege(user_key)
    levels = quality_candidates(level, full_ladder=full_ladder)
    last_err = None
    # 逐档都拿不到 URL 时，上游会在 data[0].code 里给出真实原因（402 需 VIP / 404 无版权 /
    # 403 风控）。只报「无 XX 音质」会让用户完全看不出原因，故保留最后一个有效 code。
    last_code = None
    for lv in levels:
        try:
            r = await song_url_v1(song_id, lv, unblock=False, user_key=user_key)
            if r.get("url"):
                return r
            code = _opt_int(r.get("code"))
            if code is not None:
                last_code = code
            last_err = ApiError(f"无 {QUALITY_LABEL.get(lv, lv)} 音质（{lv}）")
        except ApiError as e:
            last_err = e
            last_code = _opt_int(e.code) or last_code
    if unblock_fallback:
        try:
            r = await song_url_v1(song_id, "lossless", unblock=True, user_key=user_key)
            if r.get("url"):
                r["unblocked"] = True
                return r
            code = _opt_int(r.get("code"))
            if code is not None:
                last_code = code
        except ApiError as e:
            last_err = e
            last_code = _opt_int(e.code) or last_code
    if last_code is not None and last_code in ERR_MESSAGES:
        raise ApiError(ERR_MESSAGES[last_code], code=last_code)
    if last_err is not None:
        raise last_err
    raise ApiError("无法获取播放链接（可能无版权或需要 VIP）")
async def song_detail(ids, user_key: str = "") -> list:
    id_str = ",".join(str(i) for i in ids)
    body = await request("/song/detail", {"ids": id_str}, "get", user_key)
    songs = (body or {}).get("songs") or []
    return _collect(songs, _normalize_song)
async def lyric(song_id, user_key: str = "") -> dict:
    body = await request("/lyric", {"id": song_id}, "get", user_key)
    lrc = ((body or {}).get("lrc") or {}).get("lyric") or ""
    tly = ((body or {}).get("tlyric") or {}).get("lyric") or ""
    return {"lrc": lrc, "tlyric": tly}
async def simi_songs(song_id, limit: int = 10, user_key: str = "") -> list:
    body = await request("/simi/song", {"id": song_id, "limit": limit}, "get", user_key)
    songs = (body or {}).get("songs") or []
    return _collect(songs, _normalize_song)
async def related_playlists(playlist_id, limit: int = 10, user_key: str = "") -> list:
    # 旧 /related/playlist 已废弃（html 抓取，返回空），改用 /playlist/detail/rcmd/get。
    # 实测（2026-08）：参数必须是「歌单 id」（传歌曲 id 会 502/空），仅部分歌单有相关推荐。
    body = await request("/playlist/detail/rcmd/get", {"id": playlist_id}, "get", user_key)
    data = (body or {}).get("data") or {}
    recs = data.get("recPlaylist") or []
    out = []
    for i, rec in enumerate(recs[:limit]):
        if not isinstance(rec, dict):
            continue
        norm = _normalize_playlist(rec.get("playlist"), i)
        if norm:
            out.append(norm)
    return out
