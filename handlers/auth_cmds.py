from __future__ import annotations

import re
from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image

if TYPE_CHECKING:
    from ..core.service import MusicService

try:
    from ..core import api as ncmapi
    from ..core.api import ApiError
except ImportError:
    from core import api as ncmapi
    from core.api import ApiError
from .base import Route


async def start_qr_login(service: MusicService, event: AstrMessageEvent):
    """#ncm登录：扫码登录（二维码轮询自动写入 Cookie，仅主人）"""
    cfg = service.cfg()
    if not cfg.get("enable", True):
        return
    if cfg.get("qrLoginEnable") is False:
        await service.reply(event, "扫码登录已在配置中关闭")
        event.stop_event()
        return
    user_key = service.user_key(event)
    service.stop_poll(user_key)
    try:
        await service.reply(event, "正在获取网易云登录二维码…")
        key = await ncmapi.qr_key(user_key=user_key)
        if not key:
            await service.reply(event, "获取二维码失败：无法获取 unikey，请检查 API 服务")
            event.stop_event()
            return
        info = await ncmapi.qr_create(key, user_key=user_key)
        qrurl = info.get("qrurl") or ""
        qrimg = info.get("qrimg") or ""
        tip_text = "请使用网易云音乐 App 扫码登录\n二维码约 5 分钟内有效"
        qr_path = await service.save_qr_image(qrimg) if qrimg else None
        img_sent = False
        if qr_path:
            try:
                await service.send_chain(
                    event, Image.fromFileSystem(qr_path), service.plain(tip_text)
                )
                img_sent = True
            except Exception:
                pass
            import asyncio

            asyncio.get_running_loop().call_later(
                120, lambda: service.safe_unlink(qr_path)
            )
        if not img_sent:
            await service.reply(
                event, tip_text + (f"\n或打开链接扫码：{qrurl}" if qrurl else "")
            )
        service.start_poll(event, key, 300)
    except ApiError as err:
        await service.reply(event, f"扫码登录失败：{err}")
    event.stop_event()


async def login_status_cmd(service: MusicService, event: AstrMessageEvent):
    """#ncm状态 / #ncms：查看登录状态"""
    if not service.cfg().get("enable", True):
        return
    await service.send_status(event, service.user_key(event))
    event.stop_event()


async def logout(service: MusicService, event: AstrMessageEvent):
    """#ncm登出：登出并清除本地 Cookie（仅主人）"""
    user_key = service.user_key(event)
    try:
        try:
            await ncmapi.logout(user_key=user_key)
        except ApiError:
            pass
        if service.plugin.config.get("defaultCookie"):
            service.plugin.config["defaultCookie"] = ""
            service.plugin.config["defaultUid"] = ""
            service.plugin.config.save_config()
            await service.reply(event, "已登出网易云账号，并清除插件配置中的默认 Cookie")
        else:
            await service.reply(event, "已登出网易云账号")
    except Exception as err:
        await service.reply(event, f"登出失败：{err}")
    event.stop_event()


ROUTES = [
    Route(
        pattern=re.compile(
            r"^#?(ncm登录|ncm扫码登录|网易云登录|网易云扫码登录)$", re.IGNORECASE
        ),
        name="start_qr_login",
        doc="#ncm登录：扫码登录（二维码轮询自动写入 Cookie，仅主人）",
        run=start_qr_login,
        admin=True,
        priority=6,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm状态|ncm登录状态|ncms)$", re.IGNORECASE),
        name="login_status_cmd",
        doc="#ncm状态 / #ncms：查看登录状态",
        run=login_status_cmd,
        priority=6,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm登出|ncm注销|ncm解绑)$", re.IGNORECASE),
        name="logout",
        doc="#ncm登出：登出并清除本地 Cookie（仅主人）",
        run=logout,
        admin=True,
        priority=6,
    ),
]
