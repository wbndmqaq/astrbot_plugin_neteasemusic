"""astrbot_plugin_neteasemusic API —— （自 core/api.py 拆分；公开符号经本包 __init__ 再导出）。"""

from __future__ import annotations

from ._core import _num, request

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
