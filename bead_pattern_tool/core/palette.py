"""拼豆固定色板 — 40色标准色 + LAB量化工具"""

PERLER_PALETTE = [
    # Row 1 — 黑白灰
    (34, 34, 34), (180, 180, 180), (234, 231, 223), (255, 255, 255),
    # Row 2 — 红
    (211, 47, 54), (156, 10, 0), (214, 12, 74), (230, 150, 141),
    # Row 3 — 粉/桃
    (254, 152, 117), (247, 208, 192), (252, 239, 234), (251, 246, 232),
    # Row 4 — 中性/橙
    (220, 210, 200), (226, 206, 171), (213, 99, 34), (212, 140, 66),
    # Row 5 — 黄/金
    (242, 153, 0), (249, 201, 51), (252, 228, 153), (179, 180, 122),
    # Row 6 — 绿/棕
    (194, 218, 114), (108, 110, 0), (177, 145, 85), (169, 143, 116),
    # Row 7 — 棕/紫
    (170, 146, 40), (63, 43, 18), (116, 73, 31), (83, 70, 88),
    # Row 8 — 蓝/紫
    (42, 36, 70), (57, 69, 153), (90, 69, 157), (186, 163, 215),
    # Row 9 — 浅蓝/青
    (182, 188, 223), (169, 172, 190), (99, 171, 185), (180, 210, 220),
    # Row 10 — 青/蓝
    (145, 216, 230), (71, 174, 160), (182, 211, 200), (39, 56, 100),
]

# 预计算 LAB 空间表示，避免重复转换
_PALETTE_LAB = None


def _get_palette_lab():
    global _PALETTE_LAB
    if _PALETTE_LAB is None:
        import cv2
        import numpy as np
        arr = np.uint8([[c] for c in PERLER_PALETTE])
        _PALETTE_LAB = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)[:, 0, :].astype(np.float32)
    return _PALETTE_LAB


def nearest_palette_color(rgb):
    """用 LAB 欧氏距离找最近色板颜色。rgb: (R,G,B) tuple"""
    import cv2
    import numpy as np
    target = np.uint8([[rgb]])
    target_lab = cv2.cvtColor(target, cv2.COLOR_RGB2LAB)[0, 0].astype(np.float32)
    dists = np.sqrt(((_get_palette_lab() - target_lab) ** 2).sum(axis=1))
    return PERLER_PALETTE[int(np.argmin(dists))]


def quantize_to_palette(pix):
    """将 RGB 元组列表全部量化到色板。pix: [(R,G,B), ...]"""
    return [nearest_palette_color(p) for p in pix]


def col_label(idx):
    """0-based 列索引 -> A, B, C... Z, AA, AB..."""
    n = idx + 1
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def build_catalog():
    """将 40 色 PERLER_PALETTE 包装为 BeadEngineOptimized 可用的 PaletteEntry catalog。

    code 固定为 P001..P040，name 用序号标注；catalog 传给 generate() 后，
    select_catalog_subset 只能从这 40 个真实色号中选色，输出必然 ⊆ 40 色板。
    """
    from .bead_engine import PaletteEntry
    return [
        PaletteEntry(code=f"P{i + 1:03d}", name=f"Derived {i + 1}", rgb=rgb)
        for i, rgb in enumerate(PERLER_PALETTE)
    ]
