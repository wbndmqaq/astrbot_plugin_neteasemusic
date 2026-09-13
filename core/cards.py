from __future__ import annotations

import asyncio
import datetime
import json
import re
import time
import unicodedata
from urllib.parse import urlparse

from .api import as_int
from .help_data import HELP_PLAIN_FOOTER, HELP_SECTIONS, PLAIN_HELP_COL  # noqa: F401  再导出保持历史路径
from .messages import (
    LABEL_QUALITY_PREFIX,
    MSG_LYRIC_TIP,
    TIP_HELP,
    TIP_PLAYLIST_SEARCH,
)
from .quality import QUALITY_LABEL

# ──────────── 会话存储 ────────────


class SessionStore:
    _mem: dict = {}
    # 写盘串行锁（见 set()）：保证 KV 落盘顺序与调用顺序一致
    _write_lock = asyncio.Lock()
    TTL = 600
    # 内存缓存条数上限：防止大量群/私聊会话长期驻留导致无界增长。
    # 超过上限时按 updatedAt 淘汰最旧条目（数据已持久化到 KV，淘汰不丢数据）。
    MAX_MEM = 512

    @classmethod
    def _evict_if_needed(cls) -> None:
        if len(cls._mem) <= cls.MAX_MEM:
            return
        overflow = len(cls._mem) - cls.MAX_MEM
        oldest = sorted(
            cls._mem.items(), key=lambda kv: kv[1].get("updatedAt") or 0
        )[:overflow]
        for k, _ in oldest:
            cls._mem.pop(k, None)

    @classmethod
    def _key(cls, scope: str, kind: str = "songs") -> str:
        """KV 键：默认的歌曲会话沿用历史键名，避免老数据失效。"""
        return f"ncm:song:{scope}" if kind == "songs" else f"ncm:sess:{kind}:{scope}"

    @classmethod
    async def get(cls, plugin, scope: str, kind: str = "songs") -> dict | None:
        k = cls._key(scope, kind)
        mem_key = f"{kind}:{scope}"
        mem_val = cls._mem.get(mem_key)
        if mem_val:
            ts = mem_val.get("updatedAt") or 0
            if time.time() - ts < cls.TTL:
                return mem_val
            cls._mem.pop(mem_key, None)
        try:
            raw = await plugin.get_kv_data(k, None)
            if raw:
                if isinstance(raw, str):
                    raw = json.loads(raw)
                ts = raw.get("updatedAt") or 0
                if time.time() - ts < cls.TTL:
                    # 命中 KV 时回填内存，避免同一会话每次都读一次 KV
                    cls._mem[mem_key] = raw
                    cls._evict_if_needed()
                    return raw
                await plugin.delete_kv_data(k)
        except Exception:
            pass
        return None

    @classmethod
    async def set(cls, plugin, scope: str, session: dict, kind: str = "songs") -> dict:
        # 新时间戳必须最后落键：调用方常传 dict(旧会话)（其中携带旧 updatedAt），
        # 若 time.time() 排在 session 展开之前会被旧值覆盖——TTL 便会锚定首次写入，
        # 「读→改→写回」不再续期，用户会遇到一次静默无响应
        data = {"group_id": scope, **session, "updatedAt": time.time()}
        mem_key = f"{kind}:{scope}"
        cls._mem[mem_key] = data
        # 插入后再淘汰，容量严格不超过 MAX_MEM
        cls._evict_if_needed()
        # 写盘串行化：KV 写入顺序与调用顺序一致，避免同 scope 并发写时
        # 后落盘的旧值覆盖新值（等待期间若已被更新的一次写入替代，则本次跳过）
        async with cls._write_lock:
            current = cls._mem.get(mem_key)
            if current is not None and current.get("updatedAt", 0) > data["updatedAt"]:
                return data
            try:
                await plugin.put_kv_data(
                    cls._key(scope, kind), json.dumps(data, ensure_ascii=False)
                )
            except Exception:
                pass
        return data


# ──────────── 隐私脱敏 ────────────


def mask_api_base(url: str) -> str:
    u = str(url or "").strip()
    if not u:
        return "****"
    try:
        parsed = urlparse(u)
        host = parsed.hostname or ""
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host) or ":" in host:
            masked_host = "***"  # IPv4/IPv6 整体打码
        else:
            last_dot = host.rfind(".")
            masked_host = "***" + (host[last_dot:] if last_dot > 0 else "")
        port = f":{parsed.port}" if parsed.port else ""
        path_part = parsed.path if (parsed.path and parsed.path != "/") else ""
        return f"{parsed.scheme}://{masked_host}{port}{path_part}"
    except Exception:
        return "****"


def api_hint_for(cfg: dict) -> str:
    if not cfg.get("apiBase"):
        return "API 未配置"
    return f"API · {mask_api_base(cfg['apiBase']).replace('https://', '').replace('http://', '')}"


def fmt_count(n: float) -> str:
    n = as_int(n, 0)
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def fmt_time_ago(ts_ms, now_ms: int | None = None) -> str:

    if not ts_ms:
        return ""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    sec = (now - int(ts_ms)) // 1000
    if sec < 3600:
        return "刚刚"
    if sec < 86400:
        return f"{sec // 3600} 小时前"
    if sec < 86400 * 30:
        return f"{sec // 86400} 天前"
    return f"{sec // (86400 * 30)} 个月前"


# ──────────── 文本格式化（纯文本兜底） ────────────


def _pay_tag(s: dict) -> str:
    if s.get("payplay"):
        return " [VIP/付费]"
    if s.get("trial"):
        return " [试听]"
    return ""


def format_song_list(lst: list, title: str, tip: str = "") -> str:
    if not isinstance(lst, list) or not lst:
        return f"♫ {title}\n\n📭 暂无数据\n可能原因：\n1. API 未启动或网络异常\n2. 账号未登录（需要 #ncm登录）\n3. 请求超时，请稍后重试"
    lines = [f"♫ {title}"]
    for i, s in enumerate(lst):
        idx = i + 1
        dur = f" ({s['duration']})" if s.get("duration") else ""
        lines.append(f"{idx}. {s.get('name') or '未知'} - {s.get('artist') or '未知'}{_pay_tag(s)}{dur}")
    lines.append(f"\n发送 #ncm听序号 播放（共{len(lst)}首）")
    if tip:
        lines.append(tip)
    return "\n".join(lines)


def format_hot_text(lst: list) -> str:
    lines = []
    for i, h in enumerate(lst):
        score = f"  {fmt_count(h['score'])}" if h.get("score") else ""
        lines.append(f"{i + 1}. {h.get('word') or ''}{score}")
    return "\n".join(lines) or "📭 暂无热搜数据"


def format_lyric_text(song: dict, lines: list) -> str:
    head = f"♪ {song.get('name') or ''} - {song.get('artist') or ''}"
    if not lines:
        return f"{head}\n\n（暂无歌词）"
    return head + "\n" + "\n".join(lines)


def format_detail_text(song: dict, play: dict | None = None, tip: str = "") -> str:
    quality_label = ""
    if play and (play.get("qualityLabel") or play.get("level")):
        quality_label = play.get("qualityLabel") or QUALITY_LABEL.get(play.get("level") or "", play.get("level") or "")
    lines = [
        f"♪ {song.get('name') or '未知'} - {song.get('artist') or '未知'}{_pay_tag(song)}",
        f"专辑：{song.get('album') or ''}" if song.get("album") else "",
        f"{LABEL_QUALITY_PREFIX}{quality_label}" if quality_label else "",
    ]
    if tip:
        lines.append(tip)
    return "\n".join(x for x in lines if x)


def format_comment_text(song: dict, comments: list) -> str:
    lines = [f"♪ {song.get('name') or ''} - {song.get('artist') or ''} 热评"]
    for c in comments[:15]:
        lines.append(
            f"{c['index']}. {c.get('nick') or '匿名'}（{fmt_count(c.get('likes') or 0)}赞）：{(c.get('content') or '')[:80]}"
        )
    return "\n".join(lines) or "📭 暂无评论"


def format_status_text(status: dict) -> str:
    lines = []
    if status.get("loggedIn"):
        lines.append(f"✅ 已登录：{status.get('nickname') or ''}")
        if status.get("uin"):
            lines.append(f"账号：{status['uin']}")
        try:
            lv = int(status.get("level"))
        except (TypeError, ValueError):
            lv = 0
        if lv:
            lines.append(f"等级：{lv}")
        vip = _vip_label(status.get("vipType"), status.get("vipExpire"))
        if vip:
            lv = as_int(status.get("vipLevel"), 0)
            lines.append(f"会员：{vip}" + (f" Lv.{lv}" if lv else ""))
    else:
        lines.append("❌ 未登录")
        lines.append("发送 #ncm登录 扫码登录")
    if status.get("apiBase"):
        lines.append(f"API：{mask_api_base(status['apiBase'])}")
    if status.get("quality"):
        lines.append(f"音质：{status['quality']}")
    if status.get("keyStatus"):
        lines.append(status["keyStatus"])
    return "\n".join(lines)


def _display_width(s: str) -> int:
    """字符串显示宽度（东亚全角按 2 计），纯文本帮助用它对齐命令列。"""
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        for ch in str(s or "")
    )


def _plain_help_line(item: dict, col: int) -> str:
    """把一条帮助数据渲染成纯文本行。

    ``plain`` 为逐字保留的历史整行（合并行 / 超宽行）；否则用
    ``plain_name`` / ``plain_desc``（缺省回落到卡片字段）左对齐到
    ``item['plain_col']`` 或分组列 ``col``。
    """
    if item.get("plain") is not None:
        return item["plain"]
    name = item.get("plain_name") or item.get("name") or ""
    desc = item.get("plain_desc") or item.get("desc") or ""
    col = item.get("plain_col") or col
    return f"{name}{' ' * max(1, col - _display_width(name))}{desc}"


def format_help_text(cfg: dict, version: str = "") -> str:
    """帮助纯文本：与帮助卡片同源（HELP_SECTIONS），不再各维护一份指令清单。"""
    api_hint = api_hint_for(cfg) if cfg.get("apiBase") else "⚠ API 未配置"
    lines = [
        f"🎵 网易云音乐插件 v{version}" if version else "🎵 网易云音乐插件",
        f"「{api_hint}」",
    ]
    for sec in HELP_SECTIONS:
        if not sec.get("plain_no_head", False):
            lines.append("")
            lines.append(f"── {sec.get('plain_title') or sec['title']} ──")
        col = sec.get("plain_col") or PLAIN_HELP_COL
        for item in sec["items"]:
            if item.get("plain_skip"):
                continue
            lines.append(_plain_help_line(item, col))
    lines.extend(HELP_PLAIN_FOOTER)
    return "\n".join(lines)


# ──────────── 卡片数据构建 ────────────


def _clean_name(s) -> str:
    return re.sub(r"<[^>]+>", "", str(s or "")).strip()


def build_list_card_data(keyword: str, songs: list, options: dict | None = None, cfg: dict | None = None) -> dict:
    cfg = cfg or {}
    options = options or {}
    return {
        "keyword": keyword or "歌曲列表",
        "total": len(songs),
        "quality": str(cfg.get("quality") or "auto").upper(),
        "apiHint": api_hint_for(cfg),
        "songs": [
            {
                "index": i + 1,
                "songName": _clean_name(s.get("name")),
                "singerName": _clean_name(s.get("artist")),
                "albumName": _clean_name(s.get("album")),
                "cover": s.get("cover") or "",
                "duration": s.get("duration") or "",
                "payplay": bool(s.get("payplay")),
            }
            for i, s in enumerate(songs)
        ],
        "tip": options.get("tip") or "发送 #ncm听序号 播放（会话内也可 #听序号）；列表约 10 分钟内有效",
    }


def build_detail_card_data(song: dict, quality_label: str = "", source: str = "", tip: str = "") -> dict:
    return {
        "songName": _clean_name(song.get("name")),
        "singerName": _clean_name(song.get("artist")),
        "albumName": _clean_name(song.get("album")),
        "cover": song.get("cover") or "",
        "songId": song.get("id") or 0,
        "duration": song.get("duration") or "",
        "qualityLabel": quality_label or "",
        "payplay": bool(song.get("payplay")),
        "source": source or "",
        "tip": tip or "",
    }


def build_playlist_card_data(
    title: str,
    playlists: list,
    *,
    subtitle: str = "",
    tip: str = "",
    tip_title: str = "提示",
    cfg: dict | None = None,
) -> dict:

    cfg = cfg or {}
    items = []
    for p in playlists:
        items.append(
            {
                "index": p.get("index") or 0,
                "name": _clean_name(p.get("name")),
                "creator": _clean_name(p.get("creator")),
                "cover": p.get("cover") or "",
                "trackCount": int(p.get("trackCount") or 0),
                "playCountText": f"{fmt_count(p.get('playCount') or 0)}播放",
            }
        )
    return {
        "title": title or "歌单列表",
        "subtitle": subtitle or "网易云音乐歌单",
        "total": len(items),
        "totalPlay": fmt_count(sum(int(p.get("playCount") or 0) for p in playlists)),
        "items": items,
        "tip": tip or TIP_PLAYLIST_SEARCH,
        "tipTitle": tip_title,
        "apiHint": api_hint_for(cfg),
    }


def format_playlist_text(title: str, playlists: list, tip: str = "") -> str:

    lines = [f"♫ {title}"]
    for p in playlists:
        lines.append(
            f"{p.get('index') or 0}. {p.get('name') or '未知'}（{fmt_count(p.get('playCount') or 0)}播放 · {p.get('trackCount') or 0}首）"
        )
    lines.append("")
    lines.append(tip or TIP_PLAYLIST_SEARCH)
    return "\n".join(lines)


def build_generic_card_data(
    title: str,
    items: list,
    *,
    subtitle: str = "",
    tip: str = "",
    tip_title: str = "提示",
    stat_mid: str = "",
    stat_mid_label: str = "",
    cfg: dict | None = None,
) -> dict:
    # stat_mid 未传时默认显示条目数，避免 hero 中间格出现 "-"
    stat_mid = stat_mid or str(len(items))
    stat_mid_label = stat_mid_label or "条"

    cfg = cfg or {}
    out = []
    for i, it in enumerate(items):
        out.append(
            {
                "index": i + 1,
                "name": _clean_name(it.get("name") or it.get("main") or ""),
                "sub": _clean_name(it.get("sub") or ""),
                "tag": _clean_name(it.get("tag") or ""),
                "cover": it.get("cover") or "",
            }
        )
    return {
        "title": title or "列表",
        "subtitle": subtitle or "网易云音乐",
        "total": len(out),
        "statMid": stat_mid,
        "statMidLabel": stat_mid_label,
        "items": out,
        "tip": tip or TIP_HELP,
        "tipTitle": tip_title,
        "apiHint": api_hint_for(cfg),
    }


def format_generic_text(title: str, items: list, tip: str = "") -> str:

    lines = [f"♫ {title}"]
    for i, it in enumerate(items):
        name = it.get("name") or it.get("main") or ""
        sub = " · ".join(x for x in (it.get("sub"), it.get("tag")) if x)
        line = f"{i + 1}. {name}"
        if sub:
            line += f"（{sub}）"
        lines.append(line)
    lines.append("")
    lines.append(tip or TIP_HELP)
    return "\n".join(lines)


def build_lyric_card_data(song: dict, lines: list, line_count: int = 0) -> dict:
    return {
        "songName": _clean_name(song.get("name")),
        "singerName": _clean_name(song.get("artist")),
        "cover": song.get("cover") or "",
        "lines": lines,
        "lineCount": line_count,
        "tip": MSG_LYRIC_TIP,
    }


def build_hot_card_data(items: list, title: str = "热搜榜") -> dict:
    return {
        "title": title,
        "subtitle": "网易云音乐热搜",
        "total": len(items),
        "items": [
            {
                "index": i + 1,
                "word": h.get("word") or "",
                "hot": fmt_count(h.get("score") or 0),
            }
            for i, h in enumerate(items)
        ],
        "tip": "发送 #ncm搜索建议 关键词 获取补全建议",
    }


def clean_comment_text(s: str) -> str:
    if not s:
        return ""
    s = str(s)
    s = re.sub(r"\[em\]e\d+\[/em\]", "", s)
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = s.replace("\\r\\n", " ").replace("\\n", " ").replace("\r\n", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def format_comment_time(ts_ms: int) -> str:
    try:
        return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
    except Exception:
        return ""


def build_comment_card_data(song: dict, comments: list, total: int = 0) -> dict:
    items = []
    for c in comments[:20]:
        nick = c.get("nick") or ""
        items.append(
            {
                "nick": nick,
                "avatar": c.get("avatar") or "",
                "avatarPh": nick[:1] if nick else "♪",
                "time": format_comment_time(c.get("time") or 0),
                "likes": fmt_count(c.get("likes") or 0),
                "content": clean_comment_text(c.get("content")),
                "hot": bool(c.get("hot")),
            }
        )
    return {
        "songName": _clean_name(song.get("name")),
        "singerName": _clean_name(song.get("artist")),
        "cover": song.get("cover") or "",
        "comments": items,
        "total": total or len(comments),
        "tip": "评论来自网易云音乐",
    }


def build_help_card_data(
    version: str = "", cfg: dict | None = None, *, stat_commands: str = ""
) -> dict:
    """帮助卡片数据（数据源见 HELP_SECTIONS）。

    ``stat_commands`` 由调用方传入真实指令路由数（``len(handlers.ALL_ROUTES)``），
    不再硬编码统计数字。
    """
    cfg = cfg or {}
    return {
        "version": version or "?",
        "statCommands": stat_commands,
        "statQuality": str(cfg.get("quality") or "auto"),
        "apiHint": api_hint_for(cfg),
        "tip": "发送网易云分享卡片/链接自动解析；VIP 歌曲自动解灰；语音/文件投递可配置。",
        "sections": [
            {
                "title": sec["title"],
                "tag": sec["tag"],
                "items": [
                    {
                        "name": item["name"],
                        "desc": item["desc"],
                        "example": item["example"],
                    }
                    for item in sec["items"]
                ],
            }
            for sec in HELP_SECTIONS
        ],
    }



def _vip_label(vip_type, vip_expire: int = 0) -> str:

    try:
        t = int(vip_type or 0)
    except (TypeError, ValueError):
        t = 0
    if t in (11, 110):
        try:
            exp = int(vip_expire or 0)
        except (TypeError, ValueError):
            exp = 0
        if exp and exp > int(time.time() * 1000):
            return "黑胶SVIP"
        return "黑胶VIP"
    if t > 0:
        return "会员"
    return ""


def build_status_card_data(status: dict) -> dict:
    nickname = status.get("nickname") or ""
    vip_type = as_int(status.get("vipType"), 0)
    vip_expire = as_int(status.get("vipExpire"), 0)
    vip_label = _vip_label(vip_type, vip_expire)
    # level 为 0 / "0" / 空 时一律置空，卡片不显示 "Lv.0"（注意字符串 "0" 也是 truthy）
    raw_level = status.get("level")
    try:
        level = str(int(raw_level)) if int(raw_level) else ""
    except (TypeError, ValueError):
        level = str(raw_level) if raw_level else ""
    return {
        "title": "网易云音乐 · 登录状态",
        "loggedIn": bool(status.get("loggedIn")),
        "badge": status.get("badge") or ("已登录" if status.get("loggedIn") else "未登录"),
        "nickname": nickname,
        "avatar": status.get("avatar") or "",
        "avatarPh": nickname[:1] or "♪",
        "uin": status.get("uin") or "",
        "level": level,
        "vipLabel": vip_label,
        "vipGold": vip_label == "黑胶SVIP",
        "vipLevel": as_int(status.get("vipLevel"), 0),
        "apiBase": mask_api_base(status.get("apiBase") or ""),
        "keyStatus": status.get("keyStatus") or "",
        "quality": status.get("quality") or "",
        "tip": "发送 #ncm登录 扫码登录；#ncm音质 <档位> 修改音质",
    }


def build_settings_card_data(cfg: dict, uid: str = "") -> dict:

    default_cookie = str(cfg.get("defaultCookie") or "")
    cookie_tail = default_cookie[-4:] if len(default_cookie) >= 4 else "****"
    q = str(cfg.get("quality") or "auto")
    return {
        "title": "网易云音乐 · 插件设置",
        "apiBase": mask_api_base(cfg.get("apiBase") or "") or "未配置",
        "apiHint": api_hint_for(cfg),
        "cookieStatus": f"已配置（***{cookie_tail}）" if default_cookie else "未配置",
        "quality": QUALITY_LABEL.get(q, q),
        "maxList": as_int(cfg.get("maxList") or 10, 10),
        "loginStatus": (f"有 Cookie · uid={uid}" if uid else ("默认账号" if default_cookie else "未登录")),
        "toggles": [
            {"name": "点歌", "on": cfg.get("enableSongRequest", True) is not False},
            {"name": "自动解析", "on": cfg.get("enableResolve", True) is not False},
            {"name": "解灰兜底", "on": cfg.get("qualityUnblock", True) is not False},
            {"name": "语音", "on": cfg.get("sendVocal", True) is not False},
            {"name": "文件", "on": cfg.get("uploadFile", True) is not False},
            {"name": "卡片渲染", "on": cfg.get("renderListCard", True) is not False},
            {"name": "扫码登录", "on": cfg.get("qrLoginEnable", True) is not False},
        ],
        "commands": [
            # 写裸 < >，交给模板的 autoescape 处理；预转义实体会被二次转义成 &amp;lt;
            {"cmd": "#ncm音质 <档位>", "desc": "修改音质"},
            {"cmd": "#ncm api <地址>", "desc": "修改 API 地址"},
            {"cmd": "#ncm开启点歌 / #ncm关闭解析", "desc": "功能开关"},
        ],
    }


def format_settings_text(cfg: dict, uid: str = "") -> str:

    default_cookie = str(cfg.get("defaultCookie") or "")
    cookie_tail = default_cookie[-4:] if len(default_cookie) >= 4 else "****"
    q = str(cfg.get("quality") or "auto")
    lines = [
        "🎵 网易云音乐插件设置",
        f"API：{mask_api_base(cfg.get('apiBase') or '') or '未配置'}",
        f"默认Cookie：{'已配置（***' + cookie_tail + '）' if default_cookie else '未配置'}",
        (
            f"点歌：{'开' if cfg.get('enableSongRequest', True) is not False else '关'}　"
            f"自动解析：{'开' if cfg.get('enableResolve', True) is not False else '关'}"
        ),
        f"音质：{QUALITY_LABEL.get(q, q)}",
        f"解灰兜底：{'开' if cfg.get('qualityUnblock', True) is not False else '关'}",
        (
            f"语音：{'开' if cfg.get('sendVocal', True) is not False else '关'}　"
            f"文件：{'开' if cfg.get('uploadFile', True) is not False else '关'}"
        ),
        f"卡片渲染：{'开' if cfg.get('renderListCard', True) is not False else '关'}",
        (
            f"列表上限：{cfg.get('maxList', 10)}　"
            f"扫码登录：{'开' if cfg.get('qrLoginEnable', True) is not False else '关'}"
        ),
        f"登录：{('有 Cookie · uid=' + uid) if uid else (('默认账号') if default_cookie else '未登录')}",
        "",
        "可修改：#ncm音质 <档位> / #ncm api <地址> / #ncm开启点歌 / #ncm关闭解析",
    ]
    return "\n".join(lines)
