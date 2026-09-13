"""会话「列表 → 选歌 / #ncm听N」流程（从 MusicService 整块搬出，行为等价）。

包含：进入选歌流程（``start_select``）、把歌曲/歌单/专辑候选写入会话并出卡片
（``list_to_session`` / ``playlist_list_to_session`` / ``album_list_to_session``）、
以及会话序号展开（``expand_playlist`` / ``expand_album``）。

``MusicService`` 上的同名公开方法保持签名不变，改为薄委托。
"""

from __future__ import annotations

import re

from astrbot.api.event import AstrMessageEvent

from . import api as ncmapi
from . import cards as cardlib
from .api import ApiError
from .messages import (
    TIP_ALBUM_TRACKS,
    TIP_PLAYLIST_TRACKS,
    fmt_album_show_tip,
    fmt_album_tracks_tip,
    fmt_playlist_show_tip,
    fmt_playlist_title,
)

# 列表类命令最多展示的条数（歌单/专辑/链接解析展开）
LIST_PREVIEW_LIMIT = 30
# 日推/新歌/历史日推/云盘等列表最多展示的条数
LIST_SHOW_LIMIT = 20


async def _search_songs(kw: str, page_size: int, user_key: str) -> list:
    """关键词检索：纯数字按歌曲 id 处理（与 README/帮助声明的「关键词|id」一致），
    id 不存在时回退关键词搜索（歌名本身可能就叫「12345」）。
    """
    if re.fullmatch(r"\d+", kw):
        lst = await ncmapi.song_detail([int(kw)], user_key=user_key)
        if lst:
            return lst
    return await ncmapi.search(kw, type_=1, limit=page_size, user_key=user_key)


async def start_select(
    service,
    event: AstrMessageEvent,
    action: str,
    kw: str,
    *,
    label: str,
    verb: str,
    user_key: str,
) -> None:
    """进入"先选歌再操作"流程。"""
    scope = service.scope(event)
    session = await cardlib.SessionStore.get(service.plugin, scope)
    if (kw or "").strip():
        page_size = max(1, min(ncmapi.as_int(service.cfg().get("maxList") or 10, 10), 20))
        try:
            lst = await _search_songs(kw, page_size, user_key)
        except ApiError as err:
            # API 不可用时若放任异常外抛，handler 直接崩掉、用户什么都收不到
            service.log_warn(f"选歌失败: {err}")
            await service.reply(event, f"获取歌曲失败：{err}")
            return
        if not lst:
            await service.reply(event, f"没有搜到「{kw}」")
            return
        keyword = kw
    else:
        lst = (session or {}).get("data") or []
        if not lst:
            await service.reply(
                event,
                f"用法：先 #ncm点歌 关键词 选中歌曲，再发 #ncm{label}；或直接 #ncm{label} 关键词 选择",
            )
            return
        if (session or {}).get("type") != "songs":
            # 会话里是歌单/专辑候选（type=playlistList/albumList，data 是歌单/专辑字典），
            # 直接当成歌曲列表会把歌单 id 当歌曲 id 用（#ncm听N 只会拿到「未获取到播放链接」），
            # 因此这里不改写会话，提示用户先点歌。
            await service.reply(event, "请先 #ncm点歌 关键词 生成歌曲列表")
            return
        keyword = (session or {}).get("keyword") or "当前会话"
    base = dict(session) if session else {}
    base.update({"type": "songs", "keyword": keyword, "data": lst, "action": action})
    await cardlib.SessionStore.set(service.plugin, scope, base)
    await service.mark_session_owner()
    tip = f"回复 #ncm听N 即可{verb}"
    if service.cfg().get("renderListCard", True):
        data = cardlib.build_list_card_data(
            keyword, lst, options={"tip": tip}, cfg=service.cfg()
        )
        if await service.reply_card_or_text(
            event,
            tpl_name="ncm-list",
            data=data,
            format_text=lambda d: cardlib.format_song_list(lst, keyword, tip=tip),
        ):
            return
    await service.reply(event, cardlib.format_song_list(lst, keyword, tip=tip))


async def list_to_session(
    service, event: AstrMessageEvent, keyword: str, songs: list, *, tip: str = ""
) -> bool:
    """歌曲列表写入会话并出卡片（#ncm听N 的播放来源）。"""
    scope = service.scope(event)
    await cardlib.SessionStore.set(
        service.plugin, scope, {"type": "songs", "keyword": keyword, "data": songs}
    )
    await service.mark_session_owner()
    def text() -> str:
        return cardlib.format_song_list(songs, keyword, tip=tip)
    if service.cfg().get("renderListCard", True):
        data = cardlib.build_list_card_data(
            keyword, songs, options={"tip": tip}, cfg=service.cfg()
        )
        if await service.reply_card_or_text(
            event, tpl_name="ncm-list", data=data, format_text=lambda d: text()
        ):
            return True
    await service.reply(event, text())
    return True


async def playlist_list_to_session(
    service, event: AstrMessageEvent, title: str, pls: list, *, subtitle: str = ""
) -> bool:
    """歌单候选列表写入会话（#ncm听N 展开曲目）。"""
    scope = service.scope(event)
    await cardlib.SessionStore.set(
        service.plugin, scope, {"type": "playlistList", "keyword": title, "data": pls}
    )
    tip = TIP_PLAYLIST_TRACKS
    data = cardlib.build_playlist_card_data(
        title, pls, subtitle=subtitle, tip=tip, cfg=service.cfg()
    )
    return await service.reply_card_or_text(
        event,
        tpl_name="ncm-playlist",
        data=data,
        format_text=lambda d: cardlib.format_playlist_text(title, pls, tip=tip),
    )


async def album_list_to_session(
    service, event: AstrMessageEvent, title: str, albums: list
) -> bool:
    """专辑候选列表写入会话（#ncm听N 展开曲目）。"""
    scope = service.scope(event)
    await cardlib.SessionStore.set(
        service.plugin, scope, {"type": "albumList", "keyword": title, "data": albums}
    )
    tip = TIP_ALBUM_TRACKS
    items = [
        {
            "name": a.get("name") or "",
            "sub": a.get("artist") or "",
            "tag": f"{a.get('size') or a.get('trackCount') or '?'}首"
            if (a.get("size") or a.get("trackCount"))
            else "",
            "cover": a.get("cover") or "",
        }
        for a in albums
    ]
    data = cardlib.build_generic_card_data(
        title, items, subtitle="专辑候选", tip=tip, cfg=service.cfg()
    )
    return await service.reply_card_or_text(
        event,
        tpl_name="ncm-generic",
        data=data,
        format_text=lambda d: cardlib.format_generic_text(title, items, tip=tip),
    )


async def expand_playlist(
    service, event: AstrMessageEvent, session: dict, n: int
) -> None:
    """会话中的歌单候选序号展开为曲目列表。"""
    pls = session.get("data") or []
    if n < 1 or n > len(pls):
        await service.reply(event, f"序号超出范围（1-{len(pls)}）")
        event.stop_event()
        return
    p = pls[n - 1]
    try:
        songs = await ncmapi.playlist_tracks(p["id"], user_key=service.user_key(event))
    except ApiError as err:
        service.log_warn(f"歌单展开失败: {err}")
        await service.reply(event, f"歌单展开失败：{err}")
        event.stop_event()
        return
    if not songs:
        await service.reply(event, f"歌单「{p['name']}」暂无曲目")
        event.stop_event()
        return
    shown = songs[:LIST_PREVIEW_LIMIT]
    await list_to_session(
        service,
        event,
        fmt_playlist_title(p["name"]),
        shown,
        tip=fmt_playlist_show_tip(len(songs), len(shown)),
    )
    event.stop_event()


async def expand_album(service, event: AstrMessageEvent, session: dict, n: int) -> None:
    """会话中的专辑候选序号展开为曲目列表。"""
    albums = session.get("data") or []
    if n < 1 or n > len(albums):
        await service.reply(event, f"序号超出范围（1-{len(albums)}）")
        event.stop_event()
        return
    a = albums[n - 1]
    try:
        album_info, songs = await ncmapi.album_detail(
            a["id"], user_key=service.user_key(event)
        )
    except ApiError as err:
        service.log_warn(f"专辑展开失败: {err}")
        await service.reply(event, f"专辑展开失败：{err}")
        event.stop_event()
        return
    info = album_info or {}
    name = info.get("name") or a.get("name") or "专辑"
    if not songs:
        await service.reply(event, f"专辑「{name}」暂无曲目")
        event.stop_event()
        return
    # 与歌单展开同一口径：只展示前 LIST_PREVIEW_LIMIT 首，否则上百首的专辑
    # 会生成一张极高的卡片（截图与消息都很难看）。
    shown = songs[:LIST_PREVIEW_LIMIT]
    tip = (
        fmt_album_show_tip(info.get("artist") or "", len(songs), len(shown))
        if len(shown) < len(songs)
        else fmt_album_tracks_tip(info.get("artist") or "", len(songs))
    )
    await list_to_session(service, event, f"专辑 · {name}", shown, tip=tip)
    event.stop_event()
