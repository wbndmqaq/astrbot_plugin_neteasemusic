# 更新日志


## [v1.1.0] - 2026-08-09

对照 api-enhanced v4.40 版本更新

### 🐛 修复

- **二维码登录被缓存卡死**：api-enhanced 对相同 URL 缓存 2 分钟且无 login 豁免，`qr_key` 传固定 `timestamp=0`、`qr_check` 不带时间戳导致扫码状态轮询永远拿到第一次的结果 → 两接口均改传实时时间戳绕过缓存
- **相关歌单功能失效**：`/related/playlist` 已被官方废弃（html 抓取，实测返回空）→ 改用 `/playlist/detail/rcmd/get`，解析嵌套 `recPlaylist[].playlist`
- **排行榜用废弃接口**：`top_detail` 走 `/top/list`（v3.34.0 后不再支持 idx）→ 改用 `/playlist/track/all`（榜单本质是歌单，实测有效）
- **`#ncm精准` 死功能**：`/search/match` 新语义为本地文件匹配（必填 duration/md5），缺参实测必返空 → 删除该命令及 api 层 `search_match`
- **错误码缺失**：`ERR_MESSAGES` 补 460（cheating 风控）、503（请求频繁）
- **API 地址打码误伤 IP**：`mask_api_base` 对 `127.0.0.1` 只打码末段 → IPv4/IPv6 整体打码为 `***`

### ✨ 新功能

- **163cn.tv 短链适配**：`_is_ncm_message` / 链接解析正则识别 `163cn.tv`；`expand_short_links` 跟随 302 展开为最终 `music.163.com` 链接（不下载响应体，失败原样保留），随后走既有歌单/专辑/歌曲解析
- **个人微信（weixin_oc）适配**：`support_platforms` 增加 `weixin_oc`；该平台适配器出站不支持语音（Record 被静默跳过）→ 语音自动降级为文件发送，原生音乐卡片自动跳过
- **Telegram / 钉钉 / 飞书适配**：`support_platforms` 增加 `telegram` / `dingtalk` / `lark`；三平台适配器出站均原生支持语音与文件（Telegram 隐私拒绝自动回退文档、钉钉/飞书自动转码），插件双通道无需降级，原生音乐卡片自动跳过；新增参数化测试验证不降级行为
- **KOOK / Discord 适配**：`support_platforms` 增加 `kook` / `discord`（KOOK 以 AUDIO 卡片发语音、Discord 转 wav 附件），同样无需降级；参数化测试扩展覆盖
- **设置面板卡片**：`#ncm设置` 渲染为 HTML 卡片（第 8 套模板 `ncm-settings`：hero 三统计 + 基础设置 + 7 个功能开关 + 音质条 + 可修改命令），数据构建 `build_settings_card_data` / 文本兜底 `format_settings_text`；API 地址统一脱敏、Cookie 只显示打码尾号
- **`#ncm测试` 回复脱敏**：API 地址不再明文回显，改用 `mask_api_base` 脱敏形式
- **`#ncm我的歌单` 卡片**：新增第 9 套模板 `ncm-playlist`（歌单数/累计播放统计 + 歌单列表：序号/封面/名称/创建者/播放量 pill），数据构建 `build_playlist_card_data` / 文本兜底 `format_playlist_text`（通用，可复用于歌单榜/推荐/精品歌单等歌单列表命令）
- **修复模板转换器 bug（tpl_adapter）**：字符串字面量判定误伤——`'a' + x + 'b'` 这类首尾都是引号的拼接表达式被当作字符串字面量，内部引号被转义、变量变成字符串内容（`#ncm我的歌单` 等卡片副标题显示异常）→ 改为整体字面量判定（首尾引号 + 中间无裸引号），新增回归测试
- **修复 `#ncm最近` 空结果**：`/record/recent/song` 的歌曲信息在 `item.data`（实测无 `resource` 字段）→ 解析改用 data 字段，并保留 playTime（最近播放时间戳）
- **修复 `#ncm历史日推` 空结果**：`/history/recommend/songs` 的 data 变为 dict（`dates` 日期列表 + `songs` 为 None，黑胶VIP 近 5 次特权）→ 自动取最近日期再调 `/history/recommend/songs/detail?date=` 拿歌曲
- **修复 `#ncmMV榜` 被 `#ncmMV` 抢正则**：`MV\s*(.+)$` 的 `\s*` 允许 0 空格，`#ncmMV榜` 被当作 `MV` + 关键词「榜」去搜 MV 单条详情 → `#ncmMV` 改为 `MV\s+(.+)`（关键词前至少一个空格），与 `#ncmMV榜` 精确区分
- **修复列表卡片统计区显示「-」**：ncm-generic 模板 hero 中间统计格 `statMid` 未传时显示「-」→ `build_generic_card_data` 未传时默认显示条目数（「N 条」），热门分类/电台/歌手榜等全部生效
- **修复 `#ncm相关歌单` 无数据**：实测 `/playlist/detail/rcmd/get` 参数必须是**歌单 id**（旧插件传歌曲 id 返回 502/空，且仅部分歌单有推荐）→ 命令改为「输入歌单关键词/ID → 相关歌单推荐」，帮助/README 同步更新
- **全部列表命令卡片化**：新增通用模板 `ncm-generic`（条目列表：序号/封面/名称/副信息/标签），15 个长文本命令接入卡片渲染（失败自动回退纯文本）：相关歌单/精品歌单/相似歌单/歌单榜（复用 ncm-playlist）+ 排行榜/搜索建议/首页轮播/歌单分类/歌手榜/热门歌手/新碟/新碟榜/MV榜/热门分类/电台推荐（ncm-generic）；`#ncmMV` 为单条详情保留文本
- **`#ncm最近` 随机抽 30 首**：实测 `/record/recent/song` 的 limit 不生效（返回全部 300 首）→ 改为拉全量后随机抽 30 首，按最近播放时间排序，时长处展示「X 天前播放」；沿用 ncm-list 卡片渲染
- **`#ncm推荐` 渲染卡片**：复用 `ncm-playlist` 模板（推荐歌单统计 + 列表），文本过长问题解决，失败回退纯文本
- **`#ncm状态` 卡片会员标**：登录状态卡片显示会员标识——实测网易云 profile.vipType：`110`（用户账号 App 确认「黑胶VIP」）与老体系 `11` 均显示「黑胶VIP Lv.N」、其他 >0「会员」、0 不显示；数据来自 login/status 的 profile.vipType/redVipLevel；黑胶SVIP 的 vipType 值待真实 SVIP 账号确认后补充
- **黑胶SVIP / 黑胶VIP 区分**：两真实账号实测——黑胶VIP 与黑胶SVIP 的 vipType 均为 110/11（接口无独立 SVIP 字段），区分点在 `/vip/info` 的 `redplus`（黑胶PLUS）有效期：SVIP 账号 redplus 有效期在未来、黑胶VIP 账号已过期 → `_vip_label` 增加有效期判断，SVIP 金色「黑胶SVIP Lv.N」、VIP 红色「黑胶VIP Lv.N」
- **修复黑胶等级不显示**：`redVipLevel` 只在 `/vip/info` 返回（实测 `login/status` 的 profile 只有 vipType，无 redVipLevel），此前徽章永远无等级 → `_build_status` 补调 `/vip/info` 取黑胶等级；vipType 双字段兜底（profile=110 新体系 / account=11 老体系，实测并存）
- **修复 `Lv.0` 显示**：`_build_status` 把 level=0 转成字符串 "0"（truthy）导致卡片显示「Lv.0 / 等级：0」→ level/format 两层均改为数值判定，0 时不显示
- **`#ncm取消红心` 子命令**：`#ncm红心` 保持自动切换（已红心则取消、未红心则红心）；新增 `#ncm取消红心 关键词` 不查状态直接取消，帮助/README 同步更新

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
