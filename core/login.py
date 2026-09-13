"""扫码登录与登录态轮询流程（从 MusicService 整块搬出，行为等价）。

``MusicService.start_poll`` / ``MusicService.stop_poll`` 保持签名不变，改为薄委托；
``finish_login`` 由本模块的轮询在扫码确认后直接调用（不再有同名的 service 包装）。

轮询状态（``active_logins``）仍挂在 service 上：它是服务级共享状态，
``terminate()`` 与各 handler 都直接读写。
"""

from __future__ import annotations

import asyncio
import time

from astrbot.api.event import AstrMessageEvent

from . import api as ncmapi
from .messages import MSG_SAVE_CONFIG_FAIL

# 扫码登录轮询间隔（秒）
POLL_INTERVAL_SEC = 2.0
# 上一次轮询尚未结束时的重试间隔（秒）
POLL_BUSY_INTERVAL_SEC = 0.8

# 轮询单步结果：继续调度 / 终止本次轮询
_POLL_CONTINUE = "continue"
_POLL_STOP = "stop"


def _abandon_poll(service, user_key: str, task: dict) -> None:
    """终止一次轮询：标记停止并摘掉登录态登记。"""
    task["stopped"] = True
    service.active_logins.pop(user_key, None)


async def finish_login(
    service, event: AstrMessageEvent, body: dict, user_key: str, task: dict
):
    """扫码确认后落地 Cookie 与 UID，并回报登录状态。"""
    _abandon_poll(service, user_key, task)
    cookie = (body or {}).get("cookie") or ""
    nickname = (body or {}).get("nickname") or ""
    if not cookie:
        await service.reply(
            event, "登录成功但未获取到 Cookie（可能登录状态异常），请重新 #ncm登录"
        )
        return
    service.plugin.config["defaultCookie"] = cookie
    # 登录态变了：清掉匿名期间缓存的「无特权」，让 auto 立刻能走全阶梯
    ncmapi.clear_privilege_cache()
    if await service.save_config():
        service.log_info("扫码登录成功，Cookie 已写入插件配置 defaultCookie")
        await service.reply(
            event,
            f"✅ 登录成功：{nickname or '已写入 Cookie'}\nCookie 已存入插件配置，全群默认使用该账号",
        )
    else:
        # 内存里已生效，但没能落盘：重启后会失效，不能谎报"已存入配置"
        await service.reply(
            event,
            f"✅ 登录成功：{nickname or '已获取 Cookie'}\n"
            f"⚠ Cookie 未能写入插件配置（重启后会失效）：{MSG_SAVE_CONFIG_FAIL}",
        )
    st = None
    try:
        st = await ncmapi.login_status(user_key=user_key)
        profile = st.get("profile") or {}
        if profile and profile.get("userId"):
            service.plugin.config["defaultUid"] = str(profile["userId"])
            await service.save_config()
    except Exception:
        pass
    await service.send_status(event, user_key, status_data=st)


def stop_poll(service, user_key: str):
    """停止并清理某个会话的扫码轮询（登出/重载/重新登录时调用）。"""
    task = service.active_logins.pop(user_key, None)
    if not task:
        return
    task["stopped"] = True
    if task.get("timer") is not None:
        try:
            task["timer"].cancel()
        except Exception:
            pass
    # 取消所有已创建但仍在运行的 _tick 轮询任务，避免重载/登出后残留协程对旧 event 发消息
    for job in task.get("jobs") or []:
        if job and hasattr(job, "cancel"):
            try:
                job.cancel()
            except Exception:
                pass


async def _poll_on_failure(service, event, user_key: str, task: dict, err) -> str:
    """一次轮询请求异常后的退避计数与文案；返回是否终止。"""
    task["failStreak"] += 1
    if task["failStreak"] == 5:
        await service.reply(event, f"轮询暂时失败：{err}（继续重试）")
    if task["failStreak"] >= 25:
        _abandon_poll(service, user_key, task)
        await service.reply(event, "轮询失败过多，请检查 API 服务或重新 #ncm登录")
        return _POLL_STOP
    return _POLL_CONTINUE


async def _poll_once(
    service, event: AstrMessageEvent, user_key: str, key: str, task: dict
) -> str:
    """查询一次二维码状态并按 code 分支；返回是否终止本次轮询。"""
    try:
        body = await ncmapi.qr_check(key, user_key=user_key)
        code = (body or {}).get("code")
        if code == 800:
            _abandon_poll(service, user_key, task)
            await service.reply(event, "二维码已失效，请重新 #ncm登录")
            return _POLL_STOP
        if code == 802 and not task["notifiedScan"]:
            task["notifiedScan"] = True
            await service.reply(event, "已扫码，请在手机上确认登录")
        elif code == 803:
            await finish_login(service, event, body, user_key, task)
            return _POLL_STOP
        task["failStreak"] = 0
    except Exception as err:  # noqa: BLE001 - 轮询失败按次数退避，不中断
        return await _poll_on_failure(service, event, user_key, task, err)
    return _POLL_CONTINUE


async def _poll_tick(
    service,
    event: AstrMessageEvent,
    user_key: str,
    key: str,
    task: dict,
    started: float,
    max_sec: int,
    schedule,
) -> None:
    """一次轮询调度：守卫 → 超时 → 查询 → 续排下一次。"""
    if task["stopped"]:
        return
    if task["busy"]:
        schedule(POLL_BUSY_INTERVAL_SEC)
        return
    if time.time() - started > max_sec:
        _abandon_poll(service, user_key, task)
        await service.reply(event, "二维码已过期，请重新 #ncm登录")
        return
    task["busy"] = True
    try:
        outcome = await _poll_once(service, event, user_key, key, task)
    finally:
        task["busy"] = False
    if outcome == _POLL_STOP:
        return
    still_current = service.active_logins.get(user_key, {}).get("key") == key
    if not task["stopped"] and still_current:
        task["timer"] = schedule(POLL_INTERVAL_SEC)


def start_poll(service, event: AstrMessageEvent, key: str, max_sec: int = 300):
    """开始轮询二维码状态（task 句柄由 service.active_logins 与 _bg_tasks 持有）。"""
    user_key = service.user_key(event)
    started = time.time()
    task = {
        "key": key,
        "stopped": False,
        "busy": False,
        "notifiedScan": False,
        "failStreak": 0,
        "jobs": [],
    }
    service.active_logins[user_key] = task
    loop = asyncio.get_running_loop()

    def _spawn():
        t = asyncio.create_task(_tick())
        task["jobs"].append(t)
        return t

    def _schedule(delay: float):
        handle = loop.call_later(delay, _spawn)
        task["jobs"].append(handle)
        return handle

    async def _tick():
        await _poll_tick(
            service, event, user_key, key, task, started, max_sec, _schedule
        )

    task["timer"] = _schedule(POLL_INTERVAL_SEC)
