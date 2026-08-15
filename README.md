<p align="center">  
  <img src="logo.png" width="120" alt="logo">
</p>

<h1 align="center">astrbot_plugin_neteasemusic</h1>

# 🎵 网易云音乐点歌/解析插件（AstrBot）

> 基于 [api-enhanced](https://github.com/neteasecloudmusicapienhanced/api-enhanced) API 的 AstrBot 网易云音乐插件。

点歌、播放、歌词/逐字歌词、热搜、排行榜、歌手/专辑/歌单/评论、相似推荐、新歌速递、日推、签到、云盘、扫码登录、音质自适配与解灰、语音/文件发送、网易云链接自动解析 —— 一站式网易云体验。

---

## ✨ 功能特性

- **点歌播放**：关键词搜索 → 列表卡片 → `#ncm听N` 选歌，或 `#ncm播放` 直接播第一首
- **音质自适配**：`auto` 自动匹配歌曲最高可用音质并逐级降级；VIP/灰歌自动解灰兜底
- **音频投递**：语音（silk 转码）+ 群/好友文件双通道，互不阻塞，失败自动回退
- **多平台适配**：QQ 官方（合并消息规避额度、大文件分片上传、ffmpeg 压缩兜底、纯文本兜底）、个人微信 weixin_oc（语音自动降级为文件）、Telegram / 钉钉 / 飞书 / KOOK / Discord 原生支持语音与文件
- **卡片渲染**：7 套网易云红主题 HTML 卡片（列表/详情/歌词/热搜/评论/帮助/状态），自动裁剪白边
- **链接自动解析**：发送 `music.163.com` / `163music.com` 分享卡片或链接，自动识别歌曲/歌单/专辑并播放
- **扫码登录**：`#ncm登录` 生成二维码，轮询自动写入 Cookie，支持多账号（按会话隔离）
- **临时文件自清理**：卡片图、二维码、音频文件发出后自动延时清除，`keepFileSec=0` 即时清除

---

## 📦 依赖与前提

| 依赖 | 说明 |
| --- | --- |
| **AstrBot** | `>=4.16, <5`（推荐 4.26+） |
| **API 服务** | [api-enhanced](https://github.com/neteasecloudmusicapienhanced/api-enhanced)（默认 `http://127.0.0.1:3000`） |

> ⚠️ 本插件不内置 API，需自行部署 api-enhanced 服务端。插件通过 HTTP 调用其接口，所有数据来自网易云。

### 部署 API 服务

```bash
git clone https://github.com/neteasecloudmusicapienhanced/api-enhanced.git
cd api-enhanced
pnpm install
node app.js   # 默认监听 http://localhost:3000
```

---

## 🚀 安装方式：WebUI 插件市场

AstrBot WebUI → 插件管理 → 搜索 `astrbot_plugin_neteasemusic` → 安装。

---

## ⚙️ 配置项

WebUI → 插件管理 → 本插件 → 设置面板。也可用指令热改部分项。

| 配置项 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `apiBase` | string | `""` | api-enhanced 服务地址，如 `http://127.0.0.1:3000` |
| `enable` | bool | `true` | 插件总开关 |
| `enableSongRequest` | bool | `true` | 点歌功能开关 |
| `enableResolve` | bool | `true` | 网易云链接自动解析开关 |
| `resolveLinks` | bool | `true` | 识别 `music.163.com` / `163music.com` 链接 |
| `maxList` | int | `10` | 点歌列表最大显示条数（1–20） |
| `quality` | string | `auto` | 最高播放音质，可选 `auto/jymaster/sky/jyeffect/hires/lossless/exhigh/higher/standard` |
| `qualityUnblock` | bool | `true` | VIP/灰歌自动解灰兜底（`unblock=true`，音质可能降为源站可用） |
| `sendVocal` | bool | `true` | 以语音消息方式发送音频 |
| `uploadFile` | bool | `true` | 以群/好友文件方式发送音频 |
| `tempDir` | string | `temp/neteasemusic` | 临时下载目录（相对插件目录） |
| `downloadTimeout` | int | `90000` | 音频下载超时（毫秒） |
| `keepFileSec` | int | `60` | 临时文件保留秒数（`0` 表示发出后立即删除，作用于音频+卡片图） |
| `ffmpegCompress` | bool | `true` | 大文件/FLAC 无法作为文件发送时（QQ 官方无分片上传/发送失败），用 ffmpeg 压成紧凑 mp3 兜底发送 |
| `compressBitrate` | int | `128` | 压缩兜底 mp3 码率（kbps） |
| `identifyPrefix` | string | `识别：` | 识别提示前缀 |
| `qrLoginEnable` | bool | `true` | 允许 `#ncm登录` 扫码登录 |
| `qqofficialAdapt` | bool | `true` | QQ 官方机器人专用适配（合并消息/语音回退文件/跳过原生卡片） |
| `qqofficialChunkedUpload` | bool | `true` | QQ 官方大文件分片上传（需 AstrBot ≥ 4.27.3）：FLAC/>10MB 文件以分片方式发送；关闭则保留旧守卫（大文件仅发语音） |
| `renderListCard` | bool | `true` | 列表/搜索结果渲染为图片卡片（关闭则纯文本） |
| `sendNativeCard` | bool | `false` | OneBot 原生音乐卡片（type=163，仅 aiocqhttp；QQ 官方自动跳过） |
| `sendTextInfo` | bool | `true` | 发送音频时附带歌曲信息文案 |
| `defaultCookie` | string | `""` | 网易云 Cookie（`MUSIC_U=...`），可手填或扫码自动写入 |
| `defaultUid` | string | `""` | 扫码登录后自动写入的账号 UID（自动反查补全，一般留空） |

---

## 🎮 指令一览

所有指令以 `#ncm` 为前缀（`#` 可省略，大小写不敏感）。会话内选歌也可用简写 `#听N`。

### 🎤 点歌播放

| 指令 | 说明 | 示例 |
| --- | --- | --- |
| `#ncm点歌 关键词` | 搜索并列出歌曲 | `#ncm点歌 晴天` |
| `#ncm听N` / `#听N` | 播放列表第 N 首 | `#ncm听1` |
| `#ncm播放 关键词` | 搜索并直接播放第一首 | `#ncm播放 晴天` |
| `#ncm来首歌` | 随机来一首（个人 FM，未登录退推荐新歌） | `#ncm来首歌` |
| `#ncm歌词 关键词\|id` | 获取歌词 | `#ncm歌词 晴天` |
| `#ncm逐字歌词 关键词\|id` | 逐字歌词 | `#ncm逐字歌词 晴天` |
| `#ncm热搜` | 热搜榜 | `#ncm热搜` |

### 🌐 发现音乐

| 指令 | 说明 | 示例 |
| --- | --- | --- |
| `#ncm排行 [榜单名]` | 排行榜列表 / 查看具体榜单 | `#ncm排行 飙升榜` |
| `#ncm歌手 关键词` | 歌手热门歌曲 | `#ncm歌手 周杰伦` |
| `#ncm专辑 关键词` | 专辑曲目 | `#ncm专辑 叶惠美` |
| `#ncm歌单 关键词` | 歌单曲目 | `#ncm歌单 华语` |
| `#ncm评论 关键词` | 歌曲热评 | `#ncm评论 晴天` |
| `#ncm相似 关键词\|id` | 相似歌曲 | `#ncm相似 晴天` |
| `#ncm相似歌单 关键词\|id` | 相似歌单 | `#ncm相似歌单 晴天` |
| `#ncm相关歌单 歌单名\|id` | 相关歌单推荐 | `#ncm相关歌单 华语` |
| `#ncm新歌 [地区]` | 新歌速递（华语/欧美/日本/韩国） | `#ncm新歌 华语` |
| `#ncm精品歌单 [分类]` | 精品歌单 | `#ncm精品歌单 华语` |
| `#ncm搜索建议 关键词` | 关键词补全 | `#ncm搜索建议 晴天` |
| `#ncmbanner` | 首页轮播 | `#ncmbanner` |
| `#ncm歌单分类` | 歌单分类列表 | `#ncm歌单分类` |
| `#ncm热门分类` | 热门歌单分类 | `#ncm热门分类` |
| `#ncmMV 关键词` | MV 详情与播放链接 | `#ncmMV 晴天` |
| `#ncm歌手榜` | 歌手榜 | `#ncm歌手榜` |
| `#ncm热门歌手` | 热门歌手 | `#ncm热门歌手` |
| `#ncm新碟` / `#ncm新碟榜 [地区]` | 新碟上架 / 新碟排行 | `#ncm新碟榜 华语` |
| `#ncmMV榜` | MV 排行 | `#ncmMV榜` |
| `#ncm电台` | 电台推荐 | `#ncm电台` |
| `#ncm歌单榜 [分类]` | 分类歌单榜 | `#ncm歌单榜 华语` |
| `#ncm歌单评论 关键词` | 歌单热评 | `#ncm歌单评论 华语` |
| `#ncm专辑评论 关键词` | 专辑热评 | `#ncm专辑评论 叶惠美` |

### 💎 推荐（部分需登录）

| 指令 | 说明 | 备注 |
| --- | --- | --- |
| `#ncm推荐` | 推荐歌单 | 需登录 |
| `#ncm日推` | 每日推荐 | 需登录 |
| `#ncm推荐新歌` | 推荐新歌 | — |
| `#ncm喜欢` | 我喜欢的音乐 | 需登录 |
| `#ncm听歌排行` | 本周听歌排行 | 需登录 |
| `#ncm历史日推` | 历史每日推荐 | 需登录（主人） |
| `#ncm签到` | 每日签到领经验 | 需登录（主人） |
| `#ncm云盘` | 我的云盘歌曲 | 需登录（主人） |
| `#ncm最近` | 最近播放歌曲 | 需登录（主人） |
| `#ncm我的歌单` | 我创建/收藏的歌单 | 需登录（主人） |
| `#ncm红心 关键词` | 红心/取消红心（自动判断状态） | 需登录（主人） |
| `#ncm取消红心 关键词` | 直接取消红心（不查状态） | 需登录（主人） |

### 👤 账号状态

| 指令 | 说明 |
| --- | --- |
| `#ncm登录` | 扫码登录（生成二维码，轮询自动写入 Cookie，仅主人） |
| `#ncm状态` / `#ncms` | 查看登录状态 |
| `#ncm登出` | 登出并清除本地 Cookie（仅主人） |

### 🛠️ 管理（仅主人）

| 指令 | 说明 | 示例 |
| --- | --- | --- |
| `#ncm设置` | 设置面板（登录态/音质/开关/脱敏 API） | `#ncm设置` |
| `#ncm音质 <档位>` | 修改音质 | `#ncm音质 lossless` |
| `#ncm api <地址>` | 修改 API 地址 | `#ncm api http://127.0.0.1:3000` |
| `#ncm 开启/关闭 点歌\|解析` | 功能开关 | `#ncm 关闭 解析` |
| `#ncm测试` | 测试 API 连通性 | `#ncm测试` |
| `#ncm帮助` | 帮助卡片 | `#ncm帮助` |

### 🔗 自动解析

直接发送网易云分享卡片或链接（`music.163.com` / `163music.com` / `y.music.163.com`），自动识别：

- **单曲** `song?id=` → 详情卡片 + 播放
- **歌单** `playlist?id=` → 列表卡片
- **专辑** `album?id=` → 列表卡片

---

## 🔊 音频投递说明

插件按 `sendVocal`（语音）和 `uploadFile`（文件）配置双通道投递，两者互不阻塞：

1. **语音**：本地音频经 silk 转码以语音消息发送
2. **文件**：以原始音质文件（mp3/flac 等）作为群/好友文件发送

### QQ 官方机器人适配（`qqofficialAdapt`）

QQ 官方机器人接口与 OneBot 差异较大，插件做了专项适配：

- **合并消息**：文案与首个媒体合并发送，规避被动回复额度限制
- **大文件分片上传**：AstrBot ≥ 4.27.3 的 QQ 官方适配器对本地 >10MB 文件自动走分片上传，无损 FLAC 也可作为文件发送（`qqofficialChunkedUpload` 开关，默认开）。旧版 AstrBot 或关闭该开关时保留守卫：按大小（>10MB）或后缀（`.flac`）拦截文件上传——此时开启 `ffmpegCompress`（默认开）会用 ffmpeg 压成紧凑 mp3 发送；ffmpeg 缺失/压缩失败才退回仅发语音（silk）
- **纯文本兜底**：媒体发送失败时，文本走 `msg_type=0` 纯文本，规避 `40034011 无效 markdown` 报错
- **原生卡片跳过**：QQ 官方无 OneBot `send_api`，原生音乐卡片自动跳过

### 个人微信（`weixin_oc`）适配

微信开放平台 ilink 通道（手机扫码登录个人微信）。weixin_oc 适配器出站**不支持语音（Record）**，`sendBySession` 只接收 Plain/Image/Video/File：

- **语音自动降级**：`sendVocal` 开启时，语音自动改为文件发送（无需改配置，图片卡片/二维码/文件均正常）
- **原生卡片跳过**：weixin_oc 无 OneBot `send_api`，原生音乐卡片自动跳过

### Telegram / 钉钉 / 飞书 / KOOK / Discord

五个平台适配器出站均**原生支持语音与文件**，插件双通道投递无需调整：

- **Telegram**：`send_voice`，用户隐私设置拒绝语音时适配器自动回退发文档
- **钉钉**：`_prepare_voice_for_dingtalk` 自动转码语音格式
- **飞书**：`convert_audio_to_opus` 转 opus 发送
- **KOOK**：上传资源后以 AUDIO 卡片发送
- **Discord**：语音转 wav 作为附件发送
- 五个平台均无 OneBot `send_api`，原生音乐卡片自动跳过

### aiocqhttp（OneBot）增强

- 可选 `sendNativeCard`：发送 OneBot 原生音乐卡片（type=163），需协议端支持 `send_api`

---

## 🧹 临时文件管理

插件会在 `tempDir`（默认 `temp/neteasemusic/`）下生成临时文件，并**在发出后自动清理**：

| 类型 | 文件 | 清理策略 |
| --- | --- | --- |
| 卡片图片 | `card_*.png` | 发出后 `keepFileSec` 秒清除（`finally` 保证孤儿文件也清） |
| 二维码 | `qr_*.png` | 发出后 120 秒清除 |
| 音频文件 | `*_*.mp3/.flac...` | 发出后 `keepFileSec` 秒清除 |

设置 `keepFileSec=0` 即「发出后立即删除」。由于平台适配器在 `await event.send` 返回前已将文件读入内存，即时删除对发送无影响。

---

## 📁 目录结构

```
astrbot_plugin_neteasemusic/
├── main.py                  # Star 主类：#ncm 指令 handler + 辅助方法
├── api.py                   # NcmApiClient：aiohttp HTTP 客户端 + Cookie 透传 + 归一化
├── quality.py               # 网易云音质阶梯与标签
├── delivery.py              # 音频下载 → Record/File 投递（含 QQ 官方适配）
├── cards.py                 # SessionStore + 卡片数据构建 + 文本兜底格式化
├── tpl_adapter.py           # art-template → Jinja2 模板适配
├── _conf_schema.json        # 配置项 schema
├── metadata.yaml            # 插件元数据
├── requirements.txt         # Python 依赖
├── __init__.py
└── resources/html/          # 7 套 HTML 卡片模板（网易云红主题）
    ├── ncm-list/            # 列表卡片
    ├── ncm-detail/          # 歌曲详情
    ├── ncm-lyric/           # 歌词
    ├── ncm-hot/             # 热搜榜
    ├── ncm-comment/         # 评论
    ├── ncm-help/            # 帮助
    └── ncm-status/          # 登录状态
```

---

## ❓ 常见问题

**Q：提示「未登录」或个人化接口失败？**
A：部分接口（日推、喜欢、云盘、听歌排行等）需登录。发送 `#ncm登录` 扫码登录，或手动在配置 `defaultCookie` 填入 `MUSIC_U=...` Cookie。多账号按会话隔离，各自扫码互不干扰。

**Q：VIP 歌曲播放失败/只有试听？**
A：确认 `qualityUnblock`（默认开）启用，且 API 服务端 `ENABLE_GENERAL_UNBLOCK=true`（默认开）。解灰可能将音质降为源站可用音质。

**Q：QQ 官方机器人发不出音频文件？**
A：AstrBot ≥ 4.27.3 起 QQ 官方适配器支持大文件分片上传（`qqofficialChunkedUpload` 默认开），FLAC/>10MB 也可正常发送。若分片不可用或仍发送失败，开启 `ffmpegCompress`（默认开）会用 ffmpeg 压成紧凑 mp3 兜底——请确认本机已安装 ffmpeg。

**Q：卡片不显示图片/渲染失败？**
A：卡片由本地 Playwright 渲染。确认已安装 `playwright` 依赖并执行过 `playwright install chromium`；如无法渲染，可关闭 `renderListCard` 退回纯文本。

**Q：自动解析不生效？**
A：确认 `enableResolve` 与 `resolveLinks` 均开启，且消息中含完整 `music.163.com` / `163music.com` 链接。插件指令消息不会被误解析。

**Q：临时文件堆积？**
A：默认 60 秒自动清理。如仍堆积，检查 `keepFileSec` 是否被设为过大值；设为 `0` 即时清理。

---
## 📮 用户群

QQ 群：[点击加入](https://qm.qq.com/q/8sOZdZTnaw)

---
## 🙏 致谢

- [api-enhanced](https://github.com/neteasecloudmusicapienhanced/api-enhanced) — 网易云音乐 API 服务
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) — 多平台聊天机器人框架

---

## 📄 许可

本项目仅供学习交流使用。所有音乐版权归属网易云音乐及相应权利人，使用本插件产生的任何后果由使用者自行承担。

---

<div align="center">

如果觉得这个插件对你有帮助，欢迎 Star 一下哈哈

</div>