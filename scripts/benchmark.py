#!/usr/bin/env python3
"""XiDrawing 性能基准脚本

对任意图片运行拼豆转换，统计每个（网格尺寸 x 渲染模式）组合的耗时，
用于定位性能热点（大尺寸图片会显著变慢）。

用法示例:
    python scripts/benchmark.py --image path/to/your_image.png
    python scripts/benchmark.py --image path/to/your_image.png --sizes 24 48 96 --modes photo illustration edge dither --repeat 1
    python scripts/benchmark.py --sample --sizes 24 48

参数:
    --image  输入图片路径（--sample 时忽略）
    --sample 生成一张内置渐变测试图代替外部图片
    --sizes  网格尺寸列表，默认 24 48 96
    --modes  渲染模式列表，默认全部模式
    --repeat 每个组合重复次数（取平均），默认 1
    --max-colors 最大颜色数，默认 40
"""

import argparse
import os
import sys
import time
from pathlib import Path

# 将仓库根目录加入 sys.path，保证从任意位置运行时都能 import bead_pattern_tool
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
from PIL import Image

from bead_pattern_tool.core import DEFAULT_MODES, BeadEngineOptimized, build_catalog


def make_sample_image(path: Path, size: int = 512) -> None:
    """生成一张渐变 + 色块的测试图，模拟游戏截图/角色图。"""
    rng = np.random.default_rng(0)
    x = np.linspace(0, 1, size, dtype=np.float32)
    y = np.linspace(0, 1, size, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    rgb = np.stack(
        [
            (xx * 255).astype(np.uint8),
            (yy * 255).astype(np.uint8),
            (np.clip(0.5 + 0.4 * np.sin(6 * xx + 3 * yy), 0, 1) * 255).astype(np.uint8),
        ],
        axis=-1,
    )
    # 叠加随机色块，模拟角色/背景区域
    for _ in range(8):
        cx, cy = int(rng.integers(0, size)), int(rng.integers(0, size))
        r, g, b = [int(v) for v in rng.integers(0, 256, size=3)]
        w = int(rng.integers(size // 8, size // 3))
        x0, x1 = max(0, cx - w // 2), min(size, cx + w // 2)
        y0, y1 = max(0, cy - w // 2), min(size, cy + w // 2)
        rgb[y0:y1, x0:x1] = (r, g, b)
    Image.fromarray(rgb).save(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=str, default=None, help="输入图片路径")
    parser.add_argument("--sample", action="store_true", help="使用内置渐变测试图")
    parser.add_argument("--sizes", type=int, nargs="+", default=[24, 48, 96], help="网格尺寸列表")
    parser.add_argument("--modes", type=str, nargs="+", default=list(DEFAULT_MODES), help="渲染模式列表")
    parser.add_argument("--repeat", type=int, default=1, help="每个组合重复次数（取平均）")
    parser.add_argument("--max-colors", type=int, default=40, help="最大颜色数")
    args = parser.parse_args()

    if not args.sample and not args.image:
        parser.error("必须提供 --image 或 --sample 之一")
    if not Path(args.image or "").exists() and not args.sample:
        parser.error(f"图片不存在: {args.image}")

    img_path = Path(args.image) if args.image else None
    if args.sample:
        img_path = Path("_benchmark_sample.png")
        print(f"[sample] 生成测试图: {img_path}")
        make_sample_image(img_path)
    assert img_path is not None

    engine = BeadEngineOptimized(catalog=build_catalog())
    print(f"\n输入: {img_path}  sizes={args.sizes}  modes={args.modes}  max_colors={args.max_colors}  repeat={args.repeat}\n")
    print(f"{'网格':>6} | {'模式':<12} | {'耗时(秒)':>10} | {'每格(ms)':>10}")
    print("-" * 52)

    for n in args.sizes:
        for mode in args.modes:
            times = []
            for _ in range(args.repeat):
                t0 = time.perf_counter()
                engine.generate(
                    str(img_path),
                    rows=n,
                    cols=n,
                    max_colors=args.max_colors,
                    modes=[mode],
                    crop="auto",
                    background=(255, 255, 255),
                )
                times.append(time.perf_counter() - t0)
            avg = sum(times) / len(times)
            per_cell = avg * 1000.0 / (n * n)
            print(f"{n:>6} | {mode:<12} | {avg:>10.3f} | {per_cell:>10.3f}")

    if args.sample:
        img_path.unlink(missing_ok=True)
    print("\n提示: 网格尺寸或 max_colors 越大耗时越高；dither 模式误差扩散为顺序循环，相对更慢。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
