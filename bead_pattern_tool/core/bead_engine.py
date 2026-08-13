#!/usr/bin/env python3
"""Generate several bead-art candidates from one image.

Required packages:
    pip install numpy pillow

Optional package for ``--crop face``:
    pip install opencv-python-headless

Examples:
    python bead_engine_optimized.py input.jpg --size 64 --colors 32
    python bead_engine_optimized.py input.jpg --rows 48 --cols 64 --crop auto
    python bead_engine_optimized.py input.jpg --palette brand_colors.csv

Palette CSV columns:
    code,name,r,g,b

Palette JSON format:
    [{"code": "A01", "name": "White", "rgb": [245, 245, 240]}]

Without ``--palette``, every candidate derives a palette from colors observed in
the image. Such a result is suitable for previewing, but it is not a physical
manufacturer palette. Supply a measured brand palette for production output.

Each run writes a candidate contact sheet, one grid and preview per mode,
palette usage CSV files, a ranked JSON manifest, and recommended result files.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps

MODE_DESCRIPTIONS = {
    "photo": "Linear-light area sampling; best general color fidelity.",
    "illustration": "Dominant observed colors and stronger region coherence.",
    "edge": "Hybrid sampling that keeps hard contours from becoming muddy.",
    "dither": "Serpentine error diffusion for gradients and large bead boards.",
}
DEFAULT_MODES = tuple(MODE_DESCRIPTIONS)


@dataclass(frozen=True)
class PaletteEntry:
    code: str
    name: str
    rgb: tuple[int, int, int]


@dataclass
class BeadResult:
    mode: str
    grid: np.ndarray
    labels: np.ndarray
    palette: list[PaletteEntry]
    metrics: dict[str, float | int]


def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    """移除 sRGB 伽马，避免直接平均 RGB 时让降采样结果偏暗。"""
    rgb = np.asarray(rgb, dtype=np.float32)
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(rgb: np.ndarray) -> np.ndarray:
    rgb = np.clip(np.asarray(rgb, dtype=np.float32), 0.0, 1.0)
    return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1.0 / 2.4) - 0.055)


def srgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    """将 [0, 1] 范围的 sRGB 转为更适合计算感知色差的 OKLab。"""
    linear = srgb_to_linear(rgb)
    l = (
        0.4122214708 * linear[..., 0]
        + 0.5363325363 * linear[..., 1]
        + 0.0514459929 * linear[..., 2]
    )
    m = (
        0.2119034982 * linear[..., 0]
        + 0.6806995451 * linear[..., 1]
        + 0.1073969566 * linear[..., 2]
    )
    s = (
        0.0883024619 * linear[..., 0]
        + 0.2817188376 * linear[..., 1]
        + 0.6299787005 * linear[..., 2]
    )
    l_, m_, s_ = np.cbrt(l), np.cbrt(m), np.cbrt(s)
    return np.stack(
        (
            0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
        ),
        axis=-1,
    ).astype(np.float32)


def _resize_float_channel(
    channel: np.ndarray, size: tuple[int, int], downsampling: bool
) -> np.ndarray:
    resampling = Image.Resampling.BOX if downsampling else Image.Resampling.BICUBIC
    image = Image.fromarray(np.asarray(channel, dtype=np.float32), mode="F")
    return np.asarray(image.resize(size, resampling), dtype=np.float32)


def area_sample_linear(image: Image.Image, rows: int, cols: int) -> np.ndarray:
    """在线性光空间做面积采样，让每个输出像素对应一颗拼豆。"""
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    linear = srgb_to_linear(rgb)
    downsampling = image.width > cols or image.height > rows
    resized = np.stack(
        [
            _resize_float_channel(linear[..., c], (cols, rows), downsampling)
            for c in range(3)
        ],
        axis=-1,
    )
    return np.round(linear_to_srgb(resized) * 255.0).astype(np.uint8)


def _gradient_magnitude(lightness: np.ndarray) -> np.ndarray:
    gy, gx = np.gradient(np.asarray(lightness, dtype=np.float32))
    return np.hypot(gx, gy)


def importance_weights(oklab: np.ndarray, edge_weight: float = 1.0) -> np.ndarray:
    # 边缘和高彩度区域通常承载眼睛、文字、轮廓等关键信息，应优先占用色板名额。
    gradient = _gradient_magnitude(oklab[..., 0])
    gradient /= max(float(np.percentile(gradient, 95)), 1e-6)
    gradient = np.clip(gradient, 0.0, 1.0)
    chroma = np.hypot(oklab[..., 1], oklab[..., 2])
    chroma /= max(float(np.percentile(chroma, 95)), 1e-6)
    return (1.0 + edge_weight * gradient + 0.25 * np.clip(chroma, 0.0, 1.0)).astype(
        np.float32
    )


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.strip()
    if not value.startswith("#"):
        value = "#" + value
    return tuple(int(v) for v in ImageColor.getrgb(value))


def _palette_entry(item: dict, index: int) -> PaletteEntry:
    lowered = {str(key).strip().lower(): value for key, value in item.items()}
    code = str(lowered.get("code") or lowered.get("id") or f"P{index + 1:03d}")
    name = str(lowered.get("name") or lowered.get("color") or code)
    if "rgb" in lowered:
        rgb_value = lowered["rgb"]
        if isinstance(rgb_value, str):
            parts = [part for part in re.split(r"[\s,;/]+", rgb_value.strip()) if part]
            rgb = tuple(int(float(part)) for part in parts)
        else:
            rgb = tuple(int(value) for value in rgb_value)
    elif "hex" in lowered:
        rgb = _hex_rgb(str(lowered["hex"]))
    else:
        rgb = tuple(int(float(lowered[channel])) for channel in ("r", "g", "b"))
    if len(rgb) != 3 or any(value < 0 or value > 255 for value in rgb):
        raise ValueError(f"Invalid RGB value for palette entry {code}: {rgb}")
    return PaletteEntry(code=code, name=name, rgb=rgb)


def load_palette(path: str | Path) -> list[PaletteEntry]:
    path = Path(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("colors") or data.get("palette") or data.get("entries")
        if not isinstance(data, list):
            raise ValueError("Palette JSON must contain a list of colors")
        entries = [_palette_entry(dict(item), index) for index, item in enumerate(data)]
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            entries = [
                _palette_entry(dict(row), index)
                for index, row in enumerate(csv.DictReader(handle))
            ]
    if not entries:
        raise ValueError(f"Palette is empty: {path}")

    unique: list[PaletteEntry] = []
    seen: set[tuple[int, int, int]] = set()
    for entry in entries:
        if entry.rgb not in seen:
            unique.append(entry)
            seen.add(entry.rgb)
    return unique


def _weighted_kmeans(
    points: np.ndarray,
    weights: np.ndarray,
    clusters: int,
    seed: int = 0,
    restarts: int = 4,
    max_iter: int = 40,
) -> np.ndarray:
    """小规模确定性加权 KMeans，仅用于寻找候选色中心。"""
    points = np.asarray(points, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    rng = np.random.default_rng(seed)
    best_centers: np.ndarray | None = None
    best_loss = math.inf

    for _ in range(restarts):
        # 使用加权 KMeans++ 初始化，降低小面积关键颜色被忽略的概率。
        centers = [points[int(rng.choice(len(points), p=weights / weights.sum()))]]
        nearest = np.sum((points - centers[0]) ** 2, axis=1)
        while len(centers) < clusters:
            probabilities = weights * nearest
            if probabilities.sum() <= 1e-15:
                centers.append(points[int(rng.integers(0, len(points)))])
            else:
                centers.append(
                    points[
                        int(
                            rng.choice(
                                len(points), p=probabilities / probabilities.sum()
                            )
                        )
                    ]
                )
            candidate_distance = np.sum((points - centers[-1]) ** 2, axis=1)
            nearest = np.minimum(nearest, candidate_distance)
        center_array = np.asarray(centers, dtype=np.float64)

        for _iteration in range(max_iter):
            distances = np.sum(
                (points[:, None, :] - center_array[None, :, :]) ** 2, axis=2
            )
            labels = np.argmin(distances, axis=1)
            updated = center_array.copy()
            for cluster in range(clusters):
                members = labels == cluster
                if np.any(members):
                    updated[cluster] = np.average(
                        points[members], axis=0, weights=weights[members]
                    )
                else:
                    farthest = int(np.argmax(weights * np.min(distances, axis=1)))
                    updated[cluster] = points[farthest]
            if np.max(np.abs(updated - center_array)) < 1e-6:
                center_array = updated
                break
            center_array = updated

        final_distances = np.sum(
            (points[:, None, :] - center_array[None, :, :]) ** 2, axis=2
        )
        loss = float(np.sum(weights * np.min(final_distances, axis=1)))
        if loss < best_loss:
            best_loss = loss
            best_centers = center_array

    if best_centers is None:
        raise RuntimeError("KMeans initialization failed")
    return best_centers.astype(np.float32)


def derive_observed_palette(
    target_rgb: np.ndarray, weights: np.ndarray, max_colors: int
) -> list[PaletteEntry]:
    flat_rgb = target_rgb.reshape(-1, 3)
    flat_oklab = srgb_to_oklab(flat_rgb.astype(np.float32) / 255.0)
    flat_weights = weights.reshape(-1)
    unique_rgb = np.unique(flat_rgb, axis=0)
    if len(unique_rgb) <= max_colors:
        chosen_rgb = unique_rgb
    else:
        centers = _weighted_kmeans(flat_oklab, flat_weights, max_colors)
        chosen_indices: list[int] = []
        chosen_colors: set[tuple[int, int, int]] = set()
        # 聚类中心可能是不存在于原图中的虚构颜色，因此吸附到实际观察色上。
        for center in centers:
            distances = np.sum((flat_oklab - center) ** 2, axis=1)
            for index in np.argsort(distances):
                color = tuple(int(v) for v in flat_rgb[index])
                if color not in chosen_colors:
                    chosen_indices.append(int(index))
                    chosen_colors.add(color)
                    break

        while len(chosen_indices) < max_colors:
            palette_oklab = flat_oklab[chosen_indices]
            nearest = np.min(
                np.sum(
                    (flat_oklab[:, None, :] - palette_oklab[None, :, :]) ** 2, axis=2
                ),
                axis=1,
            )
            for index in np.argsort(flat_weights * nearest)[::-1]:
                color = tuple(int(v) for v in flat_rgb[index])
                if color not in chosen_colors:
                    chosen_indices.append(int(index))
                    chosen_colors.add(color)
                    break
            else:
                break
        chosen_rgb = flat_rgb[chosen_indices]

    chosen_oklab = srgb_to_oklab(chosen_rgb.astype(np.float32) / 255.0)
    order = np.lexsort((chosen_oklab[:, 2], chosen_oklab[:, 1], chosen_oklab[:, 0]))
    return [
        PaletteEntry(
            code=f"D{rank + 1:03d}",
            name=f"Derived {rank + 1}",
            rgb=tuple(int(v) for v in chosen_rgb[index]),
        )
        for rank, index in enumerate(order)
    ]


def select_catalog_subset(
    catalog: Sequence[PaletteEntry],
    target_oklab: np.ndarray,
    weights: np.ndarray,
    max_colors: int,
) -> list[PaletteEntry]:
    if len(catalog) <= max_colors:
        return list(catalog)
    catalog_rgb = np.asarray([entry.rgb for entry in catalog], dtype=np.float32) / 255.0
    catalog_oklab = srgb_to_oklab(catalog_rgb)
    points = target_oklab.reshape(-1, 3)
    flat_weights = weights.reshape(-1)
    costs = np.sum((points[:, None, :] - catalog_oklab[None, :, :]) ** 2, axis=2)

    # 从真实品牌色表中贪心选择能最大幅度降低总体色差的颜色子集。
    weighted_totals = np.sum(costs * flat_weights[:, None], axis=0)
    selected = [int(np.argmin(weighted_totals))]
    nearest = costs[:, selected[0]].copy()
    while len(selected) < max_colors:
        improvements = np.sum(
            flat_weights[:, None] * np.maximum(nearest[:, None] - costs, 0.0),
            axis=0,
        )
        improvements[selected] = -1.0
        candidate = int(np.argmax(improvements))
        if improvements[candidate] <= 1e-12:
            break
        selected.append(candidate)
        nearest = np.minimum(nearest, costs[:, candidate])
    return [catalog[index] for index in selected]


def crop_to_ratio(
    image: Image.Image, ratio: float, center: tuple[float, float]
) -> Image.Image:
    width, height = image.size
    current = width / height
    if current > ratio:
        crop_height = height
        crop_width = max(1, round(height * ratio))
    else:
        crop_width = width
        crop_height = max(1, round(width / ratio))
    cx, cy = center
    left = int(np.clip(cx - crop_width / 2, 0, width - crop_width))
    top = int(np.clip(cy - crop_height / 2, 0, height - crop_height))
    return image.crop((left, top, left + crop_width, top + crop_height))


def saliency_center(image: Image.Image) -> tuple[float, float]:
    # 用亮度梯度和彩度估计主体中心；它只是稳健兜底，不替代用户手动构图。
    preview = image.copy()
    preview.thumbnail((256, 256), Image.Resampling.LANCZOS)
    array = np.asarray(preview.convert("RGB"), dtype=np.float32) / 255.0
    oklab = srgb_to_oklab(array)
    gradient = _gradient_magnitude(oklab[..., 0])
    chroma = np.hypot(oklab[..., 1], oklab[..., 2])
    saliency = gradient + 0.2 * chroma
    saliency -= float(np.percentile(saliency, 20))
    saliency = np.clip(saliency, 0.0, None)
    if float(saliency.sum()) <= 1e-8:
        return image.width / 2, image.height / 2
    yy, xx = np.indices(saliency.shape)
    cx = float(np.sum(xx * saliency) / saliency.sum()) * image.width / preview.width
    cy = float(np.sum(yy * saliency) / saliency.sum()) * image.height / preview.height
    # 单条强边缘可能拉偏显著性中心，因此与几何中心混合以稳定裁剪。
    return 0.65 * cx + 0.35 * image.width / 2, 0.65 * cy + 0.35 * image.height / 2


def face_center(image: Image.Image) -> tuple[float, float] | None:
    try:
        import cv2  # type: ignore
    except ImportError:
        return None
    detector = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    if detector.empty():
        return None
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    faces = detector.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=4, minSize=(48, 48)
    )
    if len(faces) == 0:
        return None
    x, y, width, height = max(faces, key=lambda item: int(item[2]) * int(item[3]))
    return float(x + width / 2), float(y + height * 0.46)


def prepare_image(
    image_path: str | Path,
    rows: int,
    cols: int,
    crop: str,
    background: tuple[int, int, int],
) -> Image.Image:
    # 先修正 EXIF 方向，再把透明区域合成到用户指定背景，避免透明像素变黑。
    opened = ImageOps.exif_transpose(Image.open(image_path)).convert("RGBA")
    backdrop = Image.new("RGBA", opened.size, background + (255,))
    image = Image.alpha_composite(backdrop, opened).convert("RGB")
    ratio = cols / rows

    if crop == "fit":
        width, height = image.size
        if width / height > ratio:
            canvas_height = math.ceil(width / ratio)
            canvas_size = (width, canvas_height)
        else:
            canvas_width = math.ceil(height * ratio)
            canvas_size = (canvas_width, height)
        canvas = Image.new("RGB", canvas_size, background)
        canvas.paste(
            image, ((canvas.width - width) // 2, (canvas.height - height) // 2)
        )
        return canvas

    if crop == "center":
        center = (image.width / 2, image.height / 2)
    elif crop == "face":
        center = face_center(image) or saliency_center(image)
    else:
        center = saliency_center(image)
    return crop_to_ratio(image, ratio, center)


def _cell_samples(
    image: Image.Image, rows: int, cols: int, scale: int = 4
) -> tuple[np.ndarray, np.ndarray]:
    fine = area_sample_linear(image, rows * scale, cols * scale)
    samples = (
        fine.reshape(rows, scale, cols, scale, 3)
        .transpose(0, 2, 1, 3, 4)
        .reshape(rows, cols, scale * scale, 3)
    )
    sample_oklab = srgb_to_oklab(samples.astype(np.float32) / 255.0)
    return samples, sample_oklab


def representative_grid(
    image: Image.Image, rows: int, cols: int, mode: str
) -> np.ndarray:
    # photo/dither 使用综合色；illustration/edge 还会分析每颗豆覆盖区域内的子采样点。
    photo = area_sample_linear(image, rows, cols)
    if mode in {"photo", "dither"}:
        return photo

    samples, sample_oklab = _cell_samples(image, rows, cols)
    mean = np.mean(sample_oklab, axis=2, keepdims=True)
    distance = np.sum((sample_oklab - mean) ** 2, axis=-1)
    medoid_index = np.argmin(distance, axis=2)
    medoid = np.take_along_axis(samples, medoid_index[..., None, None], axis=2)[
        ..., 0, :
    ]
    if mode == "illustration":
        # 选实际出现过的代表色，减少轮廓交界处产生并不存在的混合脏色。
        return medoid.astype(np.uint8)

    # edge 只在单元内部差异明显时改用代表色，平坦区域仍保留面积平均的渐变。
    variance = np.mean(np.sum((sample_oklab - mean) ** 2, axis=-1), axis=2)
    threshold = max(float(np.percentile(variance, 65)), 2.5e-4)
    edge_cells = variance >= threshold
    return np.where(edge_cells[..., None], medoid, photo).astype(np.uint8)


def assign_nearest(
    target_oklab: np.ndarray,
    palette: Sequence[PaletteEntry],
    use_fast_palette: bool | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """OKLab 欧氏距离最近邻映射；固定色板场景默认走 cKDTree 快速路径。"""
    palette_rgb = np.asarray([entry.rgb for entry in palette], dtype=np.uint8)
    if use_fast_palette is None:
        try:
            from .. import config

            use_fast_palette = bool(config.DEFAULT_USE_FAST_PALETTE)
        except Exception:
            use_fast_palette = True
    if use_fast_palette:
        from .fast_palette_map import map_to_palette

        return map_to_palette(target_oklab, palette_rgb, use_kdtree=True)
    palette_oklab = srgb_to_oklab(palette_rgb.astype(np.float32) / 255.0)
    costs = np.sum(
        (target_oklab[..., None, :] - palette_oklab[None, None, :, :]) ** 2, axis=-1
    )
    labels = np.argmin(costs, axis=2)
    return palette_rgb[labels], labels.astype(np.int32)


def spatial_refine(
    target_oklab: np.ndarray,
    palette: Sequence[PaletteEntry],
    labels: np.ndarray,
    strength: float,
    passes: int,
) -> np.ndarray:
    if strength <= 0 or passes <= 0:
        return labels
    palette_rgb = np.asarray([entry.rgb for entry in palette], dtype=np.float32) / 255.0
    palette_oklab = srgb_to_oklab(palette_rgb)
    data_cost = np.sum(
        (target_oklab[..., None, :] - palette_oklab[None, None, :, :]) ** 2, axis=-1
    )
    rows, cols = labels.shape
    current = labels.copy()
    palette_index = np.arange(len(palette))

    # 预计算四方向邻域权重（exp(-delta / 0.0025)），避免循环内重复 numpy 开销。
    # 上/下、左/右两两对称：down 复用 up，right 复用 left。
    delta_vertical = np.sum(
        (target_oklab[1:] - target_oklab[:-1]) ** 2, axis=-1
    )
    weight_vertical = np.exp(-delta_vertical / 0.0025).astype(np.float32)
    delta_horizontal = np.sum(
        (target_oklab[:, 1:] - target_oklab[:, :-1]) ** 2, axis=-1
    )
    weight_horizontal = np.exp(-delta_horizontal / 0.0025).astype(np.float32)

    # 相邻原图颜色接近时鼓励使用同色；跨越强边缘时自动降低平滑约束。
    for _ in range(passes):
        previous = current.copy()
        for row in range(rows):
            for col in range(cols):
                energy = data_cost[row, col].copy()
                if row > 0:
                    energy += (
                        strength
                        * weight_vertical[row - 1, col]
                        * (palette_index != previous[row - 1, col])
                    )
                if row < rows - 1:
                    energy += (
                        strength
                        * weight_vertical[row, col]
                        * (palette_index != previous[row + 1, col])
                    )
                if col > 0:
                    energy += (
                        strength
                        * weight_horizontal[row, col - 1]
                        * (palette_index != previous[row, col - 1])
                    )
                if col < cols - 1:
                    energy += (
                        strength
                        * weight_horizontal[row, col]
                        * (palette_index != previous[row, col + 1])
                    )
                current[row, col] = int(np.argmin(energy))
        if np.array_equal(previous, current):
            break
    return current


def dither_assign(
    target_oklab: np.ndarray, palette: Sequence[PaletteEntry]
) -> tuple[np.ndarray, np.ndarray]:
    palette_rgb = np.asarray([entry.rgb for entry in palette], dtype=np.uint8)
    palette_oklab = srgb_to_oklab(palette_rgb.astype(np.float32) / 255.0)
    # 展开平方距离公式 ||p - x||^2 = ||p||^2 - 2 p·x + ||x||^2，避免每格构造 (P,3) 中间数组。
    palette_oklab64 = palette_oklab.astype(np.float64)
    palette_norm = np.sum(palette_oklab64 ** 2, axis=1)
    work = target_oklab.astype(np.float32).copy()
    rows, cols = work.shape[:2]
    labels = np.zeros((rows, cols), dtype=np.int32)

    # 蛇形 Floyd-Steinberg 扩散可避免误差长期偏向同一侧。
    for row in range(rows):
        left_to_right = row % 2 == 0
        columns = range(cols) if left_to_right else range(cols - 1, -1, -1)
        direction = 1 if left_to_right else -1
        for col in columns:
            pixel = work[row, col].astype(np.float64)
            distances = (
                palette_norm
                - 2.0 * (palette_oklab64 @ pixel)
                + float(np.sum(pixel * pixel))
            )
            label = int(np.argmin(distances))
            labels[row, col] = label
            error = work[row, col] - palette_oklab[label]
            neighbors = (
                (row, col + direction, 7 / 16),
                (row + 1, col - direction, 3 / 16),
                (row + 1, col, 5 / 16),
                (row + 1, col + direction, 1 / 16),
            )
            for ny, nx, weight in neighbors:
                if 0 <= ny < rows and 0 <= nx < cols:
                    work[ny, nx] += error * weight
                    work[ny, nx, 0] = np.clip(work[ny, nx, 0], 0.0, 1.0)
                    work[ny, nx, 1:] = np.clip(work[ny, nx, 1:], -0.5, 0.5)
    return palette_rgb[labels], labels


def _box_blur(array: np.ndarray) -> np.ndarray:
    padded = np.pad(array, ((1, 1), (1, 1), (0, 0)), mode="edge")
    return (
        sum(
            padded[dy : dy + array.shape[0], dx : dx + array.shape[1]]
            for dy in range(3)
            for dx in range(3)
        )
        / 9.0
    )


def result_metrics(
    reference_rgb: np.ndarray, grid: np.ndarray, labels: np.ndarray
) -> dict[str, float | int]:
    # 综合单豆色差、远观色差、轮廓误差和孤立豆比例；分数只用于候选初排。
    reference_oklab = srgb_to_oklab(reference_rgb.astype(np.float32) / 255.0)
    result_oklab = srgb_to_oklab(grid.astype(np.float32) / 255.0)
    error = np.linalg.norm(reference_oklab - result_oklab, axis=2)
    blurred_error = np.linalg.norm(
        _box_blur(reference_oklab) - _box_blur(result_oklab), axis=2
    )
    reference_edge = _gradient_magnitude(reference_oklab[..., 0])
    result_edge = _gradient_magnitude(result_oklab[..., 0])
    edge_error = float(np.mean(np.abs(reference_edge - result_edge)))

    same_neighbor = np.zeros_like(labels, dtype=bool)
    same_neighbor[1:] |= labels[1:] == labels[:-1]
    same_neighbor[:-1] |= labels[:-1] == labels[1:]
    same_neighbor[:, 1:] |= labels[:, 1:] == labels[:, :-1]
    same_neighbor[:, :-1] |= labels[:, :-1] == labels[:, 1:]
    isolated_ratio = float(np.mean(~same_neighbor))
    mean_error = float(np.mean(error))
    perceptual_error = float(np.mean(blurred_error))
    score = 100.0 * (
        0.48 * mean_error
        + 0.32 * perceptual_error
        + 0.15 * edge_error
        + 0.05 * isolated_ratio
    )
    return {
        "score": round(score, 4),
        "mean_oklab_error_x100": round(mean_error * 100.0, 4),
        "blurred_oklab_error_x100": round(perceptual_error * 100.0, 4),
        "edge_error_x100": round(edge_error * 100.0, 4),
        "isolated_bead_percent": round(isolated_ratio * 100.0, 3),
        "colors_used": len(np.unique(labels)),
    }


def bead_preview(grid: np.ndarray, cell: int = 10) -> Image.Image:
    rows, cols = grid.shape[:2]
    canvas = Image.new("RGB", (cols * cell, rows * cell), (236, 237, 234))
    draw = ImageDraw.Draw(canvas)
    inset = max(1, cell // 10)
    for row in range(rows):
        for col in range(cols):
            color = tuple(int(value) for value in grid[row, col])
            outline = tuple(max(0, int(value * 0.68)) for value in color)
            x0, y0 = col * cell + inset, row * cell + inset
            x1, y1 = (col + 1) * cell - inset - 1, (row + 1) * cell - inset - 1
            draw.ellipse((x0, y0, x1, y1), fill=color, outline=outline, width=1)
    return canvas


def _font(
    size: int, bold: bool = False
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path(
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
            if bold
            else "/System/Library/Fonts/Supplemental/Arial.ttf"
        ),
        Path(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(str(candidate), size)
        except OSError:
            continue
    return ImageFont.load_default()


def candidate_sheet(results: Sequence[BeadResult], cell: int) -> Image.Image:
    previews = [(result, bead_preview(result.grid, cell)) for result in results]
    panel_width = max(preview.width for _, preview in previews)
    panel_height = max(preview.height for _, preview in previews) + 74
    columns = 2 if len(previews) > 1 else 1
    rows = math.ceil(len(previews) / columns)
    gutter = 20
    canvas = Image.new(
        "RGB",
        (
            panel_width * columns + gutter * (columns - 1),
            panel_height * rows + gutter * (rows - 1),
        ),
        (249, 249, 247),
    )
    draw = ImageDraw.Draw(canvas)
    title_font = _font(24, bold=True)
    detail_font = _font(16)
    for index, (result, preview) in enumerate(previews):
        col = index % columns
        row = index // columns
        x = col * (panel_width + gutter)
        y = row * (panel_height + gutter)
        canvas.paste(preview, (x, y))
        draw.text(
            (x, y + preview.height + 12),
            result.mode,
            font=title_font,
            fill=(25, 28, 30),
        )
        draw.text(
            (x, y + preview.height + 43),
            f"score {result.metrics['score']:.4f}  |  {result.metrics['colors_used']} colors",
            font=detail_font,
            fill=(69, 73, 76),
        )
    return canvas


class BeadEngineOptimized:
    """生成多种拼豆候选，并按统一指标排序。"""

    def __init__(self, catalog: Sequence[PaletteEntry] | None = None, seed: int = 0):
        self.catalog = list(catalog) if catalog else None
        self.seed = seed

    def generate(
        self,
        image_path: str | Path,
        rows: int = 64,
        cols: int = 64,
        max_colors: int = 32,
        modes: Iterable[str] = DEFAULT_MODES,
        crop: str = "auto",
        background: tuple[int, int, int] = (255, 255, 255),
    ) -> tuple[Image.Image, list[BeadResult]]:
        if rows < 1 or cols < 1:
            raise ValueError("rows and cols must be positive")
        if not 1 <= max_colors <= 256:
            raise ValueError("max_colors must be in [1, 256]")
        requested_modes = list(dict.fromkeys(modes))
        invalid = set(requested_modes) - set(DEFAULT_MODES)
        if invalid:
            raise ValueError(f"Unknown modes: {', '.join(sorted(invalid))}")
        if not requested_modes:
            raise ValueError("At least one mode is required")

        prepared = prepare_image(image_path, rows, cols, crop, background)
        # 所有模式共用 photo 采样结果作为评分基准，确保候选之间可比较。
        reference = representative_grid(prepared, rows, cols, "photo")
        results: list[BeadResult] = []

        for mode in requested_modes:
            target = representative_grid(prepared, rows, cols, mode)
            target_oklab = srgb_to_oklab(target.astype(np.float32) / 255.0)
            edge_weight = 1.5 if mode in {"illustration", "edge"} else 0.7
            weights = importance_weights(target_oklab, edge_weight=edge_weight)
            if self.catalog:
                # 有品牌色表时只能在真实可购买的颜色中选择。
                palette = select_catalog_subset(
                    self.catalog, target_oklab, weights, max_colors
                )
            else:
                # 未提供品牌色表时生成图像内预览色板，不应直接当作实体色号。
                palette = derive_observed_palette(target, weights, max_colors)

            if mode == "dither":
                grid, labels = dither_assign(target_oklab, palette)
            else:
                grid, labels = assign_nearest(target_oklab, palette)
                if mode == "illustration":
                    labels = spatial_refine(
                        target_oklab, palette, labels, strength=0.0012, passes=3
                    )
                elif mode == "edge":
                    labels = spatial_refine(
                        target_oklab, palette, labels, strength=0.00055, passes=2
                    )
                palette_rgb = np.asarray(
                    [entry.rgb for entry in palette], dtype=np.uint8
                )
                grid = palette_rgb[labels]

            metrics = result_metrics(reference, grid, labels)
            results.append(
                BeadResult(
                    mode=mode,
                    grid=grid.astype(np.uint8),
                    labels=labels,
                    palette=palette,
                    metrics=metrics,
                )
            )

        results.sort(key=lambda result: float(result.metrics["score"]))
        return prepared, results


def _write_palette_counts(path: Path, result: BeadResult) -> None:
    counts = np.bincount(result.labels.reshape(-1), minlength=len(result.palette))
    total = int(counts.sum())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("code", "name", "r", "g", "b", "count", "percent"))
        for index in np.argsort(counts)[::-1]:
            count = int(counts[index])
            if count == 0:
                continue
            entry = result.palette[int(index)]
            writer.writerow(
                (
                    entry.code,
                    entry.name,
                    *entry.rgb,
                    count,
                    round(100.0 * count / total, 3),
                )
            )


def save_results(
    output_dir: str | Path,
    prepared: Image.Image,
    results: Sequence[BeadResult],
    preview_cell: int = 10,
) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prepared.save(output_dir / "source-crop.png")

    manifest_results = []
    for result in results:
        grid_path = output_dir / f"{result.mode}-grid.png"
        pixel_path = output_dir / f"{result.mode}-pixels.png"
        bead_path = output_dir / f"{result.mode}-beads.png"
        Image.fromarray(result.grid).save(grid_path)
        Image.fromarray(result.grid).resize(
            (result.grid.shape[1] * preview_cell, result.grid.shape[0] * preview_cell),
            Image.Resampling.NEAREST,
        ).save(pixel_path)
        bead_preview(result.grid, preview_cell).save(bead_path)
        _write_palette_counts(output_dir / f"{result.mode}-palette.csv", result)
        manifest_results.append(
            {
                "mode": result.mode,
                "description": MODE_DESCRIPTIONS[result.mode],
                "metrics": result.metrics,
                "grid": grid_path.name,
                "pixel_preview": pixel_path.name,
                "bead_preview": bead_path.name,
                "palette_counts": f"{result.mode}-palette.csv",
            }
        )

    recommended = results[0]
    shutil.copyfile(
        output_dir / f"{recommended.mode}-grid.png", output_dir / "recommended-grid.png"
    )
    shutil.copyfile(
        output_dir / f"{recommended.mode}-pixels.png",
        output_dir / "recommended-pixels.png",
    )
    shutil.copyfile(
        output_dir / f"{recommended.mode}-beads.png",
        output_dir / "recommended-beads.png",
    )
    candidate_sheet(results, preview_cell).save(output_dir / "candidates.png")
    manifest = {
        "recommended": recommended.mode,
        "ranking_note": "Lower score is better. Visual inspection remains authoritative.",
        "palette_note": "Derived image colors"
        if recommended.palette[0].code.startswith("D")
        else "External catalog colors",
        "results": manifest_results,
    }
    (output_dir / "results.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _parse_background(value: str) -> tuple[int, int, int]:
    try:
        return tuple(int(channel) for channel in ImageColor.getrgb(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("image", type=Path, help="Input image")
    parser.add_argument(
        "--output", type=Path, help="Output directory; defaults to <image>_beads"
    )
    parser.add_argument(
        "--size", type=int, default=64, help="Square grid size (default: 64)"
    )
    parser.add_argument("--rows", type=int, help="Grid rows; overrides --size")
    parser.add_argument("--cols", type=int, help="Grid columns; overrides --size")
    parser.add_argument(
        "--colors",
        type=int,
        default=32,
        help="Maximum colors per candidate (default: 32)",
    )
    parser.add_argument(
        "--modes", nargs="+", choices=DEFAULT_MODES, default=list(DEFAULT_MODES)
    )
    parser.add_argument(
        "--crop", choices=("auto", "center", "face", "fit"), default="auto"
    )
    parser.add_argument("--palette", type=Path, help="Manufacturer palette CSV or JSON")
    parser.add_argument(
        "--background",
        type=_parse_background,
        default=(255, 255, 255),
        help="Alpha background, e.g. '#ffffff'",
    )
    parser.add_argument(
        "--preview-cell",
        type=int,
        default=10,
        help="Preview pixels per bead (default: 10)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    rows = args.rows if args.rows is not None else args.size
    cols = args.cols if args.cols is not None else args.size
    if args.preview_cell < 3:
        raise ValueError("preview-cell must be at least 3")
    output = args.output or args.image.with_name(args.image.stem + "_beads")
    catalog = load_palette(args.palette) if args.palette else None
    engine = BeadEngineOptimized(catalog=catalog)
    prepared, results = engine.generate(
        args.image,
        rows=rows,
        cols=cols,
        max_colors=args.colors,
        modes=args.modes,
        crop=args.crop,
        background=args.background,
    )
    manifest = save_results(output, prepared, results, preview_cell=args.preview_cell)
    print(f"Recommended: {manifest['recommended']}")
    print(f"Candidates: {output / 'candidates.png'}")
    print(f"Manifest: {output / 'results.json'}")


if __name__ == "__main__":
    main()
