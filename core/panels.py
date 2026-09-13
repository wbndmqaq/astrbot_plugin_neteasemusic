"""状态卡 / 登录状态数据构造与发送（从 MusicService 整块搬出，行为等价）。

模块级 ``build_status`` / ``send_status`` 供 service 与 handlers 直接调用；
``MusicService.send_status`` 保持签名不变，改为薄委托（``build_status`` 只在
本模块内使用，service 上没有对应包装）。
"""

from __future__ import annotations

from astrbot.api.event import AstrMessageEvent

from . import api as ncmapi
from . import cards as cardlib
from .api import ApiError, as_int


async def build_status(
    service, user_key: str, *, status_data: dict | None = None
) -> dict:
    """汇总登录态（昵称/头像/等级/会员/API/音质）为卡片数据源。"""
    cfg = service.cfg()
    default_cookie = str(cfg.get("defaultCookie") or "")
    status = {
        "loggedIn": False,
        "nickname": "",
        "avatar": "",
        "uin": "",
        "level": "",
        "vipType": 0,
        "vipLevel": 0,
        "vipExpire": 0,
        "apiBase": cfg.get("apiBase") or "",
        "keyStatus": "默认 Cookie" if default_cookie else "无 Cookie",
        "quality": str(cfg.get("quality") or "auto"),
    }
    if status_data is not None:
        st = status_data
    else:
        try:
            st = await ncmapi.login_status(user_key=user_key)
        except ApiError as e:
            status["keyStatus"] = f"查询失败：{e}"
            return status
    profile = st.get("profile") or {}
    account = st.get("account") or {}
    if profile and profile.get("userId"):
        status["loggedIn"] = True
        status["nickname"] = profile.get("nickname") or ""
        status["avatar"] = profile.get("avatarUrl") or ""
        status["uin"] = str(profile.get("userId") or "")
        lv = profile.get("level") or 0
        status["level"] = str(lv) if lv else ""
        status["vipType"] = as_int(profile.get("vipType") or account.get("vipType"), 0)
        status["vipLevel"] = 0
        status["vipExpire"] = 0
        try:
            vip = await ncmapi.vip_info(user_key=user_key)
            vd = (vip or {}).get("data") or {}
            # 会员等级优先读顶层 redVipLevel（黑胶等级，只有部分上游版本返回），
            # 取不到再从三个会员包里挑最高档兜底 —— 只认其中一边都会漏掉另一种响应形态。
            redplus = vd.get("redplus") if isinstance(vd.get("redplus"), dict) else {}
            level = as_int(vd.get("redVipLevel"), 0)
            expire = as_int(redplus.get("expireTime"), 0)
            if not level:
                for pkg_key in ("redplus", "associator", "musicPackage"):
                    pkg = vd.get(pkg_key)
                    if not isinstance(pkg, dict):
                        continue
                    pkg_level = as_int(pkg.get("vipLevel"), 0)
                    if pkg_level > level:
                        level = pkg_level
                        expire = as_int(pkg.get("expireTime"), 0)
            status["vipLevel"] = level
            status["vipExpire"] = expire
        except ApiError:
            pass
    elif default_cookie:
        status["keyStatus"] = "Cookie 已失效或未写入（登录态 301）"
    return status


async def send_status(
    service,
    event: AstrMessageEvent,
    user_key: str,
    *,
    status_data: dict | None = None,
):
    """发送状态卡片，失败时回退到纯文本（永不静默）。"""
    try:
        status = await build_status(service, user_key, status_data=status_data)
        data = cardlib.build_status_card_data(status)
        await service.reply_card_or_text(
            event,
            tpl_name="ncm-status",
            data=data,
            format_text=lambda d: cardlib.format_status_text(status),
        )
    except Exception as err:  # noqa: BLE001 - 兜底必须覆盖一切，保证有回复
        service.log_warn(f"状态卡片失败: {err}")
        await service.reply(
            event,
            cardlib.format_status_text(
                {
                    "loggedIn": False,
                    "apiBase": service.cfg().get("apiBase") or "",
                    "quality": str(service.cfg().get("quality") or "auto"),
                    "keyStatus": str(err),
                }
            ),
        )
