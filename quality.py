from __future__ import annotations

# 网易云音质档位（song/url/v1 的 level 参数）
NCM_QUALITY_LIST = [
    {"label": "自动（自适配最高可用）", "value": "auto"},
    {"label": "超清母带 jymaster", "value": "jymaster"},
    {"label": "沉浸环绕声 sky", "value": "sky"},
    {"label": "高清环绕声 jyeffect", "value": "jyeffect"},
    {"label": "Hi-Res", "value": "hires"},
    {"label": "无损 FLAC", "value": "lossless"},
    {"label": "极高", "value": "exhigh"},
    {"label": "较高", "value": "higher"},
    {"label": "标准", "value": "standard"},
]

# 从高到低完整阶梯（auto 时从 lossless 起试）
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


def quality_candidates(preferred: str = "auto", fallback: bool = True) -> list[str]:

    q = (preferred or "auto").lower()
    if q in ("auto", "adaptive", "best"):
        return list(QUALITY_LADDER[QUALITY_LADDER.index("lossless"):]) if fallback else ["lossless"]
    idx = QUALITY_LADDER.index(q) if q in QUALITY_LADDER else QUALITY_LADDER.index("lossless")
    if not fallback:
        return [QUALITY_LADDER[idx]]
    return QUALITY_LADDER[idx:]
