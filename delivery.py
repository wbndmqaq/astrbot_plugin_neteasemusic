from __future__ import annotations

import asyncio
import os
import re
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
    async with (
        aiohttp.ClientSession(timeout=timeout) as sess,
        sess.get(url, headers=headers, allow_redirects=True) as res,
    ):
        if res.status >= 400:
            raise RuntimeError(f"下载失败 HTTP {res.status}")
        data = await res.read()
        if len(data) < 256:
            raise RuntimeError("下载内容过小，可能是无效链接")
        head = data[:32].decode("utf-8", errors="ignore").lower()
        if "<html" in head or "<!doctype" in head:
            raise RuntimeError("下载内容为 HTML，音频链接已失效")
        with open(file_path, "wb") as f:
            f.write(data)
    return {"filePath": file_path, "size": len(data)}


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

    keep_sec = int(cfg.get("keepFileSec", 60))
    want_vocal = bool(cfg.get("sendVocal"))
    want_file = bool(cfg.get("uploadFile"))
    # 个人微信（weixin_oc）出站不支持 Record 语音（adapter 的 send_by_session 只收
    # Plain/Image/Video/File，Record 会被静默跳过）→ 语音自动降级为文件发送
    if is_wxoc:
        want_vocal = False
        want_file = want_file or bool(cfg.get("sendVocal"))
    ext = os.path.splitext(local_path)[1] or ".mp3"
    file_display = build_music_filename(
        singer=singer, title=title, quality=play.get("level") or cfg.get("quality") or "", ext=ext
    )
    # QQ 官方 /files 上传 base64 后体积约 +33%，无损 FLAC(~30MB)必然 413，且会被
    # _qqofficial_retry 重试 5 次(约 30s)后放弃并留下裸 markdown 触发 40034011。
    # 文件分量走原始路径不做转码，故按大小/后缀守卫：超限直接跳过文件上传，保留语音(silk)。
    file_size = int(dl.get("size", 0))
    file_blocked = is_qqoff and want_file and (file_size > 10 * 1024 * 1024 or ext.lower() == ".flac")
    if file_blocked:
        plugin._log_warn(
            f"文件发送已跳过（QQ 官方）：{title} - {singer} {ext} 约 {file_size / 1024 / 1024:.1f}MB，"
            "超出官方接口上传限制，改发语音(silk)转码版本"
        )

    async def _send_media(media_comp):
        comps = [plugin._plain(pending_text), media_comp] if pending_text else [media_comp]
        await plugin._send_chain(event, *comps)

    # 语音(silk 转码)与文件互不阻塞：任一失败不影响另一个；清理始终执行避免临时文件残留
    try:
        if want_vocal:
            try:
                await _send_media(Record.fromFileSystem(local_path))
                pending_text = ""
            except Exception as e:
                plugin._log_warn(f"语音发送失败{'（QQ 官方）' if is_qqoff else ''}: {e}")
        if want_file and not file_blocked:
            try:
                await _send_media(File(file_display, file=local_path))
                pending_text = ""
            except Exception as e:
                plugin._log_warn(f"文件发送失败{'（QQ 官方）' if is_qqoff else ''}: {e}")
    finally:
        _schedule_cleanup(local_path, keep_sec)

    # 兜底：所有媒体发送均失败时，至少把文案发出去
    if pending_text:
        await plugin._send_chain(event, plugin._plain(pending_text))

    return {"ok": True, "downloaded": True}
