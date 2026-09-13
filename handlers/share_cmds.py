from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent, filter

if TYPE_CHECKING:
    from ..core.service import MusicService

from ..core.service import (
    collect_message_text,
    is_ncm_message,
    is_plugin_command_msg,
)
from .base import Route

# 前置子串判断用的网易云域名/关键字（比正则便宜）：
# 文本里一个都不含且消息链里没有 Json 分享卡片时，直接返回，避免每条群消息都走正则与文本采集。
# 覆盖 is_ncm_message 的全部匹配目标（含 y.music.163.com → music.163.com）。
_NCM_TEXT_HINTS = ("music.163.com", "163music.com", "163cn.tv", "网易云音乐", "cloudmusic")


async def resolve(service: MusicService, event: AstrMessageEvent):
    """自动解析：识别网易云分享卡片/链接（单曲/歌单/专辑）。"""
    cfg = service.cfg()
    if not cfg.get("enable", True) or cfg.get("enableResolve") is False:
        return
    msg_str = str(event.message_str or "")
    # host 大小写不敏感（MUSIC.163.COM 也是合法链接），故按小写比对
    if not any(hint in msg_str.lower() for hint in _NCM_TEXT_HINTS):
        # OneBot json 分享卡片不会写入 message_str，此时才需要扫描消息链
        chain = getattr(getattr(event, "message_obj", None), "message", None) or []
        if not any(type(seg).__name__ == "Json" for seg in chain):
            return
    text = collect_message_text(event)
    if not is_ncm_message(text):
        return
    if is_plugin_command_msg(msg_str):
        return
    handled = await service.handle_resolve(event, text)
    if handled:
        event.stop_event()


ROUTES = [
    Route(
        pattern=None,
        name="resolve",
        doc="自动解析：识别网易云分享卡片/链接（单曲/歌单/专辑）",
        run=resolve,
        priority=5,
        event_message_type=filter.EventMessageType.ALL,
    ),
]
