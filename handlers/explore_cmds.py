from __future__ import annotations

import random
import re
from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent

if TYPE_CHECKING:
    from ..core.service import MusicService

from ..core import api as ncmapi
from ..core import cards as cardlib
from ..core.api import ApiError
from ..core.lists import LIST_PREVIEW_LIMIT, LIST_SHOW_LIMIT
from ..core.messages import (
    MSG_PLAYLIST_FAIL,
    TIP_ALBUM_SEARCH,
    TIP_ARTIST_SONGS,
    TIP_PLAYLIST_TRACKS,
    fmt_album_show_tip,
    fmt_album_tracks_tip,
    fmt_playlist_show_tip,
    fmt_playlist_title,
    msg_no_album,
    msg_no_playlist,
)
from ..core.service import NEW_SONG_AREAS
from .base import Route


async def _reply_generic(
    service: MusicService,
    event: AstrMessageEvent,
    title: str,
    items: list,
    *,
    subtitle: str = "",
    tip: str = "",
    text_tip: str = "",
) -> bool:
    """通用列表卡片 + 文本兜底（原先 11 处逐字重复的两段式构造收敛到这一处）。

    ``text_tip`` 仅在「卡片 tip 与文本 tip 不同」时传入（如 #ncmbanner）。
    """
    data = cardlib.build_generic_card_data(
        title, items, subtitle=subtitle, tip=tip, cfg=service.cfg()
    )
    return await service.reply_card_or_text(
        event,
        tpl_name="ncm-generic",
        data=data,
        format_text=lambda d: cardlib.format_generic_text(
            title, items, tip=text_tip or tip
        ),
    )


async def chart(service: MusicService, event: AstrMessageEvent):
    """#ncm排行 [榜单名]：排行榜列表 / 查看具体榜单"""
    if not service.cfg().get("enable", True):
        return
    user_key = service.user_key(event)
    m = re.match(r"^#?(?:ncm|NCM)\s*排行\s*(.*)$", event.message_str.strip(), re.IGNORECASE)
    name = (m.group(1).strip() if m else "").strip()
    try:
        tops = await ncmapi.toplist(user_key=user_key)
        if not tops:
            await service.reply(event, "暂无榜单数据")
            event.stop_event()
            return
        if not name:
            # 榜单列表无需存会话（读取分支不存在，写入还会覆盖用户正在用的歌曲会话）
            title = "网易云排行榜"
            tip = "发送 #ncm排行 榜单名 查看（如 #ncm排行 飙升榜）"
            items = [
                {
                    "name": t.get("name") or "",
                    "sub": t.get("updateFrequency") or "未知更新频率",
                }
                for t in tops
            ]
            await _reply_generic(
                service,
                event,
                title,
                items,
                subtitle=f"共 {len(tops)} 个榜单",
                tip=tip,
            )
            event.stop_event()
            return
        target = None
        for t in tops:
            if name == str(t.get("id")):
                target = t
                break
        if not target:
            for t in tops:
                t_name = t.get("name") or ""
                if t_name and (t_name in name or name in t_name):
                    target = t
                    break
        if not target:
            await service.reply(event, f"未找到榜单「{name}」，发送 #ncm排行 查看全部榜单")
            event.stop_event()
            return
        # 与全插件口径一致：会话只存 LIST_PREVIEW_LIMIT 首（原先取 60 首，
        # 会话/卡片/连播都按 60 走，超出其它列表页 30 首的约定）。
        songs = await ncmapi.top_detail(
            target.get("id"), limit=LIST_PREVIEW_LIMIT, user_key=user_key
        )
        if not songs:
            await service.reply(event, f"榜单「{target.get('name')}」暂无数据")
            event.stop_event()
            return
        await service.list_to_session(event, f"排行榜 · {target.get('name')}", songs)
    except ApiError as err:
        service.log_warn(f"排行失败: {err}")
        await service.reply(event, f"获取排行榜失败：{err}")
    event.stop_event()


async def artist(service: MusicService, event: AstrMessageEvent):
    """#ncm歌手 关键词：歌手热门歌曲"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*歌手\s+(.+)$")
    if not m:
        return
    kw = m.group(1).strip()

    user_key = service.user_key(event)
    try:
        artists = await ncmapi.search_artists(kw, limit=5, user_key=user_key)
        if not artists:
            await service.reply(event, f"没有搜到歌手「{kw}」")
            event.stop_event()
            return
        a = artists[0]
        songs = await ncmapi.artist_top_songs(a.get("id"), limit=30, user_key=user_key)
        if not songs:
            await service.reply(event, f"歌手「{a.get('name')}」暂无热门歌曲")
            event.stop_event()
            return
        await service.list_to_session(
            event,
            f"歌手 · {a.get('name')}",
            songs,
            tip=f"歌手：{a.get('name')} 的热门歌曲（共 {len(songs)} 首）",
        )
    except ApiError as err:
        service.log_warn(f"歌手失败: {err}")
        await service.reply(event, f"获取歌手歌曲失败：{err}")
    event.stop_event()


async def album(service: MusicService, event: AstrMessageEvent):
    """#ncm专辑 关键词：搜索专辑出候选列表，回复 #ncm听N 查看曲目"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*专辑\s+(.+)$")
    if not m:
        return
    kw = m.group(1).strip()

    user_key = service.user_key(event)
    try:
        albums = await ncmapi.search_albums(kw, limit=8, user_key=user_key)
        if not albums:
            await service.reply(event, msg_no_album(kw))
            event.stop_event()
            return
        if len(albums) == 1:
            a = albums[0]
            album_info, songs = await ncmapi.album_detail(
                a.get("id"), user_key=user_key
            )
            info = album_info or {}
            if songs:
                # 与歌单展开/会话展开同一口径：只展示前 LIST_PREVIEW_LIMIT 首，
                # 否则上百首的专辑会渲染出极高的卡片
                shown = songs[:LIST_PREVIEW_LIMIT]
                tip = (
                    fmt_album_show_tip(
                        info.get("artist") or "", len(songs), len(shown)
                    )
                    if len(shown) < len(songs)
                    else fmt_album_tracks_tip(info.get("artist") or "", len(songs))
                )
                await service.list_to_session(
                    event,
                    f"专辑 · {info.get('name') or a.get('name')}",
                    shown,
                    tip=tip,
                )
                event.stop_event()
                return
        title = f"「{kw}」的专辑候选"
        await service.album_list_to_session(event, title, albums)
    except ApiError as err:
        service.log_warn(f"专辑失败: {err}")
        await service.reply(event, f"获取专辑失败：{err}")
    event.stop_event()


async def playlist(service: MusicService, event: AstrMessageEvent):
    """#ncm歌单 关键词：搜索歌单出候选列表，回复 #ncm听N 查看曲目"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*歌单\s+(.+)$")
    if not m:
        return
    kw = m.group(1).strip()

    user_key = service.user_key(event)
    try:
        pls = await ncmapi.search_playlists(kw, limit=10, user_key=user_key)
        if not pls:
            await service.reply(event, msg_no_playlist(kw))
            event.stop_event()
            return
        if len(pls) == 1:
            p = pls[0]
            songs = await ncmapi.playlist_tracks(p.get("id"), user_key=user_key)
            if songs:
                shown = songs[:LIST_PREVIEW_LIMIT]
                await service.list_to_session(
                    event,
                    fmt_playlist_title(p.get("name")),
                    shown,
                    tip=fmt_playlist_show_tip(len(songs), len(shown)),
                )
                event.stop_event()
                return
        await service.playlist_list_to_session(
            event, f"「{kw}」的歌单候选", pls, subtitle="同名/相似歌单较多时在此挑选"
        )
    except ApiError as err:
        service.log_warn(f"歌单失败: {err}")
        await service.reply(event, MSG_PLAYLIST_FAIL.format(err=err))
    event.stop_event()


async def new_song(service: MusicService, event: AstrMessageEvent):
    """#ncm新歌 [地区]：新歌速递（华语/欧美/日本/韩国）"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*新歌\s*(.*)$")
    if not m:
        return
    area = m.group(1).strip()

    area_id = NEW_SONG_AREAS.get(area, 0)
    label = area or "全部"
    try:
        songs = await ncmapi.new_songs(area=area_id, limit=30, user_key=service.user_key(event))
        if not songs:
            await service.reply(event, "暂无新歌数据")
            event.stop_event()
            return
        await service.list_to_session(event, f"新歌速递 · {label}", songs[:LIST_SHOW_LIMIT])
    except ApiError as err:
        service.log_warn(f"新歌失败: {err}")
        await service.reply(event, f"获取新歌失败：{err}")
    event.stop_event()


async def highquality(service: MusicService, event: AstrMessageEvent):
    """#ncm精品歌单 [分类]：精品歌单"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*精品歌单\s*(.*)$")
    if not m:
        return
    cat = m.group(1).strip()

    try:
        pls = await ncmapi.highquality_playlists(cat=cat, limit=20, user_key=service.user_key(event))
        if not pls:
            await service.reply(event, "暂无精品歌单数据")
            event.stop_event()
            return
        await service.playlist_list_to_session(
            event,
            f"精品歌单{f' · {cat}' if cat else ''}",
            pls,
            subtitle="精选歌单推荐",
        )
    except ApiError as err:
        service.log_warn(f"精品歌单失败: {err}")
        await service.reply(event, f"获取精品歌单失败：{err}")
    event.stop_event()


async def suggest(service: MusicService, event: AstrMessageEvent):
    """#ncm搜索建议 关键词：关键词补全"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*搜索建议\s+(.+)$")
    if not m:
        return
    kw = m.group(1).strip()

    try:
        suggests = await ncmapi.search_suggest(kw, user_key=service.user_key(event))
        if not suggests:
            await service.reply(event, "暂无补全建议")
        else:
            title = f"「{kw}」的搜索建议"
            items = [{"name": w} for w in suggests]
            await _reply_generic(service, event, title, items, subtitle="关键词补全")
    except ApiError as err:
        service.log_warn(f"搜索建议失败: {err}")
        await service.reply(event, f"获取搜索建议失败：{err}")
    event.stop_event()


async def banner(service: MusicService, event: AstrMessageEvent):
    """#ncmbanner：首页轮播"""
    if not service.cfg().get("enable", True):
        return
    try:
        banners = await ncmapi.banner(user_key=service.user_key(event))
        if not banners:
            await service.reply(event, "暂无轮播数据")
            event.stop_event()
            return
        title = "网易云首页轮播"
        items = [
            {
                "name": b.get("title") or b.get("typeTitle") or "(无标题)",
                "sub": b.get("url") or "",
            }
            for b in banners
        ]
        await _reply_generic(
            service,
            event,
            title,
            items,
            subtitle="App 首页 Banner（活动/专辑/歌单推广位）",
            tip="这是网易云 App 首页的轮播推广图；点击对应条目的链接即可在网页打开查看",
            text_tip="首页轮播推广位：复制条目链接到浏览器打开查看",
        )
    except ApiError as err:
        service.log_warn(f"banner 失败: {err}")
        await service.reply(event, f"获取轮播失败：{err}")
    event.stop_event()


async def catlist(service: MusicService, event: AstrMessageEvent):
    """#ncm歌单分类：歌单分类列表"""
    if not service.cfg().get("enable", True):
        return
    try:
        cats = await ncmapi.playlist_cats(user_key=service.user_key(event))
        if not cats:
            await service.reply(event, "暂无歌单分类数据")
            event.stop_event()
            return
        title = "歌单分类"
        tip = "发送 #ncm精品歌单 分类名 查看该分类精品歌单"
        items = [
            {"name": c.get("name") or "", "tag": f"{cardlib.fmt_count(c.get('count'))}"}
            for c in cats[:60]
        ]
        await _reply_generic(
            service, event, title, items, subtitle="歌单标签分类", tip=tip
        )
    except ApiError as err:
        service.log_warn(f"歌单分类失败: {err}")
        await service.reply(event, f"获取歌单分类失败：{err}")
    event.stop_event()


async def mv(service: MusicService, event: AstrMessageEvent):
    """#ncmMV [关键词]：先选歌（回复 #ncm听N）再显示 MV"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*MV\s*(.*)$")
    if not m:
        return
    await service.start_select(event, "mv", m.group(1).strip(), label="MV", verb="查看MV", user_key=service.user_key(event))
    event.stop_event()


async def simi_playlist(service: MusicService, event: AstrMessageEvent):
    """#ncm相似歌单 关键词|歌曲id：关键词走歌单检索，纯数字按歌曲 id 走 /simi/playlist"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*相似歌单\s+(.+)$")
    if not m:
        return
    kw = m.group(1).strip()

    user_key = service.user_key(event)
    try:
        if re.fullmatch(r"\d+", kw):
            # /simi/playlist 的参数必须是歌曲 id（传歌单 id/关键词返回空）
            pls = await ncmapi.simi_playlists(int(kw), limit=10, user_key=user_key)
        else:
            pls = await ncmapi.search_playlists(kw, limit=10, user_key=user_key)
        if not pls:
            await service.reply(event, f"没有搜到与「{kw}」相关的歌单")
            event.stop_event()
            return
        await service.playlist_list_to_session(
            event,
            f"与「{kw}」相似的歌单",
            pls,
            subtitle=TIP_PLAYLIST_TRACKS,
        )
    except ApiError as err:
        service.log_warn(f"相似歌单失败: {err}")
        await service.reply(event, f"获取相似歌单失败：{err}")
    event.stop_event()


async def related_playlists_cmd(service: MusicService, event: AstrMessageEvent):
    """#ncm相关歌单 歌单名|id：给出该歌单的相关推荐，回复 #ncm听N 展开曲目"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*相关歌单\s+(.+)$")
    if not m:
        return
    kw = m.group(1).strip()

    user_key = service.user_key(event)
    try:
        # /playlist/detail/rcmd/get 的参数必须是「歌单 id」（传歌曲 id 会 502/空），
        # 因此先按关键词或 ID 定位到具体歌单，再取它的相关推荐。
        pl = await service.resolve_playlist(kw, user_key)
        if not pl:
            await service.reply(event, f"没有搜到「{kw}」对应的歌单")
            event.stop_event()
            return
        title = pl.get("name") or kw
        pls = await ncmapi.related_playlists(pl.get("id"), limit=10, user_key=user_key)
        if not pls:
            await service.reply(event, f"「{title}」暂无相关歌单推荐")
            event.stop_event()
            return
        await service.playlist_list_to_session(
            event, f"与「{title}」相关的歌单", pls, subtitle=TIP_PLAYLIST_TRACKS
        )
    except ApiError as err:
        service.log_warn(f"相关歌单失败: {err}")
        await service.reply(event, f"获取相关歌单失败：{err}")
    event.stop_event()


async def toplist_artist(service: MusicService, event: AstrMessageEvent):
    """#ncm歌手榜：歌手榜"""
    if not service.cfg().get("enable", True):
        return
    try:
        artists = await ncmapi.toplist_artist(user_key=service.user_key(event))
        if not artists:
            await service.reply(event, "暂无歌手榜数据")
            event.stop_event()
            return
        title = "网易云歌手榜"
        items = [
            {"name": a.get("name") or "", "cover": a.get("cover") or ""}
            for a in artists[:15]
        ]
        await _reply_generic(
            service, event, title, items, subtitle="歌手排行榜", tip=TIP_ARTIST_SONGS
        )
    except ApiError as err:
        service.log_warn(f"歌手榜失败: {err}")
        await service.reply(event, f"获取歌手榜失败：{err}")
    event.stop_event()


async def album_newest(service: MusicService, event: AstrMessageEvent):
    """#ncm新碟：新碟上架"""
    if not service.cfg().get("enable", True):
        return
    try:
        albums = await ncmapi.album_newest(limit=10, user_key=service.user_key(event))
        if not albums:
            await service.reply(event, "暂无新碟数据")
            event.stop_event()
            return
        title = "新碟上架"
        items = [
            {
                "name": a.get("name") or "",
                "sub": a.get("artist") or "",
                "tag": f"{cardlib.fmt_count(a.get('size'))}首",
                "cover": a.get("cover") or "",
            }
            for a in albums
        ]
        await _reply_generic(
            service, event, title, items, subtitle="最新专辑", tip=TIP_ALBUM_SEARCH
        )
    except ApiError as err:
        service.log_warn(f"新碟失败: {err}")
        await service.reply(event, f"获取新碟失败：{err}")
    event.stop_event()


async def top_artists(service: MusicService, event: AstrMessageEvent):
    """#ncm热门歌手：热门歌手"""
    if not service.cfg().get("enable", True):
        return
    try:
        artists = await ncmapi.top_artists(limit=20, user_key=service.user_key(event))
        if not artists:
            await service.reply(event, "暂无热门歌手数据")
            event.stop_event()
            return
        title = "热门歌手"
        items = [{"name": a.get("name") or "", "cover": a.get("cover") or ""} for a in artists]
        await _reply_generic(
            service, event, title, items, subtitle="热门歌手", tip=TIP_ARTIST_SONGS
        )
    except ApiError as err:
        service.log_warn(f"热门歌手失败: {err}")
        await service.reply(event, f"获取热门歌手失败：{err}")
    event.stop_event()


async def top_album(service: MusicService, event: AstrMessageEvent):
    """#ncm新碟榜 [地区]：新碟排行（华语/欧美/日本/韩国）"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*新碟榜\s*(.*)$")
    if not m:
        return
    area = m.group(1).strip() or "ALL"
    area_map = {"华语": "ZH", "欧美": "EA", "韩国": "KR", "日本": "JP", "all": "ALL"}
    area_id = area_map.get(area, area)
    try:
        albums = await ncmapi.top_album(area=area_id, limit=10, user_key=service.user_key(event))
        if not albums:
            await service.reply(event, "暂无新碟榜数据")
            event.stop_event()
            return
        title = f"新碟榜 · {area if area != 'ALL' else '全部'}"
        items = [
            {
                "name": a.get("name") or "",
                "sub": a.get("artist") or "",
                "cover": a.get("cover") or "",
            }
            for a in albums
        ]
        await _reply_generic(
            service, event, title, items, subtitle="新碟排行榜", tip=TIP_ALBUM_SEARCH
        )
    except ApiError as err:
        service.log_warn(f"新碟榜失败: {err}")
        await service.reply(event, f"获取新碟榜失败：{err}")
    event.stop_event()


async def top_mv(service: MusicService, event: AstrMessageEvent):
    """#ncmMV榜：MV 排行"""
    if not service.cfg().get("enable", True):
        return
    try:
        mvs = await ncmapi.top_mv(limit=10, user_key=service.user_key(event))
        if not mvs:
            await service.reply(event, "暂无 MV 榜数据")
            event.stop_event()
            return
        title = "MV 排行"
        tip = "发送 #ncmMV 关键词 查看 MV 详情与链接"
        items = [
            {
                "name": mv.get("name") or "",
                "sub": mv.get("artist") or "",
                "tag": f"{cardlib.fmt_count(mv.get('playCount'))}播放",
                "cover": mv.get("cover") or "",
            }
            for mv in mvs
        ]
        await _reply_generic(
            service, event, title, items, subtitle="MV 排行榜", tip=tip
        )
    except ApiError as err:
        service.log_warn(f"MV榜失败: {err}")
        await service.reply(event, f"获取 MV 榜失败：{err}")
    event.stop_event()


async def dj_recommend(service: MusicService, event: AstrMessageEvent):
    """#ncm电台：电台推荐"""
    if not service.cfg().get("enable", True):
        return
    try:
        radios = await ncmapi.dj_recommend(limit=10, user_key=service.user_key(event))
        if not radios:
            await service.reply(event, "暂无电台推荐数据")
            event.stop_event()
            return
        title = "电台推荐"
        items = [
            {
                "name": r.get("name") or "",
                "sub": r.get("desc") or "",
                "tag": f"{cardlib.fmt_count(r.get('subCount'))}订阅"
                if r.get("subCount")
                else "",
                "cover": r.get("cover") or "",
            }
            for r in radios
        ]
        await _reply_generic(service, event, title, items, subtitle="推荐电台")
    except ApiError as err:
        service.log_warn(f"电台失败: {err}")
        await service.reply(event, f"获取电台推荐失败：{err}")
    event.stop_event()


async def top_playlist(service: MusicService, event: AstrMessageEvent):
    """#ncm歌单榜 [分类]：分类歌单榜"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*歌单榜\s*(.*)$")
    if not m:
        return
    cat = m.group(1).strip() or "全部"
    try:
        pls = await ncmapi.top_playlists(cat=cat, limit=20, user_key=service.user_key(event))
        if not pls:
            await service.reply(event, f"分类「{cat}」暂无歌单数据")
            event.stop_event()
            return
        await service.playlist_list_to_session(
            event, f"歌单榜 · {cat}", pls, subtitle="分类歌单排行榜"
        )
    except ApiError as err:
        service.log_warn(f"歌单榜失败: {err}")
        await service.reply(event, f"获取歌单榜失败：{err}")
    event.stop_event()


async def playlist_hot_tags(service: MusicService, event: AstrMessageEvent):
    """#ncm热门分类：热门歌单分类"""
    if not service.cfg().get("enable", True):
        return
    try:
        tags = await ncmapi.playlist_hot_tags(user_key=service.user_key(event))
        if not tags:
            await service.reply(event, "暂无热门分类数据")
            event.stop_event()
            return
        title = "热门歌单分类"
        tip = "发送 #ncm歌单榜 分类名 查看该分类歌单"
        items = [
            {
                "name": t.get("name") or "",
                "tag": f"{cardlib.fmt_count(t.get('usedCount'))}个歌单",
            }
            for t in tags
        ]
        await _reply_generic(service, event, title, items, subtitle="热门标签", tip=tip)
    except ApiError as err:
        service.log_warn(f"热门分类失败: {err}")
        await service.reply(event, f"获取热门分类失败：{err}")
    event.stop_event()


async def recommend(service: MusicService, event: AstrMessageEvent):
    """#ncm推荐：推荐歌单（需登录）"""
    if not service.cfg().get("enable", True):
        return
    try:
        pls = await ncmapi.recommend_playlists(limit=15, user_key=service.user_key(event))
        if not pls:
            await service.reply(event, "暂无推荐歌单")
            event.stop_event()
            return
        await service.playlist_list_to_session(
            event, "推荐歌单", pls, subtitle="猜你喜欢 · 每日推荐歌单"
        )
    except ApiError as err:
        service.log_warn(f"推荐失败: {err}")
        await service.reply(event, f"获取推荐失败：{err}\n可能需要 #ncm登录")
    event.stop_event()


async def random_song(service: MusicService, event: AstrMessageEvent):
    """#ncm来首歌：随机来一首（个人 FM，未登录退推荐新歌）"""
    if not service.cfg().get("enable", True):
        return
    user_key = service.user_key(event)
    try:
        try:
            songs = await ncmapi.personal_fm(user_key=user_key)
        except ApiError:
            songs = await ncmapi.recommend_newsong(limit=20, user_key=user_key)
        if not songs:
            await service.reply(event, "没有拿到推荐歌曲，请稍后再试")
            event.stop_event()
            return
        song = random.choice(songs)
        await service.play_song(event, song, user_key=user_key, source="推荐")
    except ApiError as err:
        service.log_warn(f"来首歌失败: {err}")
        await service.reply(event, f"随机点歌失败：{err}")
    event.stop_event()


async def daily(service: MusicService, event: AstrMessageEvent):
    """#ncm日推：每日推荐（需登录）"""
    if not service.cfg().get("enable", True):
        return
    user_key = service.user_key(event)
    try:
        songs = await ncmapi.daily_songs(user_key=user_key)
        if not songs:
            await service.reply(event, "今日暂无推荐（可能已获取过或没有听歌记录）")
            event.stop_event()
            return
        await service.list_to_session(event, "每日推荐", songs[:LIST_SHOW_LIMIT])
    except ApiError as err:
        service.log_warn(f"日推失败: {err}")
        await service.reply(event, f"获取每日推荐失败：{err}\n可能需要 #ncm登录")
    event.stop_event()


async def newsong_recommend(service: MusicService, event: AstrMessageEvent):
    """#ncm推荐新歌：推荐新歌"""
    if not service.cfg().get("enable", True):
        return
    try:
        songs = await ncmapi.recommend_newsong(limit=15, user_key=service.user_key(event))
        if not songs:
            await service.reply(event, "暂无推荐新歌")
            event.stop_event()
            return
        await service.list_to_session(event, "推荐新歌", songs)
    except ApiError as err:
        service.log_warn(f"推荐新歌失败: {err}")
        await service.reply(event, f"获取推荐新歌失败：{err}")
    event.stop_event()


ROUTES = [
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*排行\s*(.*)$", re.IGNORECASE),
        name="chart",
        doc="#ncm排行 [榜单名]：排行榜列表 / 查看具体榜单",
        run=chart,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*歌手\s+(.+)$", re.IGNORECASE),
        name="artist",
        doc="#ncm歌手 关键词：歌手热门歌曲",
        run=artist,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*专辑\s+(.+)$", re.IGNORECASE),
        name="album",
        doc="#ncm专辑 关键词：搜索专辑出候选列表，回复 #ncm听N 查看曲目",
        run=album,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*歌单\s+(.+)$", re.IGNORECASE),
        name="playlist",
        doc="#ncm歌单 关键词：搜索歌单出候选列表，回复 #ncm听N 查看曲目",
        run=playlist,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*新歌\s*(.*)$", re.IGNORECASE),
        name="new_song",
        doc="#ncm新歌 [地区]：新歌速递（华语/欧美/日本/韩国）",
        run=new_song,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*精品歌单\s*(.*)$", re.IGNORECASE),
        name="highquality",
        doc="#ncm精品歌单 [分类]：精品歌单",
        run=highquality,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*搜索建议\s+(.+)$", re.IGNORECASE),
        name="suggest",
        doc="#ncm搜索建议 关键词：关键词补全",
        run=suggest,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*banner$", re.IGNORECASE),
        name="banner",
        doc="#ncmbanner：首页轮播",
        run=banner,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*歌单分类$", re.IGNORECASE),
        name="catlist",
        doc="#ncm歌单分类：歌单分类列表",
        run=catlist,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*MV\s*(.*)$", re.IGNORECASE),
        name="mv",
        doc="#ncmMV [关键词]：先选歌（回复 #ncm听N）再显示 MV",
        run=mv,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*相似歌单\s+(.+)$", re.IGNORECASE),
        name="simi_playlist",
        doc="#ncm相似歌单 关键词|歌曲id：候选歌单列表，回复 #ncm听N 展开曲目",
        run=simi_playlist,
        # 必须高于 #ncm相似（^…相似\s*(.*)$ 会把「歌单 关键词」整段吃成关键词）
        priority=1,
    ),
    Route(
        pattern=re.compile(r"^#?(?:ncm|NCM)\s*相关歌单\s+(.+)$", re.IGNORECASE),
        name="related_playlists_cmd",
        doc="#ncm相关歌单 歌单名|id：给出该歌单的相关推荐，回复 #ncm听N 展开曲目",
        run=related_playlists_cmd,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*歌手榜$", re.IGNORECASE),
        name="toplist_artist",
        doc="#ncm歌手榜：歌手榜",
        run=toplist_artist,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*新碟$", re.IGNORECASE),
        name="album_newest",
        doc="#ncm新碟：新碟上架",
        run=album_newest,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*热门歌手$", re.IGNORECASE),
        name="top_artists",
        doc="#ncm热门歌手：热门歌手",
        run=top_artists,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*新碟榜\s*(.*)$", re.IGNORECASE),
        name="top_album",
        doc="#ncm新碟榜 [地区]：新碟排行（华语/欧美/日本/韩国）",
        run=top_album,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*MV榜$", re.IGNORECASE),
        name="top_mv",
        doc="#ncmMV榜：MV 排行",
        run=top_mv,
        # 必须高于 #ncmMV（^…MV\s*(.*)$ 会把「榜」吃成关键词并 stop_event）
        priority=1,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*电台$", re.IGNORECASE),
        name="dj_recommend",
        doc="#ncm电台：电台推荐",
        run=dj_recommend,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*歌单榜\s*(.*)$", re.IGNORECASE),
        name="top_playlist",
        doc="#ncm歌单榜 [分类]：分类歌单榜",
        run=top_playlist,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*热门分类$", re.IGNORECASE),
        name="playlist_hot_tags",
        doc="#ncm热门分类：热门歌单分类",
        run=playlist_hot_tags,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*推荐$", re.IGNORECASE),
        name="recommend",
        doc="#ncm推荐：推荐歌单（需登录）",
        run=recommend,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*(来首歌|随机|放一首|来一首)$", re.IGNORECASE),
        name="random_song",
        doc="#ncm来首歌：随机来一首（个人 FM，未登录退推荐新歌）",
        run=random_song,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*(日推|每日推荐)$", re.IGNORECASE),
        name="daily",
        doc="#ncm日推：每日推荐（需登录）",
        run=daily,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*推荐新歌$", re.IGNORECASE),
        name="newsong_recommend",
        doc="#ncm推荐新歌：推荐新歌",
        run=newsong_recommend,
    ),
]
