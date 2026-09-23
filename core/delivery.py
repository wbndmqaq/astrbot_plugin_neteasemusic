from __future__ import annotations

import asyncio
import os
import re
import shutil
import time

import aiohttp
from astrbot.api import logger
from astrbot.api.message_components import File, Record

from .api import USER_AGENT, as_int, get_session
from .quality import QUALITY_LABEL

PLUGIN_NAME = "astrbot_plugin_neteasemusic"

# ──────────── 常量 ────────────

# 网易云 CDN 下载请求头（Referer 必须是音乐主页，否则部分链接触发 403）
MUSIC_REFERER = "https://music.163.com/"
# QQ 官方平台单文件上限（>10MB 需分片上传，无分片能力时跳过文件上传）
QQ_FILE_SIZE_LIMIT = 10 * 1024 * 1024
# aiocqhttp 跨容器直发（base64 内联）的压缩触发阈值：超过则先压成紧凑 mp3
AIOCQ_COMPRESS_THRESHOLD = 8 * 1024 * 1024
# 流式下载块大小 / 落盘攒批阈值 / 最小有效音频体积
STREAM_CHUNK_SIZE = 256 * 1024
FLUSH_SIZE = 1024 * 1024
MIN_AUDIO_BYTES = 256
# 临时文件清理延迟下限（秒）：keepFileSec=0 也不能在发送端读盘前就删除
MIN_KEEP_SEC = 5
# cancel/terminate 补删的宽限期（秒）：登记不足该秒数的临时文件可能正处于
# 「已登记、但发送方仍在读盘」窗口（卡片图在 send_chain 之前就登记），
# 此时直接补删会把正在发送的文件删掉，故跳过年轻条目（宁可残留也不误删）。
CLEANUP_CANCEL_GRACE_SEC = 5


def _platform_name(event) -> str:
    try:
        name = event.get_platform_name()
        if not name:
            return ""
        return str(name)
    except Exception:
        return ""


def _is_qqofficial(event) -> bool:
    return "qq_official" in _platform_name(event)


def _is_weixin_oc(event) -> bool:
    return "weixin_oc" in _platform_name(event)


def _is_aiocqhttp(event) -> bool:
    return "aiocqhttp" in _platform_name(event)


def _file_to_base64(path: str) -> str:
    import base64

    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


async def _aiocq_call_action(event, action: str, sid: int, segs: list) -> None:
    """aiocqhttp 直连 OneBot call_action 发送原始消息段。

    bot 实例来自 event.bot（CQHttp）；napcat 与 AstrBot 跨容器时 file:// 路径
    不可见（realpath ENOENT），且 File/Record 组件对 base64:// 有各自的坑
    （File 擦空不存在路径、Record 强制转 WAV），因此走底层 action 直发 base64。
    """
    bot = getattr(event, "bot", None)
    if bot is None:
        raise RuntimeError("无法获取 aiocqhttp bot 实例")
    is_group = action == "send_group_msg"
    if is_group:
        await bot.call_action(action, group_id=int(sid), message=segs)
    else:
        await bot.call_action(action, user_id=int(sid), message=segs)


async def _aiocq_send_file(event, text: str, display: str, path: str) -> None:
    """aiocqhttp 发送文件：无损/大文件已由调用方压成 mp3，这里 base64 内联直发。"""
    b64 = await asyncio.to_thread(_file_to_base64, path)
    segs: list = []
    if text:
        segs.append({"type": "text", "data": {"text": text}})
    segs.append({"type": "file", "data": {"file": f"base64://{b64}", "name": display}})
    is_group = bool(getattr(event.message_obj, "group_id", None))
    sid = event.message_obj.group_id if is_group else event.get_sender_id()
    await _aiocq_call_action(event, "send_group_msg" if is_group else "send_private_msg", int(sid), segs)


async def _aiocq_send_record(event, text: str, src_path: str) -> tuple[bool, str]:
    """aiocqhttp 语音：紧凑音频以 base64:// record 段直发（OneBot v11 标准段）。

    不在插件侧预编码 silk：协议端（NapCat / SnowLuma 等按 OneBot v11 实现的 NTQQ
    框架）对 record 段统一「非 silk → 自带 ffmpeg addon 转 Tencent silk
    （\\x02#!SILK_V3、24kHz 单声道），时长用 ffmpeg 对原始文件精确探测」。
    此前自编的裸 silk 会被协议端按魔数透传——非标码流导致手机无法播放、
    时长探测失败钳到 1 秒。走 base64 直发而非 Record 组件，是为了绕开 AstrBot
    把语音强制转 WAV 再编码（载荷 ~50MB+ → WS 超时）。任何一步失败返回
    (False, 原因)，由调用方退回标准 Record 组件发送。
    """
    try:
        b64 = await asyncio.to_thread(_file_to_base64, src_path)
        segs: list = []
        if text:
            segs.append({"type": "text", "data": {"text": text}})
        segs.append({"type": "record", "data": {"file": f"base64://{b64}"}})
        is_group = bool(getattr(event.message_obj, "group_id", None))
        sid = event.message_obj.group_id if is_group else event.get_sender_id()
        await _aiocq_call_action(event, "send_group_msg" if is_group else "send_private_msg", int(sid), segs)
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _qq_official_chunked_upload_supported(astrbot_version: str | None = None) -> bool:
    """AstrBot ≥ 4.27.3 的 QQ 官方适配器对本地大文件自动走分片上传。

    Args:
        astrbot_version: AstrBot 版本串；缺省取运行时的 ``astrbot.__version__``。
    """
    if astrbot_version is None:
        try:
            from astrbot import __version__ as astrbot_version
        except Exception:  # noqa: BLE001 - 清理失败不影响投递
            return False
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", str(astrbot_version))
    return bool(m) and tuple(int(x) for x in m.groups()) >= (4, 27, 3)


def _should_block_qqofficial_file(
    *,
    is_qqoff: bool,
    want_file: bool,
    file_size: int,
    ext: str,
    cfg: dict,
) -> bool:
    """QQ 官方平台是否跳过文件上传，仅发语音。

    开启 ``qqofficialChunkedUpload`` 且 AstrBot ≥ 4.27.3（适配器对本地大文件自动
    分片上传）时放行 FLAC/>10MB 文件；否则旧守卫生效：>10MB 或 .flac 跳过文件上传。
    """
    if not (is_qqoff and want_file):
        return False
    chunk_on = (
        cfg.get("qqofficialChunkedUpload", True) is not False
        and _qq_official_chunked_upload_supported()
    )
    if chunk_on:
        return False
    return file_size > QQ_FILE_SIZE_LIMIT or ext.lower() == ".flac"
# 临时目录：固定在插件数据目录下（``data/plugin_data/<插件名>/temp``）。
# 不放插件自身目录——插件更新/重装会整体替换该目录，会让在途的下载与卡片图失效。
# 首次调用在线程池里建目录（阻塞 IO 不占事件循环），此后直接命中进程内缓存。
_temp_dir: str = ""


async def get_temp_dir() -> str:
    """返回（并按需创建）插件数据目录下的临时目录。"""
    global _temp_dir
    if _temp_dir:
        return _temp_dir

    def _resolve() -> str:
        import time as _time

        from astrbot.api.star import StarTools

        d = StarTools.get_data_dir(PLUGIN_NAME) / "temp"
        d.mkdir(parents=True, exist_ok=True)
        # 启动后首次触达时顺手扫地：删掉 1 小时前的残留文件（崩溃/强杀时
        # 定时清理与 terminate 补删都不会执行，孤儿文件只能在这里回收）。
        # 在途文件最长几分钟就会被正常清理，不会被误删。
        try:
            now = _time.time()
            for f in d.iterdir():
                if not f.is_file():
                    continue
                try:
                    if now - f.stat().st_mtime > 3600:
                        f.unlink(missing_ok=True)
                except OSError:
                    continue  # 单文件 stat/unlink 竞态（文件被并发清理）不中断扫地
        except Exception:  # noqa: BLE001  扫地失败不影响本次使用
            pass
        return str(d)

    _temp_dir = await asyncio.to_thread(_resolve)
    return _temp_dir




def _clean_track_text(s: str, max_len: int = 40) -> str:
    if not s:
        return ""
    s = str(s)
    s = s.replace("【", "(").replace("】", ")").replace("《", "(").replace("》", ")")
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[:max_len]
    return re.sub(r'[\\/:*?"<>|]', "", s).strip()


def build_music_filename(
    *, singer: str, title: str, quality: str = "", ext: str = "", include_quality: bool = False
) -> str:
    s = _clean_track_text(singer, 30)
    t = _clean_track_text(title, 40)
    base = f"{s}-{t}" if (s and t) else (s or t or "NeteaseMusic")
    if include_quality and quality:
        base = f"{base}_{quality}"
    return f"{base}{ext}"


def _ext_for_quality(quality_hint: str, url: str) -> str:
    q = (quality_hint or "").lower()
    if q in ("flac", "jymaster", "sky", "jyeffect", "hires", "lossless"):
        return ".flac"
    u = (url or "").lower()
    for ext in (".flac", ".ogg", ".m4a", ".mp3", ".wav"):
        if ext in u:
            return ext
    return ".mp3"


async def download_audio(
    url: str, save_dir: str, filename: str = "neteasemusic", timeout_ms: int = 90000, quality_hint: str = ""
) -> dict:
    """流式下载音频到本地临时文件；内容过小/HTML 报错，失败时清理残留文件。"""
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": MUSIC_REFERER,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }
    ext = _ext_for_quality(quality_hint, url)
    safe_name = re.sub(r"[^\w.-]", "", filename) or "neteasemusic"
    file_path = os.path.join(save_dir, f"{safe_name}_{int(time.time() * 1000)}{ext}")

    timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
    size = 0
    # 确保目标文件不存在（时间戳通常唯一，此处双保险避免 ab 追加到残留文件）
    await _unlink_quiet(file_path)
    try:
        # 复用 api 层的模块级会话：连播 30 首时不再重复 TCP/DNS 握手；
        # 超时按每请求传入（不改复用会话的全局默认超时）
        sess = get_session()
        async with sess.get(url, headers=headers, timeout=timeout, allow_redirects=True) as res:
            if res.status >= 400:
                raise RuntimeError(f"下载失败 HTTP {res.status}")
            # 流式分块下载：避免整文件读进内存；每攒 ~1MB 落盘一次（线程池，不阻塞事件循环）
            pending = bytearray()
            first_chunk = True
            async for chunk in res.content.iter_chunked(STREAM_CHUNK_SIZE):
                if first_chunk:
                    head = chunk[:32].decode("utf-8", errors="ignore").lower()
                    if "<html" in head or "<!doctype" in head:
                        raise RuntimeError("下载内容为 HTML，音频链接已失效")
                    first_chunk = False
                size += len(chunk)
                pending.extend(chunk)
                if len(pending) >= FLUSH_SIZE:
                    await asyncio.to_thread(_append_bytes, file_path, bytes(pending))
                    pending.clear()
            if pending:
                await asyncio.to_thread(_append_bytes, file_path, bytes(pending))
            if size < MIN_AUDIO_BYTES:
                raise RuntimeError("下载内容过小，可能是无效链接")
    except Exception:
        await _unlink_quiet(file_path)
        raise
    return {"filePath": file_path, "size": size}


async def _unlink_quiet(path: str) -> None:
    """异步删除临时文件（不存在或删除失败时静默）。"""

    def _rm():
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:  # noqa: BLE001 - 清理失败不影响投递
            pass

    await asyncio.to_thread(_rm)


def _write_bytes(path: str, data: bytes):
    with open(path, "wb") as f:
        f.write(data)


def _append_bytes(path: str, data: bytes):
    with open(path, "ab") as f:
        f.write(data)


def _schedule_cleanup(file_path: str, keep_sec: int, plugin) -> None:
    """调度临时文件清理（延迟下限 MIN_KEEP_SEC，句柄由 service 持有并随卸载取消）。"""
    plugin.service.schedule_cleanup(file_path, keep_sec)


async def _send_music_segment(event, music_data: dict) -> bool:
    """以 OneBot v11 ``music`` 段直发原生音乐卡，平台不支持时返回 False。

    适配器实例是 ``event.bot``（CQHttp）；``event.platform``（与 ``platform_meta``
    同指向的兼容别名）只是平台元数据，没有发送接口。OneBot 侧只有 ``call_action``，
    不存在 ``send_api``。
    """
    bot = getattr(event, "bot", None)
    call_action = getattr(bot, "call_action", None)
    if call_action is None:
        return False
    is_group = bool(getattr(event.message_obj, "group_id", None))
    sid = event.message_obj.group_id if is_group else event.get_sender_id()
    target = {"group_id": int(sid)} if is_group else {"user_id": int(sid)}
    action = "send_group_msg" if is_group else "send_private_msg"
    try:
        await call_action(
            action,
            message=[{"type": "music", "data": music_data}],
            **target,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[neteasemusic] 音乐卡片发送失败（{action}）: {e}")
        return False


async def send_native_music_card(event, music_id: str) -> bool:
    return await _send_music_segment(event, {"type": "163", "id": str(music_id)})


# ffmpeg 路径缓存：``shutil.which`` 会遍历 PATH 做多次 stat（阻塞 IO），而它在每首歌
# 投递前都会被调用一次；进程运行期间可执行文件不会变，缓存即可。
_ffmpeg_cache: str | None = None
_ffmpeg_checked = False


def probe_ffmpeg_path() -> str | None:
    """供启动预热调用（线程池内执行）：触发一次 ffmpeg 路径探测并缓存。"""
    return _ffmpeg_path()


def _ffmpeg_path() -> str | None:
    """返回 ffmpeg 可执行路径；未安装返回 None（结果进程内缓存）。"""
    global _ffmpeg_cache, _ffmpeg_checked
    if not _ffmpeg_checked:
        try:
            _ffmpeg_cache = shutil.which("ffmpeg")
        except Exception:  # noqa: BLE001
            _ffmpeg_cache = None
        _ffmpeg_checked = True
    return _ffmpeg_cache


async def _compress_to_mp3(local_path: str, bitrate_kbps: int = 128) -> str | None:
    """用 ffmpeg 把音频压成紧凑 mp3，返回新文件路径；ffmpeg 缺失或失败返回 None。

    输出放在源文件同目录，文件名 ``compact_<毫秒时间戳>.mp3``。
    """
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        return None
    out_path = os.path.join(
        os.path.dirname(local_path), f"compact_{int(time.time() * 1000)}.mp3"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y", "-i", local_path, "-vn", "-b:a", f"{bitrate_kbps}k", "-ac", "2",
            out_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # ffmpeg 偶发挂起（坏文件/管道卡死）会永久阻塞 wait()，卡死整个连播循环；
        # 120 秒足够压完一首，超时按压缩失败处理并杀掉残留进程
        return_code = await asyncio.wait_for(proc.wait(), timeout=120)
    except (TimeoutError, asyncio.TimeoutError):
        try:
            proc.kill()
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass
        # 清理压缩到一半的半成品，避免残留被误当成有效音频
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except Exception:  # noqa: BLE001
            pass
        return None
    except Exception:
        return None
    if return_code != 0 or not os.path.exists(out_path):
        return None
    return out_path


class _LocalAudioDelivery:
    """一次「本地音频双通道投递」的上下文。

    把原先 153 行的单函数流程拆成有序私有步骤，**调用顺序与分支结果不变**：

        run() → _prepare_compact() → _build_file_payload()
              → _send_vocal() / _send_file_payload() → _schedule_asset_cleanup()
              → 补发未消费的文案

    - 个人微信（weixin_oc）出站不支持 Record 语音 → 语音自动降级为文件发送
    - QQ 官方大文件（>10MB/FLAC）守卫：分片上传不可用时跳过文件仅发语音
    - 大文件/FLAC 无法作为文件发送时（守卫拦截、或发送失败）→ ffmpeg 压成紧凑 mp3 兜底
    - 文案（pending_text）挂在首个成功发送的媒体上；全部失败则单独补发文案兜底
    - 语音/文件互不阻塞：任一失败不影响另一个；清理始终执行避免临时文件残留
    """

    def __init__(
        self,
        plugin,
        event,
        *,
        cfg: dict,
        is_qqoff: bool,
        is_wxoc: bool,
        is_aiocq: bool,
        title: str,
        singer: str,
        local_path: str,
        file_size: int,
        pending_text: str,
        filename_quality: str,
        include_quality: bool,
    ):
        self.plugin = plugin
        self.event = event
        self.cfg = cfg
        self.is_qqoff = is_qqoff
        self.is_wxoc = is_wxoc
        self.is_aiocq = is_aiocq
        self.title = title
        self.singer = singer
        self.local_path = local_path
        self.file_size = file_size
        self.pending_text = pending_text
        self.filename_quality = filename_quality
        self.include_quality = include_quality

        self.keep_sec = as_int(cfg.get("keepFileSec", 60), 60)
        self.want_vocal = bool(cfg.get("sendVocal"))
        self.want_file = bool(cfg.get("uploadFile"))
        if is_wxoc:
            self.want_vocal = False
            self.want_file = self.want_file or bool(cfg.get("sendVocal"))
        self.ext = os.path.splitext(local_path)[1] or ".mp3"

        # QQ 官方大文件（>10MB/FLAC）守卫：分片上传可用则放行；
        # 否则 ffmpeg 压成紧凑 mp3 兜底
        self.file_blocked = _should_block_qqofficial_file(
            is_qqoff=is_qqoff,
            want_file=self.want_file,
            file_size=file_size,
            ext=self.ext,
            cfg=cfg,
        )
        self.ffmpeg_compress = (
            cfg.get("ffmpegCompress", True) is not False and _ffmpeg_path() is not None
        )
        self.compress_bitrate = max(32, as_int(cfg.get("compressBitrate") or 128, 128))

        self.aiocq_compact: str | None = None
        self.file_payload: tuple | None = None

    def _file_display(self, ext_override: str = "") -> str:
        return build_music_filename(
            singer=self.singer,
            title=self.title,
            quality=self.filename_quality,
            ext=ext_override or self.ext,
            include_quality=self.include_quality,
        )

    async def run(self) -> dict:
        sent_vocal = False
        sent_file = False
        try:
            # 准备阶段也必须包在 try 内：_prepare_compact/_build_file_payload 抛异常、
            # 或协程在此被取消（插件重载）时，finally 仍会执行，已下载的音频才进入清理
            # 调度；否则该文件既不会被定时删除，也不会被 terminate 补删（永久残留）。
            await self._prepare_compact()
            await self._build_file_payload()
            if self.want_vocal:
                sent_vocal = await self._send_vocal()
            if self.file_payload:
                sent_file = await self._send_file_payload()
        finally:
            self._schedule_asset_cleanup()

        if self.pending_text:
            await self.plugin._send_chain(
                self.event, self.plugin._plain(self.pending_text)
            )

        # 语音/文件两条通道都只打日志、没有真的发出时为 False（连播统计据此计数，
        # 否则「下载失败」的歌曲仍会被计成成功）
        return {"ok": sent_vocal or sent_file, "downloaded": True}

    async def _prepare_compact(self) -> None:
        """aiocqhttp(napcat)：跨容器不共享文件系统，file:// 路径不可见(ENOENT)；
        Record 组件强制转 WAV+base64（载荷大→napcat 转码慢→WS 超时）。因此无损/过大
        音频先压成紧凑 mp3，语音走 silk、文件走 base64 内联直发（见 _aiocq_*）。
        """
        if not (self.is_aiocq and self.ffmpeg_compress):
            return
        lossless_or_big = self.ext.lower() in (".flac", ".wav", ".ogg", ".m4a") or (
            self.file_size > AIOCQ_COMPRESS_THRESHOLD
        )
        if not lossless_or_big:
            return
        self.aiocq_compact = await _compress_to_mp3(
            self.local_path, self.compress_bitrate
        )
        if self.aiocq_compact:
            self.plugin._log_warn(
                f"aiocqhttp 无损/大文件已压成紧凑 mp3："
                f"{self.title} - {self.singer} {self.ext} 约 "
                f"{self.file_size / 1024 / 1024:.1f}MB"
            )

    async def _build_file_payload(self) -> None:
        """文件通道候选：(display, 路径, 已压缩标记)；None = 不发文件。"""
        if not self.want_file:
            return
        if self.is_aiocq:
            # aiocqhttp：文件走 base64 直发，不依赖共享挂载；载荷用压缩 mp3 控制
            src = self.aiocq_compact or self.local_path
            display = (
                self._file_display(".mp3")
                if self.aiocq_compact
                else self._file_display()
            )
            self.file_payload = (display, src, True)
            return
        if not self.file_blocked:
            self.file_payload = (self._file_display(), self.local_path, False)
            return
        if self.ffmpeg_compress:
            compressed = await _compress_to_mp3(self.local_path, self.compress_bitrate)
            if compressed:
                self.file_payload = (self._file_display(".mp3"), compressed, True)
        if self.file_payload:
            self.plugin._log_warn(
                f"文件过大已 ffmpeg 压成紧凑 mp3 发送：{self.title} - {self.singer} "
                f"{self.ext} 约 {self.file_size / 1024 / 1024:.1f}MB"
            )
        else:
            self.plugin._log_warn(
                f"文件发送已跳过（QQ 官方）："
                f"{self.title} - {self.singer} {self.ext} 约 "
                f"{self.file_size / 1024 / 1024:.1f}MB，"
                f"{_skip_hint(self.want_vocal)}"
            )

    async def _send_media(self, media_comp) -> None:
        comps = (
            [self.plugin._plain(self.pending_text), media_comp]
            if self.pending_text
            else [media_comp]
        )
        await self.plugin._send_chain(self.event, *comps)

    async def _send_vocal(self) -> bool:
        """语音通道：aiocqhttp 走 silk 直发，失败或其它平台退回 Record 组件。

        返回是否真的发出（供 :meth:`run` 汇总 ok；失败路径的日志与文案不变）。
        """
        voice_path = self.aiocq_compact or self.local_path
        record_ok = False
        if self.is_aiocq:
            ok, reason = await _aiocq_send_record(
                self.event, self.pending_text, voice_path
            )
            record_ok = ok
            if ok:
                self.pending_text = ""
            else:
                self.plugin._log_warn(
                    f"aiocqhttp 语音直发失败（{reason}），退回 Record 组件"
                )
        if not record_ok:
            try:
                await self._send_media(Record.fromFileSystem(voice_path))
                self.pending_text = ""
                return True
            except Exception as e:  # noqa: BLE001
                self.plugin._log_warn(
                    f"语音发送失败{'（QQ 官方）' if self.is_qqoff else ''}: {e}"
                )
                return False
        return True

    async def _send_file_payload(self) -> bool:
        """发送文件；发送失败且未压缩过时 ffmpeg 压成紧凑 mp3 重试一次。

        返回是否真的发出（供 :meth:`run` 汇总 ok；失败路径的日志与文案不变）。
        """
        display, path, is_compressed = self.file_payload
        try:
            if self.is_aiocq:
                await _aiocq_send_file(self.event, self.pending_text, display, path)
                self.pending_text = ""
                return True
            await self._send_media(File(display, file=path))
            self.pending_text = ""
            return True
        except Exception as e:  # noqa: BLE001
            self.plugin._log_warn(
                f"文件发送失败{'（QQ 官方）' if self.is_qqoff else ''}: {e}"
            )
        if is_compressed or not self.ffmpeg_compress:
            return False
        compressed = await _compress_to_mp3(path, self.compress_bitrate)
        if not compressed:
            return False
        self.plugin._log_warn("文件过大发送失败，已 ffmpeg 压成紧凑 mp3 重试")
        try:
            await self._send_media(File(self._file_display(".mp3"), file=compressed))
            self.pending_text = ""
            return True
        except Exception as e2:  # noqa: BLE001
            self.plugin._log_warn(f"压缩版文件发送仍失败: {e2}")
            return False
        finally:
            _schedule_cleanup(compressed, self.keep_sec, self.plugin)

    def _schedule_asset_cleanup(self) -> None:
        _schedule_cleanup(self.local_path, self.keep_sec, self.plugin)
        if self.aiocq_compact and self.aiocq_compact != self.local_path:
            _schedule_cleanup(self.aiocq_compact, self.keep_sec, self.plugin)
        if self.file_payload and self.file_payload[1] not in (
            self.local_path,
            self.aiocq_compact,
        ):
            _schedule_cleanup(self.file_payload[1], self.keep_sec, self.plugin)


async def _deliver_local_audio(
    plugin,
    event,
    *,
    cfg: dict,
    is_qqoff: bool,
    is_wxoc: bool,
    is_aiocq: bool = False,
    title: str,
    singer: str,
    local_path: str,
    file_size: int,
    pending_text: str = "",
    filename_quality: str = "",
    include_quality: bool = False,
) -> dict:
    """把已下载的本地音频按配置双通道投递（语音 silk + 文件），并调度清理临时文件。

    具体步骤见 `_LocalAudioDelivery`；本函数只负责组装上下文并执行（签名不变）。
    """
    return await _LocalAudioDelivery(
        plugin,
        event,
        cfg=cfg,
        is_qqoff=is_qqoff,
        is_wxoc=is_wxoc,
        is_aiocq=is_aiocq,
        title=title,
        singer=singer,
        local_path=local_path,
        file_size=file_size,
        pending_text=pending_text,
        filename_quality=filename_quality,
        include_quality=include_quality,
    ).run()


def _resolve_quality_label(play: dict, cfg: dict) -> str:
    """音质标签（播放结果优先，其次档位映射，最后配置默认；解灰另加后缀）。"""
    quality_label = (
        play.get("qualityLabel")
        or QUALITY_LABEL.get(play.get("level", ""), play.get("level") or "")
        or cfg.get("quality")
        or ""
    )
    if play.get("unblocked"):
        quality_label = f"{quality_label}（解灰）" if quality_label else "解灰音源"
    return quality_label


def _skip_hint(want_vocal: bool) -> str:
    """QQ 官方跳过文件上传时的如实提示（"改发语音"仅在语音开启时成立）。"""
    return "改发语音(silk)转码版本" if want_vocal else "且语音发送未开启，音频文件未发送"


def _native_card_allowed(
    cfg: dict, options: dict, is_qqoff: bool, is_wxoc: bool
) -> bool:
    """QQ 官方无 OneBot send_api，原生音乐卡本是 no-op，显式跳过避免误导。"""
    return (
        not options.get("skipNativeCard", False)
        and bool(cfg.get("sendNativeCard"))
        and not is_qqoff
        and not is_wxoc
    )


def _delivery_platform_flags(event, cfg: dict) -> tuple[bool, bool, bool]:
    """(QQ 官方适配生效, 个人微信, aiocqhttp)。"""
    is_qqoff = _is_qqofficial(event) and cfg.get("qqofficialAdapt", True) is not False
    is_wxoc = _is_weixin_oc(event)
    is_aiocq = _is_aiocqhttp(event)
    return is_qqoff, is_wxoc, is_aiocq


async def _emit_text_info(
    plugin,
    event,
    *,
    cfg: dict,
    title: str,
    singer: str,
    album: str,
    quality_label: str,
    play: dict,
    options: dict,
    is_qqoff: bool,
    is_wxoc: bool,
) -> str:
    """歌曲信息文案。

    非 QQ 官方直接发；QQ 官方下延后，与首个媒体合并以省被动回复额度。
    返回仍需随媒体一起发送的文案（"" 表示无需再发）。
    """
    if options.get("skipTextInfo", False) or not cfg.get("sendTextInfo", True):
        return ""
    lines = [
        f"{cfg.get('identifyPrefix') or ''}网易云音乐",
        f"♪ {title} - {singer}",
        f"专辑：{album}" if album else "",
        f"音质：{quality_label}" if quality_label else "",
        "" if play.get("url") else "⚠ 未获取到播放链，请 #ncm登录",
    ]
    pending_text = "\n".join(x for x in lines if x)
    # weixin_oc 适配器不支持 Plain+媒体合并（send_by_session 每段拆成独立消息），
    # 独立文案显得多余--文件名已含歌手-歌名-音质，详情卡片也已含歌曲信息
    if is_wxoc:
        return ""
    if not is_qqoff:
        await plugin._send_chain(event, plugin._plain(pending_text))
        return ""
    return pending_text


async def _download_audio_to_temp(
    plugin, event, *, play: dict, cfg: dict
) -> tuple[str | None, int, str]:
    """下载播放链到临时文件；失败时已回复文案并返回 (None, 0, 错误串)。"""
    try:
        save_dir = await get_temp_dir()
        timeout = as_int(cfg.get("downloadTimeout") or 90000, 90000)
        dl = await download_audio(
            play["url"],
            save_dir,
            "neteasemusic",
            timeout,
            play.get("level") or cfg.get("quality") or "",
        )
        return dl["filePath"], int(dl.get("size", 0)), ""
    except Exception as err:  # noqa: BLE001
        await plugin._send_chain(
            event,
            plugin._plain(f"下载音频失败：{err}\n可尝试 #ncm登录 后重发，或换一首歌"),
        )
        return None, 0, str(err)


async def deliver_song(
    plugin, event, song: dict, play: dict, *, cfg: dict, options: dict | None = None
) -> dict:
    """交付一首歌：文案 / 原生音乐卡 → 下载 → 双通道投递。"""
    options = options or {}
    title = song.get("name") or "未知歌曲"
    singer = song.get("artist") or "未知歌手"
    quality_label = _resolve_quality_label(play, cfg)

    is_qqoff, is_wxoc, is_aiocq = _delivery_platform_flags(event, cfg)

    allow_native = _native_card_allowed(cfg, options, is_qqoff, is_wxoc)

    pending_text = await _emit_text_info(
        plugin,
        event,
        cfg=cfg,
        title=title,
        singer=singer,
        album=song.get("album") or "",
        quality_label=quality_label,
        play=play,
        options=options,
        is_qqoff=is_qqoff,
        is_wxoc=is_wxoc,
    )

    if allow_native and song.get("id"):
        await send_native_music_card(event, song["id"])

    if not play.get("url"):
        return {"ok": False, "reason": "no_url"}

    need_download = cfg.get("sendVocal") or cfg.get("uploadFile")
    if not need_download:
        # 语音与文件通道都关了。QQ 官方把文案留着「挂媒体」一起发，没有媒体时会被丢弃
        # （sendTextInfo=true 会静默失效），这里补发一条；同时不能回 ok=True——媒体确实
        # 没发出去，否则 #ncm听所有 会把每一首都计成「成功」。
        if pending_text:
            await plugin._send_chain(event, plugin._plain(pending_text))
        return {"ok": False, "reason": "no_channel", "downloaded": False}

    local_path, file_size, err = await _download_audio_to_temp(
        plugin, event, play=play, cfg=cfg
    )
    if local_path is None:
        return {"ok": False, "reason": "download_fail", "error": err}

    return await _deliver_local_audio(
        plugin,
        event,
        cfg=cfg,
        is_qqoff=is_qqoff,
        is_wxoc=is_wxoc,
        is_aiocq=is_aiocq,
        title=title,
        singer=singer,
        local_path=local_path,
        file_size=file_size,
        pending_text=pending_text,
        filename_quality=play.get("level") or cfg.get("quality") or "",
        include_quality=True,
    )
