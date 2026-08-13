"""颜色映射：从染料图片提取色块 RGB，建立图纸颜色→最近染料映射"""
import os
import sys
import math
import cv2
import numpy as np


def _rgb_to_lab(rgb):
    """RGB -> CIE LAB，使用 OpenCV 转换后归一化到标准范围

    OpenCV 的 LAB 格式: L: 0-255, a: 0-255, b: 0-255
    标准 CIE LAB: L: 0-100, a: -128~127, b: -128~127
    """
    arr = np.uint8([[list(rgb)]])  # (1,1,3)
    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)[0, 0]
    L = float(lab[0]) * 100.0 / 255.0
    a = float(lab[1]) - 128.0
    b = float(lab[2]) - 128.0
    return (L, a, b)


def _lab_distance(lab1, lab2):
    """LAB 欧氏距离 (CIE76 ΔE)

    LAB 空间专为感知均匀设计：相同距离 ≈ 相同视觉差异。
    这是颜色匹配的业界主流方案（Photoshop/印刷/染料行业标配）。

    参考阈值:
        ΔE < 3  — 人眼几乎看不出差异
        ΔE < 10 — 可接受范围
        ΔE > 20 — 明显色差

    Args:
        lab1, lab2: (L, a, b) 元组，标准 CIE LAB 范围
    Returns:
        float: 色差值 ΔE
    """
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    return math.sqrt((L1 - L2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2)

def _get_assets_dir():
    """定位 assets 目录，兼容 PyInstaller 打包(onefile/onedir)与开发模式直接运行 .py。

    开发模式下不能依赖 sys.executable（它指向 Python 解释器目录），
    必须基于当前文件向上查找包含 assets 的项目根目录。
    """
    # 1) 打包 onefile：_MEIPASS 指向临时解包目录
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass and os.path.isdir(meipass):
        return os.path.join(meipass, 'assets')

    # 2) 打包 onedir：sys.executable 同级 _internal 目录
    exe_dir = os.path.dirname(sys.executable)
    internal = os.path.join(exe_dir, '_internal')
    if os.path.isdir(internal):
        return os.path.join(internal, 'assets')

    # 3) 开发模式：从当前文件向上逐级查找包含 assets 的目录
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    for _ in range(6):
        cand = os.path.join(cur, 'assets')
        if os.path.isdir(cand):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    # 4) 兜底：sys.executable 同级 assets
    return os.path.join(exe_dir, 'assets')
_ASSETS_DIR = _get_assets_dir()


class DyeColor:
    """单个染料颜色"""
    __slots__ = ("index", "page", "rgb", "row", "col", "base_rgb")

    def __init__(self, index, page, rgb, row, col, base_rgb=None):
        self.index = index      # 全局序号 0~47
        self.page = page        # 1 或 2
        self.rgb = rgb          # (r, g, b) 实时取色（屏幕刷新）
        self.base_rgb = base_rgb if base_rgb is not None else rgb  # 基准身份色（load 时固定）
        self.row = row          # 在染料板中的行
        self.col = col          # 在染料板中的列


class DyePalette:
    """染料色板：从(模版) colors1.png / colors2.png 提取所有染料色块"""

    def __init__(self):
        self.dyes = []          # [DyeColor, ...]
        self._dye_labs = []     # 与 dyes 一一对应的实时 LAB 值
        self._base_labs = []    # 与 dyes 一一对应的基准 LAB 值（身份识别用）
        self._page_info = {}    # page → (w, h, rows, cols)

    def load(self):
        """加载两张染料图，提取色块"""
        self.dyes = []
        self._dye_labs = []
        for page, name in enumerate([["colors1.png", 1], ["colors2.png", 2]], 0):
            fname = name[0]
            page_num = name[1]
            path = os.path.join(_ASSETS_DIR, fname)
            if not os.path.exists(path):
                continue
            with open(path, 'rb') as f:
                img_data = np.frombuffer(f.read(), np.uint8)
            img = cv2.imdecode(img_data, cv2.IMREAD_COLOR)
            if img is None:
                continue
            self._extract_dyes_from_image(img, page_num)

        # 预计算所有染料的 LAB 值，用于 LAB ΔE 匹配
        self._dye_labs = [_rgb_to_lab(d.rgb) for d in self.dyes]
        self._base_labs = [_rgb_to_lab(d.base_rgb) for d in self.dyes]

    def _extract_dyes_from_image(self, img_bgr, page):
        """从染料板截图中提取色块

        使用均匀网格分割：模板图为固定行列数（colors1=4×5, colors2=4×5），
        网格取色比分隔线检测更稳定可靠。
        """
        h, w = img_bgr.shape[:2]
        cols = 4
        rows = 5  # 新模板统一为 4×5（20 色/页）

        global_idx = len(self.dyes)
        for r in range(rows):
            for c in range(cols):
                x0 = c * w // cols
                y0 = r * h // rows
                x1 = (c + 1) * w // cols
                y1 = (r + 1) * h // rows

                # 向内缩进 12%，避开分隔线和边缘
                pad_x = max(1, (x1 - x0) // 8)
                pad_y = max(1, (y1 - y0) // 8)
                cx0 = x0 + pad_x
                cx1 = x1 - pad_x
                cy0 = y0 + pad_y
                cy1 = y1 - pad_y

                patch = img_bgr[cy0:cy1, cx0:cx1]
                if patch.size == 0:
                    continue
                avg = patch.mean(axis=(0, 1))  # BGR
                rgb = (int(avg[2]), int(avg[1]), int(avg[0]))  # → RGB

                self.dyes.append(DyeColor(
                    index=global_idx, page=page, rgb=rgb,
                    row=r, col=c
                ))
                global_idx += 1

        self._page_info[page] = (w, h, rows, cols)

    def extract_from_region(self, img_bgr, page, rows):
        """从屏幕截图的染料板区域提取色块

        Args:
            img_bgr: 染料板区域的 BGR 截图
            page: 页码 1 或 2
            rows: 该区域包含的染料行数（page1=5, page2=5）
        """
        h, w = img_bgr.shape[:2]
        cols = 4
        global_idx = len(self.dyes)
        for r in range(rows):
            for c in range(cols):
                x0 = c * w // cols
                y0 = r * h // rows
                x1 = (c + 1) * w // cols
                y1 = (r + 1) * h // rows

                pad_x = max(1, (x1 - x0) // 8)
                pad_y = max(1, (y1 - y0) // 8)
                cx0 = x0 + pad_x
                cx1 = x1 - pad_x
                cy0 = y0 + pad_y
                cy1 = y1 - pad_y

                patch = img_bgr[cy0:cy1, cx0:cx1]
                if patch.size == 0:
                    continue
                avg = patch.mean(axis=(0, 1))
                rgb = (int(avg[2]), int(avg[1]), int(avg[0]))

                self.dyes.append(DyeColor(
                    index=global_idx, page=page, rgb=rgb,
                    row=r, col=c
                ))
                global_idx += 1

        # 重建 LAB 缓存，确保颜色匹配使用最新屏幕取色结果
        self._dye_labs = [_rgb_to_lab(d.rgb) for d in self.dyes]
        self._base_labs = [_rgb_to_lab(d.base_rgb) for d in self.dyes]

    def update_visible_dyes(self, img_bgr, scrolled=False):
        """从屏幕可见染料区域截图，更新对应位置染料的实时 RGB。

        Args:
            img_bgr: 染料板可见区域（8 行）的 BGR 截图
            scrolled: True=已下拉（可见全局行 2-9），False=顶部（可见全局行 0-7）
        """
        h, w = img_bgr.shape[:2]
        cols = 4
        visible_rows = 8
        start_row = 2 if scrolled else 0

        for r in range(visible_rows):
            for c in range(cols):
                x0 = c * w // cols
                y0 = r * h // visible_rows
                x1 = (c + 1) * w // cols
                y1 = (r + 1) * h // visible_rows

                pad_x = max(1, (x1 - x0) // 8)
                pad_y = max(1, (y1 - y0) // 8)
                cx0 = x0 + pad_x
                cx1 = x1 - pad_x
                cy0 = y0 + pad_y
                cy1 = y1 - pad_y

                patch = img_bgr[cy0:cy1, cx0:cx1]
                if patch.size == 0:
                    continue
                avg = patch.mean(axis=(0, 1))
                rgb = (int(avg[2]), int(avg[1]), int(avg[0]))

                global_row = start_row + r
                for dye in self.dyes:
                    g_row = dye.row if dye.page == 1 else 6 + dye.row
                    if g_row == global_row and dye.col == c:
                        dye.rgb = rgb
                        break

        self._dye_labs = [_rgb_to_lab(d.rgb) for d in self.dyes]

    def get_dyes_by_page(self, page):
        """返回指定页的染料列表"""
        return [d for d in self.dyes if d.page == page]

    def nearest(self, target_rgb):
        """找到与目标颜色最近的染料 (LAB 欧氏距离 ΔE)"""
        if not self.dyes:
            return None
        target_lab = _rgb_to_lab(target_rgb)
        best = None
        best_dist = float('inf')
        for i, dye in enumerate(self.dyes):
            dist = _lab_distance(target_lab, self._dye_labs[i])
            if dist < best_dist:
                best_dist = dist
                best = dye
        return best

    def nearest_by_base(self, target_rgb):
        """按染料基准身份色找最近（免疫显示器色偏对身份识别的影响）

        用于选色时识别"屏幕可见格实际是哪个染料"，基准色固定为 load 时模板色，
        不受运行时屏幕取色误差与显示器色偏影响。
        """
        if not self.dyes:
            return None
        if len(self._base_labs) != len(self.dyes):
            self._base_labs = [_rgb_to_lab(d.base_rgb) for d in self.dyes]
        target_lab = _rgb_to_lab(target_rgb)
        best, best_dist = None, float('inf')
        for i, dye in enumerate(self.dyes):
            dist = _lab_distance(target_lab, self._base_labs[i])
            if dist < best_dist:
                best_dist, best = dist, dye
        return best

    def find_approximate(self, target_rgb, threshold=10, first_match=False):
        """近似选择染料：LAB 欧氏距离 ΔE 在 threshold 以内即认为匹配

        Args:
            target_rgb: 目标颜色 (r, g, b)
            threshold: LAB ΔE 阈值（典型范围 3~30，默认 10）
                       ΔE<3 几乎无差异, ΔE<10 可接受, ΔE>20 明显色差
            first_match: True 则命中阈值后立刻返回；False 在命中集合里选最近的

        Returns:
            DyeColor 或 None
        """
        if not self.dyes:
            return None

        target_lab = _rgb_to_lab(target_rgb)
        best = None
        best_dist = float('inf')

        for i, dye in enumerate(self.dyes):
            dist = _lab_distance(target_lab, self._dye_labs[i])
            if dist <= threshold:
                if first_match:
                    return dye
                if dist < best_dist:
                    best_dist = dist
                    best = dye

        # 没有任何染料达阈值时，回退到最近邻
        if best is None:
            return self.nearest(target_rgb)
        return best


def build_color_mapping(pattern_pixels, palette, threshold=10, first_match=False):
    """为图纸中所有唯一颜色建立映射

    Args:
        pattern_pixels: [(r,g,b), ...] 像素列表
        palette: DyePalette 实例
        threshold: LAB ΔE 色差阈值，达到即视为可接受（默认 10）
        first_match: 是否命中阈值后立即返回

    Returns:
        dict: {pattern_color_tuple → DyeColor}
    """
    unique_colors = set(pattern_pixels)
    mapping = {}
    for color in unique_colors:
        mapping[color] = palette.find_approximate(color, threshold=threshold,
                                                  first_match=first_match)
    return mapping
