from __future__ import annotations

import re
import time
from typing import Any

import aiohttp

from .quality import QUALITY_LABEL, quality_candidates

# 模块级配置访问器，由 main.py 在插件加载时注入
_cfg_getter = None


def set_config_getter(fn):
    global _cfg_getter
    _cfg_getter = fn


def _cfg() -> dict:
    if _cfg_getter is not None:
        try:
            return _cfg_getter() or {}
        except Exception:
            return {}
    return {}


def _get_cookie() -> str:
    try:
        return str(_cfg().get("defaultCookie") or "")
    except Exception:
        return ""


# ──────────── 复用 HTTP 会话 ────────────

# 模块级 aiohttp.ClientSession 复用：避免每请求新建会话（反复 TCP 握手 + DNS 解析）。
# 会话由 service.terminate() 调 close_session() 统一关闭；懒加载，未使用时不会创建。
_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def close_session() -> None:
    """关闭并释放复用的 HTTP 会话（插件卸载/重载时由 service 调用）。"""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


# ──────────── 错误处理 ────────────

ERR_MESSAGES = {
    301: "未登录或登录态已失效",
    400: "参数错误",
    402: "该曲需 VIP/会员才能播放",
    403: "请求被风控拒绝（403），可尝试给 API 服务配置 realIP 或随机中国 IP",
    404: "资源不存在或无版权",
    406: "需要登录，请先发送 #ncm登录",
    460: "IP 被风控（460 cheating），请给 API 服务配置 realIP 或 randomCNIP",
    502: "网易接口调用失败，请稍后重试",
    503: "请求过于频繁，请稍后再试",
    1101: "登录已过期，请重新 #ncm登录",
}


class ApiError(Exception):
    def __init__(self, message: str, *, code=None, payload=None):
        super().__init__(message)
        self.code = code
        self.payload = payload


def _err_msg_for(code, status: int = 0) -> str:
    if code is not None and int(code) in ERR_MESSAGES:
        return ERR_MESSAGES[int(code)]
    if status >= 500:
        return f"网易云 API 服务错误（HTTP {status}），请检查 api-enhanced 服务"
    if status == 401:
        return "API 鉴权失败（401）"
    if status >= 400:
        return f"请求失败（HTTP {status}）"
    return "请求失败"


# ──────────── 基础请求 ────────────


def _get_base() -> str:
    base = str(_cfg().get("apiBase") or "")
    return base.rstrip("/")


def _query_safe_params(params: dict) -> dict:
    out: dict = {}
    for k, v in params.items():
        if isinstance(v, bool):
            out[k] = int(v)
        elif v is None:
            continue
        elif isinstance(v, (str, int, float)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


def _num(v: Any) -> float:
    if v is None or isinstance(v, (list, dict)):
        return 0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    return f if f == f and f != float("inf") else 0  # NaN/inf guard


async def request(pathname: str, params: dict | None = None, method: str = "get", user_key: str = "") -> dict:
    params = dict(params or {})
    base = _get_base()
    if not base:
        # 空 base 会拼出 "/cloudsearch" 这类无协议 URL，aiohttp 报 InvalidURL，
        # 错误信息令人费解；直接给出可操作的配置提示
        raise ApiError("API 地址未配置：请发送 #ncm api <地址>，或在插件设置面板填写 apiBase")
    if "://" not in base:
        raise ApiError(f"API 地址格式错误（缺少 http:// 协议头）：{base}")
    url = f"{base}{pathname if pathname.startswith('/') else '/' + pathname}"
    cookie = _get_cookie()
    if cookie:
        params["cookie"] = cookie

    timeout = aiohttp.ClientTimeout(total=20)
    try:
        sess = _get_session()
        if method == "get":
            async with sess.get(url, params=_query_safe_params(params), timeout=timeout) as res:
                return await _handle_response(res, pathname)
        else:
            async with sess.post(url, json=params, timeout=timeout) as res:
                return await _handle_response(res, pathname)
    except aiohttp.ClientConnectorError as e:
        raise ApiError(f"无法连接网易云 API（{base}），请确认 api-enhanced 服务已启动") from e
    except aiohttp.ServerTimeoutError as e:
        raise ApiError(f"请求超时：{base}") from e
    except aiohttp.ClientError as e:
        raise ApiError(f"网络错误：{e}") from e


async def _handle_response(res: aiohttp.ClientResponse, pathname: str = "") -> dict:
    status = res.status
    try:
        data = await res.json(content_type=None)
    except Exception:
        text = (await res.text())[:200]
        raise ApiError(f"返回非 JSON（HTTP {status}）：{text}")
    if not isinstance(data, dict):
        raise ApiError(f"返回格式异常（HTTP {status}）")
    if status >= 400:
        code = data.get("code")
        msg = data.get("msg") or data.get("message") or ""
        raise ApiError(msg or _err_msg_for(code, status), code=code, payload=data)
    # 网易登录态类接口匿名时返回 HTTP 200 + body.code=301（登录接口自身除外）
    if data.get("code") == 301 and not pathname.startswith("/login/"):
        raise ApiError(ERR_MESSAGES[301], code=301, payload=data)
    return data


# ──────────── 归一化 ────────────


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
    mvid = item.get("mvid")
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
        "mvid": mvid or 0,
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


# ──────────── 短链展开（163cn.tv → music.163.com） ────────────


SHORT_LINK_RE = re.compile(r"https?://[\w.-]*\.?163cn\.tv/\S+")


async def expand_short_links(text: str) -> str:

    links = SHORT_LINK_RE.findall(str(text or ""))
    if not links:
        return text
    out = text
    for link in links:
        final_url = await _follow_redirect(link)
        if final_url:
            out = out.replace(link, final_url)
    return out


async def _follow_redirect(url: str) -> str:
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        sess = _get_session()
        async with sess.get(
            url,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            },
            timeout=timeout,
        ) as res:
            return str(res.url)
    except Exception:
        return ""


# ──────────── 搜索 ────────────


async def search(keyword: str, type_: int = 1, limit: int = 10, offset: int = 0, user_key: str = "") -> list:
    body = await request(
        "/cloudsearch", {"keywords": keyword, "type": type_, "limit": limit, "offset": offset}, "get", user_key
    )
    songs = (((body or {}).get("result") or {}).get("songs")) or []
    out = []
    for i, s in enumerate(songs):
        norm = _normalize_song(s, i)
        if norm:
            out.append(norm)
    return out


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
    out = []
    for i, p in enumerate(pls):
        norm = _normalize_playlist(p, i)
        if norm:
            out.append(norm)
    return out


async def search_suggest(keyword: str, user_key: str = "") -> list:
    body = await request("/search/suggest", {"keywords": keyword}, "get", user_key)
    result = (body or {}).get("result") or {}
    out = []
    for m in result.get("allMatch") or []:
        if isinstance(m, dict) and m.get("keyword"):
            out.append(m["keyword"])
    if not out:
        for s in (result.get("songs") or [])[:5]:
            if isinstance(s, dict) and s.get("name"):
                out.append(s["name"])
    return out[:10]


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
    levels = quality_candidates(level)
    last_err = None
    for lv in levels:
        try:
            r = await song_url_v1(song_id, lv, unblock=False, user_key=user_key)
            if r.get("url"):
                return r
            last_err = ApiError(f"无 {QUALITY_LABEL.get(lv, lv)} 音质（{lv}）")
        except ApiError as e:
            last_err = e
    if unblock_fallback:
        try:
            r = await song_url_v1(song_id, "lossless", unblock=True, user_key=user_key)
            if r.get("url"):
                r["unblocked"] = True
                return r
        except ApiError as e:
            last_err = e
    if last_err is not None:
        raise last_err
    raise ApiError("无法获取播放链接（可能无版权或需要 VIP）")


async def song_detail(ids, user_key: str = "") -> list:
    id_str = ",".join(str(i) for i in ids)
    body = await request("/song/detail", {"ids": id_str}, "get", user_key)
    songs = (body or {}).get("songs") or []
    out = []
    for i, s in enumerate(songs):
        norm = _normalize_song(s, i)
        if norm:
            out.append(norm)
    return out


async def lyric(song_id, user_key: str = "") -> dict:
    body = await request("/lyric", {"id": song_id}, "get", user_key)
    lrc = ((body or {}).get("lrc") or {}).get("lyric") or ""
    tly = ((body or {}).get("tlyric") or {}).get("lyric") or ""
    return {"lrc": lrc, "tlyric": tly}


async def simi_songs(song_id, limit: int = 10, user_key: str = "") -> list:
    body = await request("/simi/song", {"id": song_id, "limit": limit}, "get", user_key)
    songs = (body or {}).get("songs") or []
    out = []
    for i, s in enumerate(songs):
        norm = _normalize_song(s, i)
        if norm:
            out.append(norm)
    return out


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


# ──────────── 评论 ────────────


def _normalize_comment(item: dict, idx: int = 0, hot: bool = False) -> dict | None:
    if not isinstance(item, dict):
        return None
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    return {
        "index": idx + 1,
        "nick": user.get("nickname") or "",
        "avatar": user.get("avatarUrl") or "",
        "time": int(_num(item.get("time"))),
        "likes": int(_num(item.get("likedCount"))),
        "content": item.get("content") or "",
        "hot": hot,
    }


def _comments_from_body(body: dict) -> list:
    out = []
    hot = (body or {}).get("hotComments") or []
    normal = (body or {}).get("comments") or []
    for i, c in enumerate(hot):
        norm = _normalize_comment(c, i, hot=True)
        if norm:
            out.append(norm)
    for i, c in enumerate(normal):
        norm = _normalize_comment(c, len(hot) + i, hot=False)
        if norm:
            out.append(norm)
    return out


async def _comments_for(pathname: str, res_id, limit: int = 20, user_key: str = "") -> list:
    body = await request(pathname, {"id": res_id, "limit": limit}, "get", user_key)
    return _comments_from_body(body)


async def comment(song_id, limit: int = 20, user_key: str = "") -> list:
    return await _comments_for("/comment/music", song_id, limit, user_key)


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
    out = []
    for i, s in enumerate(songs):
        norm = _normalize_song(s, i)
        if norm:
            out.append(norm)
    return out


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
    out = []
    for i, s in enumerate(songs):
        norm = _normalize_song(s, i)
        if norm:
            out.append(norm)
    return out


async def new_songs(area: int = 0, limit: int = 30, user_key: str = "") -> list:
    body = await request("/top/song", {"type": area, "limit": limit}, "get", user_key)
    songs = (body or {}).get("data") or []
    out = []
    for i, s in enumerate(songs):
        norm = _normalize_song(s, i)
        if norm:
            out.append(norm)
    return out


async def highquality_playlists(cat: str = "", limit: int = 20, user_key: str = "") -> list:
    params = {"limit": limit}
    if cat:
        params["cat"] = cat
    body = await request("/top/playlist/highquality", params, "get", user_key)
    pls = (body or {}).get("playlists") or []
    out = []
    for i, p in enumerate(pls):
        norm = _normalize_playlist(p, i)
        if norm:
            out.append(norm)
    return out


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


# ──────────── 推荐 / 用户（需登录） ────────────


async def recommend_playlists(limit: int = 15, user_key: str = "") -> list:
    body = await request("/personalized", {"limit": limit}, "get", user_key)
    lst = (body or {}).get("result") or []
    out = []
    for i, p in enumerate(lst):
        norm = _normalize_playlist(p, i)
        if norm:
            out.append(norm)
    return out


async def daily_songs(user_key: str = "") -> list:
    body = await request("/recommend/songs", {}, "get", user_key)
    songs = (((body or {}).get("data") or {}).get("dailySongs")) or []
    out = []
    for i, s in enumerate(songs):
        norm = _normalize_song(s, i)
        if norm:
            out.append(norm)
    return out


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
    out = []
    for i, s in enumerate(songs):
        norm = _normalize_song(s, i)
        if norm:
            out.append(norm)
    return out


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


# ──────────── 歌手 / 专辑 / MV ────────────


async def artist_top_songs(artist_id, limit: int = 30, user_key: str = "") -> list:
    body = await request("/artist/top/song", {"id": artist_id, "limit": limit}, "get", user_key)
    songs = (body or {}).get("songs") or []
    out = []
    for i, s in enumerate(songs):
        norm = _normalize_song(s, i)
        if norm:
            out.append(norm)
    return out


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
    out = []
    for i, s in enumerate(songs):
        norm = _normalize_song(s, i)
        if norm:
            out.append(norm)
    return album_info, out


# ──────────── 登录 ────────────


def _now_ts() -> int:
    # api-enhanced 对相同 URL 缓存 2 分钟（server.js apicache，无 login 豁免）；
    # 二维码三接口必须带实时时间戳才能绕过缓存，否则轮询永远拿到第一次的结果
    return int(time.time() * 1000)


async def qr_key(user_key: str = "") -> str:
    body = await request("/login/qr/key", {"timestamp": _now_ts()}, "get", user_key)
    return (((body or {}).get("data") or {}).get("unikey")) or ""


async def qr_create(key: str, user_key: str = "") -> dict:
    body = await request("/login/qr/create", {"key": key, "qrimg": "true"}, "get", user_key)
    data = (body or {}).get("data") or {}
    return {
        "qrurl": data.get("qrurl") or "",
        "qrimg": data.get("qrimg") or "",
    }


async def qr_check(key: str, user_key: str = "") -> dict:
    # 时间戳防缓存（见 _now_ts 注释）；轮询频率 2s，缓存会直接卡死状态更新
    return await request("/login/qr/check", {"key": key, "timestamp": _now_ts()}, "get", user_key)


async def logout(user_key: str = "") -> dict:
    return await request("/logout", {}, "get", user_key)


async def vip_info(user_key: str = "") -> dict:

    return await request("/vip/info", {}, "get", user_key)


async def login_status(user_key: str = "") -> dict:
    body = await request("/login/status", {}, "get", user_key)
    data = (body or {}).get("data") or {}
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else None
    return {
        "code": (body or {}).get("code") or (data.get("code") if isinstance(data, dict) else None),
        "profile": profile,
        "account": data.get("account") if isinstance(data.get("account"), dict) else None,
    }


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
    out = []
    for i, s in enumerate(songs):
        norm = _normalize_song(s, i)
        if norm:
            out.append(norm)
    return out


async def user_playlist(uid, limit: int = 30, user_key: str = "") -> list:

    body = await request("/user/playlist", {"uid": uid, "limit": limit, "offset": 0}, "get", user_key)
    pls = (body or {}).get("playlist") or []
    out = []
    for i, p in enumerate(pls):
        norm = _normalize_playlist(p, i)
        if norm:
            out.append(norm)
    return out


# ──────────── 红心（需登录） ────────────


async def song_like_check(ids, user_key: str = "") -> dict:

    body = await request("/song/like/check", {"ids": ",".join(str(i) for i in ids)}, "get", user_key)
    data = (body or {}).get("data") or []
    out = {}
    for item in data:
        if isinstance(item, dict):
            out[str(item.get("id"))] = bool(item.get("bool"))
    return out


async def like(song_id, like_: bool = True, user_key: str = "") -> dict:

    return await request("/like", {"id": song_id, "like": "true" if like_ else "false"}, "get", user_key)


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


# ──────────── 相似歌单 / 榜单 / 新碟 / 电台 ────────────


async def simi_playlists(song_id, limit: int = 10, user_key: str = "") -> list:
    body = await request("/simi/playlist", {"id": song_id, "limit": limit}, "get", user_key)
    pls = (body or {}).get("playlists") or []
    out = []
    for i, p in enumerate(pls[:limit]):
        norm = _normalize_playlist(p, i)
        if norm:
            out.append(norm)
    return out


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
    out = []
    for i, p in enumerate(pls):
        norm = _normalize_playlist(p, i)
        if norm:
            out.append(norm)
    return out


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


# ──────────── 逐字歌词 / 评论扩展 ────────────


async def lyric_new(song_id, user_key: str = "") -> dict:

    body = await request("/lyric/new", {"id": song_id}, "get", user_key)
    lrc = ((body or {}).get("lrc") or {}).get("lyric") or ""
    yrc = ((body or {}).get("yrc") or {}).get("lyric") or ""
    return {"lrc": lrc, "yrc": yrc}


async def comment_playlist(playlist_id, limit: int = 20, user_key: str = "") -> list:
    return await _comments_for("/comment/playlist", playlist_id, limit, user_key)


async def comment_album(album_id, limit: int = 20, user_key: str = "") -> list:
    return await _comments_for("/comment/album", album_id, limit, user_key)
