from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Plain

if TYPE_CHECKING:
    from ..main import NeteaseMusicPlugin

from . import api as ncmapi
from . import cards as cardlib
from . import lists as listlib
from . import login as loginlib
from . import panels as panellib
from . import resolve as resolvelib
from .api import ApiError, as_int
from .delivery import (
    CLEANUP_CANCEL_GRACE_SEC,
    MIN_KEEP_SEC,
    _write_bytes,
    deliver_song,
    get_temp_dir,
)
from .messages import MSG_LYRIC_TIP, unblock_label
from .quality import QUALITY_LABEL
from .render import close_browser, render_card_png

PLUGIN_DIR = str(Path(__file__).resolve().parent.parent)
PLUGIN_NAME = "astrbot_plugin_neteasemusic"
NEW_SONG_AREAS = {"华语": 7, "欧美": 96, "日本": 8, "韩国": 16}
# 连播上限（#ncm听所有）
PLAY_ALL_LIMIT = 30
# 歌词卡片每页行数
LYRIC_PAGE_LINES = 36
# 歌词翻译最多展示行数
TRANSLATION_MAX_LINES = 12
# 逐字歌词最多展示行数
YRC_MAX_LINES = 72
# 登录二维码图片保留秒数
QR_IMAGE_KEEP_SEC = 120


def _owner_marker_path() -> "Path | None":
    """裸 #听N 跨插件仲裁标记（三音乐插件共用）。固定在 data/plugin_data/ 下；
    取不到路径时返回 None（仲裁退化为「无主」，绝不写插件自身目录）。"""
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path
        return Path(get_astrbot_data_path()) / "plugin_data" / "_music_session_owner.json"
    except Exception:
        return None


def _mv_name_key(text: str) -> str:
    """MV/歌曲名归一化：去空白与常见括号修饰，便于同名比对。"""
    return re.sub(r"[\s\-_·（）()《》\[\]【】]", "", text or "").lower()


# ──────────── 裸 #听N 跨插件仲裁标记写失败的一次性告警标志 ────────────
# 模块级：磁盘只读等持续性故障不逐首刷日志，进程生命周期内只告警一次
_owner_marker_warned = False


def is_plugin_command_msg(msg: str) -> bool:
    return bool(
        re.match(
            r"^#?(?:ncm|NCM)(?:[^\x00-\x7F]|\b)|^#听\s*[1-9]",
            str(msg or "").strip(),
            re.IGNORECASE,
        )
    )


def is_ncm_message(text: str) -> bool:
    if not text:
        return False
    return bool(
        re.search(
            r"music\.163\.com|163music\.com|y\.music\.163\.com|163cn\.tv",
            text,
            re.IGNORECASE,
        )
        or re.search(r"网易云音乐|com\.netease\.cloudmusic", text, re.IGNORECASE)
    )


def collect_message_text(event: AstrMessageEvent) -> str:
    parts: list[str] = []
    try:
        msg_str = event.message_str
        if msg_str:
            parts.append(str(msg_str))
    except Exception:
        pass
    try:
        mobj = event.message_obj
        chain = getattr(mobj, "message", None) or []
        for seg in chain:
            seg_type = type(seg).__name__
            if isinstance(seg, Plain):
                t = seg.text if hasattr(seg, "text") else None
                if t:
                    parts.append(str(t))
            elif seg_type in ("Image", "File", "Record", "Video", "Face"):
                continue
            elif seg_type == "Json":
                d = getattr(seg, "data", None)
                if d:
                    parts.append(str(d))
            else:
                try:
                    parts.append(json.dumps(seg, ensure_ascii=False, default=str))
                except Exception:
                    pass
    except Exception:
        pass
    try:
        raw = event.message_obj.raw_message
        if raw:
            if isinstance(raw, str):
                parts.append(raw)
            else:
                parts.append(json.dumps(raw, ensure_ascii=False, default=str))
    except Exception:
        pass
    return "\n".join(p for p in parts if p)


class MusicService:
    """网易云音乐业务服务层，封装状态管理、卡片渲染、歌曲解析播放、扫码登录等。"""

    def __init__(self, plugin: NeteaseMusicPlugin):
        self.plugin = plugin
        self.active_logins: dict[str, dict[str, Any]] = {}
        # 后台任务引用集合（清理定时器等）：持有引用避免被 GC，卸载时统一取消
        self._bg_tasks: set[asyncio.Task] = set()
        # 已排期清理的临时文件：任务 → 路径，供 terminate() 在取消定时器后补删
        self._cleanup_paths: dict[asyncio.Task, tuple[str, float]] = {}
        # 注入配置访问器给 api 模块
        ncmapi.set_config_getter(lambda: self.plugin.config or {})

    # ──────────── 裸 #听N 跨插件抢占 ────────────

    async def mark_session_owner(self, scope: str) -> None:
        """点歌出列表后，把本插件记录为该 scope（群/私聊）内「最近活跃的音乐插件」。

        标记文件格式（三音乐插件统一，v2）：
        ``{"version": 2, "scopes": {"<scope>": {"plugin": "<插件名>", "ts": <unix秒>}}}``；
        读到旧格式（无 scopes 键）一律视为「无主」。
        """
        global _owner_marker_warned
        name = PLUGIN_NAME

        def _w() -> bool:
            try:
                p = _owner_marker_path()
                if p is None:
                    return False
                p.parent.mkdir(parents=True, exist_ok=True)
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
                scopes = data.get("scopes") if isinstance(data, dict) else None
                scopes = dict(scopes) if isinstance(scopes, dict) else {}
                scopes[scope] = {"plugin": name, "ts": int(time.time())}
                # 防止长期跨群累积无界增长：超 256 个 scope 按 ts 保留最新 256 个
                if len(scopes) > 256:
                    keep = sorted(
                        scopes.items(),
                        key=lambda kv: (kv[1].get("ts") or 0) if isinstance(kv[1], dict) else 0,
                        reverse=True,
                    )[:256]
                    scopes = dict(keep)
                # 临时文件名带插件专属后缀，避免三插件并发写同一 tmp 互相踩踏
                tmp = p.with_name(f"{p.stem}.{PLUGIN_NAME}.tmp")
                tmp.write_text(
                    json.dumps({"version": 2, "scopes": scopes}, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(tmp, p)  # 原子替换，避免并发写坏
                return True
            except Exception:
                return False

        ok = await asyncio.to_thread(_w)
        if not ok and not _owner_marker_warned:
            _owner_marker_warned = True
            self.log_warn("写入跨插件仲裁标记失败，裸 #听N 仲裁退化为「无主」（仅告警一次）")

    async def is_session_owner(self, event: AstrMessageEvent) -> bool:
        """本插件是否为该 scope（群/私聊）内最近活跃的音乐插件。

        读取语义（三音乐插件统一口径）：
          - 取不到标记路径 → False；文件不存在 → True（无仲裁条件，退化为
            「谁有会话谁响应」）；
          - 内容损坏/旧格式（无 scopes 键）/本 scope 无归属记录 → True（视为无主）；
          - 读取抛异常 → False：宁可偶尔静默，也不能多插件双重响应。
        """
        scope = self.scope(event)
        name = PLUGIN_NAME

        def _r() -> bool:
            p = _owner_marker_path()
            if p is None:
                return False
            try:
                if not p.exists():
                    return True
                raw = p.read_text(encoding="utf-8")
            except Exception:
                logger.debug("[neteasemusic] 读取跨插件仲裁标记失败，本次按非本插件处理")
                return False
            try:
                data = json.loads(raw)
            except Exception:
                return True  # 内容损坏：视为无主，退化为「谁有会话谁响应」
            scopes = data.get("scopes") if isinstance(data, dict) else None
            if not isinstance(scopes, dict):
                return True  # 旧格式（无 scopes 键）：视为无主
            entry = scopes.get(scope)
            if not isinstance(entry, dict):
                return True  # 本群/私聊无主
            return entry.get("plugin") == name

        return await asyncio.to_thread(_r)

    def cfg(self) -> dict:
        return self.plugin.config or {}

    async def save_config(self) -> bool:
        """安全写盘：兼容旧版 AstrBot（无异步写盘 API）。返回是否写入成功。"""
        cfg = self.plugin.config
        try:
            saver = getattr(cfg, "save_config_async", None)
            if callable(saver):
                # AstrBot 4.27.4 起该 API 返回「本次快照是否被提交」：并发写盘
                # 时有更新的快照胜出则返回 False（此时盘上已是更新的配置，本次
                # 修改也已包含在其中，但本次调用确实未提交，故按未提交回报）；
                # 旧版返回 None，语义是「调用未抛异常即已写入」——None 视为成功，
                # 否则会把旧版一律误判为写盘失败。只有明确的 False 才算未落盘。
                return (await saver()) is not False
            sync_saver = getattr(cfg, "save_config", None)
            if callable(sync_saver):
                # 同步写盘 API 无返回值（None）即视为成功，判定口径与上面一致
                return (await asyncio.to_thread(sync_saver)) is not False
        except Exception as e:  # noqa: BLE001
            self.log_warn(f"配置保存失败: {e}")
            return False
        self.log_warn("配置保存失败: 当前 AstrBot 版本不支持配置写盘 API")
        return False

    def _spawn_bg(self, coro):
        """后台任务：持有引用避免被 GC，并记录异常，插件卸载时统一取消。"""
        try:
            task = asyncio.create_task(coro)
        except Exception as e:  # noqa: BLE001
            self.log_warn(f"后台任务启动失败: {e}")
            return None
        self._bg_tasks.add(task)

        def _done(t: asyncio.Task):
            self._bg_tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                self.log_warn(f"后台任务异常: {t.exception()}")

        task.add_done_callback(_done)
        return task

    def schedule_cleanup(self, path: str, keep_sec: int) -> None:
        """延时删除临时文件（延迟下限 MIN_KEEP_SEC）。

        句柄由 _bg_tasks 持有，插件卸载/重载时随 terminate() 取消，
        避免旧实例的清理定时器在重载后继续跑。
        取消也意味着回调不再执行，因此这里把「任务 → 待删路径」登记到
        _cleanup_paths，让 terminate() 在取消后补删这些文件（否则音频/卡片图
        会永远留在 tempDir，重载越频繁残留越多）；定时器正常触发时由
        done 回调自行注销，不会重复删除。
        """
        delay = max(as_int(keep_sec, 0), MIN_KEEP_SEC)
        task = self._spawn_bg(self._cleanup_later(path, delay))
        if task is None:
            return
        self._cleanup_paths[task] = (path, time.time())

        def _forget(t: asyncio.Task):
            self._cleanup_paths.pop(t, None)

        task.add_done_callback(_forget)

    async def _cleanup_later(self, path: str, delay: float) -> None:
        await asyncio.sleep(delay)
        # 删除走线程池：事件循环内不做同步磁盘 IO
        await asyncio.to_thread(self.safe_unlink, path)

    def log_warn(self, msg: str):
        logger.warning(f"[neteasemusic] {msg}")

    def log_info(self, msg: str):
        logger.info(f"[neteasemusic] {msg}")

    def plain(self, text: str) -> Plain:
        return Plain(text=text)

    async def send_chain(self, event: AstrMessageEvent, *components, raise_on_error: bool = False) -> bool:
        """发送消息链；返回是否真的发出。

        ``raise_on_error=True`` 会把发送失败重新抛出：投递层（core/delivery.py）依赖
        这个异常触发降级链——语音直发失败时退回 `Record` 组件、文件发送失败时用
        ffmpeg 压成紧凑 mp3 重试。把异常吞掉，整条降级链就都成了死代码。

        默认 ``False``：只记日志并返回 ``False``，避免平台侧发送失败（风控/超限/协议
        错误）穿透成内核的通用报错——那种情况下用户看到的是「调用插件时出现异常」，
        而且 handler 里的 ``stop_event()`` 也不会执行。调用方按返回值决定是否降级。
        """
        comps = [c for c in components if c is not None]
        if not comps:
            return False
        # 先探测能力，而不是捕获 AttributeError：把「内核太旧、没有 event.send」与
        # 「平台适配器内部抛 AttributeError」区分开，后者应被当成发送失败。
        if getattr(event, "send", None) is None:
            # 事件对象根本没有 send（极旧内核）：退无可退，直接按发送失败处理。
            # 注意这里**不能**再调 event.send 重发文本——它按定义就是 None，
            # 只会抛 AttributeError 掩盖真正的原因。
            texts = [str(t) for t in (getattr(_c, "text", None) for _c in comps) if t]
            self.log_warn(
                "_send_chain：事件对象没有 send 方法，无法发送"
                + (f"（原有文本 {len(texts)} 段）" if texts else "")
            )
            if raise_on_error:
                raise RuntimeError("event.send 不可用，无法发送消息")
            return False
        mc = MessageChain(chain=list(comps))
        mc.use_markdown_ = False
        try:
            await event.send(mc)
            return True
        except Exception as err:  # noqa: BLE001
            self.log_warn(f"_send_chain 发送失败: {err}")
            if raise_on_error:
                raise
            return False

    async def reply(self, event: AstrMessageEvent, text: str):
        try:
            await self.send_chain(event, self.plain(text))
        except Exception as e:  # noqa: BLE001
            import traceback as _tb

            self.log_warn(f"_reply 发送失败: {e}\n{_tb.format_exc()}")

    def scope(self, event: AstrMessageEvent) -> str:
        """会话作用域键（群/私聊）。

        加平台前缀防止多平台 ID 撞车（如 aiocqhttp 群号与 qqofficial 用户 ID 相同）。
        注意：KV 会话键随之变化，旧格式（无前缀）会话在升级后一次性失效
        （TTL 仅 10 分钟，影响可接受）。
        """
        gid = getattr(event.message_obj, "group_id", None)
        return f"{event.get_platform_name()}:{gid or event.get_sender_id()}"

    def user_key(self, event: AstrMessageEvent) -> str:
        """用户维度键。

        加平台前缀与 scope() 同理：不同平台的用户 ID 可能重复。
        本插件中 user_key 只用作进程内临时缓存/轮询任务的键（登录态是全局
        cookie 配置，不按 user_key 持久化），加前缀无迁移成本。
        """
        return f"{event.get_platform_name()}:{event.get_sender_id()}"

    def check_cmd(
        self, event: AstrMessageEvent, pattern: str, *, song_request: bool = False
    ) -> re.Match | None:
        cfg = self.cfg()
        if not cfg.get("enable", True):
            return None
        if song_request and cfg.get("enableSongRequest") is False:
            return None
        return re.match(pattern, event.message_str.strip(), re.IGNORECASE)

    async def start_select(
        self,
        event: AstrMessageEvent,
        action: str,
        kw: str,
        *,
        label: str,
        verb: str,
        user_key: str,
    ) -> None:
        """进入"先选歌再操作"流程（实现见 core/lists.py）。"""
        await listlib.start_select(
            self, event, action, kw, label=label, verb=verb, user_key=user_key
        )

    async def show_lyric(self, event: AstrMessageEvent, song: dict, user_key: str) -> None:
        lr = await ncmapi.lyric(song["id"], user_key=user_key)
        lines = self.extract_lyric_lines(lr.get("lrc") or "", lr.get("tlyric") or "")
        if not lines:
            await self.reply(event, "暂无歌词")
            return
        await self.send_lyric_pages(event, song, lines, base_tip=MSG_LYRIC_TIP)

    async def show_lyric_word(self, event: AstrMessageEvent, song: dict, user_key: str) -> None:
        lr = await ncmapi.lyric_new(song["id"], user_key=user_key)
        lines = self.extract_yrc_lines(lr.get("yrc") or "")
        if not lines:
            await self.reply(event, "该歌曲暂无逐字歌词，已改为显示普通歌词")
            await self.show_lyric(event, song, user_key)
            return
        await self.send_lyric_pages(event, song, lines, base_tip="逐字歌词来自网易云音乐")

    async def send_lyric_pages(
        self, event: AstrMessageEvent, song: dict, lines: list, *, base_tip: str
    ) -> None:
        pages = [
            lines[i : i + LYRIC_PAGE_LINES]
            for i in range(0, len(lines), LYRIC_PAGE_LINES)
        ]
        total = len(lines)
        for pi, page_lines in enumerate(pages):
            data = cardlib.build_lyric_card_data(song, page_lines, line_count=total)
            data["tip"] = (
                f"{base_tip} · 第 {pi + 1}/{len(pages)} 页"
                if len(pages) > 1
                else base_tip
            )
            ok = await self.reply_card_or_text(
                event,
                tpl_name="ncm-lyric",
                data=data,
                format_text=lambda d: cardlib.format_lyric_text(
                    song, d.get("lines") or []
                ),
            )
            if not ok:
                break

    async def show_comment(self, event: AstrMessageEvent, song: dict, user_key: str) -> None:
        comments = await ncmapi.comment(song["id"], limit=20, user_key=user_key)
        if not comments:
            await self.reply(event, "该歌曲暂无评论")
            return
        data = cardlib.build_comment_card_data(song, comments, total=len(comments))
        await self.reply_card_or_text(
            event,
            tpl_name="ncm-comment",
            data=data,
            format_text=lambda d: cardlib.format_comment_text(song, comments),
        )

    async def show_simi(self, event: AstrMessageEvent, song: dict, user_key: str) -> None:
        songs = await ncmapi.simi_songs(song["id"], limit=10, user_key=user_key)
        if not songs:
            await self.reply(event, "暂无相似歌曲")
            return
        await self.list_to_session(event, f"相似歌曲 · {song['name'] or ''}", songs)

    @staticmethod
    def _match_song_mv(candidates: list, name: str, artist: str) -> list:
        """从 MV 检索结果里挑歌名对得上的候选（歌名命中优先，其次歌手命中）。

        网易 MV 检索经常混入「同名但不同歌手」的结果（实测按「晴天」检索返回
        高伟 / 邓天晴 / zeevi 三首《晴天》），因此不能直接取第一条。
        """
        if not candidates or not name:
            return []
        target = _mv_name_key(name)
        if not target:
            return []
        hits = []
        for item in candidates:
            cand = _mv_name_key(item.get("name") or "")
            if cand and (cand == target or target in cand or cand in target):
                hits.append(item)
        if not hits:
            return []
        if artist:
            hits.sort(key=lambda item: artist not in (item.get("artist") or ""))
        return hits

    async def show_mv(self, event: AstrMessageEvent, song: dict, user_key: str) -> None:
        mvid = song.get("mvid") or 0
        if mvid:
            mvs = [
                {
                    "id": mvid,
                    "name": song.get("name") or "",
                    "artist": song.get("artist") or "",
                    "duration": "",
                    "playCount": 0,
                }
            ]
        else:
            # 上游当前 build 在 /cloudsearch、/song/detail 里都不给 MV id（``mv`` 恒为 0，
            # 且无 ``mvid`` 字段），直连路径实际不可达，只能按歌名检索。检索质量见
            # ``_match_song_mv``：歌名对不上时宁可回「暂无 MV」，也不给用户看错 MV。
            name = song.get("name") or ""
            artist = song.get("artist") or ""
            candidates = await ncmapi.search_mv(f"{name} {artist}".strip(), limit=5, user_key=user_key)
            mvs = self._match_song_mv(candidates, name, artist)
        if not mvs:
            await self.reply(event, "该歌曲暂无 MV")
            return
        mv_item = mvs[0]
        detail = await ncmapi.mv_detail(mv_item["id"], user_key=user_key)
        url = await ncmapi.mv_url(mv_item["id"], user_key=user_key)
        lines = [
            f"🎬 MV：{detail.get('name') or mv_item['name']} - {detail.get('artist') or mv_item['artist']}",
            f"时长：{detail.get('duration') or mv_item['duration']} · 播放：{cardlib.fmt_count(detail.get('playCount') or mv_item['playCount'])}",
        ]
        if detail.get("desc"):
            lines.append(f"简介：{detail['desc']}")
        lines.append(f"详情：https://music.163.com/#/mv?id={mv_item['id']}")
        if url:
            lines.append(f"播放：{url}")
        else:
            lines.append("⚠ 未获取到 MV 播放地址（可能需登录）")
        await self.reply(event, "\n".join(lines))

    async def show_like(
        self, event: AstrMessageEvent, song: dict, user_key: str, *, unlike: bool
    ) -> None:
        unliked_text = f"💔 已取消红心：{song['name']} - {song['artist']}"
        if unlike:
            await ncmapi.like(song["id"], like_=False, user_key=user_key)
            await self.reply(event, unliked_text)
            return
        try:
            checked = await ncmapi.song_like_check([song["id"]], user_key=user_key)
            liked = checked.get(str(song["id"]), False)
        except ApiError:
            liked = False
        await ncmapi.like(song["id"], like_=not liked, user_key=user_key)
        if liked:
            await self.reply(event, unliked_text)
        else:
            await self.reply(event, f"❤️ 已红心：{song['name']} - {song['artist']}")

    async def resolve_playlist(self, kw: str, user_key: str) -> dict | None:
        if not (kw or "").strip():
            return None
        if re.fullmatch(r"\d+", kw):
            pl = await ncmapi.playlist_detail(int(kw), user_key=user_key)
        else:
            pls = await ncmapi.search_playlists(kw, limit=3, user_key=user_key)
            pl = pls[0] if pls else None
        if not pl:
            return None
        return {**pl, "artist": pl.get("creator") or pl.get("artist") or ""}

    async def resolve_album(self, kw: str, user_key: str) -> dict | None:
        if not (kw or "").strip():
            return None
        if re.fullmatch(r"\d+", kw):
            info, _songs = await ncmapi.album_detail(int(kw), user_key=user_key)
            return info or {"id": int(kw), "name": "", "artist": "", "cover": ""}
        albums = await ncmapi.search_albums(kw, limit=3, user_key=user_key)
        return albums[0] if albums else None

    def has_cookie(self) -> bool:
        return bool(self.cfg().get("defaultCookie"))

    async def get_uid(self, user_key: str) -> str:
        """返回当前账号 uid。

        区分两种「拿不到 uid」：
          - 未登录（API 正常返回、无 profile）→ 返回 ``""``，调用方回 ``MSG_NEED_LOGIN``；
          - API 本身不可用（301/风控/服务未启动/网络错误）→ 抛 ``ApiError``，
            调用方必须如实回复真实原因（原实现 ``except Exception: pass`` 会把
            「服务挂掉」谎报成「需要登录」，引导用户做无用的扫码）。
        """
        uid = str(self.cfg().get("defaultUid") or "")
        if uid:
            return uid
        try:
            st = await ncmapi.login_status(user_key=user_key)
        except ApiError as err:
            self.log_warn(f"查询登录态失败: {err}")
            raise
        except Exception as err:  # noqa: BLE001 - 统一转成 ApiError，避免穿透 handler
            self.log_warn(f"查询登录态异常: {type(err).__name__}: {err}")
            raise ApiError(f"获取账号信息失败：{type(err).__name__}: {err}") from err
        profile = st.get("profile") or {}
        if profile and profile.get("userId"):
            uid = str(profile["userId"])
            self.plugin.config["defaultUid"] = uid
            await self.save_config()
            return uid
        return ""

    async def resolve_play(self, song: dict, cfg: dict, user_key: str = "") -> dict:
        quality = cfg.get("quality") or "auto"
        unblock = cfg.get("qualityUnblock", True) is not False
        try:
            play = await ncmapi.song_url_best(
                song.get("id"),
                level=quality,
                user_key=user_key,
                unblock_fallback=unblock,
            )
            return {
                "url": play.get("url", ""),
                "level": play.get("level"),
                "qualityLabel": QUALITY_LABEL.get(
                    play.get("level") or "", play.get("level") or ""
                ),
                "unblocked": bool(play.get("unblocked")),
                "raw": play,
            }
        except ApiError as e:
            return {"url": "", "error": str(e), "raw": getattr(e, "payload", None)}

    async def play_song(
        self,
        event: AstrMessageEvent,
        song: dict,
        *,
        user_key: str,
        source: str = "",
    ) -> None:
        cfg = self.cfg()
        play = await self.resolve_play(song, cfg, user_key)
        quality_label = play.get("qualityLabel") or ""
        if play.get("unblocked"):
            quality_label = unblock_label(quality_label)
        if play.get("url"):
            tip = "正在下载并发送语音/文件…"
        elif play.get("error"):
            tip = play["error"]
        else:
            tip = "⚠ 未获取到播放链接，可尝试 #ncm登录 或换个音质"
        data = cardlib.build_detail_card_data(
            song, quality_label, source=source, tip=tip
        )
        await self.reply_card_or_text(
            event,
            tpl_name="ncm-detail",
            data=data,
            format_text=lambda d: cardlib.format_detail_text(song, play, tip),
        )
        if play.get("url"):
            await deliver_song(
                self.plugin, event, song, play, cfg=cfg
            )

    async def list_to_session(
        self, event: AstrMessageEvent, keyword: str, songs: list, *, tip: str = ""
    ) -> bool:
        """歌曲列表写入会话并出卡片（实现见 core/lists.py）。"""
        return await listlib.list_to_session(self, event, keyword, songs, tip=tip)

    async def playlist_list_to_session(
        self, event: AstrMessageEvent, title: str, pls: list, *, subtitle: str = ""
    ) -> bool:
        """歌单候选列表写入会话（实现见 core/lists.py）。"""
        return await listlib.playlist_list_to_session(
            self, event, title, pls, subtitle=subtitle
        )

    async def album_list_to_session(
        self, event: AstrMessageEvent, title: str, albums: list
    ) -> bool:
        """专辑候选列表写入会话（实现见 core/lists.py）。"""
        return await listlib.album_list_to_session(self, event, title, albums)

    async def expand_playlist(
        self, event: AstrMessageEvent, session: dict, n: int
    ) -> None:
        """会话歌单序号展开（实现见 core/lists.py）。"""
        await listlib.expand_playlist(self, event, session, n)

    async def expand_album(
        self, event: AstrMessageEvent, session: dict, n: int
    ) -> None:
        """会话专辑序号展开（实现见 core/lists.py）。"""
        await listlib.expand_album(self, event, session, n)

    async def render_card(
        self, event: AstrMessageEvent, data: dict, tpl_name: str
    ) -> str | None:
        try:
            tmpl_path = os.path.join(
                PLUGIN_DIR, "resources", "html", tpl_name, f"{tpl_name}.html"
            )
            if not os.path.exists(tmpl_path):
                return None
            raw = await render_card_png(tmpl_path, data)
            if raw is None:
                return None

            d = await get_temp_dir()
            file_path = os.path.join(
                d, f"card_{tpl_name}_{int(time.time() * 1000)}.png"
            )
            await asyncio.to_thread(_write_bytes, file_path, raw)
            return file_path
        except Exception as e:  # noqa: BLE001
            self.log_warn(f"{tpl_name} 本地渲染失败: {e}")
            return None

    async def reply_card_or_text(
        self,
        event: AstrMessageEvent,
        *,
        tpl_name: str,
        data: dict,
        format_text,
    ) -> bool:
        card_path = None
        try:
            card_path = await self.render_card(event, data, tpl_name)
            # 必须看发送结果：卡片被平台拒绝（风控/体积/协议限制）时要落到下面的纯文本兜底，
            # 否则用户什么都收不到，只在日志里留一条警告。
            if card_path and await self.send_chain(event, Image.fromFileSystem(card_path)):
                return True
        except Exception as e:  # noqa: BLE001
            self.log_warn(f"{tpl_name} 卡片渲染失败，回退文本: {e}")
        finally:
            if card_path:
                self.schedule_cleanup(card_path, as_int(self.cfg().get("keepFileSec", 60), 60))
        try:
            text = format_text(data)
            if text and await self.send_chain(event, self.plain(text)):
                return True
        except Exception as e:  # noqa: BLE001
            self.log_warn(f"{tpl_name} 文本兜底失败: {e}")
        return False

    async def save_qr_image(self, b64: str) -> str | None:
        try:
            import base64

            raw = b64
            if "," in raw and raw.split(",", 1)[0].startswith("data:"):
                raw = raw.split(",", 1)[1]
            data = base64.b64decode(raw)

            path = os.path.join(
                await get_temp_dir(),
                f"qr_{int(time.time() * 1000)}.png",
            )
            await asyncio.to_thread(_write_bytes, path, data)
            return path
        except Exception as e:  # noqa: BLE001
            self.log_warn(f"保存二维码失败: {e}")
            return None

    def safe_unlink(self, path: str):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    @staticmethod
    def extract_lyric_lines(lrc: str, tlyric: str = "") -> list:
        def _parse_line(line: str) -> str:
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    obj = json.loads(line)
                    parts = [
                        c.get("tx", "")
                        for c in (obj.get("c") or [])
                        if isinstance(c, dict)
                    ]
                    return "".join(str(p) for p in parts).strip()
                except Exception:
                    return ""
            return re.sub(r"^\[[^\]]*\]", "", line).strip()

        def _clean(raw: str) -> list:
            out = []
            for line in raw.splitlines():
                t = _parse_line(line)
                if t and not re.match(
                    r"^\s*\[(ti|ar|al|by|offset|total):", line, re.IGNORECASE
                ):
                    out.append(t)
            return out

        out = _clean(lrc)
        if tlyric:
            tr = []
            for line in tlyric.splitlines():
                t = _parse_line(line)
                if t:
                    tr.append(t)
                if len(tr) >= TRANSLATION_MAX_LINES:
                    break
            if tr:
                out.append("")
                out.append("── 翻译 ──")
                out.extend(tr)
        return out

    @staticmethod
    def extract_yrc_lines(yrc: str, max_lines: int = YRC_MAX_LINES) -> list:
        out = []
        for line in (yrc or "").splitlines():
            line = line.strip()
            if not line:
                continue
            text = ""
            if line.startswith("{") and line.endswith("}"):
                try:
                    obj = json.loads(line)
                    parts = [
                        c.get("tx", "")
                        for c in (obj.get("c") or [])
                        if isinstance(c, dict)
                    ]
                    text = "".join(str(p) for p in parts).strip()
                except Exception:
                    text = ""
            else:
                text = re.sub(r"^\[[^\]]*\]", "", line)
                text = re.sub(r"\([^)]*\)", "", text).strip()
            if text:
                out.append(text)
            if len(out) >= max_lines:
                break
        return out

    def stop_poll(self, user_key: str):
        """停止扫码轮询（实现见 core/login.py）。"""
        loginlib.stop_poll(self, user_key)

    def start_poll(self, event: AstrMessageEvent, key: str, max_sec: int = 300):
        """开始扫码轮询（实现见 core/login.py）。"""
        loginlib.start_poll(self, event, key, max_sec)

    async def send_status(
        self,
        event: AstrMessageEvent,
        user_key: str,
        *,
        status_data: dict | None = None,
    ):
        """发送状态卡片（实现见 core/panels.py）。"""
        await panellib.send_status(self, event, user_key, status_data=status_data)

    async def handle_resolve(self, event: AstrMessageEvent, text: str) -> bool:
        """解析分享链接/卡片（实现见 core/resolve.py）。"""
        return await resolvelib.handle_resolve(self, event, text)

    async def initialize(self):
        """预热：把 ffmpeg 路径探测（遍历 PATH 的阻塞 IO）挪出首次投递的事件循环。"""
        from .delivery import probe_ffmpeg_path

        try:
            await asyncio.to_thread(probe_ffmpeg_path)
        except Exception as e:  # noqa: BLE001  预热失败不影响后续按需探测
            self.log_warn(f"ffmpeg 预热失败: {e}")

    async def terminate(self):
        for user_key in list(self.active_logins.keys()):
            self.stop_poll(user_key)
        # 取消后台任务（临时文件清理等），避免旧实例的定时器在重载后继续跑。
        # 被取消的清理回调不会再执行，所以先取出已登记的路径，取消后立即补删：
        # 否则已排期的临时文件（音频/卡片图）会永远留在 tempDir。
        pending_files = list(self._cleanup_paths.values())
        tasks = list(self._bg_tasks)
        for task in tasks:
            task.cancel()
        # 等待被取消的任务真正退出后再关会话/浏览器：取消只投递信号，
        # in-flight 的请求/下载可能还握着 aiohttp 会话（关了会报 Session is closed）
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._cleanup_paths.clear()
        now = time.time()
        for path, registered_at in pending_files:
            # 登记不足宽限期的条目可能正处于「已登记、但发送方仍在读盘」的窗口
            # （卡片图在 send_chain 之前就登记）：立即补删会删掉正在发送的文件，
            # 故跳过；它们仍留在 tempDir，不会被别的清理路径误删。
            if now - registered_at < CLEANUP_CANCEL_GRACE_SEC:
                continue
            # best-effort：删除失败（已被删除/占用）静默忽略
            await asyncio.to_thread(self.safe_unlink, path)
        # 关闭复用的 aiohttp 会话，避免热重载后残留连接
        await ncmapi.close_session()
        # 关闭常驻 Chromium（渲染环境缺失时静默跳过）
        try:
            await close_browser()
        except Exception as e:  # noqa: BLE001
            self.log_warn(f"关闭渲染浏览器失败: {e}")
