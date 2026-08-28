from __future__ import annotations

import os

from astrbot.api import logger

_LOGGER_TAG = "[neteasemusic]"

# 渲染环境缺失的完整安装指引只输出一次，避免每次渲染刷屏
_hint_logged = False

# 手动安装教程（与 README「卡片渲染」章节保持一致）
_RENDER_TUTORIAL = (
    "本插件绝不自动执行任何系统级安装（不改 apt 源 / 不跑 apt-get / 不自动 pip 装包 /\n"
    "不自动下载内核），请按以下步骤手动操作：\n"
    "\n"
    "① 安装 playwright Python 包：\n"
    "     pip install playwright\n"
    "     # 国内镜像：pip install playwright -i https://pypi.tuna.tsinghua.edu.cn/simple\n"
    "\n"
    "② 下载 Chromium 浏览器内核：\n"
    "     python -m playwright install chromium\n"
    "     # 国内加速：PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/ \\\n"
    "     #           python -m playwright install chromium\n"
    "\n"
    "③ 仅 Linux 容器且报 libnspr4/libnss3/shared libraries 缺失时（需 root）：\n"
    "     python -m playwright install-deps chromium\n"
    "     # 或手动装库：apt-get update && apt-get install -y libnspr4 libnss3 libgbm1\n"
    "     #   libasound2 libatk-bridge2.0-0 libatk1.0-0 libcairo2 libcups2 libdrm2 \\\n"
    "     #   libx11-xcb1 libxcb1 libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 \\\n"
    "     #   libxrandr2 libxext6 libpango-1.0-0\n"
    "     # 容器内 apt 下载慢可先换阿里源（Debian 12 示例）：\n"
    "     #   sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources\n"
    "\n"
    "完成后重载本插件即可渲染图片；未安装期间所有指令自动回退纯文本，点歌播放不受影响。\n"
    "更多说明见 README「卡片渲染」章节。"
)


def _log_env_hint(reason: str) -> None:
    """卡片渲染不可用时在日志输出完整的手动安装教程。

    安全约束：插件绝不自动执行 pip/apt/Chromium 下载等系统级安装，
    仅记录指引交由用户确认后自行操作。
    """
    global _hint_logged
    if _hint_logged:
        logger.warning(f"{_LOGGER_TAG} 卡片渲染不可用（{reason}），已回退纯文本")
        return
    _hint_logged = True
    logger.error(f"{_LOGGER_TAG} 卡片渲染不可用（{reason}）。\n{_RENDER_TUTORIAL}")


async def render_card_png(tmpl_path: str, data: dict) -> bytes | None:
    """渲染 HTML 卡片为 PNG，返回截图原始 bytes；模板不存在或环境不可用返回 None。

    保持原内联渲染行为不变（viewport 640x800、2x、收缩视口、整页截图）。
    """
    try:
        import jinja2
        from playwright.async_api import async_playwright
    except ImportError as e:
        _log_env_hint(f"缺少依赖 {e.name}")
        return None

    from .tpl_adapter import get_jinja_template

    if not os.path.exists(tmpl_path):
        return None
    tmpl = get_jinja_template(tmpl_path)
    html = jinja2.Template(tmpl).render(data=data)

    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ]
    try:
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(args=launch_args)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "Executable doesn't exist" in msg or "playwright install" in msg:
                    _log_env_hint("未下载 Chromium")
                elif "shared libraries" in msg or "shared object" in msg:
                    # 二进制存在但容器缺 libnspr4/libnss3 等系统运行库
                    _log_env_hint("Chromium 缺少系统运行库")
                else:
                    logger.error(f"{_LOGGER_TAG} Chromium 启动失败: {e}")
                return None
            try:
                page = await browser.new_page(
                    viewport={"width": 640, "height": 800},
                    device_scale_factor=2,  # 2x 清晰度
                )
                await page.set_content(html, wait_until="load", timeout=30000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                # 收缩视口到 .page 实际渲染边界：viewport 固定时 body 不会随
                # fit-content 收缩，截图右侧/底部会留大片浅红空白（白边）
                try:
                    rect = await page.evaluate(
                        "() => { const el = document.querySelector('.page') || document.body; "
                        "const r = el.getBoundingClientRect(); "
                        "return { w: Math.max(1, Math.ceil(r.right)), "
                        "h: Math.max(1, Math.ceil(r.bottom)) }; }"
                    )
                    await page.set_viewport_size({"width": rect["w"], "height": rect["h"]})
                    await page.wait_for_timeout(50)
                except Exception:
                    pass
                raw = await page.screenshot(full_page=True, type="png")
            finally:
                await browser.close()
        return raw
    except Exception as e:  # noqa: BLE001
        logger.error(f"{_LOGGER_TAG} 渲染失败: {e}")
        return None
