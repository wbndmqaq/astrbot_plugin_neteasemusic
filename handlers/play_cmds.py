from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent

if TYPE_CHECKING:
    from ..core.service import MusicService

try:
    from ..core import api as ncmapi
    from ..core import cards as cardlib
    from ..core.api import ApiError
    from ..core.delivery import deliver_song
    from ..core.service import PLAY_ALL_LIMIT, PLUGIN_DIR
except ImportError:
    from core import api as ncmapi
    from core import cards as cardlib
    from core.api import ApiError
    from core.delivery import deliver_song
    from core.service import PLAY_ALL_LIMIT, PLUGIN_DIR
from .base import Route


async def pick_song(service: MusicService, event: AstrMessageEvent):
    """#ncm点歌 关键词：搜索并列出歌曲，供会话内 #ncm听N 播放"""
    cfg = service.cfg()
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*点歌\s*(.+)$", song_request=True)
    if not m:
        return
    keyword = m.group(1).strip()

    if not keyword:
        await service.reply(event, "用法：#ncm点歌 关键词")
        event.stop_event()
        return
    try:
        await service.reply(event, f"正在搜索：{keyword}")
        page_size = min(int(cfg.get("maxList") or 10), 20)
        lst = await ncmapi.search(
            keyword, type_=1, limit=page_size, user_key=service.user_key(event)
        )
        if not lst:
            await service.reply(event, "没有搜到相关歌曲")
            event.stop_event()
            return
        await service.list_to_session(event, keyword, lst)
    except ApiError as err:
        service.log_warn(f"点歌失败: {err}")
        await service.reply(event, f"点歌失败：{err}")
    event.stop_event()


async def choose_song(service: MusicService, event: AstrMessageEvent):
    """#ncm听N / #听N：播放列表第 N 首；若会话有待办动作（#ncm歌词 等先选歌）则先执行该动作"""
    cfg = service.cfg()
    if not cfg.get("enable", True) or cfg.get("enableSongRequest") is False:
        return
    m = re.match(
        r"^#?(?:ncm|NCM)\s*听\s*([1-9][0-9]?)$|^#听\s*([1-9][0-9]?)$",
        event.message_str.strip(),
        re.IGNORECASE,
    )
    n = int(m.group(1) or m.group(2) or 0) if m else 0
    # 裸 #听N（无 ncm 前缀）仅由最近活跃的音乐插件响应，避免多插件同装时抢占顺序取决于加载顺序
    if m and m.group(2) and not await service.is_session_owner():
        return
    scope = service.scope(event)
    session = await cardlib.SessionStore.get(service.plugin, scope)
    if not session or not session.get("data"):
        return
    stype = session.get("type")
    if stype == "albumList":
        await service.expand_album(event, session, n)
        return
    if stype == "playlistList":
        await service.expand_playlist(event, session, n)
        return
    if stype != "songs":
        return
    songs = session.get("data") or []
    if n < 1 or n > len(songs):
        await service.reply(event, f"序号超出范围（1-{len(songs)}）")
        event.stop_event()
        return
    song = songs[n - 1]
    action = session.get("action") or "play"
    if action != "play":
        try:
            _s = dict(session)
            _s["action"] = "play"
            await cardlib.SessionStore.set(service.plugin, scope, _s)
        except Exception:
            pass
    user_key = service.user_key(event)
    try:
        if action == "lyric":
            await service.show_lyric(event, song, user_key)
        elif action == "lyric_word":
            await service.show_lyric_word(event, song, user_key)
        elif action == "comment":
            await service.show_comment(event, song, user_key)
        elif action == "simi":
            await service.show_simi(event, song, user_key)
        elif action == "mv":
            await service.show_mv(event, song, user_key)
        elif action == "like":
            await service.show_like(event, song, user_key, unlike=False)
        elif action == "unlike":
            await service.show_like(event, song, user_key, unlike=True)
        else:
            await service.play_song(event, song, user_key=user_key, source="点歌")
    except ApiError as err:
        service.log_warn(f"执行失败: {err}")
        await service.reply(event, f"操作失败：{err}")
    event.stop_event()


async def play_all(service: MusicService, event: AstrMessageEvent):
    """#ncm听所有：依次发送当前会话列表的全部歌曲（语音+文件，上限 30 首）"""
    cfg = service.cfg()
    if not cfg.get("enable", True) or cfg.get("enableSongRequest") is False:
        return
    if not re.match(
        r"^#?(?:ncm|NCM)\s*听\s*所有$|^#\s*听\s*所有$",
        event.message_str.strip(),
        re.IGNORECASE,
    ) or not is_plugin_session_active(service, event):
        return
    # 裸 #听所有（无 ncm 前缀）仅由最近活跃的音乐插件响应
    if not re.match(r"^#?(?:ncm|NCM)", event.message_str.strip(), re.IGNORECASE) and not await service.is_session_owner():
        return
    scope = service.scope(event)
    session = await cardlib.SessionStore.get(service.plugin, scope)
    if not session or session.get("type") != "songs" or not session.get("data"):
        return
    songs = session.get("data") or []
    batch = songs[:PLAY_ALL_LIMIT]
    title = session.get("keyword") or "当前列表"
    await service.reply(
        event,
        f"▶ 开始连播「{title}」共 {len(batch)} 首"
        + (
            f"（列表共 {len(songs)} 首，仅连播前 {PLAY_ALL_LIMIT} 首）"
            if len(songs) > len(batch)
            else ""
        )
        + "，逐首下载发送需要一些时间…",
    )
    ok = fail = 0
    user_key = service.user_key(event)
    for i, song in enumerate(batch):
        try:
            play = await service.resolve_play(song, cfg, user_key)
            if not play.get("url"):
                fail += 1
                service.log_warn(f"连播 {i + 1}/{len(batch)} 无播放链: {song.get('name')}")
                continue
            await deliver_song(
                service.plugin,
                event,
                song,
                play,
                cfg=cfg,
                plugin_dir=PLUGIN_DIR,
                options={"skipTextInfo": True, "skipNativeCard": True},
            )
            ok += 1
        except ApiError as err:
            fail += 1
            service.log_warn(f"连播 {i + 1}/{len(batch)} 失败: {err}")
        except Exception as err:
            fail += 1
            service.log_warn(
                f"连播 {i + 1}/{len(batch)} 失败: {type(err).__name__}: {err}"
            )
        if i < len(batch) - 1:
            await asyncio.sleep(1)
    await service.reply(event, f"连播完成：成功 {ok} 首，失败 {fail} 首")
    event.stop_event()


def is_plugin_session_active(service: MusicService, event: AstrMessageEvent) -> bool:
    return True


async def play_direct(service: MusicService, event: AstrMessageEvent):
    """#ncm播放 关键词：搜索并直接播放第一首"""
    m = service.check_cmd(event, r"^#?(?:ncm|NCM)\s*播放\s*(.+)$", song_request=True)
    if not m:
        return
    keyword = m.group(1).strip()

    if not keyword:
        await service.reply(event, "用法：#ncm播放 关键词")
        event.stop_event()
        return
    try:
        lst = await ncmapi.search(
            keyword, type_=1, limit=1, user_key=service.user_key(event)
        )
        if not lst:
            await service.reply(event, f"没有搜到「{keyword}」")
            event.stop_event()
            return
        await service.play_song(
            event, lst[0], user_key=service.user_key(event), source="搜索"
        )
    except ApiError as err:
        service.log_warn(f"播放失败: {err}")
        await service.reply(event, f"播放失败：{err}")
    event.stop_event()


ROUTES = [
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*点歌\s*(.+)$", re.IGNORECASE),
        name="pick_song",
        doc="#ncm点歌 关键词：搜索并列出歌曲，供会话内 #ncm听N 播放",
        run=pick_song,
    ),
    Route(
        pattern=re.compile(
            r"^#?(ncm|NCM)\s*听\s*([1-9][0-9]?)$|^#听\s*([1-9][0-9]?)$", re.IGNORECASE
        ),
        name="choose_song",
        doc="#ncm听N / #听N：播放列表第 N 首；若会话有待办动作（#ncm歌词 等先选歌）则先执行该动作",
        run=choose_song,
    ),
    Route(
        pattern=re.compile(
            r"^#?(?:ncm|NCM)\s*听\s*所有$|^#\s*听\s*所有$", re.IGNORECASE
        ),
        name="play_all",
        doc="#ncm听所有：依次发送当前会话列表的全部歌曲（语音+文件，上限 30 首）",
        run=play_all,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*播放\s*(.+)$", re.IGNORECASE),
        name="play_direct",
        doc="#ncm播放 关键词：搜索并直接播放第一首",
        run=play_direct,
    ),
]
