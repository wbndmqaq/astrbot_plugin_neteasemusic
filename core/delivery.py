from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from pathlib import Path

import aiohttp
from astrbot.api.message_components import File, Record

from .quality import QUALITY_LABEL

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


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
        bot = getattr(getattr(event, "platform", None), "bot", None)
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
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _qq_official_chunked_upload_supported(astrbot_version: str | None = None) -> bool:
    """AstrBot ≥ 4.27.3 的 QQ 官方适配器对本地大文件自动走分片上传。

    Args:
        astrbot_version: AstrBot 版本串；缺省取运行时的 ``astrbot.__version__``。
    """
    if astrbot_version is None:
        try:
            from astrbot import __version__ as astrbot_version
        except Exception:
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
    return file_size > 10 * 1024 * 1024 or ext.lower() == ".flac"


def get_temp_dir(cfg: dict, plugin_dir: str) -> str:
    d = str(cfg.get("tempDir") or "temp/neteasemusic")
    p = Path(d)
    if not p.is_absolute():
        p = Path(plugin_dir) / p
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


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
        "User-Agent": UA,
        "Referer": "https://music.163.com/",
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
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as sess,
            sess.get(url, headers=headers, allow_redirects=True) as res,
        ):
            if res.status >= 400:
                raise RuntimeError(f"下载失败 HTTP {res.status}")
            # 流式分块下载：避免整文件读进内存；每攒 ~1MB 落盘一次（线程池，不阻塞事件循环）
            pending = bytearray()
            first_chunk = True
            async for chunk in res.content.iter_chunked(256 * 1024):
                if first_chunk:
                    head = chunk[:32].decode("utf-8", errors="ignore").lower()
                    if "<html" in head or "<!doctype" in head:
                        raise RuntimeError("下载内容为 HTML，音频链接已失效")
                    first_chunk = False
                size += len(chunk)
                pending.extend(chunk)
                if len(pending) >= 1024 * 1024:
                    await asyncio.to_thread(_append_bytes, file_path, bytes(pending))
                    pending.clear()
            if pending:
                await asyncio.to_thread(_append_bytes, file_path, bytes(pending))
            if size < 256:
                raise RuntimeError("下载内容过小，可能是无效链接")
    except Exception:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        raise
    return {"filePath": file_path, "size": size}


def _write_bytes(path: str, data: bytes):
    with open(path, "wb") as f:
        f.write(data)


def _append_bytes(path: str, data: bytes):
    with open(path, "ab") as f:
        f.write(data)


def _schedule_cleanup(file_path: str, keep_sec: int):
    delay = max(0, keep_sec)
    loop = asyncio.get_running_loop()

    def _rm():
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

    loop.call_later(delay, _rm)


async def send_native_music_card(event, music_id: str) -> bool:
    try:
        bot = event.platform
        send_api = getattr(bot, "send_api", None) or getattr(bot, "sendApi", None)
        if send_api is None:
            return False
        msg = [{"type": "music", "data": {"type": "163", "id": str(music_id)}}]
        is_group = bool(getattr(event.message_obj, "group_id", None))
        action = "send_group_msg" if is_group else "send_private_msg"
        sid = event.message_obj.group_id if is_group else event.get_sender_id()
        try:
            await send_api(action, {"group_id" if is_group else "user_id": int(sid), "message": msg})
            return True
        except Exception:
            try:
                await send_api("send_msg", {"message": msg})
                return True
            except Exception:
                return False
    except Exception:
        return False


def _ffmpeg_path() -> str | None:
    """返回 ffmpeg 可执行路径；未安装返回 None。"""
    try:
        return shutil.which("ffmpeg")
    except Exception:
        return None


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
        return_code = await proc.wait()
    except Exception:
        return None
    if return_code != 0 or not os.path.exists(out_path):
        return None
    return out_path


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

    - 个人微信（weixin_oc）出站不支持 Record 语音 → 语音自动降级为文件发送
    - QQ 官方大文件（>10MB/FLAC）守卫：分片上传不可用时跳过文件仅发语音
    - 大文件/FLAC 无法作为文件发送时（守卫拦截、或发送失败）→ ffmpeg 压成紧凑 mp3 兜底
    - 文案（pending_text）挂在首个成功发送的媒体上；全部失败则单独补发文案兜底
    - 语音/文件互不阻塞：任一失败不影响另一个；清理始终执行避免临时文件残留
    """
    keep_sec = int(cfg.get("keepFileSec", 60))
    want_vocal = bool(cfg.get("sendVocal"))
    want_file = bool(cfg.get("uploadFile"))
    if is_wxoc:
        want_vocal = False
        want_file = want_file or bool(cfg.get("sendVocal"))
    ext = os.path.splitext(local_path)[1] or ".mp3"

    def _file_display(ext_override: str = ext) -> str:
        return build_music_filename(
            singer=singer,
            title=title,
            quality=filename_quality,
            ext=ext_override,
            include_quality=include_quality,
        )

    # QQ 官方大文件（>10MB/FLAC）守卫：分片上传可用则放行；否则 ffmpeg 压成紧凑 mp3 兜底
    file_blocked = _should_block_qqofficial_file(
        is_qqoff=is_qqoff, want_file=want_file, file_size=file_size, ext=ext, cfg=cfg
    )
    ffmpeg_compress = (
        cfg.get("ffmpegCompress", True) is not False and _ffmpeg_path() is not None
    )
    compress_bitrate = max(32, int(cfg.get("compressBitrate") or 128))

    # aiocqhttp(napcat)：跨容器不共享文件系统，file:// 路径不可见(ENOENT)；
    # Record 组件强制转 WAV+base64（载荷大→napcat 转码慢→WS 超时）。因此无损/过大
    # 音频先压成紧凑 mp3，语音走 silk、文件走 base64 内联直发（见 _aiocq_*）。
    aiocq_compact = None
    if is_aiocq and ffmpeg_compress:
        lossless_or_big = ext.lower() in (".flac", ".wav", ".ogg", ".m4a") or file_size > 8 * 1024 * 1024
        if lossless_or_big:
            aiocq_compact = await _compress_to_mp3(local_path, compress_bitrate)
            if aiocq_compact:
                plugin._log_warn(
                    f"aiocqhttp 无损/大文件已压成紧凑 mp3：{title} - {singer} {ext} 约 "
                    f"{file_size / 1024 / 1024:.1f}MB"
                )

    # 文件通道候选：(display, 路径, 已压缩标记)；None = 不发文件
    file_payload = None
    if want_file:
        if is_aiocq:
            # aiocqhttp：文件走 base64 直发，不依赖共享挂载；载荷用压缩 mp3 控制
            src = aiocq_compact or local_path
            display = _file_display(".mp3") if aiocq_compact else _file_display()
            file_payload = (display, src, True)
        elif file_blocked:
            if ffmpeg_compress:
                compressed = await _compress_to_mp3(local_path, compress_bitrate)
                if compressed:
                    file_payload = (_file_display(".mp3"), compressed, True)
            if file_payload:
                plugin._log_warn(
                    f"文件过大已 ffmpeg 压成紧凑 mp3 发送：{title} - {singer} "
                    f"{ext} 约 {file_size / 1024 / 1024:.1f}MB"
                )
            else:
                plugin._log_warn(
                    f"文件发送已跳过（QQ 官方）：{title} - {singer} {ext} 约 "
                    f"{file_size / 1024 / 1024:.1f}MB，"
                    f"{('改发语音(silk)转码版本' if want_vocal else '且语音发送未开启，音频文件未发送')}"
                )
        else:
            file_payload = (_file_display(), local_path, False)

    async def _send_media(media_comp):
        comps = [plugin._plain(pending_text), media_comp] if pending_text else [media_comp]
        await plugin._send_chain(event, *comps)

    async def _send_file_payload() -> None:
        """发送文件；发送失败且未压缩过时 ffmpeg 压成紧凑 mp3 重试一次。"""
        nonlocal pending_text
        display, path, is_compressed = file_payload
        try:
            if is_aiocq:
                await _aiocq_send_file(event, pending_text, display, path)
                pending_text = ""
                return
            await _send_media(File(display, file=path))
            pending_text = ""
            return
        except Exception as e:
            plugin._log_warn(f"文件发送失败{'（QQ 官方）' if is_qqoff else ''}: {e}")
        if is_compressed or not ffmpeg_compress:
            return
        compressed = await _compress_to_mp3(path, compress_bitrate)
        if not compressed:
            return
        plugin._log_warn("文件过大发送失败，已 ffmpeg 压成紧凑 mp3 重试")
        try:
            await _send_media(File(_file_display(".mp3"), file=compressed))
            pending_text = ""
        except Exception as e2:
            plugin._log_warn(f"压缩版文件发送仍失败: {e2}")
        finally:
            _schedule_cleanup(compressed, keep_sec)

    try:
        if want_vocal:
            voice_path = aiocq_compact or local_path
            record_ok = False
            if is_aiocq:
                ok, reason = await _aiocq_send_record(event, pending_text, voice_path)
                record_ok = ok
                if ok:
                    pending_text = ""
                else:
                    plugin._log_warn(f"aiocqhttp 语音直发失败（{reason}），退回 Record 组件")
            if not record_ok:
                try:
                    await _send_media(Record.fromFileSystem(voice_path))
                    pending_text = ""
                except Exception as e:
                    plugin._log_warn(f"语音发送失败{'（QQ 官方）' if is_qqoff else ''}: {e}")
        if file_payload:
            await _send_file_payload()
    finally:
        _schedule_cleanup(local_path, keep_sec)
        if aiocq_compact and aiocq_compact != local_path:
            _schedule_cleanup(aiocq_compact, keep_sec)
        if file_payload and file_payload[1] not in (local_path, aiocq_compact):
            _schedule_cleanup(file_payload[1], keep_sec)

    if pending_text:
        await plugin._send_chain(event, plugin._plain(pending_text))

    return {"ok": True, "downloaded": True}


async def deliver_song(
    plugin, event, song: dict, play: dict, *, cfg: dict, plugin_dir: str, options: dict | None = None
) -> dict:
    options = options or {}
    title = song.get("name") or "未知歌曲"
    singer = song.get("artist") or "未知歌手"

    quality_label = (
        play.get("qualityLabel")
        or QUALITY_LABEL.get(play.get("level", ""), play.get("level") or "")
        or cfg.get("quality")
        or ""
    )
    if play.get("unblocked"):
        quality_label = f"{quality_label}（解灰）" if quality_label else "解灰音源"

    skip_text = options.get("skipTextInfo", False)
    skip_native = options.get("skipNativeCard", False)

    is_qqoff = _is_qqofficial(event) and cfg.get("qqofficialAdapt", True) is not False
    is_wxoc = _is_weixin_oc(event)
    is_aiocq = _is_aiocqhttp(event)

    # QQ 官方无 OneBot send_api，原生音乐卡本是 no-op，显式跳过避免误导
    allow_native = (not skip_native) and cfg.get("sendNativeCard") and not is_qqoff and not is_wxoc

    # 文案：非 QQ 官方直接发；QQ 官方下延后，与首个媒体合并以省被动回复额度
    pending_text = ""
    if not skip_text and cfg.get("sendTextInfo", True):
        lines = [
            f"{cfg.get('identifyPrefix') or ''}网易云音乐",
            f"♪ {title} - {singer}",
            f"专辑：{song['album']}" if song.get("album") else "",
            f"音质：{quality_label}" if quality_label else "",
            "" if play.get("url") else "⚠ 未获取到播放链，请 #ncm登录",
        ]
        pending_text = "\n".join(x for x in lines if x)
        # weixin_oc 适配器不支持 Plain+媒体合并（send_by_session 每段拆成独立消息），
        # 独立文案显得多余--文件名已含歌手-歌名-音质，详情卡片也已含歌曲信息
        if is_wxoc:
            pending_text = ""
        elif not is_qqoff:
            await plugin._send_chain(event, plugin._plain(pending_text))
            pending_text = ""

    if allow_native and song.get("id"):
        await send_native_music_card(event, song["id"])

    if not play.get("url"):
        return {"ok": False, "reason": "no_url"}

    need_download = cfg.get("sendVocal") or cfg.get("uploadFile")
    if not need_download:
        return {"ok": True, "downloaded": False}

    local_path = ""
    try:
        save_dir = get_temp_dir(cfg, plugin_dir)
        timeout = int(cfg.get("downloadTimeout") or 90000)
        dl = await download_audio(
            play["url"], save_dir, "neteasemusic", timeout, play.get("level") or cfg.get("quality") or ""
        )
        local_path = dl["filePath"]
    except Exception as err:
        await plugin._send_chain(event, plugin._plain(f"下载音频失败：{err}\n可尝试 #ncm登录 后重发，或换一首歌"))
        return {"ok": False, "reason": "download_fail", "error": str(err)}

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
        file_size=int(dl.get("size", 0)),
        pending_text=pending_text,
        filename_quality=play.get("level") or cfg.get("quality") or "",
        include_quality=True,
    )
