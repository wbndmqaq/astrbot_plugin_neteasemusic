from __future__ import annotations

# 网易云音质档位（song/url/v1 的 level 参数）
NCM_QUALITY_LIST = [
    {"label": "自动（按账号权限自适配）", "value": "auto"},
    {"label": "超清母带 jymaster", "value": "jymaster"},
    {"label": "沉浸环绕声 sky", "value": "sky"},
    {"label": "高清环绕声 jyeffect", "value": "jyeffect"},
    {"label": "Hi-Res", "value": "hires"},
    {"label": "无损 FLAC", "value": "lossless"},
    {"label": "极高", "value": "exhigh"},
    {"label": "较高", "value": "higher"},
    {"label": "标准", "value": "standard"},
]

# 从高到低完整阶梯（auto 的起试档位由账号特权决定，见 quality_candidates）
QUALITY_LADDER = [
    "jymaster",
    "sky",
    "jyeffect",
    "hires",
    "lossless",
    "exhigh",
    "higher",
    "standard",
]

QUALITY_LABEL = {"auto": "自动适配"}
QUALITY_LABEL.update({item["value"]: item["label"] for item in NCM_QUALITY_LIST if item["value"] != "auto"})


# auto 在「无高档位特权」时的起试档位：超清母带/沉浸环绕声/高清环绕声/Hi-Res 都需会员，
# 匿名或非会员请求它们必然失败，只会白打 4 次请求（每首歌多 0.5~1 秒）。
AUTO_START_NO_PRIVILEGE = "lossless"


def quality_candidates(preferred: str = "auto", *, full_ladder: bool = False) -> list[str]:
    """从偏好音质起向下返回候选阶梯。

    ``auto`` 默认从 ``lossless`` 起试；``full_ladder=True`` 时从最高档 ``jymaster``
    起试——调用方应先确认账号确实有高档位特权（见 ``api.has_high_quality_privilege``），
    否则等于每首歌多打 4 次必然失败的请求。
    显式指定档位时 ``full_ladder`` 无意义：用户要哪一档就从哪一档开始降级。
    """
    q = (preferred or "auto").lower()
    if q in ("auto", "adaptive", "best"):
        start = 0 if full_ladder else QUALITY_LADDER.index(AUTO_START_NO_PRIVILEGE)
        return list(QUALITY_LADDER[start:])
    idx = QUALITY_LADDER.index(q) if q in QUALITY_LADDER else QUALITY_LADDER.index(AUTO_START_NO_PRIVILEGE)
    return QUALITY_LADDER[idx:]
