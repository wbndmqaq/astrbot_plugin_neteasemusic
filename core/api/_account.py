"""astrbot_plugin_neteasemusic API —— （自 core/api.py 拆分；公开符号经本包 __init__ 再导出）。"""

from __future__ import annotations

import time

from astrbot.api import logger

from ._core import ApiError, _get_cookie, _opt_int, request

# ──────────── 登录 ────────────


def _now_ts() -> int:
    # api-enhanced 对相同 URL 缓存 2 分钟（server.js apicache，无 login 豁免）；
    # 二维码三接口必须带实时时间戳才能绕过缓存，否则轮询永远拿到第一次的结果
    return int(time.time() * 1000)
async def qr_key(user_key: str = "") -> str:
    body = await request("/login/qr/key", {"timestamp": _now_ts()}, "get", user_key)
    return (((body or {}).get("data") or {}).get("unikey")) or ""
async def qr_create(key: str, user_key: str = "") -> dict:
    body = await request("/login/qr/create", {"key": key, "qrimg": "true"}, "get", user_key)
    data = (body or {}).get("data") or {}
    return {
        "qrurl": data.get("qrurl") or "",
        "qrimg": data.get("qrimg") or "",
    }
async def qr_check(key: str, user_key: str = "") -> dict:
    # 时间戳防缓存（见 _now_ts 注释）；轮询频率 2s，缓存会直接卡死状态更新
    return await request("/login/qr/check", {"key": key, "timestamp": _now_ts()}, "get", user_key)
async def logout(user_key: str = "") -> dict:
    return await request("/logout", {}, "get", user_key)
# ──────────── 账号高档位特权（auto 音质自适应用） ────────────

# 缓存 {user_key: (是否有特权, 检查时刻)}：会员状态几分钟内不会变，TTL 内不重复请求 /vip/info，
# 避免每次点歌都多一次往返。
_PRIVILEGE_CACHE_TTL_SEC = 600
# 上限与「超限按检查时刻淘汰最旧」的口径同 SessionStore：成员数据本身很小，
# 但它是模块级对象、跨热重载存活，不该随用户数无界增长。
_PRIVILEGE_CACHE_MAX = 256
_privilege_cache: dict[str, tuple[bool, float]] = {}
def clear_privilege_cache() -> None:
    """清空会员特权缓存（登录/登出后调用）。

    匿名期间查过就会缓存「无特权」，登录成功后若不清空，``auto`` 会在 TTL（10 分钟）
    内仍从无损档起试，会员拿不到母带/Hi-Res。
    """
    _privilege_cache.clear()
async def has_high_quality_privilege(user_key: str = "") -> bool:
    """账号是否具备超清母带/沉浸环绕声/高清环绕声/Hi-Res 这类高档位特权（带 TTL 缓存）。

    判定口径：
    ① 没有登录 Cookie → 匿名账号必然拿不到高档位，直接返回 False（省掉一次请求）；
    ② ``/vip/info`` 的 ``associator`` / ``musicPackage`` / ``redplus`` 任一会员包
       ``vipLevel``（或 ``vipCode``）> 0 且 ``expireTime`` 未过期（毫秒时间戳）。
    任何异常一律按「无特权」处理：拿不到高档位只是少一层音质，不该让取链整体失败。
    """
    if not _get_cookie():
        return False
    now = time.time()
    cached = _privilege_cache.get(user_key)
    if cached is not None and now - cached[1] < _PRIVILEGE_CACHE_TTL_SEC:
        return cached[0]

    privileged = False
    try:
        body = await vip_info(user_key=user_key)
        data = (body or {}).get("data") or {}
        if isinstance(data, dict):
            now_ms = now * 1000
            for pkg_key in ("associator", "musicPackage", "redplus"):
                pkg = data.get(pkg_key)
                if not isinstance(pkg, dict):
                    continue
                level = _opt_int(pkg.get("vipLevel")) or _opt_int(pkg.get("vipCode")) or 0
                expire = _opt_int(pkg.get("expireTime")) or 0
                if level > 0 and expire > now_ms:
                    privileged = True
                    break
    except ApiError as e:
        logger.warning(f"[neteasemusic] 查询会员特权失败，本次按无特权处理: {e}")

    _privilege_cache[user_key] = (privileged, now)
    if len(_privilege_cache) > _PRIVILEGE_CACHE_MAX:
        overflow = len(_privilege_cache) - _PRIVILEGE_CACHE_MAX
        for old_key in sorted(_privilege_cache, key=lambda k: _privilege_cache[k][1])[:overflow]:
            _privilege_cache.pop(old_key, None)
    return privileged
async def vip_info(user_key: str = "") -> dict:

    return await request("/vip/info", {}, "get", user_key)
async def login_status(user_key: str = "") -> dict:
    body = await request("/login/status", {}, "get", user_key)
    data = (body or {}).get("data") or {}
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else None
    return {
        "code": (body or {}).get("code") or (data.get("code") if isinstance(data, dict) else None),
        "profile": profile,
        "account": data.get("account") if isinstance(data.get("account"), dict) else None,
    }
