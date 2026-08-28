from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent

if TYPE_CHECKING:
    from ..core.service import MusicService

try:
    from ..core import api as ncmapi
    from ..core import cards as cardlib
    from ..core.api import ApiError
    from ..core.quality import QUALITY_LABEL
    from ..core.service import PLUGIN_DIR
except ImportError:
    from core import api as ncmapi
    from core import cards as cardlib
    from core.api import ApiError
    from core.quality import QUALITY_LABEL
    from core.service import PLUGIN_DIR
from .base import Route


async def hot_search(service: MusicService, event: AstrMessageEvent):
    """#ncm热搜：热搜榜"""
    if not service.cfg().get("enable", True):
        return
    try:
        items = await ncmapi.hot_search(user_key=service.user_key(event))
        if not items:
            await service.reply(event, "暂无热搜数据")
            event.stop_event()
            return
        data = cardlib.build_hot_card_data(items)
        await service.reply_card_or_text(
            event,
            tpl_name="ncm-hot",
            data=data,
            format_text=lambda d: cardlib.format_hot_text(items),
        )
    except ApiError as err:
        service.log_warn(f"热搜失败: {err}")
        await service.reply(event, f"获取热搜失败：{err}")
    event.stop_event()


async def help_cmd(service: MusicService, event: AstrMessageEvent):
    """#ncm帮助：帮助卡片（指令一览）"""
    if not service.cfg().get("enable", True):
        return
    try:
        version = "?"
        try:
            import yaml

            with open(
                os.path.join(PLUGIN_DIR, "metadata.yaml"), "r", encoding="utf-8"
            ) as f:
                _meta = yaml.safe_load(f) or {}
            version = str(_meta.get("version", "?")).lstrip("v")
        except Exception:
            pass
        data = cardlib.build_help_card_data(version, service.cfg())
        await service.reply_card_or_text(
            event,
            tpl_name="ncm-help",
            data=data,
            format_text=lambda d: cardlib.format_help_text(service.cfg(), version),
        )
    except Exception as err:
        service.log_warn(f"帮助失败: {err}")
        await service.reply(event, cardlib.format_help_text(service.cfg()))
    event.stop_event()


async def settings(service: MusicService, event: AstrMessageEvent):
    """#ncm设置：设置面板（登录态/音质/开关/脱敏 API）"""
    cfg = service.cfg()
    user_key = service.user_key(event)
    try:
        uid = await service.get_uid(user_key)
    except Exception:
        uid = ""
    data = cardlib.build_settings_card_data(cfg, uid)
    await service.reply_card_or_text(
        event,
        tpl_name="ncm-settings",
        data=data,
        format_text=lambda d: cardlib.format_settings_text(cfg, uid),
    )
    event.stop_event()


async def quality_cmd(service: MusicService, event: AstrMessageEvent):
    """#ncm音质 <档位>：修改音质档位"""
    m = re.match(
        r"^#?(?:ncm|NCM)\s*音质\s*(.+)$", event.message_str.strip(), re.IGNORECASE
    )
    q = (m.group(1).strip().lower() if m else "").strip()
    if q not in QUALITY_LABEL:
        await service.reply(
            event, f"音质档位无效。可选：{' / '.join(QUALITY_LABEL.keys())}"
        )
        event.stop_event()
        return
    service.plugin.config["quality"] = q
    service.plugin.config.save_config()
    await service.reply(event, f"已设置音质：{QUALITY_LABEL.get(q, q)}")
    event.stop_event()


async def api_cmd(service: MusicService, event: AstrMessageEvent):
    """#ncm api <地址>：修改 API 地址"""
    m = re.match(
        r"^#?(?:ncm|NCM)\s*api\s*(https?://\S+)$",
        event.message_str.strip(),
        re.IGNORECASE,
    )
    url = m.group(1).strip().rstrip("/") if m else ""
    service.plugin.config["apiBase"] = url
    service.plugin.config.save_config()
    await service.reply(event, f"已设置 API 地址：{url}")
    event.stop_event()


async def toggle_cmd(service: MusicService, event: AstrMessageEvent):
    """#ncm 开启/关闭 点歌|解析：功能开关"""
    m = re.match(
        r"^#?(?:ncm|NCM)\s*(开启|关闭)(点歌|解析)$",
        event.message_str.strip(),
        re.IGNORECASE,
    )
    on = (m.group(1) if m else "") == "开启"
    what = (m.group(2) if m else "") or ""
    if what == "点歌":
        service.plugin.config["enableSongRequest"] = on
    elif what == "解析":
        service.plugin.config["enableResolve"] = on
    service.plugin.config.save_config()
    await service.reply(event, f"已{'开启' if on else '关闭'}{what}功能")
    event.stop_event()


async def api_test(service: MusicService, event: AstrMessageEvent):
    """#ncm测试：测试 API 连通性"""
    cfg = service.cfg()
    base = str(cfg.get("apiBase") or "")
    if not base:
        await service.reply(event, "⚠ API 未配置，请使用 #ncm api <地址> 配置")
        event.stop_event()
        return
    try:
        lst = await ncmapi.search("测试", type_=1, limit=1)
        masked = cardlib.mask_api_base(base)
        await service.reply(
            event, f"✅ API 连通正常：{masked}\n搜索结果 {len(lst)} 条"
        )
    except ApiError as e:
        await service.reply(event, f"❌ API 连接失败：{e}")
    event.stop_event()


ROUTES = [
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*热搜$", re.IGNORECASE),
        name="hot_search",
        doc="#ncm热搜：热搜榜",
        run=hot_search,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*(help|帮助|菜单)$", re.IGNORECASE),
        name="help",
        doc="#ncm帮助：帮助卡片（指令一览）",
        run=help_cmd,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm设置|ncm配置|网易云设置)$", re.IGNORECASE),
        name="settings",
        doc="#ncm设置：设置面板（登录态/音质/开关/脱敏 API）",
        run=settings,
        admin=True,
        priority=6,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*音质\s*(.+)$", re.IGNORECASE),
        name="quality_cmd",
        doc="#ncm音质 <档位>：修改音质档位",
        run=quality_cmd,
        admin=True,
        priority=6,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm|NCM)\s*api\s*(https?://\S+)$", re.IGNORECASE),
        name="api_cmd",
        doc="#ncm api <地址>：修改 API 地址",
        run=api_cmd,
        admin=True,
    ),
    Route(
        pattern=re.compile(
            r"^#?(ncm|NCM)\s*(开启|关闭)(点歌|解析)$", re.IGNORECASE
        ),
        name="toggle_cmd",
        doc="#ncm 开启/关闭 点歌|解析：功能开关",
        run=toggle_cmd,
        admin=True,
    ),
    Route(
        pattern=re.compile(r"^#?(ncm测试|网易云测试)$", re.IGNORECASE),
        name="api_test",
        doc="#ncm测试：测试 API 连通性",
        run=api_test,
        admin=True,
        priority=6,
    ),
]
