"""网易云音乐插件 —— AstrBot 音乐点歌/解析/探索/账号管理插件（模块化架构）。

架构：
    main.py            Star 插件主入口，生命周期管理与路由安装
    core/              业务服务层（MusicService、消息采集、状态管理）
    handlers/          声明式指令路由表（play/explore/detail/auth/system/share）
    api.py             网易云音乐 API 客户端
    cards.py           卡片数据构造与格式化
    delivery.py        音频下载、转码与分发交付
    render.py          Playwright HTML 渲染引擎
    tpl_adapter.py     模板适配器
"""

from __future__ import annotations

from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star

try:
    from .core.service import MusicService
    from .handlers import ALL_ROUTES
    from .handlers import install as install_routes
except ImportError:
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from core.service import MusicService
    from handlers import ALL_ROUTES
    from handlers import install as install_routes

PLUGIN_NAME = "astrbot_plugin_neteasemusic"


class NeteaseMusicPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.service = MusicService(self)

    # 兼容 delivery / cards 等老模块对 plugin 方法的调用
    def _cfg(self) -> dict:
        return self.service.cfg()

    def _log_warn(self, msg: str):
        self.service.log_warn(msg)

    def _log_info(self, msg: str):
        self.service.log_info(msg)

    def _plain(self, text: str):
        return self.service.plain(text)

    async def _send_chain(self, event, *components):
        await self.service.send_chain(event, *components)

    async def _reply(self, event, text: str):
        await self.service.reply(event, text)

    async def terminate(self):
        """插件卸载/重载时清理轮询等任务。"""
        await self.service.terminate()


# 安装全部声明式路由（handlers/ 目录按业务域维护）
_installed = install_routes(NeteaseMusicPlugin, filter, __name__, ALL_ROUTES)
logger.info(f"[neteasemusic] 插件已加载，共注册 {_installed} 条指令路由")
