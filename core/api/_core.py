"""astrbot_plugin_neteasemusic API —— （自 core/api.py 拆分；公开符号经本包 __init__ 再导出）。"""

from __future__ import annotations

import asyncio
import math
from typing import Any

import aiohttp

# ──────────── 常量 ────────────

# 普通接口请求超时（秒）
API_TIMEOUT_SEC = 20
# 短链展开（跟随 302）超时（秒）
SHORT_LINK_TIMEOUT_SEC = 8
# 请求网易 CDN / 网页时使用的浏览器 UA（delivery 下载音频复用同一份）
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
# 模块级配置访问器，由 main.py 在插件加载时注入
_cfg_getter = None
def set_config_getter(fn):
    global _cfg_getter
    _cfg_getter = fn
def _cfg() -> dict:
    if _cfg_getter is not None:
        try:
            return _cfg_getter() or {}
        except Exception:
            return {}
    return {}
def _get_cookie() -> str:
    try:
        return str(_cfg().get("defaultCookie") or "")
    except Exception:
        return ""
# ──────────── 复用 HTTP 会话 ────────────

# 模块级 aiohttp.ClientSession 复用：避免每请求新建会话（反复 TCP 握手 + DNS 解析）。
# 会话由 service.terminate() 调 close_session() 统一关闭；懒加载，未使用时不会创建。
_session: aiohttp.ClientSession | None = None
def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session
def get_session() -> aiohttp.ClientSession:
    """对外暴露的复用会话（供 delivery 下载音频复用，避免每次下载新建会话）。"""
    return _get_session()
async def close_session() -> None:
    """关闭并释放复用的 HTTP 会话（插件卸载/重载时由 service 调用）。"""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None
# ──────────── 错误处理 ────────────

ERR_MESSAGES = {
    301: "未登录或登录态已失效",
    400: "参数错误",
    402: "该曲需 VIP/会员才能播放",
    403: "请求被风控拒绝（403），可尝试给 API 服务配置 realIP 或随机中国 IP",
    404: "资源不存在或无版权",
    406: "需要登录，请先发送 #ncm登录",
    460: "IP 被风控（460 cheating），请给 API 服务配置 realIP 或 randomCNIP",
    502: "网易接口调用失败，请稍后重试",
    503: "请求过于频繁，请稍后再试",
    1101: "登录已过期，请重新 #ncm登录",
}
class ApiError(Exception):
    def __init__(self, message: str, *, code=None, payload=None):
        super().__init__(message)
        self.code = code
        self.payload = payload
def _err_msg_for(code, status: int = 0) -> str:
    # code 可能不是数字（实测 HTTP 500 + {"code": "oops"} / {"code": null}）：
    # 原实现直接 int(code) 会抛 ValueError，而 request() 只把 ClientError/TimeoutError
    # 转成 ApiError，ValueError 会穿透到 handler（用户零回复且 stop_event 不执行）。
    # 转不动就按「无 code」处理，保证 request() 只抛 ApiError。
    code_num = _opt_int(code)
    if code_num is not None and code_num in ERR_MESSAGES:
        return ERR_MESSAGES[code_num]
    if status >= 500:
        return f"网易云 API 服务错误（HTTP {status}），请检查 api-enhanced 服务"
    if status == 401:
        return "API 鉴权失败（401）"
    if status >= 400:
        return f"请求失败（HTTP {status}）"
    return "请求失败"
# ──────────── 基础请求 ────────────


def _get_base() -> str:
    base = str(_cfg().get("apiBase") or "")
    return base.rstrip("/")
def _query_safe_params(params: dict) -> dict:
    out: dict = {}
    for k, v in params.items():
        if isinstance(v, bool):
            out[k] = int(v)
        elif v is None:
            continue
        elif isinstance(v, (str, int, float)):
            out[k] = v
        else:
            out[k] = str(v)
    return out
def _num(v: Any) -> float:
    if v is None or isinstance(v, (list, dict)):
        return 0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    # json 默认接受裸 NaN/Infinity/-Infinity，后面对 f 做 int() 会抛 OverflowError，
    # 因此非有限值统一归零（math.isfinite 同时覆盖 NaN 与 ±inf）
    return f if math.isfinite(f) else 0
def _opt_int(v: Any) -> int | None:
    """宽松转 int：转不动（None/空串/非数字字符串等）返回 None，绝不抛异常。"""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return None
def as_int(v: Any, default: int = 0) -> int:
    """宽松转 int：None/空串/非数字字符串/非有限浮点一律返回 ``default``。

    用于「用户可改的配置值」与上游字段：这类值一旦让 ``int()`` 抛出
    ``ValueError``，指令会在 ``stop_event()`` 之前穿异常（用户零回复）。
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    return int(f)
async def request(
    pathname: str,
    params: dict | None = None,
    method: str = "get",
    user_key: str = "",
) -> dict:
    """调用 api-enhanced 接口。

    当前所有端点都是 GET，且 Cookie 取自全局配置 ``defaultCookie``（多账号按会话
    隔离尚未实现）；``method`` 传非 GET 会显式抛 ``ApiError``（拆包时 POST 分支
    已随死代码移除，拒绝优于静默按 GET 发出）。
    """
    params = dict(params or {})
    base = _get_base()
    if not base:
        # 空 base 会拼出 "/cloudsearch" 这类无协议 URL，aiohttp 报 InvalidURL，
        # 错误信息令人费解；直接给出可操作的配置提示
        raise ApiError("API 地址未配置：请发送 #ncm api <地址>，或在插件设置面板填写 apiBase")
    if "://" not in base:
        raise ApiError(f"API 地址格式错误（缺少 http:// 协议头）：{base}")
    url = f"{base}{pathname if pathname.startswith('/') else '/' + pathname}"
    cookie = _get_cookie()
    if cookie:
        params["cookie"] = cookie

    timeout = aiohttp.ClientTimeout(total=API_TIMEOUT_SEC)
    try:
        if method not in ("get", ""):
            # 拆包时 POST 分支被移除（全仓无 POST 调用方）；显式拒绝而非静默按 GET 发
            raise ApiError(f"内部错误：api-enhanced 客户端仅支持 GET（收到 {method}）")
        sess = _get_session()
        async with sess.get(url, params=_query_safe_params(params), timeout=timeout) as res:
            return await _handle_response(res, pathname)
    except aiohttp.ClientConnectorError as e:
        raise ApiError(f"无法连接网易云 API（{base}），请确认 api-enhanced 服务已启动") from e
    except aiohttp.ServerTimeoutError as e:
        raise ApiError(f"请求超时：{base}") from e
    except aiohttp.ClientError as e:
        raise ApiError(f"网络错误：{e}") from e
    except (TimeoutError, asyncio.TimeoutError) as e:
        # ClientTimeout(total=...) 到期抛的是 asyncio.TimeoutError（3.11 起即内建 TimeoutError），
        # 不是 aiohttp.ClientError，只写 ClientError 会让总超时穿透到 handler：except ApiError
        # 接不住 → 用户拿不到错误回复，且 event.stop_event() 不执行。
        raise ApiError(f"请求超时：{base}") from e
async def _handle_response(res: aiohttp.ClientResponse, pathname: str = "") -> dict:
    status = res.status
    try:
        data = await res.json(content_type=None)
    except Exception:
        text = (await res.text())[:200]
        raise ApiError(f"返回非 JSON（HTTP {status}）：{text}")
    if not isinstance(data, dict):
        raise ApiError(f"返回格式异常（HTTP {status}）")
    if status >= 400:
        code = data.get("code")
        msg = data.get("msg") or data.get("message") or ""
        raise ApiError(msg or _err_msg_for(code, status), code=code, payload=data)
    # 网易登录态类接口匿名时返回 HTTP 200 + body.code=301（登录接口自身除外）
    if data.get("code") == 301 and not pathname.startswith("/login/"):
        raise ApiError(ERR_MESSAGES[301], code=301, payload=data)
    return data
