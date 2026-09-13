"""帮助指令清单（纯数据表）。

卡片（``cards.build_help_card_data``）与纯文本（``cards.format_help_text``）
的唯一数据源：新增/改名指令只改这里，两个呈现面同步生效。各字段的语义
（plain / plain_name / plain_desc / plain_col / plain_skip / plain_title /
plain_no_head）见数据表头部的注释。
"""

# ──────────── 帮助指令清单（卡片与纯文本的唯一数据源） ────────────
#
# 每条 item 是「一条指令」的唯一登记处：新增/改名只改这里，卡片与纯文本同步生效。
#   name / desc / example —— 帮助卡片字段（build_help_card_data 逐条渲染）
#   纯文本帮助（format_help_text）默认渲染 `{name}{对齐空格}{desc}`
#   （对齐列 PLAIN_HELP_COL，CJK 按 2 宽度）；历史文案与卡片措辞不同的用以下键覆盖，
#   保证纯文本输出逐字不变：
#     plain       —— 纯文本侧整行（合并行「歌手榜 / 热门歌手」、超宽行、汇总行）
#     plain_name  —— 纯文本侧命令名（如卡片写 `#ncm歌词 关键词`、文本写 `...|id`）
#     plain_desc  —— 纯文本侧说明（如带「（需登录）」后缀差异）
#     plain_col   —— 纯文本侧该条的对齐列（原文手写缩进的逐字保留）
#     plain_skip  —— 纯文本侧不渲染（该条目已并入前一条的合并行）
# 分组：plain_title 覆盖文本侧分组标题（「管理」→「管理（主人）」）；
# plain_no_head 表示文本侧不另起分组（沿用上一分组继续列举）。
PLAIN_HELP_COL = 22
HELP_PLAIN_FOOTER = [
    "",
    "Tips：发送网易云分享卡片/链接（含 163cn.tv 短链）即可自动解析；VIP 歌曲自动解灰。",
]

HELP_SECTIONS: list = [
    {
        "title": "点歌播放",
        "tag": "全员可用",
        "items": [
            {
                "name": "#ncm点歌 关键词",
                "desc": "搜索并列出歌曲列表",
                "example": "#ncm点歌 晴天",
                "plain_desc": "搜索并列出歌曲",
            },
            {
                "name": "#ncm听N",
                "desc": "播放列表第 N 首",
                "example": "#ncm听1",
                "plain_desc": "播放列表第 N 首（可只发 #听N）",
            },
            {"name": "#ncm听所有", "desc": "依次连播当前列表全部歌曲（上限 30 首）", "example": "#ncm听所有"},
            {"name": "#ncm播放 关键词", "desc": "搜索并直接播放第一首", "example": "#ncm播放 晴天"},
            {
                "name": "#ncm歌词 关键词",
                "desc": "获取歌词",
                "example": "#ncm歌词 晴天",
                "plain_name": "#ncm歌词 关键词|id",
            },
            {"name": "#ncm热搜", "desc": "热搜榜", "example": "#ncm热搜"},
        ],
    },
    {
        "title": "发现音乐",
        "tag": "全员可用",
        "items": [
            {
                "name": "#ncm排行 [榜单名]",
                "desc": "排行榜列表 / 具体榜单",
                "example": "#ncm排行 飙升榜",
                "plain_desc": "排行榜列表 / 查看具体榜单",
            },
            {"name": "#ncm歌手 关键词", "desc": "歌手热门歌曲", "example": "#ncm歌手 周杰伦"},
            {"name": "#ncm专辑 关键词", "desc": "专辑曲目", "example": "#ncm专辑 叶惠美"},
            {"name": "#ncm歌单 关键词", "desc": "歌单曲目", "example": "#ncm歌单 华语"},
            {"name": "#ncm评论 关键词", "desc": "歌曲热评", "example": "#ncm评论 晴天"},
            {"name": "#ncm相似 关键词|id", "desc": "相似歌曲", "example": "#ncm相似 晴天"},
            {"name": "#ncm相关歌单 歌单名|id", "desc": "相关歌单推荐", "example": "#ncm相关歌单 华语"},
            {
                "name": "#ncm新歌 [地区]",
                "desc": "新歌速递（华语/欧美/日本/韩国）",
                "example": "#ncm新歌 华语",
                "plain": "#ncm新歌 [华语/欧美/日本/韩国]  新歌速递",
            },
            {"name": "#ncm精品歌单 [分类]", "desc": "精品歌单", "example": "#ncm精品歌单 华语"},
            {"name": "#ncm搜索建议 关键词", "desc": "关键词补全", "example": "#ncm搜索建议 晴天"},
            {"name": "#ncmbanner", "desc": "首页轮播", "example": "#ncmbanner"},
            {
                "name": "#ncm歌单分类",
                "desc": "歌单分类列表",
                "example": "#ncm歌单分类",
                "plain_desc": "歌单分类",
            },
            {"name": "#ncmMV 关键词", "desc": "MV 详情与播放链接", "example": "#ncmMV 晴天", "plain_col": 23},
            {
                "name": "#ncm相似歌单 关键词|歌曲id",
                "desc": "相似歌单（id 为歌曲 id，非歌单 id）",
                "example": "#ncm相似歌单 晴天",
                "plain_desc": "相似歌单",
            },
            {
                "name": "#ncm歌手榜",
                "desc": "歌手榜",
                "example": "#ncm歌手榜",
                "plain": "#ncm歌手榜 / #ncm热门歌手   歌手榜 / 热门歌手",
            },
            {"name": "#ncm热门歌手", "desc": "热门歌手", "example": "#ncm热门歌手", "plain_skip": True},
            {
                "name": "#ncm新碟",
                "desc": "新碟上架",
                "example": "#ncm新碟",
                "plain": "#ncm新碟 / #ncm新碟榜 [地区] 新碟上架 / 新碟排行",
            },
            {
                "name": "#ncm新碟榜 [地区]",
                "desc": "新碟排行（华语/欧美/韩国/日本）",
                "example": "#ncm新碟榜 华语",
                "plain_skip": True,
            },
            {"name": "#ncmMV榜", "desc": "MV 排行", "example": "#ncmMV榜", "plain_col": 23},
            {"name": "#ncm电台", "desc": "电台推荐", "example": "#ncm电台", "plain_col": 23},
            {"name": "#ncm歌单榜 [分类]", "desc": "分类歌单榜", "example": "#ncm歌单榜 华语", "plain_col": 23},
            {"name": "#ncm热门分类", "desc": "热门歌单分类", "example": "#ncm热门分类", "plain_col": 23},
            {
                "name": "#ncm逐字歌词 关键词|id",
                "desc": "逐字歌词",
                "example": "#ncm逐字歌词 晴天",
                "plain": "#ncm逐字歌词 关键词|id  逐字歌词",
            },
            {
                "name": "#ncm歌单评论 关键词",
                "desc": "歌单热评",
                "example": "#ncm歌单评论 华语",
                "plain": "#ncm歌单评论/专辑评论   歌单/专辑热评",
            },
            {
                "name": "#ncm专辑评论 关键词",
                "desc": "专辑热评",
                "example": "#ncm专辑评论 叶惠美",
                "plain_skip": True,
            },
        ],
    },
    {
        "title": "推荐（需登录）",
        "tag": "全员可用",
        # 纯文本侧不另起分组：这 7 条接在「发现音乐」之后继续列举
        "plain_no_head": True,
        "items": [
            {
                "name": "#ncm推荐",
                "desc": "推荐歌单",
                "example": "#ncm推荐",
                "plain_desc": "推荐歌单（需登录）",
            },
            {"name": "#ncm来首歌", "desc": "随机来一首", "example": "#ncm来首歌"},
            {
                "name": "#ncm日推",
                "desc": "每日推荐",
                "example": "#ncm日推",
                "plain_desc": "每日推荐（需登录）",
            },
            {"name": "#ncm推荐新歌", "desc": "推荐新歌", "example": "#ncm推荐新歌"},
            {
                "name": "#ncm喜欢",
                "desc": "我喜欢的音乐",
                "example": "#ncm喜欢",
                "plain_desc": "我喜欢的音乐（需登录）",
            },
            {
                "name": "#ncm听歌排行",
                "desc": "本周听歌排行",
                "example": "#ncm听歌排行",
                "plain_desc": "本周听歌排行（需登录）",
            },
            {
                "name": "#ncm历史日推",
                "desc": "历史每日推荐",
                "example": "#ncm历史日推",
                "plain_desc": "历史每日推荐（需登录）",
            },
        ],
    },
    {
        "title": "账号扩展",
        "tag": "需登录",
        # 纯文本侧不另起分组：这 6 条在文本里合并为一行（首条携带 plain 整行）
        "plain_no_head": True,
        "items": [
            {
                "name": "#ncm签到",
                "desc": "每日签到领经验",
                "example": "#ncm签到",
                "plain": "#ncm签到 / #ncm云盘 / #ncm最近 / #ncm我的歌单 / #ncm红心 关键词 / #ncm取消红心 关键词  （需登录）",
            },
            {
                "name": "#ncm云盘",
                "desc": "我的云盘歌曲",
                "example": "#ncm云盘",
                "plain_skip": True,
            },
            {
                "name": "#ncm最近",
                "desc": "最近播放歌曲",
                "example": "#ncm最近",
                "plain_skip": True,
            },
            {
                "name": "#ncm我的歌单",
                "desc": "我创建/收藏的歌单",
                "example": "#ncm我的歌单",
                "plain_skip": True,
            },
            {
                "name": "#ncm红心 关键词",
                "desc": "红心/取消红心（自动判断当前状态）",
                "example": "#ncm红心 晴天",
                "plain_skip": True,
            },
            {
                "name": "#ncm取消红心 关键词",
                "desc": "直接取消红心（不查状态）",
                "example": "#ncm取消红心 晴天",
                "plain_skip": True,
            },
        ],
    },
    {
        "title": "账号状态",
        "tag": "全员可用",
        "items": [
            {"name": "#ncm登录", "desc": "扫码登录", "example": "#ncm登录"},
            {
                "name": "#ncm状态 / #ncms",
                "desc": "查看登录状态",
                "example": "#ncms",
                "plain_desc": "登录状态",
            },
            {"name": "#ncm登出", "desc": "登出", "example": "#ncm登出"},
        ],
    },
    {
        "title": "管理",
        "tag": "主人",
        "plain_title": "管理（主人）",
        "items": [
            {"name": "#ncm设置", "desc": "设置面板", "example": "#ncm设置"},
            {"name": "#ncm音质 <档位>", "desc": "修改音质", "example": "#ncm音质 lossless"},
            {"name": "#ncm api <地址>", "desc": "修改 API 地址", "example": "#ncm api http://127.0.0.1:3000"},
            {
                "name": "#ncm开启点歌 / #ncm关闭解析",
                "desc": "功能开关",
                "example": "#ncm关闭解析",
                "plain": "#ncm开启点歌 / #ncm关闭解析  开关功能",
            },
            {"name": "#ncm测试", "desc": "测试 API 连通", "example": "#ncm测试"},
        ],
    },
    {
        "title": "自动解析",
        "tag": "自动",
        "items": [
            {
                "name": "网易云链接",
                "desc": "music.163.com / 163music.com / 163cn.tv 短链自动解析",
                "example": "music.163.com/#/song?id=186016",
                "plain": "发送 music.163.com / 163music.com / 163cn.tv 链接自动解析播放",
            },
        ],
    },
]
