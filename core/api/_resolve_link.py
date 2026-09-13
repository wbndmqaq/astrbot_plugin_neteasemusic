"""astrbot_plugin_neteasemusic API —— （自 core/api.py 拆分；公开符号经本包 __init__ 再导出）。"""

from __future__ import annotations

import re

import aiohttp

from ._core import SHORT_LINK_TIMEOUT_SEC, USER_AGENT, _get_session

# ──────────── 短链展开（163cn.tv → music.163.com） ────────────


SHORT_LINK_RE = re.compile(r"https?://[\w.-]*\.?163cn\.tv/\S+")
async def expand_short_links(text: str) -> str:

    links = SHORT_LINK_RE.findall(str(text or ""))
    if not links:
        return text
    out = text
    for link in links:
        final_url = await _follow_redirect(link)
        if final_url:
            out = out.replace(link, final_url)
    return out
async def _follow_redirect(url: str) -> str:
    """跟随 302 展开短链；返回最终 URL，失败（超时/异常）返回 "" 保留原链接。

    调用方 ``core/resolve.py`` 用「展开后文本里是否仍有 163cn.tv」判定失败并提示用户。
    """
    try:
        timeout = aiohttp.ClientTimeout(total=SHORT_LINK_TIMEOUT_SEC)
        sess = _get_session()
        async with sess.get(
            url,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        ) as res:
            return str(res.url)
    except Exception:
        return ""
