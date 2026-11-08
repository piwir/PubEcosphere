"""手工种子映射：常见期刊 → 大类学科/分区，保证无官方表也能端到端跑。

官方 CSV（ShowJCR 中科院分区表）加载后按 ISSN→名优先覆盖种子；
种子仅在官方匹配失败时兜底。分区为大致估计（source="seed"），
字段顺序与 cas.py 加载逻辑一致。
"""
from __future__ import annotations

# (期刊名, issn, 大类, 分区, top, source)
SEED_CAS: list[tuple] = [
    ("Scientific Reports",            "2045-2322", "综合性期刊", 3, False, "seed"),
    ("PLOS One",                      "1932-6203", "综合性期刊", 3, False, "seed"),
    ("Nature Communications",         "2041-1723", "综合性期刊", 1, True,  "seed"),
    ("Nature",                        "1476-4687", "综合性期刊", 1, True,  "seed"),
    ("Science",                       "0036-8075", "综合性期刊", 1, True,  "seed"),
    ("Nature Cancer",                 "2662-1347", "医学",       1, True,  "seed"),
    ("Cell Death & Disease",          "2041-4889", "医学",       2, False, "seed"),
    ("Blood",                         "0006-4971", "医学",       1, True,  "seed"),
    ("Cancer Research",               "0008-5472", "医学",       1, True,  "seed"),
    ("Journal of Clinical Investigation", "0021-9738", "医学",    1, True,  "seed"),
    ("International Journal of Nanomedicine", "1178-2013", "医学", 2, False, "seed"),
    ("Journal of Nanobiotechnology",  "1477-3155", "医学",       2, False, "seed"),
    ("Stem Cells",                    "1066-5099", "医学",       2, False, "seed"),
    ("Journal of Biological Chemistry", "0021-9258", "生物学",    2, False, "seed"),
    ("International Journal of Molecular Sciences", "1422-0067", "生物学", 2, False, "seed"),
    ("The EMBO Journal",              "0261-4189", "生物学",     1, True,  "seed"),
    ("Chemical Engineering Journal",  "1385-8947", "工程技术",   1, True,  "seed"),
    ("Ceramics International",        "0272-8842", "材料科学",   2, False, "seed"),
    ("Journal of Materials Science: Materials in Electronics", "0957-4522", "材料科学", 3, False, "seed"),
    ("Science of The Total Environment", "0048-9697", "环境科学与生态学", 1, True, "seed"),
    ("Multimedia Tools and Applications", "1380-7501", "计算机科学", 3, False, "seed"),
]
