from __future__ import annotations

import os
import subprocess
import sys

from astrbot.api import logger

_LOGGER_TAG = "[neteasemusic]"
_runtime_checked = False


def _switch_apt_to_aliyun():
    """Debian/Ubuntu 容器下把官方 apt 源换成阿里镜像，加速 install-deps 下载。

    仅当存在 apt-get 且源文件指向官方域名时才改写（幂等，不覆盖用户自选镜像），
    首次改写前备份为 .bak；任何失败只记日志不影响后续。返回是否发生了改动。
    仅在 Linux 上生效：Windows/macOS 无 `/etc/apt`，一律不触碰系统配置。
    """

    import glob
    import platform
    import shutil

    if platform.system() != "Linux":
        return False
    if not shutil.which("apt-get"):
        return False
    targets = ["/etc/apt/sources.list"]
    targets += glob.glob("/etc/apt/sources.list.d/*.sources")
    mapping = [
        ("http://deb.debian.org/debian", "http://mirrors.aliyun.com/debian"),
        ("https://deb.debian.org/debian", "http://mirrors.aliyun.com/debian"),
        ("http://security.debian.org/debian-security", "http://mirrors.aliyun.com/debian-security"),
        ("https://security.debian.org/debian-security", "http://mirrors.aliyun.com/debian-security"),
        ("http://archive.ubuntu.com/ubuntu", "http://mirrors.aliyun.com/ubuntu"),
        ("https://archive.ubuntu.com/ubuntu", "http://mirrors.aliyun.com/ubuntu"),
        ("http://security.ubuntu.com/ubuntu", "http://mirrors.aliyun.com/ubuntu"),
        ("https://security.ubuntu.com/ubuntu", "http://mirrors.aliyun.com/ubuntu"),
    ]
    changed = False
    for path in targets:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            content = None
        if content is None:
            continue
        new_content = content
        for old, new in mapping:
            if old in new_content:
                new_content = new_content.replace(old, new)
        if new_content == content:
            continue
        bak = path + ".bak"
        try:
            if not os.path.exists(bak):
                with open(bak, "w", encoding="utf-8") as f:
                    f.write(content)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
        except Exception as e:
            logger.warning(f"{_LOGGER_TAG} apt 源改写失败 {path}: {e}")
            continue
        changed = True
        logger.info(f"{_LOGGER_TAG} apt 源 {path} 已切换阿里镜像（原文件备份为 {bak}）")
    if changed:
        try:
            subprocess.run(["apt-get", "update"], capture_output=True, text=True, check=False, timeout=300)
        except Exception as e:
            logger.warning(f"{_LOGGER_TAG} apt-get update 失败: {e}")
    return changed


def _install_playwright():
    """自动 pip 安装 playwright 包（缺失时），附带清华镜像源加速。"""
    cmd = [sys.executable, "-m", "pip", "install", "-U", "playwright"]
    logger.info(f"{_LOGGER_TAG} 正在自动安装 playwright Python 包: {' '.join(cmd)}")
    env = os.environ.copy()
    env["PIP_INDEX_URL"] = os.environ.get(
        "PIP_INDEX_URL", "https://pypi.tuna.tsinghua.edu.cn/simple"
    )
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        raise RuntimeError(f"playwright Python 包安装失败: {res.stderr or res.stdout}")

    import importlib.util
    importlib.invalidate_caches()
    if importlib.util.find_spec("playwright") is None:
        raise RuntimeError("playwright 包安装后仍不可用")
    logger.info(f"{_LOGGER_TAG} playwright Python 包安装完成！")


def _install_chromium():
    """确保 playwright Python 包已装好，再下载 Chromium 二进制（npmmirror 加速）。"""
    _install_playwright()
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    logger.info(f"{_LOGGER_TAG} 正在自动安装 Playwright Chromium: {' '.join(cmd)}")
    env = os.environ.copy()
    env["PLAYWRIGHT_DOWNLOAD_HOST"] = "https://npmmirror.com/mirrors/playwright/"
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        raise RuntimeError(f"Playwright Chromium 安装失败: {res.stderr or res.stdout}")
    logger.info(f"{_LOGGER_TAG} Playwright Chromium 安装完成！")


def _install_deps():
    """同步执行 playwright install-deps（先切阿里 apt 源再装系统运行库）。

    仅 Linux/容器环境需要（依赖 apt 装系统包）；Windows/macOS 直接跳过。
    失败时抛异常，由调用方给出手动安装提示。
    """
    import platform

    if platform.system() != "Linux":
        logger.warning(f"{_LOGGER_TAG} 非 Linux 环境，跳过 playwright install-deps 系统依赖安装")
        return None
    _switch_apt_to_aliyun()
    cmd = [sys.executable, "-m", "playwright", "install-deps", "chromium"]
    logger.info(f"{_LOGGER_TAG} 正在安装 Chromium 系统依赖: {' '.join(cmd)}")
    env = os.environ.copy()
    env["PLAYWRIGHT_DOWNLOAD_HOST"] = "https://npmmirror.com/mirrors/playwright/"
    return subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)


def _ensure_runtime_sync():
    """跨平台确保 Playwright 渲染运行时全部就绪（幂等，首次调用执行一次）。

    按顺序保证三步：
      1. 确保 playwright Python 包已安装（缺失则 pip 装清华镜像；装不到位直接抛异常）
      2. 仅 Linux：切阿里 apt 源并用 install-deps 安装系统运行库（失败仅告警，不阻塞）
      3. 下载 Chromium 二进制（npmmirror 加速）
    """
    global _runtime_checked
    if _runtime_checked:
        return True
    _install_playwright()
    _install_deps()
    _install_chromium()
    _runtime_checked = True
    return True


async def _ensure_runtime():
    import asyncio
    await asyncio.to_thread(_ensure_runtime_sync)


async def render_card_png(tmpl_path: str, data: dict) -> bytes | None:
    """渲染 HTML 卡片为 PNG，返回截图原始 bytes；模板不存在返回 None。

    保持原内联渲染行为不变（viewport 640x800、2x、收缩视口、整页截图）。
    """
    await _ensure_runtime()
    import asyncio
    import jinja2
    from playwright.async_api import async_playwright

    from .tpl_adapter import get_jinja_template

    if not os.path.exists(tmpl_path):
        return None
    tmpl = get_jinja_template(tmpl_path)
    html = jinja2.Template(tmpl).render(data=data)
    async with async_playwright() as p:
        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ]
        try:
            browser = await p.chromium.launch(args=launch_args)
        except Exception as e:
            err_msg = str(e)
            if "Executable doesn't exist" in err_msg or "playwright install" in err_msg:
                logger.warning(f"{_LOGGER_TAG} 未找到 Playwright Chromium，正在尝试通过 npmmirror 镜像源自动下载安装...")
                await asyncio.to_thread(_install_chromium)
                browser = await p.chromium.launch(args=launch_args)
            elif "error while loading shared libraries" in err_msg or "shared object file" in err_msg:
                # 二进制已下载但容器缺系统运行库（libnspr4/libnss3 等）。
                # playwright install 只下载二进制、不装 OS 包；这里尝试 install-deps
                # （需 apt + root），失败则给出可直接执行的安装命令而非含糊报错。
                logger.warning(f"{_LOGGER_TAG} Chromium 缺少系统运行库，尝试执行 playwright install-deps 自动安装...")
                res = await asyncio.to_thread(_install_deps)
                if res is not None and res.returncode != 0:
                    hint = (
                        "请在容器内以 root 执行：\n"
                        "python -m playwright install-deps chromium\n"
                        "或手动：apt-get update && apt-get install -y libnspr4 libnss3 "
                        "libx11-xcb1 libxcb1 libxcomposite1 libxdamage1 libxfixes3 "
                        "libxrandr2 libgbm1 libasound2 libatk1.0-0 libatk-bridge2.0-0 "
                        "libcairo2 libcups2 libdrm2 libxkbcommon0 libxext6 libpango-1.0-0"
                    )
                    logger.error(
                        f"{_LOGGER_TAG} 自动安装系统依赖失败，请手动安装后重试：\n{hint}\n\n安装输出：\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}"
                    )
                    raise
                browser = await p.chromium.launch(args=launch_args)
            else:
                raise e
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
