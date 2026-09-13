from __future__ import annotations

import asyncio
import contextlib
import os

from astrbot.api import logger

_LOGGER_TAG = "[neteasemusic]"

# Chromium 启动参数（Docker / Linux 容器防崩溃）
_CHROME_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]

# 渲染视口与等待时长（与 JS 版一致：640x800 起渲染、2x 清晰度、整页截图）
VIEWPORT_WIDTH = 640
VIEWPORT_HEIGHT = 800
DEVICE_SCALE_FACTOR = 2
SET_CONTENT_TIMEOUT_MS = 15000
NETWORK_IDLE_TIMEOUT_MS = 8000
VIEWPORT_SETTLE_MS = 50

# 常驻 Chromium：避免每张卡片都启动/关闭一个浏览器进程
_playwright = None
_browser = None
_lock = asyncio.Lock()

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


# ──────────── 模板编译与卡片数据包装 ────────────

# 已编译模板缓存：模板文件在进程内不变（改模板需重载插件），因此「读盘 + 编译」只在
# 首次发生，并且整体丢进线程池（读盘是阻塞 IO、编译是纯 CPU），不占用事件循环。
_compiled_templates: dict[str, object] = {}


def _compile_template(tmpl_path: str):
    import jinja2

    with open(tmpl_path, "r", encoding="utf-8") as f:
        src = f.read()
    # autoescape=True：卡片数据含用户可控文本（点歌关键词、昵称、评论），
    # 默认不转义会把 <img onerror>/<script> 原样注入渲染页。
    return jinja2.Template(src, autoescape=True)


async def load_template(tmpl_path: str):
    """按路径缓存并返回已编译模板（首次在线程池内完成读盘与编译）。"""
    template = _compiled_templates.get(tmpl_path)
    if template is None:
        template = await asyncio.to_thread(_compile_template, tmpl_path)
        _compiled_templates[tmpl_path] = template
    return template


# 与字典方法同名的键：模板写 ``data.items`` 时 Jinja 会先取属性，普通 dict 上
# 拿到的是 bound method（渲染成方法对象、被 for 迭代时 TypeError）。
_DICT_METHODS = frozenset(
    {"items", "keys", "values", "get", "copy", "update", "pop", "popitem", "clear", "setdefault"}
)


class _SafeData(dict):
    """卡片数据包装：缺失键一律给空值，而不是 Jinja 的 Undefined。

    解决两类会让整张卡片渲染失败/渲染错的情况：

    1. **缺失键**：``data['items']`` 取不到时 Jinja 给 Undefined，被 ``for`` 迭代直接
       抛错（整卡退化成纯文本）。这里 ``__missing__`` 返回空串——``for`` 迭代 0 次、
       ``|length`` 得 0。
    2. **与字典方法同名的键**：``data.items`` 是属性取值，普通 dict 会命中 ``dict.items``
       方法。这里在 ``__getattribute__`` 里让同名**数据键优先**，没有该键时返回空串
       （模板不会去调字典方法，返回空串比返回 bound method 安全）。
    """

    def __missing__(self, key):
        return ""

    def __getattribute__(self, key):
        if key in _DICT_METHODS:
            try:
                return dict.__getitem__(self, key)
            except KeyError:
                return ""
        return dict.__getattribute__(self, key)


def wrap_card_data(value):
    """递归包装卡片数据：dict → _SafeData，list/tuple → 同构容器。"""
    if isinstance(value, dict):
        return _SafeData({k: wrap_card_data(v) for k, v in value.items()})
    if isinstance(value, list):
        return [wrap_card_data(v) for v in value]
    if isinstance(value, tuple):
        return tuple(wrap_card_data(v) for v in value)
    return value

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


async def _get_browser():
    """获取常驻 Chromium 实例；环境不可用返回 None（由调用方回退纯文本）。"""
    global _playwright, _browser
    if _browser is not None:
        return _browser
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        _log_env_hint(f"缺少依赖 {e.name}")
        return None
    async with _lock:
        if _browser is not None:
            return _browser
        pw = None
        try:
            pw = await async_playwright().start()
            _browser = await pw.chromium.launch(args=_CHROME_ARGS)
            _playwright = pw
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "Executable doesn't exist" in msg or "playwright install" in msg:
                _log_env_hint("未下载 Chromium")
            elif "shared libraries" in msg or "shared object" in msg:
                # 二进制存在但容器缺 libnspr4/libnss3 等系统运行库
                _log_env_hint("Chromium 缺少系统运行库")
            else:
                logger.error(f"{_LOGGER_TAG} Chromium 启动失败: {e}")
            # 清理半初始化的 playwright 运行时，避免进程残留
            if pw is not None:
                with contextlib.suppress(Exception):
                    await pw.stop()
            return None
    return _browser


async def _discard_browser() -> None:
    """丢弃缓存的常驻浏览器实例（实例失效时重建，避免后续渲染持续失败）。"""
    global _playwright, _browser
    async with _lock:
        pw, browser = _playwright, _browser
        _playwright = None
        _browser = None
    if browser is not None:
        with contextlib.suppress(Exception):
            await browser.close()
    if pw is not None:
        with contextlib.suppress(Exception):
            await pw.stop()


async def close_browser() -> None:
    """关闭常驻 Chromium 与 playwright 运行时（插件卸载/重载时由 service 调用）。"""
    await _discard_browser()


async def render_card_png(tmpl_path: str, data: dict) -> bytes | None:
    """渲染 HTML 卡片为 PNG，返回截图原始 bytes；模板不存在或环境不可用返回 None。

    渲染行为保持原样（viewport 640x800、2x、收缩视口、整页截图），
    仅把「每次启动/关闭一个 Chromium」改为复用常驻实例。
    """
    if not os.path.exists(tmpl_path):
        return None
    try:
        template = await load_template(tmpl_path)
    except ImportError as e:
        _log_env_hint(f"缺少依赖 {e.name}")
        return None
    # render 是纯 CPU（大榜单/长评论卡几十 ms），与编译一致移出事件循环
    html = await asyncio.to_thread(template.render, data=wrap_card_data(data))

    page = None
    for attempt in (0, 1):
        browser = await _get_browser()
        if browser is None:
            return None
        try:
            page = await browser.new_page(
                viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
                device_scale_factor=DEVICE_SCALE_FACTOR,  # 2x 清晰度
            )
            break
        except Exception as e:  # noqa: BLE001
            # 常驻实例可能已失效（内核崩溃 / 被回收）：丢弃缓存后重建重试一次
            logger.warning(f"{_LOGGER_TAG} 创建渲染页失败（第 {attempt + 1} 次）: {e}")
            await _discard_browser()
    if page is None:
        logger.error(f"{_LOGGER_TAG} 渲染失败：无法创建渲染页（浏览器实例不可用）")
        return None

    try:
        # 封面 CDN 抖动时 load 事件可能一直不触发：set_content 已经同步写好 DOM，
        # 所以超时不该让整张卡片退化成纯文本，捕获后继续截图（封面缺失但排版在）。
        try:
            await page.set_content(html, wait_until="load", timeout=SET_CONTENT_TIMEOUT_MS)
        except Exception:  # noqa: BLE001
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
        except Exception:  # noqa: BLE001
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
            await page.wait_for_timeout(VIEWPORT_SETTLE_MS)
        except Exception:  # noqa: BLE001
            pass
        return await page.screenshot(full_page=True, type="png")
    except Exception as e:  # noqa: BLE001
        logger.error(f"{_LOGGER_TAG} 渲染失败: {e}")
        return None
    finally:
        await page.close()
