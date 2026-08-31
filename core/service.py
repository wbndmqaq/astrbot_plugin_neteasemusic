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

try:
    from . import api as ncmapi
    from . import cards as cardlib
    from .api import ApiError
    from .delivery import _write_bytes, deliver_song, get_temp_dir
    from .quality import QUALITY_LABEL
    from .render import render_card_png
except ImportError:
    from core import api as ncmapi
    from core import cards as cardlib
    from core.api import ApiError
    from core.delivery import _write_bytes, deliver_song, get_temp_dir
    from core.quality import QUALITY_LABEL
    from core.render import render_card_png

PLUGIN_DIR = str(Path(__file__).resolve().parent.parent)
NEW_SONG_AREAS = {"华语": 7, "欧美": 96, "日本": 8, "韩国": 16}
PLAY_ALL_LIMIT = 30


def _owner_marker_path() -> Path:
    """三个音乐插件（网易云/酷狗/QQ）共用的「最近活跃归属」标记文件路径。

    用于裸 #听N 的跨插件抢占：点歌出列表时写入本插件名，裸 #听N 仅由最近
    活跃的插件响应，避免多插件同装时抢占顺序取决于插件加载顺序。
    """
    try:
        from astrbot.api.star import StarTools

        return Path(StarTools.get_data_dir()).parent / "_music_session_owner.json"
    except Exception:
        return Path(__file__).resolve().parent.parent / "_music_session_owner.json"


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
        # 注入配置访问器给 api 模块
        ncmapi.set_config_getter(lambda: self.plugin.config or {})

    # ──────────── 裸 #听N 跨插件抢占 ────────────

    async def mark_session_owner(self) -> None:
        """点歌出列表后，把本插件记录为「最近活跃的音乐插件」。"""
        name = str(getattr(self.plugin, "name", "") or "")

        def _w():
            try:
                p = _owner_marker_path()
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_text(
                    json.dumps({"plugin": name, "ts": int(time.time())}, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(tmp, p)  # 原子替换，避免并发写坏
            except Exception:
                pass

        await asyncio.to_thread(_w)

    async def is_session_owner(self) -> bool:
        """本插件是否为最近活跃的音乐插件（无标记时视为 True，退化为「谁有会话谁响应」）。"""
        name = str(getattr(self.plugin, "name", "") or "")

        def _r() -> bool:
            try:
                p = _owner_marker_path()
                if not p.exists():
                    return True
                data = json.loads(p.read_text(encoding="utf-8"))
                return data.get("plugin") == name
            except Exception:
                return True

        return await asyncio.to_thread(_r)

    @property
    def config(self) -> dict:
        return self.plugin.config or {}

    def cfg(self) -> dict:
        return self.plugin.config or {}

    def log_warn(self, msg: str):
        logger.warning(f"[neteasemusic] {msg}")

    def log_info(self, msg: str):
        logger.info(f"[neteasemusic] {msg}")

    def plain(self, text: str) -> Plain:
        return Plain(text=text)

    async def send_chain(self, event: AstrMessageEvent, *components):
        comps = [c for c in components if c is not None]
        if not comps:
            return
        mc = MessageChain(chain=list(comps))
        mc.use_markdown_ = False
        try:
            await event.send(mc)
        except AttributeError:
            import traceback as _tb

            self.log_warn(f"_send_chain 发送失败（AttributeError）:\n{_tb.format_exc()}")
            texts = []
            for _c in comps:
                t = getattr(_c, "text", None)
                if t:
                    texts.append(str(t))
            if texts:
                try:
                    _fb = MessageChain(chain=[self.plain("\n".join(texts))])
                    _fb.use_markdown_ = False
                    await event.send(_fb)
                except Exception as _e2:
                    self.log_warn(f"_send_chain 文本兜底也失败: {_e2}")

    async def reply(self, event: AstrMessageEvent, text: str):
        try:
            await self.send_chain(event, self.plain(text))
        except Exception as e:
            import traceback as _tb

            self.log_warn(f"_reply 发送失败: {e}\n{_tb.format_exc()}")

    def scope(self, event: AstrMessageEvent) -> str:
        gid = getattr(event.message_obj, "group_id", None)
        if gid:
            return str(gid)
        return event.get_sender_id()

    def user_key(self, event: AstrMessageEvent) -> str:
        return str(event.get_sender_id() or "")

    def check_cmd(
        self, event: AstrMessageEvent, pattern: str, *, song_request: bool = False
    ) -> re.Match | None:
        cfg = self.cfg()
        if not cfg.get("enable", True):
            return None
        if song_request and cfg.get("enableSongRequest") is False:
            return None
        return re.match(pattern, event.message_str.strip(), re.IGNORECASE)

    async def resolve_song(self, kw: str, user_key: str) -> dict | None:
        if not (kw or "").strip():
            return None
        if re.fullmatch(r"\d+", kw):
            lst = await ncmapi.song_detail([int(kw)], user_key=user_key)
            return lst[0] if lst else None
        lst = await ncmapi.search(kw, type_=1, limit=1, user_key=user_key)
        return lst[0] if lst else None

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
        """进入"先选歌再操作"流程。"""
        scope = self.scope(event)
        session = await cardlib.SessionStore.get(self.plugin, scope)
        if (kw or "").strip():
            page_size = min(int(self.cfg().get("maxList") or 10), 20)
            lst = await ncmapi.search(kw, type_=1, limit=page_size, user_key=user_key)
            if not lst:
                await self.reply(event, f"没有搜到「{kw}」")
                return
            keyword = kw
        else:
            lst = (session or {}).get("data") or []
            if not lst:
                await self.reply(
                    event,
                    f"用法：先 #ncm点歌 关键词 选中歌曲，再发 #ncm{label}；或直接 #ncm{label} 关键词 选择",
                )
                return
            keyword = (session or {}).get("keyword") or "当前会话"
        base = dict(session) if session else {}
        base.update({"type": "songs", "keyword": keyword, "data": lst, "action": action})
        await cardlib.SessionStore.set(self.plugin, scope, base)
        await self.mark_session_owner()
        tip = f"回复 #ncm听N 即可{verb}"
        if self.cfg().get("renderListCard", True):
            data = cardlib.build_list_card_data(
                keyword, lst, options={"tip": tip}, cfg=self.cfg()
            )
            if await self.reply_card_or_text(
                event,
                tpl_name="ncm-list",
                data=data,
                format_text=lambda d: cardlib.format_song_list(lst, keyword, tip=tip),
            ):
                return
        await self.reply(event, cardlib.format_song_list(lst, keyword, tip=tip))

    async def show_lyric(self, event: AstrMessageEvent, song: dict, user_key: str) -> None:
        lr = await ncmapi.lyric(song["id"], user_key=user_key)
        lines = self.extract_lyric_lines(lr.get("lrc") or "", lr.get("tlyric") or "")
        if not lines:
            await self.reply(event, "暂无歌词")
            return
        await self.send_lyric_pages(event, song, lines, base_tip="歌词来自网易云音乐")

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
        pages = [lines[i : i + 36] for i in range(0, len(lines), 36)]
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
            mvs = await ncmapi.search_mv(
                song.get("name") or "", limit=1, user_key=user_key
            )
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
        if unlike:
            await ncmapi.like(song["id"], like_=False, user_key=user_key)
            await self.reply(event, f"💔 已取消红心：{song['name']} - {song['artist']}")
            return
        try:
            checked = await ncmapi.song_like_check([song["id"]], user_key=user_key)
            liked = checked.get(str(song["id"]), False)
        except ApiError:
            liked = False
        await ncmapi.like(song["id"], like_=not liked, user_key=user_key)
        if liked:
            await self.reply(event, f"💔 已取消红心：{song['name']} - {song['artist']}")
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
        uid = str(self.cfg().get("defaultUid") or "")
        if uid:
            return uid
        try:
            st = await ncmapi.login_status(user_key=user_key)
            profile = st.get("profile") or {}
            if profile and profile.get("userId"):
                uid = str(profile["userId"])
                self.plugin.config["defaultUid"] = uid
                self.plugin.config.save_config()
                return uid
        except Exception:
            pass
        return ""

    async def resolve_play(self, song: dict, cfg: dict, user_key: str = "") -> dict:
        quality = cfg.get("quality") or "auto"
        unblock = cfg.get("qualityUnblock", True) is not False
        try:
            play = await ncmapi.song_url_best(
                song["id"], level=quality, user_key=user_key, unblock_fallback=unblock
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
            quality_label = f"{quality_label}（解灰）" if quality_label else "解灰音源"
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
                self.plugin, event, song, play, cfg=cfg, plugin_dir=PLUGIN_DIR
            )

    async def list_to_session(
        self, event: AstrMessageEvent, keyword: str, songs: list, *, tip: str = ""
    ) -> bool:
        scope = self.scope(event)
        await cardlib.SessionStore.set(
            self.plugin, scope, {"type": "songs", "keyword": keyword, "data": songs}
        )
        await self.mark_session_owner()
        text = lambda: cardlib.format_song_list(songs, keyword, tip=tip)
        if self.cfg().get("renderListCard", True):
            data = cardlib.build_list_card_data(
                keyword, songs, options={"tip": tip}, cfg=self.cfg()
            )
            if await self.reply_card_or_text(
                event, tpl_name="ncm-list", data=data, format_text=lambda d: text()
            ):
                return True
        await self.reply(event, text())
        return True

    async def playlist_list_to_session(
        self, event: AstrMessageEvent, title: str, pls: list, *, subtitle: str = ""
    ) -> bool:
        scope = self.scope(event)
        await cardlib.SessionStore.set(
            self.plugin, scope, {"type": "playlistList", "keyword": title, "data": pls}
        )
        tip = "回复 #ncm听N 查看该歌单曲目"
        data = cardlib.build_playlist_card_data(
            title, pls, subtitle=subtitle, tip=tip, cfg=self.cfg()
        )
        return await self.reply_card_or_text(
            event,
            tpl_name="ncm-playlist",
            data=data,
            format_text=lambda d: cardlib.format_playlist_text(title, pls, tip=tip),
        )

    async def album_list_to_session(
        self, event: AstrMessageEvent, title: str, albums: list
    ) -> bool:
        scope = self.scope(event)
        await cardlib.SessionStore.set(
            self.plugin, scope, {"type": "albumList", "keyword": title, "data": albums}
        )
        tip = "回复 #ncm听N 查看该专辑曲目"
        items = [
            {
                "name": a.get("name") or "",
                "sub": a.get("artist") or "",
                "tag": f"{a.get('size') or a.get('trackCount') or '?'}首"
                if (a.get("size") or a.get("trackCount"))
                else "",
                "cover": a.get("cover") or "",
            }
            for a in albums
        ]
        data = cardlib.build_generic_card_data(
            title, items, subtitle="专辑候选", tip=tip, cfg=self.cfg()
        )
        return await self.reply_card_or_text(
            event,
            tpl_name="ncm-generic",
            data=data,
            format_text=lambda d: cardlib.format_generic_text(title, items, tip=tip),
        )

    async def expand_playlist(
        self, event: AstrMessageEvent, session: dict, n: int
    ) -> None:
        pls = session.get("data") or []
        if n < 1 or n > len(pls):
            await self.reply(event, f"序号超出范围（1-{len(pls)}）")
            event.stop_event()
            return
        p = pls[n - 1]
        try:
            songs = await ncmapi.playlist_tracks(p["id"], user_key=self.user_key(event))
        except ApiError as err:
            self.log_warn(f"歌单展开失败: {err}")
            await self.reply(event, f"歌单展开失败：{err}")
            event.stop_event()
            return
        if not songs:
            await self.reply(event, f"歌单「{p['name']}」暂无曲目")
            event.stop_event()
            return
        shown = songs[:30]
        await self.list_to_session(
            event,
            f"歌单 · {p['name']}",
            shown,
            tip=f"歌单共 {len(songs)} 首，显示前 {len(shown)} 首；回复 #ncm听N 播放，#ncm听所有 连播",
        )
        event.stop_event()

    async def expand_album(
        self, event: AstrMessageEvent, session: dict, n: int
    ) -> None:
        albums = session.get("data") or []
        if n < 1 or n > len(albums):
            await self.reply(event, f"序号超出范围（1-{len(albums)}）")
            event.stop_event()
            return
        a = albums[n - 1]
        try:
            album_info, songs = await ncmapi.album_detail(
                a["id"], user_key=self.user_key(event)
            )
        except ApiError as err:
            self.log_warn(f"专辑展开失败: {err}")
            await self.reply(event, f"专辑展开失败：{err}")
            event.stop_event()
            return
        info = album_info or {}
        name = info.get("name") or a.get("name") or "专辑"
        if not songs:
            await self.reply(event, f"专辑「{name}」暂无曲目")
            event.stop_event()
            return
        await self.list_to_session(
            event,
            f"专辑 · {name}",
            songs,
            tip=f"歌手：{info.get('artist') or ''} · 共 {len(songs)} 首；回复 #ncm听N 播放，#ncm听所有 连播整张专辑",
        )
        event.stop_event()

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

            d = get_temp_dir(self.cfg(), PLUGIN_DIR)
            file_path = os.path.join(
                d, f"card_{tpl_name}_{int(time.time() * 1000)}.png"
            )
            await asyncio.to_thread(_write_bytes, file_path, raw)
            return file_path
        except Exception as e:
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
            if card_path:
                await self.send_chain(event, Image.fromFileSystem(card_path))
                return True
        except Exception as e:
            self.log_warn(f"{tpl_name} 卡片渲染失败，回退文本: {e}")
        finally:
            if card_path:
                asyncio.get_running_loop().call_later(
                    max(0, int(self.cfg().get("keepFileSec", 60))),
                    lambda: self.safe_unlink(card_path),
                )
        try:
            text = format_text(data)
            if text:
                await self.send_chain(event, self.plain(text))
                return True
        except Exception as e:
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
                get_temp_dir(self.cfg(), PLUGIN_DIR),
                f"qr_{int(time.time() * 1000)}.png",
            )
            await asyncio.to_thread(_write_bytes, path, data)
            return path
        except Exception as e:
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
            for l in raw.splitlines():
                t = _parse_line(l)
                if t and not re.match(
                    r"^\s*\[(ti|ar|al|by|offset|total):", l, re.IGNORECASE
                ):
                    out.append(t)
            return out

        out = _clean(lrc)
        if tlyric:
            tr = []
            for l in tlyric.splitlines():
                t = _parse_line(l)
                if t:
                    tr.append(t)
                if len(tr) >= 12:
                    break
            if tr:
                out.append("")
                out.append("── 翻译 ──")
                out.extend(tr)
        return out

    @staticmethod
    def extract_yrc_lines(yrc: str, max_lines: int = 72) -> list:
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
        task = self.active_logins.pop(user_key, None)
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

    def start_poll(
        self, event: AstrMessageEvent, key: str, max_sec: int = 300
    ):
        user_key = self.user_key(event)
        started = time.time()
        task = {
            "key": key,
            "stopped": False,
            "busy": False,
            "notifiedScan": False,
            "failStreak": 0,
            "jobs": [],
        }
        self.active_logins[user_key] = task
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
            if task["stopped"]:
                return
            if task["busy"]:
                _schedule(0.8)
                return
            if time.time() - started > max_sec:
                task["stopped"] = True
                self.active_logins.pop(user_key, None)
                await self.reply(event, "二维码已过期，请重新 #ncm登录")
                return
            task["busy"] = True
            try:
                body = await ncmapi.qr_check(key, user_key=user_key)
                code = (body or {}).get("code")
                if code == 800:
                    task["stopped"] = True
                    self.active_logins.pop(user_key, None)
                    await self.reply(event, "二维码已失效，请重新 #ncm登录")
                    return
                if code == 802 and not task["notifiedScan"]:
                    task["notifiedScan"] = True
                    await self.reply(event, "已扫码，请在手机上确认登录")
                elif code == 803:
                    await self.finish_login(event, body, user_key, task)
                    return
                task["failStreak"] = 0
            except Exception as err:
                task["failStreak"] += 1
                if task["failStreak"] == 5:
                    await self.reply(event, f"轮询暂时失败：{err}（继续重试）")
                if task["failStreak"] >= 25:
                    task["stopped"] = True
                    self.active_logins.pop(user_key, None)
                    await self.reply(
                        event, "轮询失败过多，请检查 API 服务或重新 #ncm登录"
                    )
                    return
            finally:
                task["busy"] = False
            if (
                not task["stopped"]
                and self.active_logins.get(user_key, {}).get("key") == key
            ):
                task["timer"] = _schedule(2)

        task["timer"] = _schedule(2)

    async def finish_login(
        self, event: AstrMessageEvent, body: dict, user_key: str, task: dict
    ):
        task["stopped"] = True
        self.active_logins.pop(user_key, None)
        cookie = (body or {}).get("cookie") or ""
        nickname = (body or {}).get("nickname") or ""
        if not cookie:
            await self.reply(
                event, "登录成功但未获取到 Cookie（可能登录状态异常），请重新 #ncm登录"
            )
            return
        try:
            self.plugin.config["defaultCookie"] = cookie
            self.plugin.config.save_config()
            self.log_info("扫码登录成功，Cookie 已写入插件配置 defaultCookie")
        except Exception as e:
            self.log_warn(f"写入默认 Cookie 失败: {e}")
        await self.reply(
            event,
            f"✅ 登录成功：{nickname or '已写入 Cookie'}\nCookie 已存入插件配置，全群默认使用该账号",
        )
        st = None
        try:
            st = await ncmapi.login_status(user_key=user_key)
            profile = st.get("profile") or {}
            if profile and profile.get("userId"):
                self.plugin.config["defaultUid"] = str(profile["userId"])
                self.plugin.config.save_config()
        except Exception:
            pass
        await self.send_status(event, user_key, status_data=st)

    async def build_status(
        self, user_key: str, *, status_data: dict | None = None
    ) -> dict:
        cfg = self.cfg()
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
            status["vipType"] = int(
                profile.get("vipType") or account.get("vipType") or 0
            )
            status["vipLevel"] = 0
            status["vipExpire"] = 0
            try:
                vip = await ncmapi.vip_info(user_key=user_key)
                vd = (vip or {}).get("data") or {}
                status["vipLevel"] = int(vd.get("redVipLevel") or 0)
                status["vipExpire"] = int(
                    (vd.get("redplus") or {}).get("expireTime") or 0
                )
            except ApiError:
                pass
        elif default_cookie:
            status["keyStatus"] = "Cookie 已失效或未写入（登录态 301）"
        return status

    async def send_status(
        self,
        event: AstrMessageEvent,
        user_key: str,
        *,
        status_data: dict | None = None,
    ):
        try:
            status = await self.build_status(user_key, status_data=status_data)
            data = cardlib.build_status_card_data(status)
            await self.reply_card_or_text(
                event,
                tpl_name="ncm-status",
                data=data,
                format_text=lambda d: cardlib.format_status_text(status),
            )
        except Exception as err:
            self.log_warn(f"状态卡片失败: {err}")
            await self.reply(
                event,
                cardlib.format_status_text(
                    {
                        "loggedIn": False,
                        "apiBase": self.cfg().get("apiBase") or "",
                        "quality": str(self.cfg().get("quality") or "auto"),
                        "keyStatus": str(err),
                    }
                ),
            )

    async def handle_resolve(self, event: AstrMessageEvent, text: str) -> bool:
        user_key = self.user_key(event)
        try:
            text = await ncmapi.expand_short_links(text)
            m = re.search(r"playlist\?(?:[^&\s]*&)*id=(\d+)|playlist/(\d+)", text)
            if m:
                pid = int(m.group(1) or m.group(2))
                songs = await ncmapi.playlist_tracks(pid, user_key=user_key)
                if not songs:
                    await self.reply(event, "歌单暂无曲目或不存在")
                    return True
                shown = songs[:30]
                await self.list_to_session(
                    event,
                    "链接解析 · 歌单",
                    shown,
                    tip=f"歌单共 {len(songs)} 首，显示前 {len(shown)} 首",
                )
                return True
            m = re.search(r"album\?(?:[^&\s]*&)*id=(\d+)|album/(\d+)", text)
            if m:
                aid = int(m.group(1) or m.group(2))
                album_info, songs = await ncmapi.album_detail(aid, user_key=user_key)
                if not songs:
                    await self.reply(event, "专辑暂无曲目或不存在")
                    return True
                info = album_info or {}
                await self.list_to_session(
                    event, f"链接解析 · {info.get('name') or '专辑'}", songs[:30]
                )
                return True
            m = re.search(r"song\?(?:[^&\s]*&)*id=(\d+)|song/(\d+)", text)
            if m:
                sid = int(m.group(1) or m.group(2))
                lst = await ncmapi.song_detail([sid], user_key=user_key)
                if not lst:
                    await self.reply(event, "歌曲不存在或无版权")
                    return True
                await self.play_song(
                    event, lst[0], user_key=user_key, source="链接解析"
                )
                return True
            # 仅在文本里确实存在网易云分享域名（链接/卡片）时才做关键词兜底搜索。
            # 避免只含「网易云音乐」等字样、但没有真实分享链接的消息（例如登录提示文案）
            # 被误判为点歌请求而自动发送一首歌。
            if not re.search(
                r"music\.163\.com|163music\.com|163cn\.tv|y\.music\.163\.com",
                text,
                re.IGNORECASE,
            ):
                return False
            kw = re.sub(r"https?://\S+|\[CQ:[^\]]*\]", "", text).strip()
            kw = re.sub(
                r"music\.163\.com|163music\.com|网易云音乐|分享|歌曲|链接",
                "",
                kw,
                flags=re.IGNORECASE,
            ).strip()
            if len(kw) >= 2:
                lst = await ncmapi.search(kw, type_=1, limit=1, user_key=user_key)
                if lst:
                    await self.play_song(
                        event, lst[0], user_key=user_key, source="链接解析"
                    )
                    return True
            return False
        except ApiError as err:
            self.log_warn(f"解析失败: {err}")
            await self.reply(event, f"解析失败：{err}")
            return True

    async def terminate(self):
        for user_key in list(self.active_logins.keys()):
            self.stop_poll(user_key)
        # 关闭复用的 aiohttp 会话，避免热重载后残留连接
        await ncmapi.close_session()
