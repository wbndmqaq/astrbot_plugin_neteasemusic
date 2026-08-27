# 更新日志

## [v1.1.8] - 2026-08-27

### ⚡ 异步与性能加固

- **全面非阻塞文件 IO**：音频大文件下载（`download_audio`）、卡片渲染 PNG 缓存（`_render_card`）、登录二维码（`_save_qr_image`）全面接入 `asyncio.to_thread(_write_bytes, ...)` 异步写入，消除主事件循环因磁盘 IO 导致的卡顿。
- **并发与作用域安全**：统一修正临时卡片延迟清理机制（采用全局 `asyncio.get_running_loop()`），杜绝作用域泄漏风险。

### 🐛 异常与防御完善

- **全量 AST 静态审查**：排查并修复所有潜在的命名作用域、字典嵌套索引及 `NameError` / `TypeError` 风险，全库代码语法与协程状态 0 缺陷。

---
## [v1.1.7] - 2026-08-23

### 🔒 安全合规

- **移除渲染环境的全部自动安装行为**（插件市场安全审查整改）：删除 render.py 中的 apt 源改写、`apt-get update`、`pip install playwright`、Chromium 二进制自动下载与 `install-deps` 自动执行——插件不再执行任何未经用户确认的系统级安装/网络下载。
- **渲染环境缺失优雅降级**：playwright 包 / Chromium 内核 / 系统库缺失时，所有指令自动回退纯文本，点歌播放不受影响。
- **日志内置完整教程**：渲染环境缺失时日志一次性输出手动安装教程——① `pip install playwright`（含清华镜像写法）；② `python -m playwright install chromium`（含 npmmirror 加速与 Windows PowerShell 写法）；③ 仅 Linux 容器缺系统库时 `playwright install-deps chromium` 或手动 apt-get 安装库列表，附可选的阿里 apt 镜像源换源命令；之后仅简短提示不刷屏。
- **README 教程化**：「卡片渲染环境安装教程」章节扩写为四步完整教程（装包 → 下载内核 → 系统运行库 / 可选换阿里源 → 重载插件），并声明插件绝不自动执行任何系统级安装

### ✨ 新功能

- **▶ 新增 `#ncm听所有`**：对当前会话歌曲列表（点歌/歌单展开/专辑展开/日推/喜欢/听歌排行等）依次发送全部歌曲的语音+音频文件（上限 30 首，防误触发刷屏）；单曲失败不中断，结束汇报成功/失败数

### 🐛 修复与优化

- **选择链路对齐反馈**：`#ncm相似歌单 关键词` → 带序号候选歌单 → `#ncm听N` 展开曲目 → `#ncm听N` 播放 / `#ncm听所有` 连播；`#ncm歌单榜 关键词` → 20 条带序号歌单 → 同上（免手动输入歌单名）；专辑/歌单唯一命中保持直出，多结果出候选列表。帮助卡片同步新增连播说明。
- **语音修复（1秒时长/手机无法播放）**：aiocqhttp 语音不再插件侧预编码 silk。此前自编的裸 `#!SILK_V3`（无 `\x02` 前缀、且 PyPI `pysilk` 为空壳包导致该路径长期失效）会被协议端按魔数透传——非标码流导致手机无法播放、时长探测失败钳到 1 秒。改为把紧凑 mp3 以 OneBot v11 标准 `record` 段 base64 直发，由协议端（NapCat / SnowLuma 等）自带 ffmpeg addon 统一转成标准 Tencent silk（`\x02#!SILK_V3`、24kHz 单声道），时长按原始音频精确探测。
- **歌词完整显示**：移除 36 行截断，超长歌词自动按每页 36 行分页成多张卡片发送；兼容新版 JSON 行式 lrc（`{"t":..,"c":[{"tx":..}]}`）。
- **逐字歌词修复**：yrc 实际为 JSON 行格式，旧解析只认 `[t,d,0]字` 括号时间戳导致恒为空/乱码；现两种格式均支持，无逐字歌词时自动回退普通歌词而非死提示。
- **分享卡片解析修复**：解析入口从 `@filter.regex` 改为全量事件监听——OneBot `json` 段（音乐分享卡片）不会写入 `message_str`，regex 过滤器永远匹配不到；同时 `_collect_message_text` 显式读取 Json 组件数据。
- **云盘异常修复**：`#ncm云盘` 在接口返回 `data` 为 list 时抛 `'list' object has no attribute 'get'`（指令显示 ":("），已做类型守卫并兜底捕获。
- **专辑/歌单列表可选**：`#ncm专辑` / `#ncm歌单` 不再直接取第一条搜索结果（同名专辑/歌单选错），先出候选列表，回复 `#ncm听N` 展开曲目再播放；唯一命中时保持直出。
- **相似歌单重做**：原实现「关键词→歌曲→simi 接口」返回内容与关键词无关；改为按关键词检索候选歌单列表，选择后展开曲目并可播放。
- **歌单类会话打通**：`#ncm歌单榜` / `#ncm精品歌单` / `#ncm推荐` / `#ncm相关歌单` / `#ncm我的歌单` 列表现在均可回复 `#ncm听N` 展开曲目。
- **banner 说明**：`#ncmbanner` 卡片补充说明其为 App 首页轮播推广位及查看方式。

## [v1.1.6] - 2026-08-20

### ✨ 新功能

- **渲染模块独立 `render.py`**：渲染逻辑收敛进独立 `render.py`，`main.py` 仅负责调用与落盘。
- **跨平台 Playwright 三步运行时就绪**：渲染前主动按序完成——① 确保 playwright Python 包（缺失自动 pip 装清华镜像，装不到位终止）→ ② 仅 Linux：切阿里 apt 源并 `playwright install-deps` 装系统运行库 → ③ 下载 Chromium 二进制（npmmirror 加速）。幂等，首次执行一次后跳过。

### 🐛 修复与优化

- **Linux 识别**：apt 换源 / install-deps 仅在 `platform.system()=="Linux"` 时执行，Windows/macOS 一律跳过，不触碰系统配置。

## [v1.1.5] - 2026-08-20

### ✨ 新功能

- **先选歌再操作**：`#ncm歌词` / `#ncm逐字歌词` / `#ncm评论` / `#ncm相似` / `#ncmMV` / `#ncm红心` / `#ncm取消红心` 带关键词先出候选列表，`#ncm听N` 再执行对应动作（一次性，用完恢复播放）；不带关键词复用当前会话候选列表。`#ncm播放` / `#ncm相似歌单` 保持原有行为。
- **aiocqhttp（OneBot/napcat）语音与文件直发适配**：napcat 与 AstrBot 跨容器不共享文件系统时，语音自动经 ffmpeg→24kHz wav→pysilk 编成标准 silk（几 MB）直发、文件以 base64 内联直发，无需共享挂载；无损/大文件先压成紧凑 mp3 控制载荷。

### 🐛 修复与优化

- **Playwright 缺系统库自愈 + 阿里源**：容器缺 Chromium 系统库（`libnspr4.so` 等）时自动把官方 apt 源切换为阿里镜像并执行 `playwright install-deps chromium`（幂等、备份 .bak、仅动官方域名）；失败时日志给出可直接执行的安装命令。
- `metadata.yaml` 补充 `category` 分类字段。

## [v1.1.4] - 2026-08-18

### 🐛 修复与优化

- **Playwright Chromium 自动安装与镜像加速**：
  - 本地卡片渲染若检测到系统未安装 Playwright Chromium，自动使用 `sys.executable -m playwright install chromium` 进行静默安装并恢复渲染。
  - 自动注入国内镜像加速源（`PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/`），大幅提升下载速度，避免 Docker 容器或无梯子环境下卡片渲染失败。
  - 补充 `--no-sandbox`、`--disable-setuid-sandbox`、`--disable-dev-shm-usage` 等 Docker / Linux 环境防崩溃启动参数。
- **市场规范元数据补全**：`metadata.yaml` 补充 `social_link` 与 `tags` 分类标签。


## [v1.1.3] - 2026-08-15

### ✨ 新功能

- **ffmpeg 压缩兜底**：大文件/FLAC 无法作为文件发送时（QQ 官方无分片上传、或文件发送失败），用 ffmpeg 压成紧凑 mp3（`compressBitrate`，默认 128k）再发送，不再丢失文件通道。新增 `ffmpegCompress` 配置（默认开）；ffmpeg 缺失或压缩失败时退回旧行为（跳过文件仅发语音）
- 守卫拦截时压缩成功 → 文件名带 `.mp3`；原文件与压缩文件都按 `keepFileSec` 调度清理

### 🧹 质量

- `_deliver_local_audio` 新增 `_ffmpeg_path` / `_compress_to_mp3` 助手；测试扩至 36 用例（压缩成功/失败/无 ffmpeg、守卫拦截压缩兜底、发送失败压缩重试）
- **修复 53 个 handler 缺失 docstring**（外部修改再次删光）：WebUI 指令列表全部显示「无描述」。按 README 指令一览补齐全部描述（`#ncm命令：说明` 风格，与 kugou 插件一致），ruff/py_compile 全绿
- 第二轮重构：`_send_file_payload` 扁平化（提前 return 消除深嵌套）；`download_audio` 改流式写入磁盘（大 FLAC 不再整块读入内存），失败时清理残留文件；测试扩至 **42 用例**（新增 download_audio 流式/过小/HTML/HTTP 错误清理 + 压缩重试失败文案兜底）


## [v1.1.2] - 2026-08-15

### ✨ 新功能

- **QQ 官方大文件分片上传**：AstrBot ≥ 4.27.3 的 QQ 官方适配器对本地 >10MB 文件自动走分片上传（修复大文件无法发送的问题）。插件新增 `qqofficialChunkedUpload` 配置（默认开）：开启时放行 FLAC/>10MB 文件发送，不再降级为仅语音；关闭或旧版 AstrBot（< 4.27.3）保留原守卫（大文件仅发语音 silk）。语音/文件双通道互不阻塞逻辑不变。

### 🧹 质量

- 守卫逻辑抽成可单测的 `_should_block_qqofficial_file` / `_qq_official_chunked_upload_supported(version)`；修复拦截提示在语音未开启时误导（"改发语音"不成立）→ 改为如实提示"音频文件未发送"；清理 `_is_weixin_oc`/`send_native_music_card` 函数体多余空行
- `deliver_song` 拆分出可单测的 `_deliver_local_audio`（语音/文件双通道投递 + wxoc 降级 + 文件守卫 + 文案兜底 + 清理调度），`deliver_song` 只负责文案/卡片/下载后委托
- **修复文件名音质死参数**：`build_music_filename` 传了 `quality` 却漏 `include_quality=True`（代码注释声称"文件名已含歌手-歌名-音质"实际并不含）→ 补上，文件名现含音质档位（如 `周杰伦-晴天_exhigh.mp3`）
- 新增 `tests/test_delivery_chunked.py`（31 用例，mock 无网络）：版本检测 + 守卫决策矩阵 + 本地音频投递端到端（双发/单发/文件拦截回退语音/全部失败文案兜底/微信降级/清理调度）


## [v1.1.1] - 2026-08-10

v1.1.0 发布后的增量功能

### ✨ 新功能

- **163cn.tv 短链适配**：`_is_ncm_message` / 链接解析正则识别 `163cn.tv`；`expand_short_links` 跟随 302 展开为最终 `music.163.com` 链接（不下载响应体，失败原样保留），随后走既有歌单/专辑/歌曲解析
- **个人微信（weixin_oc）适配**：`support_platforms` 增加 `weixin_oc`；该平台适配器出站不支持语音（Record 被静默跳过）→ 语音自动降级为文件发送，原生音乐卡片自动跳过
- **Telegram / 钉钉 / 飞书 / KOOK / Discord 适配**：`support_platforms` 增加 `telegram` / `dingtalk` / `lark` / `kook` / `discord`；五平台适配器出站均原生支持语音与文件（Telegram 隐私拒绝自动回退文档、钉钉/飞书/ KOOK / Discord 自动转码），插件双通道无需降级；新增参数化测试验证不降级行为
- **设置面板卡片**：`#ncm设置` 渲染为 HTML 卡片（第 8 套模板 `ncm-settings`：hero 三统计 + 基础设置 + 7 个功能开关 + 音质条 + 可修改命令），数据构建 `build_settings_card_data` / 文本兜底 `format_settings_text`；API 地址统一脱敏、Cookie 只显示打码尾号
- **`#ncm我的歌单` 卡片**：新增第 9 套模板 `ncm-playlist`（歌单数/累计播放统计 + 歌单列表：序号/封面/名称/创建者/播放量 pill），数据构建 `build_playlist_card_data` / 文本兜底 `format_playlist_text`（通用，可复用于歌单榜/推荐/精品歌单等歌单列表命令）
- **全部列表命令卡片化**：新增通用模板 `ncm-generic`（条目列表：序号/封面/名称/副信息/标签），15 个长文本命令接入卡片渲染（失败自动回退纯文本）：相关歌单/精品歌单/相似歌单/歌单榜（复用 ncm-playlist）+ 排行榜/搜索建议/首页轮播/歌单分类/歌手榜/热门歌手/新碟/新碟榜/MV榜/热门分类/电台推荐（ncm-generic）；`#ncmMV` 为单条详情保留文本
- **`#ncm最近` 随机抽 30 首**：实测 `/record/recent/song` 的 limit 不生效（返回全部 300 首）→ 改为拉全量后随机抽 30 首，按最近播放时间排序，时长处展示「X 天前播放」
- **`#ncm推荐` 渲染卡片**：复用 `ncm-playlist` 模板（推荐歌单统计 + 列表），失败回退纯文本
- **`#ncm状态` 会员标识**：实测网易云 profile.vipType（`110` / 老体系 `11`）与 `/vip/info` 的 `redplus` 有效期区分黑胶SVIP / 黑胶VIP（SVIP 金色、VIP 红色徽章）+ 黑胶等级（redVipLevel）
- **`#ncm取消红心` 子命令**：`#ncm红心` 保持自动切换；新增 `#ncm取消红心 关键词` 不查状态直接取消

### 🐛 修复

- **模板转换器 bug（tpl_adapter）**：`'a' + x + 'b'` 这类首尾都是引号的拼接表达式被当作字符串字面量 → 改为整体字面量判定，新增回归测试
- **`#ncm最近` 空结果**：`/record/recent/song` 的歌曲信息在 `item.data`（实测无 `resource` 字段）→ 解析改用 data 字段，并保留 playTime
- **`#ncm历史日推` 空结果**：`/history/recommend/songs` 的 data 变为 dict（`dates` 日期列表 + `songs` 为 None，黑胶VIP 近 5 次特权）→ 自动取最近日期再调 `/history/recommend/songs/detail?date=` 拿歌曲
- **`#ncmMV榜` 被 `#ncmMV` 抢正则**：`MV\s*(.+)$` 允许 0 空格 → `#ncmMV` 改为 `MV\s+(.+)`（关键词前至少一个空格），与 `#ncmMV榜` 精确区分
- **列表卡片统计区显示「-」**：ncm-generic 模板 hero 中间统计格 `statMid` 未传时显示「-」→ `build_generic_card_data` 未传时默认显示条目数（「N 条」）
- **`#ncm相关歌单` 无数据**：实测 `/playlist/detail/rcmd/get` 参数必须是**歌单 id**（旧插件传歌曲 id 返回 502/空，且仅部分歌单有推荐）→ 命令改为「输入歌单关键词/ID → 相关歌单推荐」，帮助/README 同步更新
- **微信个人平台（weixin_oc）独立文案**：适配器不支持 Plain+媒体合并 → 不再发送「识别：网易云音乐」独立文案（文件名/详情卡片已含信息）
- **黑胶等级不显示 / `Lv.0`**：`redVipLevel` 只在 `/vip/info` 返回 → `_build_status` 补调 `/vip/info`；level/format 两层数值判定，0 时不显示


## [v1.1.0] - 2026-08-09

对照 api-enhanced v4.40 版本更新。

### 🐛 修复

- **二维码登录被缓存卡死**：api-enhanced 对相同 URL 缓存 2 分钟且无 login 豁免，`qr_key` 传固定 `timestamp=0`、`qr_check` 不带时间戳导致扫码状态轮询永远拿到第一次的结果 → 两接口均改传实时时间戳绕过缓存
- **相关歌单功能失效**：`/related/playlist` 已被官方废弃（html 抓取，实测返回空）→ 改用 `/playlist/detail/rcmd/get`，解析嵌套 `recPlaylist[].playlist`
- **排行榜用废弃接口**：`top_detail` 走 `/top/list`（v3.34.0 后不再支持 idx）→ 改用 `/playlist/track/all`（榜单本质是歌单，实测有效）
- **`#ncm精准` 死功能**：`/search/match` 新语义为本地文件匹配（必填 duration/md5），缺参实测必返空 → 删除该命令及 api 层 `search_match`
- **错误码缺失**：`ERR_MESSAGES` 补 460（cheating 风控）、503（请求频繁）
- **API 地址打码误伤 IP**：`mask_api_base` 对 `127.0.0.1` 只打码末段 → IPv4/IPv6 整体打码为 `***`

### 🔧 重构

- `main.py` 新增 `_cmd()` 统一命令样板（总开关检查 + 正则提取），收敛 20 个 handler 的重复代码
- `api.py` 三个评论接口（`comment` / `comment_playlist` / `comment_album`）合并到 `_comments_for` 公共实现

### 📌 备注

- 依赖 API 服务升级到 api-enhanced v4.40 

## [v1.0.2] - 2026-08-08

Bug 修复与代码整顿：渲染改为纯本地 Playwright，修复指令识别、数字 ID 解析、Cookie 展示等若干问题。

### 🐛 修复

- **指令识别失效**：`_is_plugin_command_msg` 的 `\b` 在中文（CJK）字符前不构成词边界，导致 `#ncm点歌` 等中文指令无法识别、被链接解析误拦截 → 改为「ASCII 字符或词边界」判定
- **数字 ID 命令名称为空**：`#ncm歌词/评论/相似/红心 12345` 等数字输入此前构造无名称的裸 dict，卡片标题与红心回复显示空歌名 → 数字 ID 统一走 `song_detail` / `playlist_detail`（新增端点）/ `album_detail` 补全信息
- **歌手列表 tip 文案错误**：「热门 50 首中的前 N 首」与实际返回数量不符 → 改为「热门歌曲（共 N 首）」
- **设置面板 Cookie 泄露**：过短 Cookie 会整段显示 → 不足 4 位一律打码
- **`asyncio.get_event_loop()` 弃用**：3 处（扫码登录、轮询、临时文件清理）改用 `get_running_loop()`，规避 Python 3.12+ 告警 / 3.14+ 报错
- **API 未配置报错不明确**：`apiBase` 为空时请求 URL 拼出 `/cloudsearch` 这类无协议地址，aiohttp 抛 `InvalidURL`，被显示成令人费解的「网络错误：/cloudsearch」→ 请求前显式校验空地址与缺失 `http://` 协议头，直接提示「#ncm api <地址>」配置方式

### 🔧 重构

- 新增 `_resolve_song` / `_resolve_playlist` / `_resolve_album` 辅助方法，收敛 9 个命令的重复「数字 ID / 关键词搜索」分支
- `_list_to_session` 文本兜底补齐 tip 传递，删除与 `_reply_card_or_text` 内部兜底重复的冗余发送
- 删除死代码：`api.py` 未使用的 `UA` 常量、`format_song_list` 的 `start_idx` 死参数、函数内 `import random`（上移模块顶部）

### 🎨 渲染

- **纯本地 Playwright 渲染**：由本地 chromium 直接截图
- 渲染失败依旧自动回退纯文本

### 📌 备注

- 首次使用请执行 `playwright install chromium`（Windows 下 `python -m playwright install chromium`）

## [v1.0.1] - 2026-08-08

社区入口更新。

### 🆕 新增

- README 新增 📮 用户群板块，提供 QQ 群一键加入入口

## [v1.0.0] - 2026-08-08

首个正式版本。基于 [api-enhanced](https://github.com/neteasecloudmusicapienhanced/api-enhanced)（NeteaseCloudMusicApiEnhanced）HTTP API 重构，镜像 qqmusic 插件架构，提供完整的网易云点歌 / 解析 / 账号能力。

### 🎉 首发功能

#### 点歌与播放
- 关键词点歌：`#ncm点歌` 搜索 → 列表卡片 → `#ncm听N` 选歌（会话内可用简写 `#听N`）
- 直接播放：`#ncm播放` 取搜索首条立即播放；`#ncm精准` 单曲精准匹配
- 随机点歌：`#ncm来首歌` 走个人 FM，未登录自动退化为推荐新歌随机
- 歌词：`#ncm歌词` 全文歌词、`#ncm逐字歌词` 逐字歌词
- 热搜：`#ncm热搜` 热搜榜

#### 发现音乐
- `#ncm排行` 排行榜列表 / 具体榜单
- `#ncm歌手` / `#ncm专辑` / `#ncm歌单` 歌手热门 / 专辑曲目 / 歌单曲目
- `#ncm评论` / `#ncm歌单评论` / `#ncm专辑评论` 热评
- `#ncm相似` / `#ncm相似歌单` / `#ncm相关歌单` 相似与关联推荐
- `#ncm新歌` / `#ncm新碟` / `#ncm新碟榜` 新歌速递 / 新碟上架与排行（支持华语/欧美/日本/韩国地区）
- `#ncm精品歌单` / `#ncm歌单分类` / `#ncm热门分类` / `#ncm歌单榜` 歌单发现
- `#ncm搜索建议` 关键词补全、`#ncmbanner` 首页轮播
- `#ncmMV` / `#ncmMV榜` MV 详情与排行
- `#ncm歌手榜` / `#ncm热门歌手` 歌手榜 / 热门歌手
- `#ncm电台` 电台推荐

#### 推荐（部分需登录）
- `#ncm推荐` 推荐歌单、`#ncm日推` 每日推荐、`#ncm推荐新歌` 推荐新歌
- `#ncm喜欢` 我喜欢的音乐、`#ncm听歌排行` 本周听歌排行、`#ncm历史日推` 历史每日推荐
- `#ncm签到` 每日签到、`#ncm云盘` 我的云盘、`#ncm最近` 最近播放、`#ncm我的歌单` 我的歌单
- `#ncm红心` 红心 / 取消红心（自动判断当前状态）

#### 账号与管理
- `#ncm登录` 扫码登录（生成二维码、轮询自动写入 Cookie、按会话隔离多账号）
- `#ncm状态` / `#ncms` 登录状态、`#ncm登出` 登出
- `#ncm设置` 设置面板、`#ncm音质` 改音质、`#ncm api` 改 API 地址
- `#ncm 开启/关闭 点歌|解析` 功能开关、`#ncm测试` API 连通测试、`#ncm帮助` 帮助卡片

#### 链接自动解析
- 自动识别 `music.163.com` / `163music.com` / `y.music.163.com` 分享卡片与链接
- 支持 单曲 `song?id=` → 详情 + 播放、歌单 `playlist?id=` → 列表、专辑 `album?id=` → 列表

### 🔧 核心特性

#### 音质与解灰
- `auto` 自动匹配歌曲最高可用音质并逐级降级（jymaster → sky → jyeffect → hires → lossless → exhigh → higher → standard）
- VIP / 灰歌自动解灰兜底（`qualityUnblock`，`unblock=true`）
- 音质档位支持配置下拉与 `#ncm音质` 指令热改

#### 音频投递
- 语音（silk 转码）+ 群 / 好友文件双通道投递，互不阻塞，失败自动回退
- 文案与首个媒体合并发送（QQ 官方），节省被动回复额度
- 文案、原生音乐卡片、语音 / 文件投递均可独立开关

#### QQ 官方机器人适配（`qqofficialAdapt`）
- 合并消息规避被动回复额度限制
- 大文件守卫：FLAC 或 >10MB 文件自动跳过文件上传，仅发语音（silk），规避 `413 Request Entity Too Large`
- 纯文本兜底：媒体失败时文本走 `msg_type=0`，规避 `40034011 无效 markdown content`
- 原生音乐卡片自动跳过（QQ 官方无 OneBot `send_api`）

#### 卡片渲染
- 7 套网易云红主题 HTML 卡片：列表 / 详情 / 歌词 / 热搜 / 评论 / 帮助 / 状态
- 远程渲染 PNG 下载后自动裁剪白边（numpy 四角色检测）
- 卡片渲染失败自动回退纯文本

#### 临时文件自清理
- 卡片图 `card_*.png`：发出后 `keepFileSec` 秒清除（`finally` 保证孤儿文件亦清理）
- 二维码 `qr_*.png`：发出后 120 秒清除
- 音频文件：发出后 `keepFileSec` 秒清除
- `keepFileSec=0` 即「发出后立即删除」

#### 会话与状态
- 列表 → `#ncm听N` 会话存储（内存 + KV 双写，TTL 600s，按群 / 私聊 scope 隔离）
- Cookie 按 UID 存储与透传，多账号互不干扰
- API 地址脱敏展示

### 📦 架构

```
main.py        # Star 主类：#ncm 指令 handler + 辅助方法（渲染/投递/会话/清理）
api.py         # NcmApiClient：aiohttp HTTP 客户端 + Cookie 透传 + 歌曲归一化 + 错误码映射
quality.py     # 网易云音质阶梯与标签
delivery.py     # 音频下载 → Record/File 投递（含 QQ 官方双发与守卫）
cards.py       # SessionStore + 卡片数据构建 + 文本兜底格式化 + 隐私脱敏
tpl_adapter.py # art-template → Jinja2 模板适配
resources/html # 7 套 HTML 卡片模板
```

### 📌 备注

- 需自行部署 api-enhanced 服务端（默认 `http://127.0.0.1:3000`）
- 支持 `aiocqhttp`（OneBot）与 `qq_official`（QQ 官方机器人）双平台
- AstrBot 版本要求 `>=4.16, <5`（推荐 4.26+）
