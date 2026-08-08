from __future__ import annotations

import json
import re
import time
from urllib.parse import urlparse

from .quality import QUALITY_LABEL

# ──────────── 会话存储 ────────────


class SessionStore:


    _mem: dict = {}
    TTL = 600

    @classmethod
    def _key(cls, scope: str) -> str:
        return f"ncm:song:{scope}"

    @classmethod
    async def get(cls, plugin, scope: str) -> dict | None:
        k = cls._key(scope)
        mem_val = cls._mem.get(str(scope))
        if mem_val:
            ts = mem_val.get("updatedAt") or 0
            if time.time() - ts < cls.TTL:
                return mem_val
            cls._mem.pop(str(scope), None)
        try:
            raw = await plugin.get_kv_data(k, None)
            if raw:
                if isinstance(raw, str):
                    raw = json.loads(raw)
                ts = raw.get("updatedAt") or 0
                if time.time() - ts < cls.TTL:
                    return raw
                await plugin.delete_kv_data(k)
        except Exception:
            pass
        return None

    @classmethod
    async def set(cls, plugin, scope: str, session: dict, ttl_sec: int = TTL) -> dict:
        data = {"group_id": scope, "updatedAt": time.time(), **session}
        cls._mem[str(scope)] = data
        try:
            await plugin.put_kv_data(cls._key(scope), json.dumps(data, ensure_ascii=False))
        except Exception:
            pass
        return data

    @classmethod
    async def clear(cls, plugin, scope: str) -> None:
        cls._mem.pop(str(scope), None)
        try:
            await plugin.delete_kv_data(cls._key(scope))
        except Exception:
            pass


# ──────────── 隐私脱敏 ────────────


def mask_api_base(url: str) -> str:
    u = str(url or "").strip()
    if not u:
        return "****"
    try:
        parsed = urlparse(u)
        host = parsed.hostname or ""
        masked_host = "***"
        last_dot = host.rfind(".")
        if last_dot > 0:
            masked_host = "***" + host[last_dot:]
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
    n = int(n or 0)
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


# ──────────── 文本格式化（纯文本兜底） ────────────


def _pay_tag(s: dict) -> str:
    if s.get("payplay"):
        return " [VIP/付费]"
    if s.get("trial"):
        return " [试听]"
    return ""


def format_song_list(lst: list, title: str, start_idx: int = 0, tip: str = "") -> str:
    if not isinstance(lst, list) or not lst:
        return f"♫ {title}\n\n📭 暂无数据\n可能原因：\n1. API 未启动或网络异常\n2. 账号未登录（需要 #ncm登录）\n3. 请求超时，请稍后重试"
    lines = [f"♫ {title}"]
    for i, s in enumerate(lst):
        idx = start_idx + i + 1
        dur = f" ({s['duration']})" if s.get("duration") else ""
        lines.append(f"{idx}. {s.get('name') or '未知'} - {s.get('artist') or '未知'}{_pay_tag(s)}{dur}")
    lines.append(f"\n发送 #ncm听序号 播放（共{len(lst)}首）")
    if tip:
        lines.append(tip)
    return "\n".join(lines)


def format_list_text(lst: list) -> str:
    lines = []
    for i, s in enumerate(lst):
        dur = f" ({s['duration']})" if s.get("duration") else ""
        lines.append(f"{i + 1}. {s.get('name') or '未知'} - {s.get('artist') or '未知'}{_pay_tag(s)}{dur}")
    if not lines:
        return "📭 暂无数据"
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
        f"音质：{quality_label}" if quality_label else "",
    ]
    if tip:
        lines.append(tip)
    return "\n".join(x for x in lines if x)


def format_comment_text(song: dict, comments: list) -> str:
    lines = [f"♪ {song.get('name') or ''} - {song.get('artist') or ''} 热评"]
    for c in comments[:15]:
        lines.append(f"{c['index']}. {c.get('nick') or '匿名'}（{fmt_count(c.get('likes') or 0)}赞）：{(c.get('content') or '')[:80]}")
    return "\n".join(lines) or "📭 暂无评论"


def format_status_text(status: dict) -> str:
    lines = []
    if status.get("loggedIn"):
        lines.append(f"✅ 已登录：{status.get('nickname') or ''}")
        if status.get("uin"):
            lines.append(f"账号：{status['uin']}")
        if status.get("level"):
            lines.append(f"等级：{status['level']}")
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


def format_help_text(cfg: dict, version: str = "") -> str:
    api_hint = api_hint_for(cfg) if cfg.get("apiBase") else "⚠ API 未配置"
    lines = [
        f"🎵 网易云音乐插件 v{version}" if version else "🎵 网易云音乐插件",
        f"「{api_hint}」",
        "",
        "── 点歌播放 ──",
        "#ncm点歌 关键词       搜索并列出歌曲",
        "#ncm听N               播放列表第 N 首（可只发 #听N）",
        "#ncm播放 关键词       搜索并直接播放第一首",
        "#ncm歌词 关键词|id    获取歌词",
        "#ncm热搜              热搜榜",
        "",
        "── 发现音乐 ──",
        "#ncm排行 [榜单名]     排行榜列表 / 查看具体榜单",
        "#ncm歌手 关键词       歌手热门歌曲",
        "#ncm专辑 关键词       专辑曲目",
        "#ncm歌单 关键词       歌单曲目",
        "#ncm评论 关键词       歌曲热评",
        "#ncm相似 关键词|id    相似歌曲",
        "#ncm相关歌单 关键词|id 包含该曲的歌单",
        "#ncm新歌 [华语/欧美/日本/韩国]  新歌速递",
        "#ncm精品歌单 [分类]   精品歌单",
        "#ncm搜索建议 关键词   关键词补全",
        "#ncmbanner            首页轮播",
        "#ncm歌单分类          歌单分类",
        "#ncmMV 关键词          MV 详情与播放链接",
        "#ncm相似歌单 关键词|id 相似歌单",
        "#ncm歌手榜 / #ncm热门歌手   歌手榜 / 热门歌手",
        "#ncm新碟 / #ncm新碟榜 [地区] 新碟上架 / 新碟排行",
        "#ncmMV榜               MV 排行",
        "#ncm电台               电台推荐",
        "#ncm歌单榜 [分类]      分类歌单榜",
        "#ncm热门分类           热门歌单分类",
        "#ncm逐字歌词 关键词|id  逐字歌词",
        "#ncm精准 关键词        单曲精准匹配并播放",
        "#ncm歌单评论/专辑评论   歌单/专辑热评",
        "#ncm推荐              推荐歌单（需登录）",
        "#ncm来首歌            随机来一首",
        "#ncm日推              每日推荐（需登录）",
        "#ncm推荐新歌          推荐新歌",
        "#ncm喜欢              我喜欢的音乐（需登录）",
        "#ncm听歌排行          本周听歌排行（需登录）",
        "#ncm历史日推          历史每日推荐（需登录）",
        "#ncm签到 / #ncm云盘 / #ncm最近 / #ncm我的歌单 / #ncm红心 关键词  （需登录）",
        "",
        "── 账号状态 ──",
        "#ncm登录              扫码登录",
        "#ncm状态 / #ncms      登录状态",
        "#ncm登出              登出",
        "",
        "── 管理（主人） ──",
        "#ncm设置              设置面板",
        "#ncm音质 <档位>       修改音质",
        "#ncm api <地址>       修改 API 地址",
        "#ncm 开启/关闭 点歌|解析  开关功能",
        "#ncm测试              测试 API 连通",
        "",
        "── 自动解析 ──",
        "发送 music.163.com / 163music.com 链接自动解析播放",
        "",
        "Tips：发送网易云分享卡片/链接即可自动解析；VIP 歌曲自动解灰。",
    ]
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
        "singerInfo": options.get("singerInfo") or "",
        "albumInfo": options.get("albumInfo") or "",
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
        "title": f"{song.get('name') or ''} - {song.get('artist') or ''}",
        "songName": _clean_name(song.get("name")),
        "singerName": _clean_name(song.get("artist")),
        "albumName": _clean_name(song.get("album")),
        "cover": song.get("cover") or "",
        "songId": song.get("id") or 0,
        "duration": song.get("duration") or "",
        "qualityLabel": quality_label or "",
        "payplay": bool(song.get("payplay")),
        "trial": bool(song.get("trial")),
        "source": source or "",
        "tip": tip or "",
    }


def build_lyric_card_data(song: dict, lines: list, line_count: int = 0) -> dict:
    return {
        "songName": _clean_name(song.get("name")),
        "singerName": _clean_name(song.get("artist")),
        "cover": song.get("cover") or "",
        "albumName": _clean_name(song.get("album")),
        "songId": song.get("id") or 0,
        "lines": lines,
        "lineCount": line_count,
        "tip": "歌词来自网易云音乐",
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
        import datetime

        return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")
    except Exception:
        return ""


def build_comment_card_data(song: dict, comments: list, total: int = 0) -> dict:
    items = []
    for c in comments[:20]:
        nick = c.get("nick") or ""
        items.append({
            "index": c.get("index") or 0,
            "nick": nick,
            "avatar": c.get("avatar") or "",
            "avatarPh": nick[:1] if nick else "♪",
            "time": format_comment_time(c.get("time") or 0),
            "likes": fmt_count(c.get("likes") or 0),
            "content": clean_comment_text(c.get("content")),
            "hot": bool(c.get("hot")),
        })
    return {
        "songName": _clean_name(song.get("name")),
        "singerName": _clean_name(song.get("artist")),
        "cover": song.get("cover") or "",
        "albumName": _clean_name(song.get("album")),
        "songId": song.get("id") or 0,
        "comments": items,
        "total": total or len(comments),
        "tip": "评论来自网易云音乐",
    }


def build_help_card_data(version: str = "", cfg: dict | None = None) -> dict:
    cfg = cfg or {}
    return {
        "version": version or "1.0.0",
        "statCommands": "50+",
        "statQuality": str(cfg.get("quality") or "auto"),
        "apiHint": api_hint_for(cfg),
        "tip": "发送网易云分享卡片/链接自动解析；VIP 歌曲自动解灰；语音/文件投递可配置。",
        "sections": [
            {
                "title": "点歌播放",
                "tag": "全员可用",
                "items": [
                    {"name": "#ncm点歌 关键词", "desc": "搜索并列出歌曲列表", "example": "#ncm点歌 晴天"},
                    {"name": "#ncm听N", "desc": "播放列表第 N 首", "example": "#ncm听1"},
                    {"name": "#ncm播放 关键词", "desc": "搜索并直接播放第一首", "example": "#ncm播放 晴天"},
                    {"name": "#ncm歌词 关键词", "desc": "获取歌词", "example": "#ncm歌词 晴天"},
                    {"name": "#ncm热搜", "desc": "热搜榜", "example": "#ncm热搜"},
                ],
            },
            {
                "title": "发现音乐",
                "tag": "全员可用",
                "items": [
                    {"name": "#ncm排行 [榜单名]", "desc": "排行榜列表 / 具体榜单", "example": "#ncm排行 飙升榜"},
                    {"name": "#ncm歌手 关键词", "desc": "歌手热门歌曲", "example": "#ncm歌手 周杰伦"},
                    {"name": "#ncm专辑 关键词", "desc": "专辑曲目", "example": "#ncm专辑 叶惠美"},
                    {"name": "#ncm歌单 关键词", "desc": "歌单曲目", "example": "#ncm歌单 华语"},
                    {"name": "#ncm评论 关键词", "desc": "歌曲热评", "example": "#ncm评论 晴天"},
                    {"name": "#ncm相似 关键词|id", "desc": "相似歌曲", "example": "#ncm相似 晴天"},
                    {"name": "#ncm相关歌单 关键词|id", "desc": "包含该曲的歌单", "example": "#ncm相关歌单 晴天"},
                    {"name": "#ncm新歌 [地区]", "desc": "新歌速递（华语/欧美/日本/韩国）", "example": "#ncm新歌 华语"},
                    {"name": "#ncm精品歌单 [分类]", "desc": "精品歌单", "example": "#ncm精品歌单 华语"},
                    {"name": "#ncm搜索建议 关键词", "desc": "关键词补全", "example": "#ncm搜索建议 晴天"},
                    {"name": "#ncmbanner", "desc": "首页轮播", "example": "#ncmbanner"},
                    {"name": "#ncm歌单分类", "desc": "歌单分类列表", "example": "#ncm歌单分类"},
                    {"name": "#ncmMV 关键词", "desc": "MV 详情与播放链接", "example": "#ncmMV 晴天"},
                    {"name": "#ncm相似歌单 关键词|id", "desc": "相似歌单", "example": "#ncm相似歌单 晴天"},
                    {"name": "#ncm歌手榜", "desc": "歌手榜", "example": "#ncm歌手榜"},
                    {"name": "#ncm热门歌手", "desc": "热门歌手", "example": "#ncm热门歌手"},
                    {"name": "#ncm新碟", "desc": "新碟上架", "example": "#ncm新碟"},
                    {"name": "#ncm新碟榜 [地区]", "desc": "新碟排行（华语/欧美/韩国/日本）", "example": "#ncm新碟榜 华语"},
                    {"name": "#ncmMV榜", "desc": "MV 排行", "example": "#ncmMV榜"},
                    {"name": "#ncm电台", "desc": "电台推荐", "example": "#ncm电台"},
                    {"name": "#ncm歌单榜 [分类]", "desc": "分类歌单榜", "example": "#ncm歌单榜 华语"},
                    {"name": "#ncm热门分类", "desc": "热门歌单分类", "example": "#ncm热门分类"},
                    {"name": "#ncm逐字歌词 关键词|id", "desc": "逐字歌词", "example": "#ncm逐字歌词 晴天"},
                    {"name": "#ncm精准 关键词", "desc": "单曲精准匹配并播放", "example": "#ncm精准 晴天"},
                    {"name": "#ncm歌单评论 关键词", "desc": "歌单热评", "example": "#ncm歌单评论 华语"},
                    {"name": "#ncm专辑评论 关键词", "desc": "专辑热评", "example": "#ncm专辑评论 叶惠美"},
                ],
            },
            {
                "title": "推荐（需登录）",
                "tag": "全员可用",
                "items": [
                    {"name": "#ncm推荐", "desc": "推荐歌单", "example": "#ncm推荐"},
                    {"name": "#ncm来首歌", "desc": "随机来一首", "example": "#ncm来首歌"},
                    {"name": "#ncm日推", "desc": "每日推荐", "example": "#ncm日推"},
                    {"name": "#ncm推荐新歌", "desc": "推荐新歌", "example": "#ncm推荐新歌"},
                    {"name": "#ncm喜欢", "desc": "我喜欢的音乐", "example": "#ncm喜欢"},
                    {"name": "#ncm听歌排行", "desc": "本周听歌排行", "example": "#ncm听歌排行"},
                    {"name": "#ncm历史日推", "desc": "历史每日推荐", "example": "#ncm历史日推"},
                ],
            },
            {
                "title": "账号扩展",
                "tag": "需登录",
                "items": [
                    {"name": "#ncm签到", "desc": "每日签到领经验", "example": "#ncm签到"},
                    {"name": "#ncm云盘", "desc": "我的云盘歌曲", "example": "#ncm云盘"},
                    {"name": "#ncm最近", "desc": "最近播放歌曲", "example": "#ncm最近"},
                    {"name": "#ncm我的歌单", "desc": "我创建/收藏的歌单", "example": "#ncm我的歌单"},
                    {"name": "#ncm红心 关键词", "desc": "红心/取消红心（自动判断当前状态）", "example": "#ncm红心 晴天"},
                ],
            },
            {
                "title": "账号状态",
                "tag": "全员可用",
                "items": [
                    {"name": "#ncm登录", "desc": "扫码登录", "example": "#ncm登录"},
                    {"name": "#ncm状态 / #ncms", "desc": "查看登录状态", "example": "#ncms"},
                    {"name": "#ncm登出", "desc": "登出", "example": "#ncm登出"},
                ],
            },
            {
                "title": "管理",
                "tag": "主人",
                "items": [
                    {"name": "#ncm设置", "desc": "设置面板", "example": "#ncm设置"},
                    {"name": "#ncm音质 <档位>", "desc": "修改音质", "example": "#ncm音质 lossless"},
                    {"name": "#ncm api <地址>", "desc": "修改 API 地址", "example": "#ncm api http://127.0.0.1:3000"},
                    {"name": "#ncm 开启/关闭 点歌|解析", "desc": "功能开关", "example": "#ncm 关闭 解析"},
                    {"name": "#ncm测试", "desc": "测试 API 连通", "example": "#ncm测试"},
                ],
            },
            {
                "title": "自动解析",
                "tag": "自动",
                "items": [
                    {"name": "网易云链接", "desc": "music.163.com / 163music.com 链接自动解析", "example": "music.163.com/#/song?id=186016"},
                ],
            },
        ],
    }


def build_status_card_data(status: dict) -> dict:
    nickname = status.get("nickname") or ""
    return {
        "title": "网易云音乐 · 登录状态",
        "loggedIn": bool(status.get("loggedIn")),
        "badge": status.get("badge") or ("已登录" if status.get("loggedIn") else "未登录"),
        "nickname": nickname,
        "avatar": status.get("avatar") or "",
        "avatarPh": nickname[:1] or "♪",
        "uin": status.get("uin") or "",
        "level": status.get("level") or "",
        "apiBase": mask_api_base(status.get("apiBase") or ""),
        "keyStatus": status.get("keyStatus") or "",
        "quality": status.get("quality") or "",
        "tip": "发送 #ncm登录 扫码登录；#ncm音质 <档位> 修改音质",
    }
