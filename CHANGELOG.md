# 更新日志


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
