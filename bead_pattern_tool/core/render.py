"""图纸渲染：色块 + 可选浅灰网格线 + 底部中文标签"""
from PIL import Image, ImageDraw
from ..config import DEFAULT_N, CELL
from ..fonts import get_cn_font


def render_pattern(pix, cell_size=CELL, n=DEFAULT_N, show_grid=True):
    ps = n * cell_size
    tw = ps * 2 + 1
    th = ps + 1 + 28
    out = Image.new("RGB", (tw, th), (30,30,30))
    draw = ImageDraw.Draw(out)
    for r in range(n):
        for c in range(n):
            color = pix[r*n + c]
            draw.rectangle([c*cell_size, r*cell_size,
                           c*cell_size+cell_size-1, r*cell_size+cell_size-1], fill=color)
            draw.rectangle([ps+c*cell_size, r*cell_size,
                           ps+c*cell_size+cell_size-1, r*cell_size+cell_size-1], fill=color)
    if show_grid:
        GRAY = (200,200,200)
        for i in range(n+1):
            draw.line([(ps+i*cell_size,0),(ps+i*cell_size,ps)], fill=GRAY, width=1)
            draw.line([(ps,i*cell_size),(ps+ps,i*cell_size)], fill=GRAY, width=1)
    font = get_cn_font(14)
    ly = ps + 5
    draw.text((4, ly), "彩色效果图 (无网格)", fill=(180,180,180), font=font)
    draw.text((ps+4, ly), "施工导航图纸 (灰色网格)" if show_grid else "施工图 (已隐藏网格)", fill=(180,180,180), font=font)
    return out


def render_pattern_full(pix, cell_size=CELL, n=DEFAULT_N, show_grid=True):
    """完整版图纸渲染（来自 D 盘原版）：色块 + 灰色网格线 + 坐标轴标签 + 象限辅助线 + 底部中文标签"""
    from ..config import MARGIN_TOP, MARGIN_LEFT
    from .palette import col_label
    ps = n * cell_size
    tw = ps * 2 + MARGIN_LEFT + 1
    th = ps + MARGIN_TOP + 1 + 28
    out = Image.new("RGB", (tw, th), (30, 30, 30))
    draw = ImageDraw.Draw(out)

    font = get_cn_font(14)
    font_axis = get_cn_font(12)   # 轴标签字体
    AXIS_COLOR = (0, 0, 0)        # 轴标签黑色

    # ── 左侧面板：彩色效果图（无轴）──
    for r in range(n):
        for c in range(n):
            color = pix[r * n + c]
            draw.rectangle([c * cell_size, MARGIN_TOP + r * cell_size,
                           c * cell_size + cell_size - 1, MARGIN_TOP + r * cell_size + cell_size - 1], fill=color)

    # ── 右侧面板起始 x 坐标 ──
    right_x = ps + MARGIN_LEFT

    # ── 右侧面板：色块 ──
    for r in range(n):
        for c in range(n):
            color = pix[r * n + c]
            draw.rectangle([right_x + c * cell_size, MARGIN_TOP + r * cell_size,
                           right_x + c * cell_size + cell_size - 1, MARGIN_TOP + r * cell_size + cell_size - 1], fill=color)

    # ── 右侧：网格线 ──
    if show_grid:
        GRAY = (200, 200, 200)
        for i in range(n + 1):
            draw.line([(right_x + i * cell_size, MARGIN_TOP),
                       (right_x + i * cell_size, MARGIN_TOP + ps)], fill=GRAY, width=1)
            draw.line([(right_x, MARGIN_TOP + i * cell_size),
                       (right_x + ps, MARGIN_TOP + i * cell_size)], fill=GRAY, width=1)

    # ── 右侧：象限辅助线（白色、稍粗）──
    if show_grid:
        mid = n // 2
        QUAD_COLOR = (240, 240, 240)
        # 垂直中线
        draw.line([(right_x + mid * cell_size, MARGIN_TOP),
                   (right_x + mid * cell_size, MARGIN_TOP + ps)],
                  fill=QUAD_COLOR, width=2)
        # 水平中线
        draw.line([(right_x, MARGIN_TOP + mid * cell_size),
                   (right_x + ps, MARGIN_TOP + mid * cell_size)],
                  fill=QUAD_COLOR, width=2)

    # ── 右侧：列号（A, B, C...）紧贴编辑图上方 ──
    for c in range(n):
        label = col_label(c)
        bx = right_x + c * cell_size
        tx = bx + max(0, (cell_size - len(label) * 8) // 2)
        draw.text((tx, MARGIN_TOP - 14), label, fill=AXIS_COLOR, font=font_axis)

    # ── 右侧：行号（1, 2, 3...）紧贴编辑图左侧 ──
    for r in range(n):
        label = str(r + 1)
        by = MARGIN_TOP + r * cell_size
        ty = by + max(0, (cell_size - 12) // 2)
        twidth = len(label) * 8
        draw.text((right_x - twidth - 4, ty), label, fill=AXIS_COLOR, font=font_axis)

    # ── 底部标签栏 ──
    ly = MARGIN_TOP + ps + 5
    draw.text((4, ly), "彩色效果图 (无网格)", fill=(180, 180, 180), font=font)
    draw.text((right_x + 4, ly), "施工导航图纸 (灰色网格)" if show_grid else "施工图 (已隐藏网格)",
              fill=(180, 180, 180), font=font)
    return out
