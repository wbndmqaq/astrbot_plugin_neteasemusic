"""跨模块复用的用户可见文案。

同一句文案在本插件出现 ≥2 次时抽到这里，各调用点引用常量，避免多处副本漂移。
"""

# ──────────── 错误 / 状态提示 ────────────

# 需要登录（#ncm喜欢 / #ncm听歌排行 / #ncm我的歌单 / #ncm最近 / #ncm历史日推 等）
MSG_NEED_LOGIN = "需要登录后使用，请先 #ncm登录"
# 配置写盘失败（FIX-1：写盘失败时不得再回复"已设置"）
MSG_SAVE_CONFIG_FAIL = (
    "⚠ 配置写入失败（本机 AstrBot 版本可能过旧），请到 WebUI 插件配置中修改，或升级 AstrBot"
)
# 歌单/云盘类失败（f-string 模板，调用点用 .format(err=...)）
MSG_PLAYLIST_FAIL = "获取歌单失败：{err}"
MSG_CLOUD_FAIL = "获取云盘失败：{err}\n需要先 #ncm登录"

# ──────────── 列表 / 详情 tip ────────────

TIP_ALBUM_SEARCH = "发送 #ncm专辑 专辑名 查看曲目"
TIP_PLAYLIST_TRACKS = "回复 #ncm听N 查看该歌单曲目"
TIP_ALBUM_TRACKS = "回复 #ncm听N 查看该专辑曲目"
TIP_PLAYLIST_SEARCH = "发送 #ncm歌单 歌单名 查看曲目"
TIP_ARTIST_SONGS = "发送 #ncm歌手 歌手名 查看热门歌曲"
TIP_HELP = "发送 #ncm帮助 查看全部指令"

# ──────────── 音质标注 ────────────

# 解灰音源后缀 / 无档位标签时的整段文案
LABEL_UNBLOCK_SUFFIX = "（解灰）"
LABEL_UNBLOCK_FALLBACK = "解灰音源"
LABEL_QUALITY_PREFIX = "音质："
MSG_LYRIC_TIP = "歌词来自网易云音乐"


def msg_no_album(kw: str) -> str:
    return f"没有搜到专辑「{kw}」"


def msg_no_playlist(kw: str) -> str:
    return f"没有搜到歌单「{kw}」"


def fmt_playlist_title(name: str) -> str:
    """歌单曲目列表标题（#ncm歌单 / 歌单序号展开共用）。"""
    return f"歌单 · {name}"


def fmt_playlist_show_tip(total: int, shown: int) -> str:
    """歌单只展示前 N 首时的 tip。"""
    return f"歌单共 {total} 首，显示前 {shown} 首；回复 #ncm听N 播放，#ncm听所有 连播"


def fmt_album_tracks_tip(artist: str, total: int) -> str:
    """专辑曲目列表 tip。"""
    return f"歌手：{artist} · 共 {total} 首；回复 #ncm听N 播放，#ncm听所有 连播整张专辑"


def fmt_album_show_tip(artist: str, total: int, shown: int) -> str:
    """专辑只展示前 N 首时的 tip（与歌单口径一致，避免上百首渲染出超长卡片）。"""
    return f"歌手：{artist} · 专辑共 {total} 首，显示前 {shown} 首；回复 #ncm听N 播放，#ncm听所有 连播"


def unblock_label(quality_label: str) -> str:
    """解灰音源在音质标签后追加「（解灰）」。"""
    return (
        f"{quality_label}{LABEL_UNBLOCK_SUFFIX}" if quality_label else LABEL_UNBLOCK_FALLBACK
    )
