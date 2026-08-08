from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star

from . import api as ncmapi
from . import cards as cardlib
from .api import ApiError
from .delivery import deliver_song
from .quality import QUALITY_LABEL

PLUGIN_DIR = str(Path(__file__).resolve().parent)

NEW_SONG_AREAS = {"华语": 7, "欧美": 96, "日本": 8, "韩国": 16}


def _is_plugin_command_msg(msg: str) -> bool:
    # 注意不能用 \b：Python 正则里汉字属 \w，ncm 与中文指令之间不构成词边界，
    # 否则 #ncm点歌 等中文指令全部误判为「非指令」；改用 ASCII 字符或词边界判定
    return bool(re.match(r"^#?(?:ncm|NCM)(?:[^\x00-\x7F]|\b)|^#听\s*[1-9]", str(msg or "").strip(), re.IGNORECASE))


def _is_ncm_message(text: str) -> bool:
    if not text:
        return False
    return bool(
        re.search(r"music\.163\.com|163music\.com|y\.music\.163\.com", text, re.IGNORECASE)
        or re.search(r"网易云音乐|com\.netease\.cloudmusic", text, re.IGNORECASE)
    )


def _collect_message_text(event: AstrMessageEvent) -> str:
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


class NeteaseMusicPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 注入配置访问器给 api 模块
        ncmapi.set_config_getter(lambda: self.config or {})
        self._active_logins: dict = {}

    # ──────────── 辅助 ────────────

    def _cfg(self) -> dict:
        return self.config or {}

    def _log_warn(self, msg: str):
        logger.warning(f"[neteasemusic] {msg}")

    def _log_info(self, msg: str):
        logger.info(f"[neteasemusic] {msg}")

    def _plain(self, text: str) -> Plain:
        return Plain(text=text)

    async def _send_chain(self, event: AstrMessageEvent, *components):
        comps = [c for c in components if c is not None]
        if not comps:
            return
        mc = MessageChain(chain=list(comps))
        # QQ 官方机器人默认走 markdown(msg_type=2)，文件/语音上传失败时会留下裸 markdown
        # 载荷，触发 "无效 markdown content"(40034011)；强制纯文本(msg_type=0) 使文案稳定送达。
        # 该字段仅 qqofficial/dingtalk 读取，对 aiocqhttp 等无影响。
        mc.use_markdown_ = False
        try:
            await event.send(mc)
        except AttributeError:
            import traceback as _tb
            self._log_warn(f"_send_chain 发送失败（AttributeError）:\n{_tb.format_exc()}")
            texts = []
            for _c in comps:
                t = getattr(_c, "text", None)
                if t:
                    texts.append(str(t))
            if texts:
                try:
                    _fb = MessageChain(chain=[self._plain("\n".join(texts))])
                    _fb.use_markdown_ = False
                    await event.send(_fb)
                except Exception as _e2:
                    self._log_warn(f"_send_chain 文本兜底也失败: {_e2}")

    async def _reply(self, event: AstrMessageEvent, text: str):
        try:
            await self._send_chain(event, self._plain(text))
        except Exception as e:
            import traceback as _tb
            self._log_warn(f"_reply 发送失败: {e}\n{_tb.format_exc()}")

    def _scope(self, event: AstrMessageEvent) -> str:
        gid = getattr(event.message_obj, "group_id", None)
        if gid:
            return str(gid)
        return event.get_sender_id()

    def _user_key(self, event: AstrMessageEvent) -> str:
        return str(event.get_sender_id() or "")

    # ──────────── 关键词 → 资源解析 ────────────

    async def _resolve_song(self, kw: str, user_key: str) -> dict | None:
        '''关键词解析为歌曲：纯数字按 ID 拉详情（补全歌名/歌手），否则搜索第一条。'''
        if re.fullmatch(r"\d+", kw):
            lst = await ncmapi.song_detail([int(kw)], user_key=user_key)
            return lst[0] if lst else None
        lst = await ncmapi.search(kw, type_=1, limit=1, user_key=user_key)
        return lst[0] if lst else None

    async def _resolve_playlist(self, kw: str, user_key: str) -> dict | None:
        '''关键词解析为歌单：纯数字按 ID 拉详情，否则搜索第一个。'''
        if re.fullmatch(r"\d+", kw):
            pl = await ncmapi.playlist_detail(int(kw), user_key=user_key)
        else:
            pls = await ncmapi.search_playlists(kw, limit=3, user_key=user_key)
            pl = pls[0] if pls else None
        if not pl:
            return None
        # 评论卡片取 artist 字段，歌单用创建者补位
        return {**pl, "artist": pl.get("creator") or pl.get("artist") or ""}

    async def _resolve_album(self, kw: str, user_key: str) -> dict | None:
        '''关键词解析为专辑：纯数字按 ID 拉详情，否则搜索第一个。'''
        if re.fullmatch(r"\d+", kw):
            info, _songs = await ncmapi.album_detail(int(kw), user_key=user_key)
            return info or {"id": int(kw), "name": "", "artist": "", "cover": ""}
        albums = await ncmapi.search_albums(kw, limit=3, user_key=user_key)
        return albums[0] if albums else None

    # ──────────── cookie / 登录态 ────────────

    def _has_cookie(self) -> bool:
        return bool(self._cfg().get("defaultCookie"))

    async def _get_uid(self, user_key: str) -> str:

        uid = str(self._cfg().get("defaultUid") or "")
        if uid:
            return uid
        try:
            st = await ncmapi.login_status(user_key=user_key)
            profile = st.get("profile") or {}
            if profile and profile.get("userId"):
                uid = str(profile["userId"])
                self.config["defaultUid"] = uid
                self.config.save_config()
                return uid
        except Exception:
            pass
        return ""

    # ──────────── 取链 ────────────

    async def _resolve_play(self, song: dict, cfg: dict, user_key: str = "") -> dict:
        quality = cfg.get("quality") or "auto"
        unblock = cfg.get("qualityUnblock", True) is not False
        try:
            play = await ncmapi.song_url_best(song["id"], level=quality, user_key=user_key, unblock_fallback=unblock)
            return {
                "url": play.get("url", ""),
                "level": play.get("level"),
                "qualityLabel": QUALITY_LABEL.get(play.get("level") or "", play.get("level") or ""),
                "unblocked": bool(play.get("unblocked")),
                "raw": play,
            }
        except ApiError as e:
            return {"url": "", "error": str(e), "raw": getattr(e, "payload", None)}

    async def _play_song(self, event: AstrMessageEvent, song: dict, *, user_key: str, source: str = "") -> None:

        cfg = self._cfg()
        play = await self._resolve_play(song, cfg, user_key)
        quality_label = play.get("qualityLabel") or ""
        if play.get("unblocked"):
            quality_label = f"{quality_label}（解灰）" if quality_label else "解灰音源"
        if play.get("url"):
            tip = "正在下载并发送语音/文件…"
        elif play.get("error"):
            tip = play["error"]
        else:
            tip = "⚠ 未获取到播放链接，可尝试 #ncm登录 或换个音质"
        data = cardlib.build_detail_card_data(song, quality_label, source=source, tip=tip)
        await self._reply_card_or_text(
            event,
            tpl_name="ncm-detail",
            data=data,
            format_text=lambda d: cardlib.format_detail_text(song, play, tip),
        )
        if play.get("url"):
            await deliver_song(self, event, song, play, cfg=cfg, plugin_dir=PLUGIN_DIR)

    async def _list_to_session(self, event: AstrMessageEvent, keyword: str, songs: list, *, tip: str = "") -> bool:

        scope = self._scope(event)
        await cardlib.SessionStore.set(self, scope, {"type": "songs", "keyword": keyword, "data": songs})
        text = lambda: cardlib.format_song_list(songs, keyword, tip=tip)
        if self._cfg().get("renderListCard", True):
            data = cardlib.build_list_card_data(keyword, songs, options={"tip": tip}, cfg=self._cfg())
            # _reply_card_or_text 内部已带文本兜底，返回 True 即已发送
            if await self._reply_card_or_text(event, tpl_name="ncm-list", data=data, format_text=lambda d: text()):
                return True
        await self._reply(event, text())
        return True

    # ──────────── 卡片渲染 ────────────

    async def _render_card(self, event: AstrMessageEvent, data: dict, tpl_name: str) -> str | None:
        '''渲染 HTML 卡片：本地 Playwright 直接渲染（不依赖 AstrBot 远程 t2i 服务）。

        依赖：pip install playwright && playwright install chromium
        '''
        try:
            import jinja2
            from playwright.async_api import async_playwright

            from .tpl_adapter import get_jinja_template
            tmpl_path = os.path.join(PLUGIN_DIR, "resources", "html", tpl_name, f"{tpl_name}.html")
            if not os.path.exists(tmpl_path):
                return None
            tmpl = get_jinja_template(tmpl_path)
            html = jinja2.Template(tmpl).render(data=data)
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                try:
                    page = await browser.new_page(
                        viewport={"width": 640, "height": 800},
                        device_scale_factor=2,  # 2x 清晰度
                    )
                    await page.set_content(html, wait_until="load", timeout=30000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
                    # 收缩视口到 .page 实际渲染边界：viewport 固定时 body 不会随
                    # fit-content 收缩，截图右侧/底部会留大片浅红空白（白边）
                    try:
                        rect = await page.evaluate(
                            "() => { const el = document.querySelector('.page') || document.body; "
                            "const r = el.getBoundingClientRect(); "
                            "return { w: Math.max(1, Math.ceil(r.right)), "
                            "h: Math.max(1, Math.ceil(r.bottom)) }; }"
                        )
                        await page.set_viewport_size({"width": rect["w"], "height": rect["h"]})
                        await page.wait_for_timeout(50)
                    except Exception:
                        pass
                    # png 截图不支持 quality 参数（playwright 限制）
                    raw = await page.screenshot(full_page=True, type="png")
                finally:
                    await browser.close()
            from .delivery import get_temp_dir
            d = get_temp_dir(self._cfg(), PLUGIN_DIR)
            file_path = os.path.join(d, f"card_{tpl_name}_{int(time.time() * 1000)}.png")
            with open(file_path, "wb") as f:
                f.write(raw)
            return file_path
        except Exception as e:
            self._log_warn(f"{tpl_name} 本地渲染失败: {e}")
            return None

    async def _reply_card_or_text(self, event: AstrMessageEvent, *, tpl_name: str, data: dict, format_text) -> bool:
        card_path = None
        try:
            card_path = await self._render_card(event, data, tpl_name)
            if card_path:
                await self._send_chain(event, Image.fromFileSystem(card_path))
                return True
        except Exception as e:
            self._log_warn(f"{tpl_name} 卡片渲染失败，回退文本: {e}")
        finally:
            # 卡片图片为临时文件，发出后即清理（与二维码/音频一致），复用 keepFileSec 配置；
            # 置于 finally 以保证发送失败留下孤儿文件时也能删除
            if card_path:
                asyncio.get_running_loop().call_later(
                    max(0, int(self._cfg().get("keepFileSec", 60))),
                    lambda: self._safe_unlink(card_path),
                )
        try:
            text = format_text(data)
            if text:
                await self._send_chain(event, self._plain(text))
                return True
        except Exception as e:
            self._log_warn(f"{tpl_name} 文本兜底失败: {e}")
        return False

    async def _save_qr_image(self, b64: str) -> str | None:
        try:
            import base64
            raw = b64
            if "," in raw and raw.split(",", 1)[0].startswith("data:"):
                raw = raw.split(",", 1)[1]
            data = base64.b64decode(raw)
            from .delivery import get_temp_dir
            path = os.path.join(get_temp_dir(self._cfg(), PLUGIN_DIR), f"qr_{int(time.time() * 1000)}.png")
            with open(path, "wb") as f:
                f.write(data)
            return path
        except Exception as e:
            self._log_warn(f"保存二维码失败: {e}")
            return None

    def _safe_unlink(self, path: str):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    # ══════════════════ 点歌 ══════════════════

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*点歌\s*(.+)$", re.IGNORECASE))
    async def pick_song(self, event: AstrMessageEvent):
        '''搜索歌曲并列出列表'''
        cfg = self._cfg()
        if not cfg.get("enable", True) or cfg.get("enableSongRequest") is False:
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*点歌\s*(.+)$", event.message_str.strip(), re.IGNORECASE)
        keyword = (m.group(1).strip() if m else "").strip()
        if not keyword:
            await self._reply(event, "用法：#ncm点歌 关键词")
            event.stop_event()
            return
        try:
            await self._reply(event, f"正在搜索：{keyword}")
            page_size = min(int(cfg.get("maxList") or 10), 20)
            lst = await ncmapi.search(keyword, type_=1, limit=page_size, user_key=self._user_key(event))
            if not lst:
                await self._reply(event, "没有搜到相关歌曲")
                event.stop_event()
                return
            await self._list_to_session(event, keyword, lst)
        except ApiError as err:
            self._log_warn(f"点歌失败: {err}")
            await self._reply(event, f"点歌失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*听\s*([1-9][0-9]?)$|^#听\s*([1-9][0-9]?)$", re.IGNORECASE))
    async def choose_song(self, event: AstrMessageEvent):
        '''播放列表第 N 首'''
        cfg = self._cfg()
        if not cfg.get("enable", True) or cfg.get("enableSongRequest") is False:
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*听\s*([1-9][0-9]?)$|^#听\s*([1-9][0-9]?)$", event.message_str.strip(), re.IGNORECASE)
        n = int(m.group(1) or m.group(2) or 0) if m else 0
        scope = self._scope(event)
        session = await cardlib.SessionStore.get(self, scope)
        if not session or session.get("type") != "songs" or not session.get("data"):
            # 无本插件会话时不抢其它插件的 #听
            return
        songs = session.get("data") or []
        if n < 1 or n > len(songs):
            await self._reply(event, f"序号超出范围（1-{len(songs)}）")
            event.stop_event()
            return
        song = songs[n - 1]
        try:
            await self._play_song(event, song, user_key=self._user_key(event), source="点歌")
        except ApiError as err:
            self._log_warn(f"播放失败: {err}")
            await self._reply(event, f"播放失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*播放\s*(.+)$", re.IGNORECASE))
    async def play_direct(self, event: AstrMessageEvent):
        '''搜索并直接播放第一首'''
        cfg = self._cfg()
        if not cfg.get("enable", True) or cfg.get("enableSongRequest") is False:
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*播放\s*(.+)$", event.message_str.strip(), re.IGNORECASE)
        keyword = (m.group(1).strip() if m else "").strip()
        if not keyword:
            await self._reply(event, "用法：#ncm播放 关键词")
            event.stop_event()
            return
        try:
            lst = await ncmapi.search(keyword, type_=1, limit=1, user_key=self._user_key(event))
            if not lst:
                await self._reply(event, f"没有搜到「{keyword}」")
                event.stop_event()
                return
            await self._play_song(event, lst[0], user_key=self._user_key(event), source="搜索")
        except ApiError as err:
            self._log_warn(f"播放失败: {err}")
            await self._reply(event, f"播放失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*歌词\s*(.+)$", re.IGNORECASE))
    async def get_lyric(self, event: AstrMessageEvent):
        '''获取歌词'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*歌词\s*(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        if not kw:
            await self._reply(event, "用法：#ncm歌词 关键词 或 #ncm歌词 歌曲ID")
            event.stop_event()
            return
        try:
            user_key = self._user_key(event)
            song = await self._resolve_song(kw, user_key)
            if not song:
                await self._reply(event, f"没有搜到「{kw}」")
                event.stop_event()
                return
            lr = await ncmapi.lyric(song["id"], user_key=user_key)
            lines = self._extract_lyric_lines(lr.get("lrc") or "", lr.get("tlyric") or "")
            data = cardlib.build_lyric_card_data(song, lines, line_count=len(lines))
            await self._reply_card_or_text(event, tpl_name="ncm-lyric", data=data, format_text=lambda d: cardlib.format_lyric_text(song, lines))
        except ApiError as err:
            self._log_warn(f"歌词失败: {err}")
            await self._reply(event, f"获取歌词失败：{err}")
        event.stop_event()

    @staticmethod
    def _extract_lyric_lines(lrc: str, tlyric: str = "", max_lines: int = 36) -> list:
        def _strip_meta(lines):
            return [l for l in lines if not re.match(r"^\s*\[(ti|ar|al|by|offset|total):", l, re.IGNORECASE)]

        out = []
        for l in _strip_meta(lrc.splitlines()):
            t = re.sub(r"^\[[^\]]*\]", "", l).strip()
            if t:
                out.append(t)
            if len(out) >= max_lines:
                break
        if tlyric:
            tr = []
            for l in _strip_meta(tlyric.splitlines())[:12]:
                t = re.sub(r"^\[[^\]]*\]", "", l).strip()
                if t:
                    tr.append(t)
            if tr:
                out.append("")
                out.append("── 翻译 ──")
                out.extend(tr)
        return out[:40]

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*热搜$", re.IGNORECASE))
    async def hot_search(self, event: AstrMessageEvent):
        '''网易云热搜榜'''
        if not self._cfg().get("enable", True):
            return
        try:
            items = await ncmapi.hot_search(user_key=self._user_key(event))
            if not items:
                await self._reply(event, "暂无热搜数据")
                event.stop_event()
                return
            data = cardlib.build_hot_card_data(items)
            await self._reply_card_or_text(event, tpl_name="ncm-hot", data=data, format_text=lambda d: cardlib.format_hot_text(items))
        except ApiError as err:
            self._log_warn(f"热搜失败: {err}")
            await self._reply(event, f"获取热搜失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*(help|帮助|菜单)$", re.IGNORECASE))
    async def help(self, event: AstrMessageEvent):
        '''插件帮助'''
        if not self._cfg().get("enable", True):
            return
        try:
            version = "?"
            try:
                import yaml
                with open(os.path.join(PLUGIN_DIR, "metadata.yaml"), "r", encoding="utf-8") as f:
                    _meta = yaml.safe_load(f) or {}
                version = str(_meta.get("version", "?")).lstrip("v")
            except Exception:
                pass
            data = cardlib.build_help_card_data(version, self._cfg())
            await self._reply_card_or_text(event, tpl_name="ncm-help", data=data, format_text=lambda d: cardlib.format_help_text(self._cfg(), version))
        except Exception as err:
            self._log_warn(f"帮助失败: {err}")
            await self._reply(event, cardlib.format_help_text(self._cfg()))
        event.stop_event()

    # ══════════════════ 探索 ══════════════════

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*排行\s*(.*)$", re.IGNORECASE))
    async def chart(self, event: AstrMessageEvent):
        '''排行榜列表 / 具体榜单'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        user_key = self._user_key(event)
        m = re.match(r"^#?(?:ncm|NCM)\s*排行\s*(.*)$", event.message_str.strip(), re.IGNORECASE)
        name = (m.group(1).strip() if m else "").strip()
        try:
            tops = await ncmapi.toplist(user_key=user_key)
            if not tops:
                await self._reply(event, "暂无榜单数据")
                event.stop_event()
                return
            if not name:
                # 无参数：列出全部榜单
                scope = self._scope(event)
                await cardlib.SessionStore.set(self, scope, {"type": "topCategory", "data": tops})
                lines = [f"♫ 网易云排行榜（{len(tops)} 个）", ""]
                for t in tops:
                    lines.append(f"{t['index']}. {t['name']}（{t['updateFrequency'] or '未知更新频率'}）")
                lines.append("")
                lines.append("发送 #ncm排行 榜单名 查看（如 #ncm排行 飙升榜）")
                await self._reply(event, "\n".join(lines))
                event.stop_event()
                return
            # 按名称匹配（包含/被包含）
            target = None
            for t in tops:
                if name == str(t["id"]):
                    target = t
                    break
            if not target:
                for t in tops:
                    if name in t["name"] or t["name"] in name:
                        target = t
                        break
            if not target:
                await self._reply(event, f"未找到榜单「{name}」，发送 #ncm排行 查看全部榜单")
                event.stop_event()
                return
            songs = await ncmapi.top_detail(target["id"], limit=60, user_key=user_key)
            if not songs:
                await self._reply(event, f"榜单「{target['name']}」暂无数据")
                event.stop_event()
                return
            await self._list_to_session(event, f"排行榜 · {target['name']}", songs)
        except ApiError as err:
            self._log_warn(f"排行失败: {err}")
            await self._reply(event, f"获取排行榜失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*歌手\s+(.+)$", re.IGNORECASE))
    async def artist(self, event: AstrMessageEvent):
        '''歌手热门歌曲'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*歌手\s+(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        user_key = self._user_key(event)
        try:
            artists = await ncmapi.search_artists(kw, limit=5, user_key=user_key)
            if not artists:
                await self._reply(event, f"没有搜到歌手「{kw}」")
                event.stop_event()
                return
            a = artists[0]
            songs = await ncmapi.artist_top_songs(a["id"], limit=30, user_key=user_key)
            if not songs:
                await self._reply(event, f"歌手「{a['name']}」暂无热门歌曲")
                event.stop_event()
                return
            await self._list_to_session(event, f"歌手 · {a['name']}", songs, tip=f"歌手：{a['name']} 的热门歌曲（共 {len(songs)} 首）")
        except ApiError as err:
            self._log_warn(f"歌手失败: {err}")
            await self._reply(event, f"获取歌手歌曲失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*专辑\s+(.+)$", re.IGNORECASE))
    async def album(self, event: AstrMessageEvent):
        '''专辑曲目'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*专辑\s+(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        user_key = self._user_key(event)
        try:
            albums = await ncmapi.search_albums(kw, limit=5, user_key=user_key)
            if not albums:
                await self._reply(event, f"没有搜到专辑「{kw}」")
                event.stop_event()
                return
            a = albums[0]
            album_info, songs = await ncmapi.album_detail(a["id"], user_key=user_key)
            if not songs:
                await self._reply(event, f"专辑「{a['name']}」暂无曲目")
                event.stop_event()
                return
            info = album_info or {}
            await self._list_to_session(event, f"专辑 · {info.get('name') or a['name']}", songs, tip=f"歌手：{info.get('artist') or ''} · 共 {len(songs)} 首")
        except ApiError as err:
            self._log_warn(f"专辑失败: {err}")
            await self._reply(event, f"获取专辑失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*歌单\s+(.+)$", re.IGNORECASE))
    async def playlist(self, event: AstrMessageEvent):
        '''歌单曲目'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*歌单\s+(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        user_key = self._user_key(event)
        try:
            pls = await ncmapi.search_playlists(kw, limit=5, user_key=user_key)
            if not pls:
                await self._reply(event, f"没有搜到歌单「{kw}」")
                event.stop_event()
                return
            p = pls[0]
            songs = await ncmapi.playlist_tracks(p["id"], user_key=user_key)
            if not songs:
                await self._reply(event, f"歌单「{p['name']}」暂无曲目")
                event.stop_event()
                return
            shown = songs[:30]
            await self._list_to_session(event, f"歌单 · {p['name']}", shown, tip=f"歌单共 {len(songs)} 首，显示前 {len(shown)} 首")
        except ApiError as err:
            self._log_warn(f"歌单失败: {err}")
            await self._reply(event, f"获取歌单失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*评论\s+(.+)$", re.IGNORECASE))
    async def get_comment(self, event: AstrMessageEvent):
        '''歌曲热评'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*评论\s+(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        user_key = self._user_key(event)
        try:
            song = await self._resolve_song(kw, user_key)
            if not song:
                await self._reply(event, f"没有搜到「{kw}」")
                event.stop_event()
                return
            comments = await ncmapi.comment(song["id"], limit=20, user_key=user_key)
            if not comments:
                await self._reply(event, "该歌曲暂无评论")
                event.stop_event()
                return
            data = cardlib.build_comment_card_data(song, comments, total=len(comments))
            await self._reply_card_or_text(event, tpl_name="ncm-comment", data=data, format_text=lambda d: cardlib.format_comment_text(song, comments))
        except ApiError as err:
            self._log_warn(f"评论失败: {err}")
            await self._reply(event, f"获取评论失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*相似\s+(.+)$", re.IGNORECASE))
    async def simi(self, event: AstrMessageEvent):
        '''相似歌曲'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*相似\s+(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        user_key = self._user_key(event)
        try:
            song = await self._resolve_song(kw, user_key)
            if not song:
                await self._reply(event, f"没有搜到「{kw}」")
                event.stop_event()
                return
            songs = await ncmapi.simi_songs(song["id"], limit=10, user_key=user_key)
            if not songs:
                await self._reply(event, "暂无相似歌曲")
                event.stop_event()
                return
            await self._list_to_session(event, f"相似歌曲 · {song['name'] or kw}", songs)
        except ApiError as err:
            self._log_warn(f"相似失败: {err}")
            await self._reply(event, f"获取相似歌曲失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*相关歌单\s+(.+)$", re.IGNORECASE))
    async def related_playlist(self, event: AstrMessageEvent):
        '''包含某首歌的歌单'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*相关歌单\s+(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        user_key = self._user_key(event)
        try:
            song = await self._resolve_song(kw, user_key)
            if not song:
                await self._reply(event, f"没有搜到「{kw}」")
                event.stop_event()
                return
            pls = await ncmapi.related_playlists(song["id"], limit=10, user_key=user_key)
            if not pls:
                await self._reply(event, "暂无相关歌单")
                event.stop_event()
                return
            lines = [f"♫ 包含「{song['name'] or kw}」的歌单"]
            for p in pls:
                lines.append(f"{p['index']}. {p['name']}（{cardlib.fmt_count(p['playCount'])}播放 · {p['trackCount']}首）")
            lines.append("")
            lines.append("发送 #ncm歌单 歌单名 查看曲目")
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"相关歌单失败: {err}")
            await self._reply(event, f"获取相关歌单失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*新歌\s*(.*)$", re.IGNORECASE))
    async def new_song(self, event: AstrMessageEvent):
        '''新歌速递'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*新歌\s*(.*)$", event.message_str.strip(), re.IGNORECASE)
        area = (m.group(1).strip() if m else "").strip()
        area_id = NEW_SONG_AREAS.get(area, 0)
        label = area or "全部"
        try:
            songs = await ncmapi.new_songs(area=area_id, limit=30, user_key=self._user_key(event))
            if not songs:
                await self._reply(event, "暂无新歌数据")
                event.stop_event()
                return
            await self._list_to_session(event, f"新歌速递 · {label}", songs[:20])
        except ApiError as err:
            self._log_warn(f"新歌失败: {err}")
            await self._reply(event, f"获取新歌失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*精品歌单\s*(.*)$", re.IGNORECASE))
    async def highquality(self, event: AstrMessageEvent):
        '''精品歌单'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*精品歌单\s*(.*)$", event.message_str.strip(), re.IGNORECASE)
        cat = (m.group(1).strip() if m else "").strip()
        try:
            pls = await ncmapi.highquality_playlists(cat=cat, limit=20, user_key=self._user_key(event))
            if not pls:
                await self._reply(event, "暂无精品歌单数据")
                event.stop_event()
                return
            lines = [f"♫ 精品歌单{f' · {cat}' if cat else ''}"]
            for p in pls:
                lines.append(f"{p['index']}. {p['name']}（{cardlib.fmt_count(p['playCount'])}播放 · {p['creator'] or '未知创建者'}）")
            lines.append("")
            lines.append("发送 #ncm歌单 歌单名 查看曲目")
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"精品歌单失败: {err}")
            await self._reply(event, f"获取精品歌单失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*搜索建议\s+(.+)$", re.IGNORECASE))
    async def suggest(self, event: AstrMessageEvent):
        '''搜索建议'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*搜索建议\s+(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        try:
            items = await ncmapi.search_suggest(kw, user_key=self._user_key(event))
            if not items:
                await self._reply(event, "暂无补全建议")
            else:
                lines = [f"♫ 「{kw}」的搜索建议"] + [f"{i + 1}. {w}" for i, w in enumerate(items)]
                await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"搜索建议失败: {err}")
            await self._reply(event, f"获取搜索建议失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*banner$", re.IGNORECASE))
    async def banner(self, event: AstrMessageEvent):
        '''首页轮播'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        try:
            items = await ncmapi.banner(user_key=self._user_key(event))
            if not items:
                await self._reply(event, "暂无轮播数据")
                event.stop_event()
                return
            lines = ["♫ 网易云首页轮播"]
            for b in items:
                title = b.get("title") or b.get("typeTitle") or "(无标题)"
                url = b.get("url") or ""
                lines.append(f"{b['index']}. {title}" + (f"\n   {url}" if url else ""))
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"banner 失败: {err}")
            await self._reply(event, f"获取轮播失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*歌单分类$", re.IGNORECASE))
    async def catlist(self, event: AstrMessageEvent):
        '''歌单分类'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        try:
            cats = await ncmapi.playlist_cats(user_key=self._user_key(event))
            if not cats:
                await self._reply(event, "暂无歌单分类数据")
                event.stop_event()
                return
            lines = ["♫ 歌单分类"]
            for c in cats[:60]:
                lines.append(f"{c['name']}（{cardlib.fmt_count(c['count'])}）")
            lines.append("")
            lines.append("发送 #ncm精品歌单 分类名 查看该分类精品歌单")
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"歌单分类失败: {err}")
            await self._reply(event, f"获取歌单分类失败：{err}")
        event.stop_event()

    # ══════════════════ 探索 · 扩展 ══════════════════

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*MV\s*(.+)$", re.IGNORECASE))
    async def mv(self, event: AstrMessageEvent):
        '''MV 搜索与播放链接'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*MV\s*(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        user_key = self._user_key(event)
        try:
            mvs = await ncmapi.search_mv(kw, limit=5, user_key=user_key)
            if not mvs:
                await self._reply(event, f"没有搜到「{kw}」的 MV")
                event.stop_event()
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
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"MV 失败: {err}")
            await self._reply(event, f"获取 MV 失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*相似歌单\s+(.+)$", re.IGNORECASE))
    async def simi_playlist(self, event: AstrMessageEvent):
        '''相似歌单'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*相似歌单\s+(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        user_key = self._user_key(event)
        try:
            song = await self._resolve_song(kw, user_key)
            if not song:
                await self._reply(event, f"没有搜到「{kw}」")
                event.stop_event()
                return
            pls = await ncmapi.simi_playlists(song["id"], limit=10, user_key=user_key)
            if not pls:
                await self._reply(event, "暂无相似歌单")
                event.stop_event()
                return
            lines = [f"♫ 与「{song['name'] or kw}」相似的歌单"]
            for p in pls:
                lines.append(f"{p['index']}. {p['name']}（{cardlib.fmt_count(p['playCount'])}播放 · {p['trackCount']}首）")
            lines.append("")
            lines.append("发送 #ncm歌单 歌单名 查看曲目")
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"相似歌单失败: {err}")
            await self._reply(event, f"获取相似歌单失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*歌手榜$", re.IGNORECASE))
    async def toplist_artist(self, event: AstrMessageEvent):
        '''歌手榜'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        try:
            artists = await ncmapi.toplist_artist(user_key=self._user_key(event))
            if not artists:
                await self._reply(event, "暂无歌手榜数据")
                event.stop_event()
                return
            lines = ["♫ 网易云歌手榜"]
            for a in artists[:15]:
                lines.append(f"{a['index']}. {a['name']}")
            lines.append("")
            lines.append("发送 #ncm歌手 歌手名 查看热门歌曲")
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"歌手榜失败: {err}")
            await self._reply(event, f"获取歌手榜失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*新碟$", re.IGNORECASE))
    async def album_newest(self, event: AstrMessageEvent):
        '''新碟上架'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        try:
            albums = await ncmapi.album_newest(limit=10, user_key=self._user_key(event))
            if not albums:
                await self._reply(event, "暂无新碟数据")
                event.stop_event()
                return
            lines = ["♫ 新碟上架"]
            for a in albums:
                lines.append(f"{a['index']}. {a['name']} - {a['artist']}（{cardlib.fmt_count(a['size'])}首）")
            lines.append("")
            lines.append("发送 #ncm专辑 专辑名 查看曲目")
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"新碟失败: {err}")
            await self._reply(event, f"获取新碟失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*热门歌手$", re.IGNORECASE))
    async def top_artists(self, event: AstrMessageEvent):
        '''热门歌手'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        try:
            artists = await ncmapi.top_artists(limit=20, user_key=self._user_key(event))
            if not artists:
                await self._reply(event, "暂无热门歌手数据")
                event.stop_event()
                return
            lines = ["♫ 热门歌手"]
            for a in artists:
                lines.append(f"{a['index']}. {a['name']}")
            lines.append("")
            lines.append("发送 #ncm歌手 歌手名 查看热门歌曲")
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"热门歌手失败: {err}")
            await self._reply(event, f"获取热门歌手失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*新碟榜\s*(.*)$", re.IGNORECASE))
    async def top_album(self, event: AstrMessageEvent):
        '''新碟排行'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*新碟榜\s*(.*)$", event.message_str.strip(), re.IGNORECASE)
        area = (m.group(1).strip() if m else "").strip() or "ALL"
        area_map = {"华语": "ZH", "欧美": "EA", "韩国": "KR", "日本": "JP", "all": "ALL"}
        area_id = area_map.get(area, area)
        try:
            albums = await ncmapi.top_album(area=area_id, limit=10, user_key=self._user_key(event))
            if not albums:
                await self._reply(event, "暂无新碟榜数据")
                event.stop_event()
                return
            lines = [f"♫ 新碟榜 · {area if area != 'ALL' else '全部'}"]
            for a in albums:
                lines.append(f"{a['index']}. {a['name']} - {a['artist']}")
            lines.append("")
            lines.append("发送 #ncm专辑 专辑名 查看曲目")
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"新碟榜失败: {err}")
            await self._reply(event, f"获取新碟榜失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*MV榜$", re.IGNORECASE))
    async def top_mv(self, event: AstrMessageEvent):
        '''MV 排行'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        try:
            mvs = await ncmapi.top_mv(limit=10, user_key=self._user_key(event))
            if not mvs:
                await self._reply(event, "暂无 MV 榜数据")
                event.stop_event()
                return
            lines = ["♫ MV 排行"]
            for mv_item in mvs:
                lines.append(f"{mv_item['index']}. {mv_item['name']} - {mv_item['artist']}（{cardlib.fmt_count(mv_item['playCount'])}播放）")
            lines.append("")
            lines.append("发送 #ncmMV 关键词 查看 MV 详情与链接")
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"MV榜失败: {err}")
            await self._reply(event, f"获取 MV 榜失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*电台$", re.IGNORECASE))
    async def dj_recommend(self, event: AstrMessageEvent):
        '''电台推荐'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        try:
            radios = await ncmapi.dj_recommend(limit=10, user_key=self._user_key(event))
            if not radios:
                await self._reply(event, "暂无电台推荐数据")
                event.stop_event()
                return
            lines = ["♫ 电台推荐"]
            for d in radios:
                desc = f"：{d['desc']}" if d.get("desc") else ""
                lines.append(f"{d['index']}. {d['name']}{desc}")
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"电台失败: {err}")
            await self._reply(event, f"获取电台推荐失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*歌单榜\s*(.*)$", re.IGNORECASE))
    async def top_playlist(self, event: AstrMessageEvent):
        '''分类歌单榜'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*歌单榜\s*(.*)$", event.message_str.strip(), re.IGNORECASE)
        cat = (m.group(1).strip() if m else "").strip() or "全部"
        try:
            pls = await ncmapi.top_playlists(cat=cat, limit=20, user_key=self._user_key(event))
            if not pls:
                await self._reply(event, f"分类「{cat}」暂无歌单数据")
                event.stop_event()
                return
            lines = [f"♫ 歌单榜 · {cat}"]
            for p in pls:
                lines.append(f"{p['index']}. {p['name']}（{cardlib.fmt_count(p['playCount'])}播放 · {p['trackCount']}首）")
            lines.append("")
            lines.append("发送 #ncm歌单 歌单名 查看曲目")
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"歌单榜失败: {err}")
            await self._reply(event, f"获取歌单榜失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*热门分类$", re.IGNORECASE))
    async def playlist_hot_tags(self, event: AstrMessageEvent):
        '''热门歌单分类'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        try:
            tags = await ncmapi.playlist_hot_tags(user_key=self._user_key(event))
            if not tags:
                await self._reply(event, "暂无热门分类数据")
                event.stop_event()
                return
            lines = ["♫ 热门歌单分类"]
            for t in tags:
                lines.append(f"{t['index']}. {t['name']}（{cardlib.fmt_count(t['usedCount'])}个歌单）")
            lines.append("")
            lines.append("发送 #ncm歌单榜 分类名 查看该分类歌单")
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"热门分类失败: {err}")
            await self._reply(event, f"获取热门分类失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*逐字歌词\s+(.+)$", re.IGNORECASE))
    async def lyric_word(self, event: AstrMessageEvent):
        '''逐字歌词（新歌词接口）'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*逐字歌词\s+(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        user_key = self._user_key(event)
        try:
            song = await self._resolve_song(kw, user_key)
            if not song:
                await self._reply(event, f"没有搜到「{kw}」")
                event.stop_event()
                return
            lr = await ncmapi.lyric_new(song["id"], user_key=user_key)
            yrc = lr.get("yrc") or ""
            if not yrc:
                await self._reply(event, "该歌曲暂无逐字歌词")
                event.stop_event()
                return
            lines = []
            for l in yrc.splitlines():
                t = re.sub(r"\[[\d,]+\]", "", l).strip()
                if t:
                    lines.append(t)
                if len(lines) >= 30:
                    break
            if not lines:
                await self._reply(event, "该歌曲暂无逐字歌词")
                event.stop_event()
                return
            data = cardlib.build_lyric_card_data(song, lines, line_count=len(lines))
            data["tip"] = "逐字歌词来自网易云音乐"
            await self._reply_card_or_text(event, tpl_name="ncm-lyric", data=data, format_text=lambda d: cardlib.format_lyric_text(song, lines))
        except ApiError as err:
            self._log_warn(f"逐字歌词失败: {err}")
            await self._reply(event, f"获取逐字歌词失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*精准\s+(.+)$", re.IGNORECASE))
    async def search_match(self, event: AstrMessageEvent):
        '''精准匹配并播放'''
        cfg = self._cfg()
        if not cfg.get("enable", True) or cfg.get("enableSongRequest") is False:
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*精准\s+(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        user_key = self._user_key(event)
        try:
            song = await ncmapi.search_match(kw, user_key=user_key)
            if not song:
                # 精确匹配接口对纯歌名不可靠，回退普通搜索第一条
                lst = await ncmapi.search(kw, type_=1, limit=1, user_key=user_key)
                if not lst:
                    await self._reply(event, f"未匹配到「{kw}」，可改用 #ncm点歌")
                    event.stop_event()
                    return
                song = lst[0]
            await self._play_song(event, song, user_key=user_key, source="精准匹配")
        except ApiError as err:
            self._log_warn(f"精准匹配失败: {err}")
            await self._reply(event, f"精准匹配失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*歌单评论\s+(.+)$", re.IGNORECASE))
    async def playlist_comment(self, event: AstrMessageEvent):
        '''歌单评论'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*歌单评论\s+(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        user_key = self._user_key(event)
        try:
            pl = await self._resolve_playlist(kw, user_key)
            if not pl:
                await self._reply(event, f"没有搜到歌单「{kw}」")
                event.stop_event()
                return
            comments = await ncmapi.comment_playlist(pl["id"], limit=20, user_key=user_key)
            if not comments:
                await self._reply(event, "该歌单暂无评论")
                event.stop_event()
                return
            data = cardlib.build_comment_card_data(pl, comments, total=len(comments))
            await self._reply_card_or_text(event, tpl_name="ncm-comment", data=data, format_text=lambda d: cardlib.format_comment_text(pl, comments))
        except ApiError as err:
            self._log_warn(f"歌单评论失败: {err}")
            await self._reply(event, f"获取歌单评论失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*专辑评论\s+(.+)$", re.IGNORECASE))
    async def album_comment(self, event: AstrMessageEvent):
        '''专辑评论'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*专辑评论\s+(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        user_key = self._user_key(event)
        try:
            album = await self._resolve_album(kw, user_key)
            if not album:
                await self._reply(event, f"没有搜到专辑「{kw}」")
                event.stop_event()
                return
            comments = await ncmapi.comment_album(album["id"], limit=20, user_key=user_key)
            if not comments:
                await self._reply(event, "该专辑暂无评论")
                event.stop_event()
                return
            data = cardlib.build_comment_card_data(album, comments, total=len(comments))
            await self._reply_card_or_text(event, tpl_name="ncm-comment", data=data, format_text=lambda d: cardlib.format_comment_text(album, comments))
        except ApiError as err:
            self._log_warn(f"专辑评论失败: {err}")
            await self._reply(event, f"获取专辑评论失败：{err}")
        event.stop_event()

    # ══════════════════ 推荐 ══════════════════

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*推荐$", re.IGNORECASE))
    async def recommend(self, event: AstrMessageEvent):
        '''推荐歌单'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        try:
            pls = await ncmapi.recommend_playlists(limit=15, user_key=self._user_key(event))
            if not pls:
                await self._reply(event, "暂无推荐歌单")
                event.stop_event()
                return
            lines = ["♫ 推荐歌单"]
            for p in pls:
                lines.append(f"{p['index']}. {p['name']}（{cardlib.fmt_count(p['playCount'])}播放 · {p['trackCount']}首）")
            lines.append("")
            lines.append("发送 #ncm歌单 歌单名 查看曲目")
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"推荐失败: {err}")
            await self._reply(event, f"获取推荐失败：{err}\n可能需要 #ncm登录")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*(来首歌|随机|放一首|来一首)$", re.IGNORECASE))
    async def random_song(self, event: AstrMessageEvent):
        '''随机来一首'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        user_key = self._user_key(event)
        try:
            try:
                songs = await ncmapi.personal_fm(user_key=user_key)
            except ApiError:
                # 未登录时退到推荐新歌随机
                songs = await ncmapi.recommend_newsong(limit=20, user_key=user_key)
            if not songs:
                await self._reply(event, "没有拿到推荐歌曲，请稍后再试")
                event.stop_event()
                return
            song = random.choice(songs)
            await self._play_song(event, song, user_key=user_key, source="推荐")
        except ApiError as err:
            self._log_warn(f"来首歌失败: {err}")
            await self._reply(event, f"随机点歌失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*(日推|每日推荐)$", re.IGNORECASE))
    async def daily(self, event: AstrMessageEvent):
        '''每日推荐'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        user_key = self._user_key(event)
        try:
            songs = await ncmapi.daily_songs(user_key=user_key)
            if not songs:
                await self._reply(event, "今日暂无推荐（可能已获取过或没有听歌记录）")
                event.stop_event()
                return
            await self._list_to_session(event, "每日推荐", songs[:20])
        except ApiError as err:
            self._log_warn(f"日推失败: {err}")
            await self._reply(event, f"获取每日推荐失败：{err}\n可能需要 #ncm登录")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*推荐新歌$", re.IGNORECASE))
    async def newsong_recommend(self, event: AstrMessageEvent):
        '''推荐新歌'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        try:
            songs = await ncmapi.recommend_newsong(limit=15, user_key=self._user_key(event))
            if not songs:
                await self._reply(event, "暂无推荐新歌")
                event.stop_event()
                return
            await self._list_to_session(event, "推荐新歌", songs)
        except ApiError as err:
            self._log_warn(f"推荐新歌失败: {err}")
            await self._reply(event, f"获取推荐新歌失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*喜欢$", re.IGNORECASE))
    async def like_list(self, event: AstrMessageEvent):
        '''我喜欢的音乐'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        user_key = self._user_key(event)
        uid = await self._get_uid(user_key)
        if not uid:
            await self._reply(event, "需要登录后使用，请先 #ncm登录")
            event.stop_event()
            return
        try:
            songs = await ncmapi.likelist(uid, limit=30, user_key=user_key)
            if not songs:
                await self._reply(event, "我喜欢的音乐为空")
                event.stop_event()
                return
            await self._list_to_session(event, "我喜欢的音乐", songs)
        except ApiError as err:
            self._log_warn(f"喜欢列表失败: {err}")
            await self._reply(event, f"获取喜欢列表失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*听歌排行$", re.IGNORECASE))
    async def user_record(self, event: AstrMessageEvent):
        '''本周听歌排行'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        user_key = self._user_key(event)
        uid = await self._get_uid(user_key)
        if not uid:
            await self._reply(event, "需要登录后使用，请先 #ncm登录")
            event.stop_event()
            return
        try:
            songs = await ncmapi.user_record(uid, type_=1, limit=30, user_key=user_key)
            if not songs:
                await self._reply(event, "本周暂无听歌记录")
                event.stop_event()
                return
            shown = []
            for i, s in enumerate(songs):
                s2 = dict(s)
                if s2.get("playCount"):
                    s2["duration"] = f"{s2['duration']} · 听{s2['playCount']}次"
                shown.append(s2)
            await self._list_to_session(event, "本周听歌排行", shown)
        except ApiError as err:
            self._log_warn(f"听歌排行失败: {err}")
            await self._reply(event, f"获取听歌排行失败：{err}")
        event.stop_event()

    # ══════════════════ 账号 · 扩展（需登录） ══════════════════

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*签到$", re.IGNORECASE), priority=6)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def daily_signin(self, event: AstrMessageEvent):
        '''每日签到'''
        if not self._cfg().get("enable", True):
            return
        user_key = self._user_key(event)
        try:
            body = await ncmapi.daily_signin(type_=0, user_key=user_key)
            d = body.get("android") if isinstance(body.get("android"), dict) else body
            code = d.get("code") if isinstance(d, dict) else None
            if code == 200:
                point = d.get("point") if isinstance(d, dict) else None
                await self._reply(event, "✅ 签到成功" + (f"，+{point} 经验" if point else ""))
            elif code == -2:
                await self._reply(event, "今日已签到过啦")
            else:
                await self._reply(event, f"签到失败：{d.get('message') if isinstance(d, dict) else '未知错误'}")
        except ApiError as err:
            self._log_warn(f"签到失败: {err}")
            await self._reply(event, f"签到失败：{err}\n需要先 #ncm登录")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*云盘$", re.IGNORECASE), priority=6)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def user_cloud(self, event: AstrMessageEvent):
        '''云盘歌曲'''
        if not self._cfg().get("enable", True):
            return
        user_key = self._user_key(event)
        try:
            songs = await ncmapi.user_cloud(limit=30, user_key=user_key)
            if not songs:
                await self._reply(event, "云盘暂无歌曲")
                event.stop_event()
                return
            await self._list_to_session(event, "我的云盘", songs[:20])
        except ApiError as err:
            self._log_warn(f"云盘失败: {err}")
            await self._reply(event, f"获取云盘失败：{err}\n需要先 #ncm登录")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*最近$", re.IGNORECASE), priority=6)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def recent_song(self, event: AstrMessageEvent):
        '''最近播放'''
        if not self._cfg().get("enable", True):
            return
        user_key = self._user_key(event)
        if not self._has_cookie():
            await self._reply(event, "需要登录后使用，请先 #ncm登录")
            event.stop_event()
            return
        try:
            songs = await ncmapi.record_recent_song(limit=30, user_key=user_key)
            if not songs:
                await self._reply(event, "暂无最近播放记录")
                event.stop_event()
                return
            shown = []
            for s in songs:
                s2 = dict(s)
                if s2.get("playCount"):
                    s2["duration"] = f"{s2['duration']} · 播{s2['playCount']}次"
                shown.append(s2)
            await self._list_to_session(event, "最近播放", shown)
        except ApiError as err:
            self._log_warn(f"最近播放失败: {err}")
            await self._reply(event, f"获取最近播放失败：{err}\n需要先 #ncm登录")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*我的歌单$", re.IGNORECASE), priority=6)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def my_playlist(self, event: AstrMessageEvent):
        '''我的歌单'''
        if not self._cfg().get("enable", True):
            return
        user_key = self._user_key(event)
        uid = await self._get_uid(user_key)
        if not uid:
            await self._reply(event, "需要登录后使用，请先 #ncm登录")
            event.stop_event()
            return
        try:
            pls = await ncmapi.user_playlist(uid, limit=30, user_key=user_key)
            if not pls:
                await self._reply(event, "暂无歌单")
                event.stop_event()
                return
            lines = ["♫ 我的歌单"]
            for p in pls:
                lines.append(f"{p['index']}. {p['name']}（{cardlib.fmt_count(p['playCount'])}播放 · {p['trackCount']}首）")
            lines.append("")
            lines.append("发送 #ncm歌单 歌单名 查看曲目")
            await self._reply(event, "\n".join(lines))
        except ApiError as err:
            self._log_warn(f"我的歌单失败: {err}")
            await self._reply(event, f"获取歌单失败：{err}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*红心\s+(.+)$", re.IGNORECASE), priority=6)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def like_toggle(self, event: AstrMessageEvent):
        '''红心/取消红心歌曲'''
        if not self._cfg().get("enable", True):
            return
        m = re.match(r"^#?(?:ncm|NCM)\s*红心\s+(.+)$", event.message_str.strip(), re.IGNORECASE)
        kw = (m.group(1).strip() if m else "").strip()
        user_key = self._user_key(event)
        try:
            song = await self._resolve_song(kw, user_key)
            if not song:
                await self._reply(event, f"没有搜到「{kw}」")
                event.stop_event()
                return
            try:
                checked = await ncmapi.song_like_check([song["id"]], user_key=user_key)
                liked = checked.get(str(song["id"]), False)
            except ApiError:
                liked = False
            await ncmapi.like(song["id"], like_=not liked, user_key=user_key)
            if liked:
                await self._reply(event, f"💔 已取消红心：{song['name']} - {song['artist']}")
            else:
                await self._reply(event, f"❤️ 已红心：{song['name']} - {song['artist']}")
        except ApiError as err:
            self._log_warn(f"红心失败: {err}")
            await self._reply(event, f"红心失败：{err}\n需要先 #ncm登录")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*历史日推$", re.IGNORECASE), priority=6)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def history_daily(self, event: AstrMessageEvent):
        '''历史每日推荐'''
        if not self._cfg().get("enable", True):
            return
        user_key = self._user_key(event)
        if not self._has_cookie():
            await self._reply(event, "需要登录后使用，请先 #ncm登录")
            event.stop_event()
            return
        try:
            songs = await ncmapi.history_recommend_songs(user_key=user_key)
            if not songs:
                await self._reply(event, "暂无历史推荐记录")
                event.stop_event()
                return
            await self._list_to_session(event, "历史每日推荐", songs[:20])
        except ApiError as err:
            self._log_warn(f"历史日推失败: {err}")
            await self._reply(event, f"获取历史日推失败：{err}\n需要先 #ncm登录")
        event.stop_event()

    # ══════════════════ 账号 ══════════════════

    @filter.regex(re.compile(r"^#?(ncm登录|ncm扫码登录|网易云登录|网易云扫码登录)$", re.IGNORECASE), priority=6)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def start_qr_login(self, event: AstrMessageEvent):
        '''扫码登录'''
        cfg = self._cfg()
        if not cfg.get("enable", True):
            return
        if cfg.get("qrLoginEnable") is False:
            await self._reply(event, "扫码登录已在配置中关闭")
            event.stop_event()
            return
        user_key = self._user_key(event)
        self._stop_poll(user_key)
        try:
            await self._reply(event, "正在获取网易云登录二维码…")
            key = await ncmapi.qr_key(user_key=user_key)
            if not key:
                await self._reply(event, "获取二维码失败：无法获取 unikey，请检查 API 服务")
                event.stop_event()
                return
            info = await ncmapi.qr_create(key, user_key=user_key)
            qrurl = info.get("qrurl") or ""
            qrimg = info.get("qrimg") or ""
            tip_text = "请使用网易云音乐 App 扫码登录\n二维码约 5 分钟内有效"
            qr_path = await self._save_qr_image(qrimg) if qrimg else None
            img_sent = False
            if qr_path:
                try:
                    await self._send_chain(event, Image.fromFileSystem(qr_path), self._plain(tip_text))
                    img_sent = True
                except Exception:
                    pass
                asyncio.get_running_loop().call_later(120, lambda: self._safe_unlink(qr_path))
            if not img_sent:
                await self._reply(event, tip_text + (f"\n或打开链接扫码：{qrurl}" if qrurl else ""))
            self._start_poll(event, key, 300)
        except ApiError as err:
            await self._reply(event, f"扫码登录失败：{err}")
        event.stop_event()

    def _stop_poll(self, user_key: str):
        task = self._active_logins.pop(user_key, None)
        if task and task.get("timer") is not None:
            try:
                task["timer"].cancel()
            except Exception:
                pass

    def _start_poll(self, event: AstrMessageEvent, key: str, max_sec: int = 300):
        user_key = self._user_key(event)
        started = time.time()
        task = {"key": key, "stopped": False, "busy": False, "notifiedScan": False, "failStreak": 0}
        self._active_logins[user_key] = task
        loop = asyncio.get_running_loop()

        async def _tick():
            if task["stopped"]:
                return
            if task["busy"]:
                loop.call_later(0.8, lambda: asyncio.create_task(_tick()))
                return
            if time.time() - started > max_sec:
                task["stopped"] = True
                self._active_logins.pop(user_key, None)
                await self._reply(event, "二维码已过期，请重新 #ncm登录")
                return
            task["busy"] = True
            try:
                body = await ncmapi.qr_check(key, user_key=user_key)
                code = (body or {}).get("code")
                if code == 800:
                    task["stopped"] = True
                    self._active_logins.pop(user_key, None)
                    await self._reply(event, "二维码已失效，请重新 #ncm登录")
                    return
                if code == 802 and not task["notifiedScan"]:
                    task["notifiedScan"] = True
                    await self._reply(event, "已扫码，请在手机上确认登录")
                elif code == 803:
                    await self._finish_login(event, body, user_key, task)
                    return
                task["failStreak"] = 0
            except Exception as err:
                task["failStreak"] += 1
                if task["failStreak"] == 5:
                    await self._reply(event, f"轮询暂时失败：{err}（继续重试）")
                if task["failStreak"] >= 25:
                    task["stopped"] = True
                    self._active_logins.pop(user_key, None)
                    await self._reply(event, "轮询失败过多，请检查 API 服务或重新 #ncm登录")
                    return
            finally:
                task["busy"] = False
            if not task["stopped"] and self._active_logins.get(user_key, {}).get("key") == key:
                task["timer"] = loop.call_later(2, lambda: asyncio.create_task(_tick()))

        task["timer"] = loop.call_later(2, lambda: asyncio.create_task(_tick()))

    async def _finish_login(self, event: AstrMessageEvent, body: dict, user_key: str, task: dict):
        task["stopped"] = True
        self._active_logins.pop(user_key, None)
        cookie = (body or {}).get("cookie") or ""
        nickname = (body or {}).get("nickname") or ""
        if not cookie:
            await self._reply(event, "登录成功但未获取到 Cookie（可能登录状态异常），请重新 #ncm登录")
            return
        # 扫码登录成功后把 Cookie 存入插件配置（全群共享账号，重启不丢）
        try:
            self.config["defaultCookie"] = cookie
            self.config.save_config()
            self._log_info("扫码登录成功，Cookie 已写入插件配置 defaultCookie")
        except Exception as e:
            self._log_warn(f"写入默认 Cookie 失败: {e}")
        await self._reply(event, f"✅ 登录成功：{nickname or '已写入 Cookie'}\nCookie 已存入插件配置，全群默认使用该账号")
        st = None
        try:
            st = await ncmapi.login_status(user_key=user_key)
            profile = st.get("profile") or {}
            if profile and profile.get("userId"):
                self.config["defaultUid"] = str(profile["userId"])
                self.config.save_config()
        except Exception:
            pass
        await self._send_status(event, user_key, status_data=st)

    async def _build_status(self, user_key: str, *, status_data: dict | None = None) -> dict:
        cfg = self._cfg()
        default_cookie = str(cfg.get("defaultCookie") or "")
        status = {
            "loggedIn": False,
            "nickname": "",
            "avatar": "",
            "uin": "",
            "level": "",
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
        if profile and profile.get("userId"):
            status["loggedIn"] = True
            status["nickname"] = profile.get("nickname") or ""
            status["avatar"] = profile.get("avatarUrl") or ""
            status["uin"] = str(profile.get("userId") or "")
            lv = profile.get("level") or 0
            status["level"] = str(lv)
        elif default_cookie:
            status["keyStatus"] = "Cookie 已失效或未写入（登录态 301）"
        return status

    async def _send_status(self, event: AstrMessageEvent, user_key: str, *, status_data: dict | None = None):
        try:
            status = await self._build_status(user_key, status_data=status_data)
            data = cardlib.build_status_card_data(status)
            await self._reply_card_or_text(event, tpl_name="ncm-status", data=data, format_text=lambda d: cardlib.format_status_text(status))
        except Exception as err:
            self._log_warn(f"状态卡片失败: {err}")
            await self._reply(event, cardlib.format_status_text({"loggedIn": False, "apiBase": self._cfg().get("apiBase") or "", "quality": str(self._cfg().get("quality") or "auto"), "keyStatus": str(err)}))

    @filter.regex(re.compile(r"^#?(ncm状态|ncm登录状态|ncms)$", re.IGNORECASE), priority=6)
    async def login_status_cmd(self, event: AstrMessageEvent):
        '''登录状态'''
        if not self._cfg().get("enable", True):
            return
        await self._send_status(event, self._user_key(event))
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm登出|ncm注销|ncm解绑)$", re.IGNORECASE), priority=6)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def logout(self, event: AstrMessageEvent):
        '''登出'''
        user_key = self._user_key(event)
        try:
            try:
                await ncmapi.logout(user_key=user_key)
            except ApiError:
                pass
            # 清除插件配置里的默认 Cookie（共享账号登出）
            if self.config.get("defaultCookie"):
                self.config["defaultCookie"] = ""
                self.config["defaultUid"] = ""
                self.config.save_config()
                await self._reply(event, "已登出网易云账号，并清除插件配置中的默认 Cookie")
            else:
                await self._reply(event, "已登出网易云账号")
        except Exception as err:
            await self._reply(event, f"登出失败：{err}")
        event.stop_event()

    # ══════════════════ 管理 ══════════════════

    @filter.regex(re.compile(r"^#?(ncm设置|ncm配置|网易云设置)$", re.IGNORECASE), priority=6)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def settings(self, event: AstrMessageEvent):
        '''设置面板'''
        cfg = self._cfg()
        user_key = self._user_key(event)
        try:
            uid = await self._get_uid(user_key)
        except Exception:
            uid = ""
        default_cookie = str(cfg.get("defaultCookie") or "")
        # Cookie 尾号仅作「是否已配置」提示，过短的 Cookie 直接打码避免完整泄露
        cookie_tail = default_cookie[-4:] if len(default_cookie) >= 4 else "****"
        lines = [
            "🎵 网易云音乐插件设置",
            f"API：{cardlib.mask_api_base(cfg.get('apiBase') or '') or '未配置'}",
            f"默认Cookie：{'已配置（***' + cookie_tail + '）' if default_cookie else '未配置'}",
            f"点歌：{'开' if cfg.get('enableSongRequest', True) else '关'}　自动解析：{'开' if cfg.get('enableResolve', True) else '关'}",
            f"音质：{QUALITY_LABEL.get(cfg.get('quality') or 'auto', cfg.get('quality') or 'auto')}",
            f"解灰兜底：{'开' if cfg.get('qualityUnblock', True) else '关'}",
            f"语音：{'开' if cfg.get('sendVocal', True) else '关'}　文件：{'开' if cfg.get('uploadFile', True) else '关'}",
            f"卡片渲染：{'开' if cfg.get('renderListCard', True) else '关'}",
            f"列表上限：{cfg.get('maxList', 10)}　扫码登录：{'开' if cfg.get('qrLoginEnable', True) else '关'}",
            f"登录：{('有 Cookie · uid=' + uid) if uid else (('默认账号') if default_cookie else '未登录')}",
            "",
            "可修改：#ncm音质 <档位> / #ncm api <地址> / #ncm 开启|关闭 点歌|解析",
        ]
        await self._reply(event, "\n".join(lines))
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*音质\s*(.+)$", re.IGNORECASE), priority=6)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def quality_cmd(self, event: AstrMessageEvent):
        '''修改音质'''
        m = re.match(r"^#?(?:ncm|NCM)\s*音质\s*(.+)$", event.message_str.strip(), re.IGNORECASE)
        q = (m.group(1).strip().lower() if m else "").strip()
        if q not in QUALITY_LABEL:
            await self._reply(event, f"音质档位无效。可选：{' / '.join(QUALITY_LABEL.keys())}")
            event.stop_event()
            return
        self.config["quality"] = q
        self.config.save_config()
        await self._reply(event, f"已设置音质：{QUALITY_LABEL.get(q, q)}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*api\s*(https?://\S+)$", re.IGNORECASE))
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def api_cmd(self, event: AstrMessageEvent):
        '''修改 API 地址'''
        m = re.match(r"^#?(?:ncm|NCM)\s*api\s*(https?://\S+)$", event.message_str.strip(), re.IGNORECASE)
        url = m.group(1).strip().rstrip("/") if m else ""
        self.config["apiBase"] = url
        self.config.save_config()
        await self._reply(event, f"已设置 API 地址：{url}")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm|NCM)\s*(开启|关闭)(点歌|解析)$", re.IGNORECASE))
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def toggle_cmd(self, event: AstrMessageEvent):
        '''开启/关闭功能'''
        m = re.match(r"^#?(?:ncm|NCM)\s*(开启|关闭)(点歌|解析)$", event.message_str.strip(), re.IGNORECASE)
        on = (m.group(1) if m else "") == "开启"
        what = (m.group(2) if m else "") or ""
        if what == "点歌":
            self.config["enableSongRequest"] = on
        elif what == "解析":
            self.config["enableResolve"] = on
        self.config.save_config()
        await self._reply(event, f"已{'开启' if on else '关闭'}{what}功能")
        event.stop_event()

    @filter.regex(re.compile(r"^#?(ncm测试|网易云测试)$", re.IGNORECASE), priority=6)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def api_test(self, event: AstrMessageEvent):
        '''测试 API 连通'''
        cfg = self._cfg()
        base = str(cfg.get("apiBase") or "")
        if not base:
            await self._reply(event, "⚠ API 未配置，请使用 #ncm api <地址> 配置")
            event.stop_event()
            return
        try:
            lst = await ncmapi.search("测试", type_=1, limit=1)
            await self._reply(event, f"✅ API 连通正常：{base}\n搜索结果 {len(lst)} 条")
        except ApiError as e:
            await self._reply(event, f"❌ API 连接失败：{e}")
        event.stop_event()

    # ══════════════════ 链接自动解析 ══════════════════

    @filter.regex(re.compile(r"(music\.163\.com|163music\.com|y\.music\.163\.com)", re.IGNORECASE))
    async def resolve(self, event: AstrMessageEvent):
        '''网易云链接自动解析'''
        cfg = self._cfg()
        if not cfg.get("enable", True) or cfg.get("enableResolve") is False:
            return
        text = _collect_message_text(event)
        if not _is_ncm_message(text):
            return
        if _is_plugin_command_msg(event.message_str):
            return
        handled = await self._handle_resolve(event, text)
        if handled:
            event.stop_event()

    async def _handle_resolve(self, event: AstrMessageEvent, text: str) -> bool:
        user_key = self._user_key(event)
        try:
            # 歌单 / 专辑优先（id 模式）
            m = re.search(r"playlist\?(?:[^&\s]*&)*id=(\d+)|playlist/(\d+)", text)
            if m:
                pid = int(m.group(1) or m.group(2))
                songs = await ncmapi.playlist_tracks(pid, user_key=user_key)
                if not songs:
                    await self._reply(event, "歌单暂无曲目或不存在")
                    return True
                shown = songs[:30]
                await self._list_to_session(event, "链接解析 · 歌单", shown, tip=f"歌单共 {len(songs)} 首，显示前 {len(shown)} 首")
                return True
            m = re.search(r"album\?(?:[^&\s]*&)*id=(\d+)|album/(\d+)", text)
            if m:
                aid = int(m.group(1) or m.group(2))
                album_info, songs = await ncmapi.album_detail(aid, user_key=user_key)
                if not songs:
                    await self._reply(event, "专辑暂无曲目或不存在")
                    return True
                info = album_info or {}
                await self._list_to_session(event, f"链接解析 · {info.get('name') or '专辑'}", songs[:30])
                return True
            m = re.search(r"song\?(?:[^&\s]*&)*id=(\d+)|song/(\d+)", text)
            if m:
                sid = int(m.group(1) or m.group(2))
                lst = await ncmapi.song_detail([sid], user_key=user_key)
                if not lst:
                    await self._reply(event, "歌曲不存在或无版权")
                    return True
                await self._play_song(event, lst[0], user_key=user_key, source="链接解析")
                return True
            # 无 id：尝试把链接文本当关键词搜索
            kw = re.sub(r"https?://\S+|\[CQ:[^\]]*\]", "", text).strip()
            kw = re.sub(r"music\.163\.com|163music\.com|网易云音乐|分享|歌曲|链接", "", kw, flags=re.IGNORECASE).strip()
            if len(kw) >= 2:
                lst = await ncmapi.search(kw, type_=1, limit=1, user_key=user_key)
                if lst:
                    await self._play_song(event, lst[0], user_key=user_key, source="链接解析")
                    return True
            return False
        except ApiError as err:
            self._log_warn(f"解析失败: {err}")
            await self._reply(event, f"解析失败：{err}")
            return True

    # ══════════════════ 生命周期 ══════════════════

    async def terminate(self):
        '''插件卸载/停用时清理登录轮询'''
        for user_key in list(self._active_logins.keys()):
            self._stop_poll(user_key)
