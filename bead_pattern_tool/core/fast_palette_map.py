"""固定调色板快速最近邻映射 — OKLab 预计算 + cKDTree 批量查询。

背景：BeadEngineOptimized 在给定固定 catalog（如 40 色 PERLER_PALETTE）时，
assign_nearest 会构造 (rows, cols, P, 3) 的全距离矩阵；当网格较大（96×96 以上）
或 palette 较大时，中间数组占用明显。本模块把 palette 一次性预转换到 OKLab
并建立 scipy cKDTree，以批量查询替代全矩阵展开；scipy 不可用时自动回退到
numpy 全矩阵 argmin，两者在 OKLab 欧氏度量下结果一致。
"""

from __future__ import annotations

import numpy as np

from .bead_engine import srgb_to_oklab

# palette RGB 元组 -> (palette_oklab, tree_or_None) 缓存，避免重复转换与建树
_cache: dict[tuple[int, ...], tuple[np.ndarray, object | None]] = {}


def _cache_key(palette_rgb: np.ndarray) -> tuple[int, ...]:
    return tuple(int(v) for v in palette_rgb.reshape(-1))


def get_palette_tree(palette_rgb: np.ndarray) -> tuple[np.ndarray, object | None]:
    """返回 (palette_oklab, cKDTree|None)，palette_rgb 形状 (P, 3) uint8。"""
    key = _cache_key(palette_rgb)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    palette_oklab = srgb_to_oklab(palette_rgb.astype(np.float32) / 255.0)
    tree: object | None = None
    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(palette_oklab)
    except Exception:
        tree = None
    _cache[key] = (palette_oklab, tree)
    return _cache[key]


def map_to_palette(
    target_oklab: np.ndarray, palette_rgb: np.ndarray, use_kdtree: bool = True
) -> tuple[np.ndarray, np.ndarray]:
    """把任意形状的 OKLab 像素批量映射到固定色板。

    Args:
        target_oklab: (..., 3) float32 OKLab 像素。
        palette_rgb: (P, 3) uint8 调色板。
        use_kdtree: 允许使用 cKDTree 快速路径（不可用时自动回退）。

    Returns:
        (mapped_rgb, labels)：mapped_rgb 形状与 target_oklab 前两维一致，
        labels 为 (..., ) int32，对应 palette_rgb 行索引。
    """
    palette_oklab, tree = get_palette_tree(palette_rgb)
    flat = np.asarray(target_oklab, dtype=np.float32).reshape(-1, 3)
    if use_kdtree and tree is not None:
        _, indices = tree.query(flat, k=1, workers=-1)
        labels_flat = np.asarray(indices, dtype=np.int32)
    else:
        costs = np.sum((flat[:, None, :] - palette_oklab[None, :, :]) ** 2, axis=-1)
        labels_flat = np.argmin(costs, axis=1).astype(np.int32)
    shape = target_oklab.shape[:-1]
    labels = labels_flat.reshape(shape)
    mapped = palette_rgb[labels_flat].reshape(shape + (3,))
    return mapped, labels
