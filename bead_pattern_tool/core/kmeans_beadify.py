"""
KMeans 精准模式 — 完全 KMeans 拼豆转换（Phase 1 默认链路）

链路：背景清理 → 大图降采样(≤512) → 全图一次 cv2.kmeans(LAB, k=16, 固定种子)
      → 聚类中心映射 40 色板 → 逐格平均色最近邻 → 输出 n×n

特点：尺寸铁律 n 即 n（不自动提分辨率）；输出颜色严格 ⊆ PERLER_PALETTE；
      固定种子 + 多次 attempts，结果可复现。
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..config import DEFAULT_N


def _downsample_for_cluster(src: np.ndarray, max_side: int = 512) -> np.ndarray:
    """聚类前降采样：保留主色彩分布，把全图 KMeans 控制在可接受耗时内。"""
    h, w = src.shape[:2]
    scale = max_side / max(h, w)
    if scale >= 1.0:
        return src
    return cv2.resize(src, (max(1, round(w * scale)), max(1, round(h * scale))),
                      interpolation=cv2.INTER_AREA)


def _cluster_centers_lab(small: np.ndarray, k: int) -> np.ndarray:
    """全图一次 KMeans，返回 k 个中心（LAB 空间）。固定种子，结果可复现。"""
    pixels = small.reshape(-1, 3).astype(np.float32)
    unique = np.unique(pixels, axis=0)
    if len(unique) <= k:
        # 颜色数本来就少于 k，无需聚类，直接拿全部颜色当中心
        return cv2.cvtColor(
            unique.astype(np.uint8)[None, ...], cv2.COLOR_RGB2LAB
        )[0].astype(np.float32)
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1.0)
    cv2.setRNGSeed(0)  # 固定初始化种子，保证可复现
    _, labels, centers = cv2.kmeans(
        lab, k, None, criteria, attempts=5, flags=cv2.KMEANS_PP_CENTERS
    )
    return centers.astype(np.float32)


def _centers_to_palette_rgb(centers_lab: np.ndarray) -> np.ndarray:
    """聚类中心(LAB) → RGB → 最近邻映射到 40 色板，并去重。"""
    from .palette import PERLER_PALETTE

    centers_rgb = cv2.cvtColor(
        np.clip(np.round(centers_lab), 0, 255).astype(np.uint8).reshape(-1, 1, 3),
        cv2.COLOR_LAB2RGB,
    ).reshape(-1, 3)
    centers_rgb = np.clip(np.round(centers_rgb), 0, 255).astype(np.uint8)

    palette_rgb = np.asarray(PERLER_PALETTE, dtype=np.uint8)
    palette_lab = cv2.cvtColor(
        palette_rgb.reshape(-1, 1, 3), cv2.COLOR_RGB2LAB
    ).reshape(-1, 3).astype(np.float32)

    chosen: list[np.ndarray] = []
    seen: set[tuple[int, int, int]] = set()
    for center in centers_rgb:
        target_lab = cv2.cvtColor(center[None, None, :], cv2.COLOR_RGB2LAB)[0, 0].astype(
            np.float32
        )
        dists = np.sqrt(((palette_lab - target_lab) ** 2).sum(axis=1))
        color = palette_rgb[int(np.argmin(dists))]
        key = (int(color[0]), int(color[1]), int(color[2]))
        if key not in seen:
            seen.add(key)
            chosen.append(color)
    return np.asarray(chosen, dtype=np.uint8)


def _cell_average_nearest(
    src: np.ndarray, n: int, palette_rgb: np.ndarray
) -> np.ndarray:
    """n×n 分块取平均色，在候选色板内做 LAB 最近邻，返回 n×n RGB 网格。"""
    h, w = src.shape[:2]
    ys = np.linspace(0, h, n + 1).astype(int)
    xs = np.linspace(0, w, n + 1).astype(int)
    palette_lab = cv2.cvtColor(
        palette_rgb.reshape(-1, 1, 3), cv2.COLOR_RGB2LAB
    ).reshape(-1, 3).astype(np.float32)

    out = np.zeros((n, n, 3), np.uint8)
    for i in range(n):
        for j in range(n):
            cell = src[ys[i]:ys[i + 1], xs[j]:xs[j + 1]].reshape(-1, 3)
            if len(cell) == 0:
                continue
            mean_rgb = cell.mean(0).astype(np.uint8)
            target_lab = cv2.cvtColor(
                mean_rgb[None, None, :], cv2.COLOR_RGB2LAB
            )[0, 0].astype(np.float32)
            dists = np.sqrt(((palette_lab - target_lab) ** 2).sum(axis=1))
            out[i, j] = palette_rgb[int(np.argmin(dists))]
    return out


def kmeans_beadify(path, n: int = DEFAULT_N, k: int = 16, crop: str = "border"):
    """
    完全 KMeans 精准模式：任意图片 → n×n 拼豆像素图。

    参数:
        path: 输入图片路径
        n:    输出网格尺寸（尺寸铁律，绝不自动修改）
        k:    全图 KMeans 聚类中心数
        crop: 'none' | 'border'(去纯色边,默认) | 'subject'(裁到主体)

    返回:
        (pix, n) — pix 为长度 n*n 的 RGB 元组列表，全部 ∈ 40 色板
    """
    if n < 1:
        raise ValueError(f"n 必须为正整数: {n}")
    if k < 1:
        raise ValueError(f"k 必须为正整数: {k}")

    from PIL import Image

    from .bead_converter import auto_crop

    image = Image.open(path).convert("RGB")
    src = np.asarray(image)
    src = auto_crop(src, mode=crop)  # ① 背景清理（去纯色边）

    small = _downsample_for_cluster(src)  # ② 大图降采样
    centers_lab = _cluster_centers_lab(small, k)  # ③ 全图一次 KMeans
    palette_rgb = _centers_to_palette_rgb(centers_lab)  # ④ 中心 → 40 色板
    grid = _cell_average_nearest(src, n, palette_rgb)  # ⑤ 逐格平均色最近邻

    pix = [tuple(int(v) for v in grid[r, c]) for r in range(n) for c in range(n)]
    return pix, n
