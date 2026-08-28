from __future__ import annotations

import random
import re
from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent

if TYPE_CHECKING:
    from ..core.service import MusicService

try:
    from ..core import api as ncmapi
    from ..core import cards as cardlib
    from ..core.api import ApiError
except ImportError:
    from core import api as ncmapi
    from core import cards as cardlib
    from core.api import ApiError
from .base import Route


async def get_lyric(service: MusicService, event: AstrMessageEvent):
    """#ncm歌词 [关键词]：先选歌（回复 #ncm听N）再显示歌词"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*歌词\s*(.*)$")
    if not m:
        return
    await service.start_select(
        event,
        "lyric",
        m.group(1).strip(),
        label="歌词",
        verb="查看歌词",
        user_key=service.user_key(event),
    )
    event.stop_event()


async def lyric_word(service: MusicService, event: AstrMessageEvent):
    """#ncm逐字歌词 [关键词]：先选歌（回复 #ncm听N）再显示逐字歌词"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*逐字歌词\s*(.*)$")
    if not m:
        return
    await service.start_select(
        event,
        "lyric_word",
        m.group(1).strip(),
        label="逐字歌词",
        verb="查看逐字歌词",
        user_key=service.user_key(event),
    )
    event.stop_event()


async def get_comment(service: MusicService, event: AstrMessageEvent):
    """#ncm评论 [关键词]：先选歌（回复 #ncm听N）再显示热评"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*评论\s*(.*)$")
    if not m:
        return
    await service.start_select(
        event,
        "comment",
        m.group(1).strip(),
        label="评论",
        verb="查看评论",
        user_key=service.user_key(event),
    )
    event.stop_event()


async def album_comment(service: MusicService, event: AstrMessageEvent):
    """#ncm专辑评论 关键词：专辑热评"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*专辑评论\s+(.+)$")
    if not m:
        return
    kw = m.group(1).strip()

    user_key = service.user_key(event)
    try:
        album = await service.resolve_album(kw, user_key)
        if not album:
            await service.reply(event, f"没有搜到专辑「{kw}」")
            event.stop_event()
            return
        comments = await ncmapi.comment_album(album["id"], limit=20, user_key=user_key)
        if not comments:
            await service.reply(event, "该专辑暂无评论")
            event.stop_event()
            return
        data = cardlib.build_comment_card_data(album, comments, total=len(comments))
        await service.reply_card_or_text(
            event,
            tpl_name="ncm-comment",
            data=data,
            format_text=lambda d: cardlib.format_comment_text(album, comments),
        )
    except ApiError as err:
        service.log_warn(f"专辑评论失败: {err}")
        await service.reply(event, f"获取专辑评论失败：{err}")
    event.stop_event()


async def playlist_comment(service: MusicService, event: AstrMessageEvent):
    """#ncm歌单评论 关键词：歌单热评"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*歌单评论\s+(.+)$")
    if not m:
        return
    kw = m.group(1).strip()

    user_key = service.user_key(event)
    try:
        pl = await service.resolve_playlist(kw, user_key)
        if not pl:
            await service.reply(event, f"没有搜到歌单「{kw}」")
            event.stop_event()
            return
        comments = await ncmapi.comment_playlist(pl["id"], limit=20, user_key=user_key)
        if not comments:
            await service.reply(event, "该歌单暂无评论")
            event.stop_event()
            return
        data = cardlib.build_comment_card_data(pl, comments, total=len(comments))
        await service.reply_card_or_text(
            event,
            tpl_name="ncm-comment",
            data=data,
            format_text=lambda d: cardlib.format_comment_text(pl, comments),
        )
    except ApiError as err:
        service.log_warn(f"歌单评论失败: {err}")
        await service.reply(event, f"获取歌单评论失败：{err}")
    event.stop_event()


async def simi(service: MusicService, event: AstrMessageEvent):
    """#ncm相似 [关键词]：先选歌（回复 #ncm听N）再显示相似歌曲"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*相似\s*(.*)$")
    if not m:
        return
    await service.start_select(
        event,
        "simi",
        m.group(1).strip(),
        label="相似",
        verb="查看相似歌曲",
        user_key=service.user_key(event),
    )
    event.stop_event()


async def like_list(service: MusicService, event: AstrMessageEvent):
    """#ncm喜欢：我喜欢的音乐（需登录）"""
    if not service.cfg().get("enable", True):
        return
    user_key = service.user_key(event)
    uid = await service.get_uid(user_key)
    if not uid:
        await service.reply(event, "需要登录后使用，请先 #ncm登录")
        event.stop_event()
        return
    try:
        songs = await ncmapi.likelist(uid, limit=30, user_key=user_key)
        if not songs:
            await service.reply(event, "我喜欢的音乐为空")
            event.stop_event()
            return
        await service.list_to_session(event, "我喜欢的音乐", songs)
    except ApiError as err:
        service.log_warn(f"喜欢列表失败: {err}")
        await service.reply(event, f"获取喜欢列表失败：{err}")
    event.stop_event()


async def user_record(service: MusicService, event: AstrMessageEvent):
    """#ncm听歌排行：本周听歌排行（需登录）"""
    if not service.cfg().get("enable", True):
        return
    user_key = service.user_key(event)
    uid = await service.get_uid(user_key)
    if not uid:
        await service.reply(event, "需要登录后使用，请先 #ncm登录")
        event.stop_event()
        return
    try:
        songs = await ncmapi.user_record(uid, type_=1, limit=30, user_key=user_key)
        if not songs:
            await service.reply(event, "本周暂无听歌记录")
            event.stop_event()
            return
        shown = []
        for s in songs:
            s2 = dict(s)
            if s2.get("playCount"):
                s2["duration"] = f"{s2['duration']} · 听{s2['playCount']}次"
            shown.append(s2)
        await service.list_to_session(event, "本周听歌排行", shown)
    except ApiError as err:
        service.log_warn(f"听歌排行失败: {err}")
        await service.reply(event, f"获取听歌排行失败：{err}")
    event.stop_event()


async def daily_signin(service: MusicService, event: AstrMessageEvent):
    """#ncm签到：每日签到领经验（需登录/主人）"""
    if not service.cfg().get("enable", True):
        return
    user_key = service.user_key(event)
    try:
        body = await ncmapi.daily_signin(type_=0, user_key=user_key)
        d = body.get("android") if isinstance(body.get("android"), dict) else body
        code = d.get("code") if isinstance(d, dict) else None
        if code == 200:
            point = d.get("point") if isinstance(d, dict) else None
            await service.reply(
                event, "✅ 签到成功" + (f"，+{point} 经验" if point else "")
            )
        elif code == -2:
            await service.reply(event, "今日已签到过啦")
        else:
            await service.reply(
                event,
                f"签到失败：{d.get('message') if isinstance(d, dict) else '未知错误'}",
            )
    except ApiError as err:
        service.log_warn(f"签到失败: {err}")
        await service.reply(event, f"签到失败：{err}\n需要先 #ncm登录")
    event.stop_event()


async def user_cloud(service: MusicService, event: AstrMessageEvent):
    """#ncm云盘：我的云盘歌曲（需登录/主人）"""
    if not service.cfg().get("enable", True):
        return
    user_key = service.user_key(event)
    try:
        songs = await ncmapi.user_cloud(limit=30, user_key=user_key)
        if not songs:
            await service.reply(event, "云盘暂无歌曲（或账号未开通云盘）")
            event.stop_event()
            return
        await service.list_to_session(event, "我的云盘", songs[:20])
    except ApiError as err:
        service.log_warn(f"云盘失败: {err}")
        await service.reply(event, f"获取云盘失败：{err}\n需要先 #ncm登录")
    except Exception as err:
        service.log_warn(f"云盘异常: {type(err).__name__}: {err}")
        await service.reply(event, f"获取云盘失败：{err}\n需要先 #ncm登录")
    event.stop_event()


async def recent_song(service: MusicService, event: AstrMessageEvent):
    """#ncm最近：最近播放歌曲（需登录/主人）"""
    if not service.cfg().get("enable", True):
        return
    user_key = service.user_key(event)
    if not service.has_cookie():
        await service.reply(event, "需要登录后使用，请先 #ncm登录")
        event.stop_event()
        return
    try:
        songs = await ncmapi.record_recent_song(limit=300, user_key=user_key)
        if not songs:
            await service.reply(event, "暂无最近播放记录")
            event.stop_event()
            return
        shown = random.sample(songs, min(30, len(songs)))
        shown.sort(key=lambda s: -(s.get("playTime") or 0))
        cleaned = []
        for s in shown:
            s2 = dict(s)
            if s2.get("playTime"):
                s2["duration"] = (
                    f"{s2['duration']} · {cardlib.fmt_time_ago(s2['playTime'])}"
                )
            cleaned.append(s2)
        await service.list_to_session(event, "最近播放", cleaned)
    except ApiError as err:
        service.log_warn(f"最近播放失败: {err}")
        await service.reply(event, f"获取最近播放失败：{err}\n需要先 #ncm登录")
    event.stop_event()


async def my_playlist(service: MusicService, event: AstrMessageEvent):
    """#ncm我的歌单：我创建/收藏的歌单（需登录/主人）"""
    if not service.cfg().get("enable", True):
        return
    user_key = service.user_key(event)
    uid = await service.get_uid(user_key)
    if not uid:
        await service.reply(event, "需要登录后使用，请先 #ncm登录")
        event.stop_event()
        return
    try:
        pls = await ncmapi.user_playlist(uid, limit=30, user_key=user_key)
        if not pls:
            await service.reply(event, "暂无歌单")
            event.stop_event()
            return
        await service.playlist_list_to_session(
            event, "我的歌单", pls, subtitle="我创建/收藏的歌单"
        )
    except ApiError as err:
        service.log_warn(f"我的歌单失败: {err}")
        await service.reply(event, f"获取歌单失败：{err}")
    event.stop_event()


async def like_toggle(service: MusicService, event: AstrMessageEvent):
    """#ncm红心 [关键词]：先选歌（回复 #ncm听N）再红心/取消红心（需登录/主人）"""
    if not service.cfg().get("enable", True):
        return
    m = re.match(r"^#?(?:ncm|NCM)\s*红心\s*(.*)$", event.message_str.strip(), re.IGNORECASE)
    if not m:
        return
    await service.start_select(
        event,
        "like",
        m.group(1).strip(),
        label="红心",
        verb="红心",
        user_key=service.user_key(event),
    )
    event.stop_event()


async def unlike(service: MusicService, event: AstrMessageEvent):
    """#ncm取消红心 [关键词]：先选歌（回复 #ncm听N）再取消红心（需登录/主人）"""
    if not service.cfg().get("enable", True):
        return
    m = re.match(
        r"^#?(?:ncm|NCM)\s*取消红心\s*(.*)$", event.message_str.strip(), re.IGNORECASE
    )
    if not m:
        return
    await service.start_select(
        event,
        "unlike",
        m.group(1).strip(),
        label="取消红心",
        verb="取消红心",
        user_key=service.user_key(event),
    )
    event.stop_event()


async def history_daily(service: MusicService, event: AstrMessageEvent):
    """#ncm历史日推：历史每日推荐（需登录/主人）"""
    if not service.cfg().get("enable", True):
        return
    user_key = service.user_key(event)
    if not service.has_cookie():
        await service.reply(event, "需要登录后使用，请先 #ncm登录")
        event.stop_event()
        return
    try:
        songs = await ncmapi.history_recommend_songs(user_key=user_key)
        if not songs:
            await service.reply(event, "暂无历史推荐记录")
            event.stop_event()
            return
        await service.list_to_session(event, "历史每日推荐", songs[:20])
    except ApiError as err:
        service.log_warn(f"历史日推失败: {err}")
        await service.reply(event, f"获取历史日推失败：{err}\n需要先 #ncm登录")
    event.stop_event()


ROUTES = [
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*歌词\s*(.*)$", re.IGNORECASE),
        name="get_lyric",
        doc="#ncm歌词 [关键词]：先选歌（回复 #ncm听N）再显示歌词",
        run=get_lyric,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*逐字歌词\s*(.*)$", re.IGNORECASE),
        name="lyric_word",
        doc="#ncm逐字歌词 [关键词]：先选歌（回复 #ncm听N）再显示逐字歌词",
        run=lyric_word,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*评论\s*(.*)$", re.IGNORECASE),
        name="get_comment",
        doc="#ncm评论 [关键词]：先选歌（回复 #ncm听N）再显示热评",
        run=get_comment,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*专辑评论\s+(.+)$", re.IGNORECASE),
        name="album_comment",
        doc="#ncm专辑评论 关键词：专辑热评",
        run=album_comment,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*歌单评论\s+(.+)$", re.IGNORECASE),
        name="playlist_comment",
        doc="#ncm歌单评论 关键词：歌单热评",
        run=playlist_comment,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*相似\s*(.*)$", re.IGNORECASE),
        name="simi",
        doc="#ncm相似 [关键词]：先选歌（回复 #ncm听N）再显示相似歌曲",
        run=simi,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*喜欢$", re.IGNORECASE),
        name="like_list",
        doc="#ncm喜欢：我喜欢的音乐（需登录）",
        run=like_list,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*听歌排行$", re.IGNORECASE),
        name="user_record",
        doc="#ncm听歌排行：本周听歌排行（需登录）",
        run=user_record,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*签到$", re.IGNORECASE),
        name="daily_signin",
        doc="#ncm签到：每日签到领经验（需登录/主人）",
        run=daily_signin,
        admin=True,
        priority=6,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*云盘$", re.IGNORECASE),
        name="user_cloud",
        doc="#ncm云盘：我的云盘歌曲（需登录/主人）",
        run=user_cloud,
        admin=True,
        priority=6,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*最近$", re.IGNORECASE),
        name="recent_song",
        doc="#ncm最近：最近播放歌曲（需登录/主人）",
        run=recent_song,
        admin=True,
        priority=6,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*我的歌单$", re.IGNORECASE),
        name="my_playlist",
        doc="#ncm我的歌单：我创建/收藏的歌单（需登录/主人）",
        run=my_playlist,
        admin=True,
        priority=6,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*红心\s*(.*)$", re.IGNORECASE),
        name="like_toggle",
        doc="#ncm红心 [关键词]：先选歌（回复 #ncm听N）再红心/取消红心（需登录/主人）",
        run=like_toggle,
        admin=True,
        priority=6,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*取消红心\s*(.*)$", re.IGNORECASE),
        name="unlike",
        doc="#ncm取消红心 [关键词]：先选歌（回复 #ncm听N）再取消红心（需登录/主人）",
        run=unlike,
        admin=True,
        priority=6,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*历史日推$", re.IGNORECASE),
        name="history_daily",
        doc="#ncm历史日推：历史每日推荐（需登录/主人）",
        run=history_daily,
        admin=True,
        priority=6,
    ),
]
