"""打分系统配置：权重、基线、打假人名单、类别、窗口、数据文件路径。

所有可调参数集中在此，调权重视图不改打分逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 报告窗口（周报取 [now-10d, now-3d]，与 README 一致）
WINDOW_DAYS: tuple[int, int] = (10, 3)

# 数据文件（data/ 已 gitignore，官方表不提交）
CAS_CSV = "data/cas2025.csv"          # 中科院分区表 2025（ShowJCR）
JCR_CSV = "data/jcr2025.csv"          # JCR 影响因子 2025（ShowJCR）
CCF_CSV = "data/ccf2026.csv"          # CCF 推荐目录 2026（会议/期刊兜底）
CCFT_CSV = "data/ccft2025.csv"        # 计算领域高质量期刊分级 T1/T2/T3
ALERT_CSV = "data/journal_alert.csv"  # 国际期刊预警名单 2025


@dataclass
class ScoringConfig:
    """打分权重与基线。每个维度先归一化到 [0,1]，再按权重加权求和。"""

    # ---- Stage 1（全部捕获，廉价数据）权重，和为 1 ----
    stage1_weights: dict = field(default_factory=lambda: {
        "journal_impact": 0.20,        # JCR IF → CAS 分区 → CCF 等级
        "comment_volume": 0.20,        # 最新评论数（v3 刷新）
        "sleuth": 0.20,                # 知名打假人出现
        "distinct_commenters": 0.15,   # 独立评论者数
        "recency": 0.10,               # 最近动态时间
        "author_overlap": 0.05,        # 评论者与论文作者重叠（代理）
        "journal_alert": 0.10,         # 期刊在预警名单
    })

    # ---- Stage 2（短名单，真实评论）权重，和为 1 ----
    stage2_weights: dict = field(default_factory=lambda: {
        "author_response": 0.20,       # 作者本人回应
        "retraction_eoc": 0.20,        # 撤稿 / 关注声明 / 更正
        "sleuth_gt": 0.15,             # 已知打假人
        "rounds": 0.10,                # 多轮交锋（#N 引用）
        "journal_impact": 0.10,        # 期刊影响力（共享 stage1）
        "comment_volume_gt": 0.05,     # 真实评论数
        "evidence_links": 0.05,        # 评论中的证据链接
        "thread_span": 0.05,           # 交锋时间跨度
        "distinct_commenters_gt": 0.05,# 独立评论者
        "images": 0.05,                # 评论含图片
    })

    # ---- 归一化基线上限 ----
    comment_baseline: float = 100.0        # log 尺
    distinct_baseline: float = 5.0
    rounds_baseline: float = 5.0
    links_baseline: float = 3.0
    span_baseline_days: float = 365.0
    recency_halflife_days: float = 30.0    # recency 软特征半衰期（天）

    # ---- Stage1/Stage2 融合：final = α·stage2 + (1-α)·stage1 ----
    blend_stage2: float = 0.7

    # ---- 大类兴趣倍乘（限制关注领域旋钮）；空 dict = 不限制 ----
    category_interest: dict = field(default_factory=dict)

    # ---- 知名打假人别名（子串匹配，小写归一） ----
    sleuths: tuple = (
        "elisabeth bik", "sholto david", "matthew schrag",
        "david sanders", "clare francis",
    )

    # ---- 撤稿/关注声明关键词（评论 markdown 子串匹配，小写） ----
    retraction_markers: tuple = (
        "retracted", "retraction", "expression of concern",
        "erratum", "withdraw", "withdrawn",
    )

    # ---- v3 富集缓存 TTL（天） ----
    enrich_ttl_days: int = 2
