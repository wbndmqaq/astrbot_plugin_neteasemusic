"""网易云分享链接 / 卡片的自动解析流程（从 MusicService 整块搬出，行为等价）。

``MusicService.handle_resolve`` 保持签名不变，改为薄委托；内部分成
「歌单链接 / 专辑链接 / 单曲链接 / 关键词兜底」四个有序私有步骤，
调用顺序与分支结果与原实现完全一致。
"""

from __future__ import annotations

import re

from astrbot.api.event import AstrMessageEvent

from . import api as ncmapi
from .api import ApiError
from .lists import LIST_PREVIEW_LIMIT

# 链接解析用到的分享域名（仅这些域名存在时才做关键词兜底搜索）
_NCM_LINK_RE = r"music\.163\.com|163music\.com|163cn\.tv|y\.music\.163\.com"


async def _resolve_playlist(service, event: AstrMessageEvent, text: str, user_key: str):
    """歌单链接 → 曲目列表；非歌单链接返回 None（继续尝试其它形态）。"""
    m = re.search(r"playlist\?(?:[^&\s]*&)*id=(\d+)|playlist/(\d+)", text)
    if not m:
        return None
    pid = int(m.group(1) or m.group(2))
    songs = await ncmapi.playlist_tracks(pid, user_key=user_key)
    if not songs:
        await service.reply(event, "歌单暂无曲目或不存在")
        return True
    shown = songs[:LIST_PREVIEW_LIMIT]
    await service.list_to_session(
        event,
        "链接解析 · 歌单",
        shown,
        tip=f"歌单共 {len(songs)} 首，显示前 {len(shown)} 首",
    )
    return True


async def _resolve_album(service, event: AstrMessageEvent, text: str, user_key: str):
    """专辑链接 → 曲目列表；非专辑链接返回 None。"""
    m = re.search(r"album\?(?:[^&\s]*&)*id=(\d+)|album/(\d+)", text)
    if not m:
        return None
    aid = int(m.group(1) or m.group(2))
    album_info, songs = await ncmapi.album_detail(aid, user_key=user_key)
    if not songs:
        await service.reply(event, "专辑暂无曲目或不存在")
        return True
    info = album_info or {}
    await service.list_to_session(
        event,
        f"链接解析 · {info.get('name') or '专辑'}",
        songs[:LIST_PREVIEW_LIMIT],
    )
    return True


async def _resolve_song(service, event: AstrMessageEvent, text: str, user_key: str):
    """单曲链接 → 直接播放；非单曲链接返回 None。"""
    m = re.search(r"song\?(?:[^&\s]*&)*id=(\d+)|song/(\d+)", text)
    if not m:
        return None
    sid = int(m.group(1) or m.group(2))
    lst = await ncmapi.song_detail([sid], user_key=user_key)
    if not lst:
        await service.reply(event, "歌曲不存在或无版权")
        return True
    await service.play_song(event, lst[0], user_key=user_key, source="链接解析")
    return True


async def _resolve_by_keyword(
    service, event: AstrMessageEvent, text: str, user_key: str
) -> bool:
    """链接之外的分享文案 → 关键词兜底搜索（首个命中直接播放）。

    仅在文本里确实存在网易云分享域名（链接/卡片）时才做关键词兜底搜索。
    避免只含「网易云音乐」等字样、但没有真实分享链接的消息（例如登录提示文案）
    被误判为点歌请求而自动发送一首歌。
    """
    if not re.search(_NCM_LINK_RE, text, re.IGNORECASE):
        return False
    kw = re.sub(r"https?://\S+|\[CQ:[^\]]*\]", "", text).strip()
    kw = re.sub(
        r"music\.163\.com|163music\.com|网易云音乐|分享|歌曲|链接",
        "",
        kw,
        flags=re.IGNORECASE,
    ).strip()
    if len(kw) >= 2:
        lst = await ncmapi.search(kw, type_=1, limit=1, user_key=user_key)
        if lst:
            await service.play_song(event, lst[0], user_key=user_key, source="链接解析")
            return True
    return False


async def handle_resolve(service, event: AstrMessageEvent, text: str) -> bool:
    """解析分享链接/卡片；返回是否已处理（已处理时调用方 stop_event）。"""
    user_key = service.user_key(event)
    try:
        expanded = await ncmapi.expand_short_links(text)
        # 展开成功时短链已被替换为完整链接（music.163.com/...）；文本里仍有
        # 163cn.tv 链接即「展开失败」（超时/异常/短链本身失效）——此时若其余形态
        # 也解析不出内容，必须回一句，否则只发短链的消息会零输出、落回 LLM 管线。
        short_link_unexpanded = bool(ncmapi.SHORT_LINK_RE.search(str(expanded or "")))
        text = expanded
        if await _resolve_playlist(service, event, text, user_key):
            return True
        if await _resolve_album(service, event, text, user_key):
            return True
        if await _resolve_song(service, event, text, user_key):
            return True
        if await _resolve_by_keyword(service, event, text, user_key):
            return True
        if short_link_unexpanded:
            service.log_warn(f"短链解析失败（展开超时或链接无效）：{text[:120]}")
            await service.reply(event, "短链解析失败，请发完整链接")
            return True
        return False
    except ApiError as err:
        service.log_warn(f"解析失败: {err}")
        await service.reply(event, f"解析失败：{err}")
        return True
