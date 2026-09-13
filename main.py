"""网易云音乐插件 —— AstrBot 音乐点歌/解析/探索/账号管理插件（模块化架构）。

架构：
    main.py            Star 插件主入口，生命周期管理与路由安装
    core/              业务服务层（MusicService、消息采集、状态管理）
    handlers/          声明式指令路由表（play/explore/detail/auth/system/share）

core/ 内部模块：
    core/service.py    MusicService：服务门面（会话归属、配置写盘、卡片渲染、歌词解析）
    core/api/          api-enhanced 客户端封装（_core HTTP 会话与错误映射，
                       _song/_search/_playlist/_artist_mv/_comment/_account/
                       _user/_resolve_link/_normalize 按域拆分端点与归一化）
    core/cards.py      会话存储、卡片数据构造与纯文本格式化
    core/delivery.py   音频下载、转码与多平台双通道投递
    core/login.py      扫码登录与二维码状态轮询
    core/lists.py      会话列表 → 选歌 / #ncm听N 展开流程
    core/panels.py     登录状态卡片数据构造与发送
    core/resolve.py    分享链接/卡片自动解析
    core/messages.py   跨模块复用的用户可见文案常量
    core/quality.py    音质档位常量与标签映射
    core/render.py     Playwright HTML 渲染引擎
    core/help_data.py  帮助指令清单（纯数据表，卡片与纯文本共用）
"""

from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star

from .core.service import MusicService
from .handlers import ALL_ROUTES
from .handlers import install as install_routes


class NeteaseMusicPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.service = MusicService(self)

    # delivery / cards 等模块以 plugin._xxx 的形式调用辅助方法，实现在 service 上
    def _log_warn(self, msg: str):
        self.service.log_warn(msg)

    def _plain(self, text: str):
        return self.service.plain(text)

    # 仅供 core/delivery.py 使用的发送门面：**只在消息含媒体组件时**于失败处抛异常。
    # 投递层靠这个异常触发降级链（语音失败退回 Record 组件、文件失败用 ffmpeg 压成紧凑
    # mp3 重试）。纯文案（Plain）只是附带信息，失败不该中断投递——
    # 抛出去会让用户连音频带回复都拿不到。
    async def _send_chain(self, event, *components):
        has_media = any(c is not None and not isinstance(c, Plain) for c in components)
        return await self.service.send_chain(
            event, *components, raise_on_error=has_media
        )

    async def initialize(self):
        """启动预热（ffmpeg 路径探测等一次性阻塞工作）。"""
        await self.service.initialize()

    async def terminate(self):
        """插件卸载/重载时清理轮询等任务。"""
        await self.service.terminate()


# 安装全部声明式路由（handlers/ 目录按业务域维护）
_installed = install_routes(NeteaseMusicPlugin, filter, __name__, ALL_ROUTES)
logger.info(f"[neteasemusic] 插件已加载，共注册 {_installed} 条指令路由")
