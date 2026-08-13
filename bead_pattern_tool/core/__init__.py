from .bead_engine import (
    BeadEngineOptimized,
    BeadResult,
    PaletteEntry,
    DEFAULT_MODES,
    save_results,
    load_palette,
)
from .render import render_pattern, render_pattern_full
from .palette import (
    PERLER_PALETTE,
    nearest_palette_color,
    quantize_to_palette,
    col_label,
    build_catalog,
)
from .bead_converter import (
    image_to_beads,
    auto_crop,
    preprocess,
    downscale_dominant,
    quantize_palette,
)
from .kmeans_beadify import kmeans_beadify
from .bead_render_engine import (
    bead_render_precise,
    bead_render_photo,
    bead_render_illustration,
    bead_render_edge,
    bead_render_dither,
    save_raw_png,
)
from .presets import (
    bead_avatar,
    bead_portrait,
    bead_portrait_pro,
    bead_auto,
)
from .pixels import (
    load_pixels,
    load_pixels_main_color,
    detect_bg,
    smooth_edges,
    smooth_pro,
    add_contour,
    pro_process,
)
from ..config import DEFAULT_N


_engine_cache = None


def _get_engine():
    global _engine_cache
    if _engine_cache is None:
        _engine_cache = BeadEngineOptimized(catalog=build_catalog())
    return _engine_cache


def make_pattern(path, brightness=0, contrast=0, saturation=0,
                 smooth_strength=5, contour=True, manual_bg=None, pro_mode=True, n=DEFAULT_N):
    """保持旧 API 兼容的适配器 — 内部使用 BeadEngineOptimized"""
    from PIL import Image, ImageEnhance
    import os, tempfile

    img = Image.open(path)
    if brightness != 0:
        img = ImageEnhance.Brightness(img).enhance(1.0 + brightness / 100.0)
    if contrast != 0:
        img = ImageEnhance.Contrast(img).enhance(1.0 + contrast / 100.0)
    if saturation != 0:
        img = ImageEnhance.Color(img).enhance(1.0 + saturation / 100.0)

    fd, temp_path = tempfile.mkstemp(suffix='.png', prefix='bead_preprocess_')
    os.close(fd)
    img.save(temp_path)

    engine = _get_engine()
    bg = manual_bg if manual_bg is not None else (255, 255, 255)
    modes = ["photo", "illustration", "edge", "dither"] if pro_mode else ["photo"]
    # catalog 固定为 40 色 PERLER_PALETTE，max_colors=40 让全部色号可用，
    # 引擎只能在真实色板内选色，输出严格 ⊆ 40 色集合。
    max_colors = 40

    prepared, results = engine.generate(
        temp_path, rows=n, cols=n,
        max_colors=max_colors, modes=modes,
        crop="auto", background=bg
    )

    try:
        os.remove(temp_path)
    except OSError:
        pass

    best = results[0]
    pix = [(int(best.grid[r, c, 0]), int(best.grid[r, c, 1]), int(best.grid[r, c, 2]))
           for r in range(n) for c in range(n)]

    return render_pattern(pix, n=n), pix
