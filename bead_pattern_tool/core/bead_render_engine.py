"""
bead_render 库算法改造版 — 集成自 D:\\文档\\arknights\\bead_pattern_tool\\bead_render

改造点：
  1. import 全部相对化（适配桌面项目包结构）
  2. 输出统一末尾量化到固定 40 色板（PERLER_PALETTE），保证颜色铁律
  3. 精准模式 = palette_optimize（边缘+中心加权全图 KMeans）+ 固定色板兜底
  4. 附赠 raw n×n PNG 保存工具（每格一像素、不放大、无网格）

参考接口文档：项目根目录 BEAD_RENDER_集成文档.md
"""
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
from sklearn.cluster import KMeans
import cv2

from ..config import DEFAULT_N
from .palette import PERLER_PALETTE, quantize_to_palette

# 人脸检测器（pro3 用；加载失败不影响其它模式）
try:
    _CASCADE = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    _FACE_DETECTOR = cv2.CascadeClassifier(_CASCADE)
except Exception:
    _FACE_DETECTOR = None


class BeadEngine:
    """原样保留 bead_render.bead_engine.BeadEngine 的算法实现（import 已相对化）"""

    def __init__(self):
        pass

    def standard(self, img_path, n=24, brightness=0, contrast=0, saturation=0):
        src = Image.open(img_path).convert("RGB")
        if brightness:
            src = ImageEnhance.Brightness(src).enhance(1.0 + brightness / 100.0)
        if contrast:
            src = ImageEnhance.Contrast(src).enhance(1.0 + contrast / 100.0)
        if saturation:
            src = ImageEnhance.Color(src).enhance(1.0 + saturation / 100.0)
        w, h = src.size
        scale = max(n / w, n / h)
        nw, nh = int(w * scale + 0.5), int(h * scale + 0.5)
        src = src.resize((nw, nh), Image.NEAREST)
        left, top = (nw - n) // 2, (nh - n) // 2
        tile = src.crop((left, top, left + n, top + n))
        return [tile.getpixel((c, r)) for r in range(n) for c in range(n)]

    def pro1(self, img_path, n=24, palette=16):
        src = self._load_rgb(img_path)
        src = self._auto_crop(src, mode="border")
        src = self._preprocess(src)
        small = self._downscale_dominant(src, n)
        small = self._quantize_palette(small, n_colors=palette)
        return [tuple(small[r, c]) for r in range(n) for c in range(n)]

    def pro2(self, img_path, n=24, max_colors=20):
        pil = Image.open(img_path).convert("RGB")
        pil = self._trim_uniform_border(pil)
        pil = self._center_square(pil)
        pil = self._enhance(pil, 0, 12, 30, True)
        tile = self._dominant_downscale(pil, n)
        tile = self._quantize(tile, max_colors)
        return [tile.getpixel((c, r)) for r in range(n) for c in range(n)]

    def pro3(self, img_path, n=64, max_colors=32):
        pil = Image.open(img_path).convert("RGB")
        pil = self._face_square(pil)
        pil = self._enhance(pil, 0, 25, 45, True)
        tile = self._dominant_downscale(pil, n)
        tile = self._quantize(tile, max_colors)
        return [tile.getpixel((c, r)) for r in range(n) for c in range(n)]

    def pro4(self, img_path, n=24, brightness=0, contrast=0, saturation=0):
        src = Image.open(img_path).convert("RGB")
        if brightness:
            src = ImageEnhance.Brightness(src).enhance(1.0 + brightness / 100.0)
        if contrast:
            src = ImageEnhance.Contrast(src).enhance(1.0 + contrast / 100.0)
        if saturation:
            src = ImageEnhance.Color(src).enhance(1.0 + saturation / 100.0)
        src = src.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=2))
        w, h = src.size
        side = min(w, h)
        left, top = (w - side) // 2, (h - side) // 2
        src = src.crop((left, top, left + side, top + side))
        arr = np.asarray(src)
        S = arr.shape[0]
        out = np.zeros((n, n, 3), np.uint8)
        e = np.linspace(0, S, n + 1).astype(int)
        for i in range(n):
            for j in range(n):
                cell = arr[e[i]:e[i + 1], e[j]:e[j + 1]].reshape(-1, 3)
                if len(cell) == 0:
                    continue
                key = (cell // 24).astype(np.int32)
                k1d = key[:, 0] * 10000 + key[:, 1] * 100 + key[:, 2]
                vals, cnt = np.unique(k1d, return_counts=True)
                dom = vals[cnt.argmax()]
                out[i, j] = np.round(cell[k1d == dom].mean(0)).astype(np.uint8)
        return [tuple(out[r, c]) for r in range(n) for c in range(n)]

    def palette_optimize(self, img_path, n=24, palette_size=40):
        """边缘+中心加权全图 KMeans 调色板优化 — 精准模式核心（保留原算法，末尾补固定色板兜底由上层做）"""
        pil = Image.open(img_path).convert("RGB")
        arr = np.asarray(pil)
        try:
            arr = self._auto_crop(arr, mode="border")
        except Exception:
            pass
        h, w = arr.shape[:2]
        max_side = max(h, w)
        if max_side > 256:
            scale = 256.0 / max_side
            arr_small = cv2.resize(arr, (max(1, int(w * scale)), max(1, int(h * scale))),
                                   interpolation=cv2.INTER_AREA)
        else:
            arr_small = arr
        # 逐格分配用 512 边工作图：保留足够细节，又避免对原图做全量 LAB/距离计算
        if max_side > 512:
            scale2 = 512.0 / max_side
            arr_work = cv2.resize(arr, (max(1, int(w * scale2)), max(1, int(h * scale2))),
                                  interpolation=cv2.INTER_AREA)
        else:
            arr_work = arr
        lab = cv2.cvtColor(arr_small, cv2.COLOR_RGB2LAB).astype(np.float32)
        gray = cv2.cvtColor(arr_small, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 60, 160).astype(np.float32) / 255.0
        yy, xx = np.indices(gray.shape)
        cy, cx = gray.shape[0] / 2.0, gray.shape[1] / 2.0
        radius = min(gray.shape) * 0.4
        center_bias = np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2.0 * radius * radius))
        importance = 1.0 + edges * 3.0 + center_bias * 0.7
        flat_lab = lab.reshape(-1, 3)
        weights = importance.reshape(-1)
        unique = np.unique(flat_lab.round().astype(np.int32), axis=0)
        k = min(palette_size, len(unique))
        if k <= 1:
            palette_rgb = np.asarray([arr_small[0, 0]], np.uint8)
        else:
            # 原算法用 sklearn KMeans(n_init=4) + sample_weight，60k 点 40 类约 4s。
            # 改造：按权重概率采样（固定种子）→ cv2.kmeans（固定种子），保留边缘/中心加权语义且提速一个量级。
            probs = weights.astype(np.float64) / max(weights.sum(), 1e-9)
            rng = np.random.RandomState(0)
            n_sample = min(20000, len(flat_lab))
            idx = rng.choice(len(flat_lab), size=n_sample, p=probs, replace=False)
            sampled = flat_lab[idx].astype(np.float32)
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1.0)
            cv2.setRNGSeed(0)
            _, _, centers = cv2.kmeans(
                sampled, k, None, criteria, attempts=3, flags=cv2.KMEANS_PP_CENTERS
            )
            palette_lab = centers.astype(np.float32)
            palette_lab = np.clip(palette_lab, 0, 255).astype(np.uint8)
            palette_rgb = cv2.cvtColor(palette_lab.reshape(-1, 1, 3),
                                       cv2.COLOR_LAB2RGB).reshape(-1, 3)
            palette_rgb = np.clip(palette_rgb, 0, 255).astype(np.uint8)
        full_lab = cv2.cvtColor(arr_work, cv2.COLOR_RGB2LAB).astype(np.float32)
        palette_lab = cv2.cvtColor(palette_rgb.reshape(-1, 1, 3),
                                   cv2.COLOR_RGB2LAB).astype(np.float32).reshape(-1, 3)
        wh, ww = arr_work.shape[:2]
        ys = np.linspace(0, wh, n + 1).astype(int)
        xs = np.linspace(0, ww, n + 1).astype(int)
        out = []
        for i in range(n):
            for j in range(n):
                cell = full_lab[ys[i]:ys[i + 1], xs[j]:xs[j + 1]].reshape(-1, 3)
                if len(cell) == 0:
                    out.append(tuple(int(v) for v in palette_rgb[0]))
                    continue
                dist = ((cell[:, None, :] - palette_lab[None, :, :]) ** 2).sum(axis=2)
                picks = np.argmin(dist, axis=1)
                choice = np.bincount(picks, minlength=palette_lab.shape[0]).argmax()
                out.append(tuple(int(v) for v in palette_rgb[choice]))
        return out

    @staticmethod
    def _load_rgb(path):
        return np.array(Image.open(path).convert("RGB"))

    @staticmethod
    def _preprocess(img, saturation=1.25, contrast=1.12, sharpen=0.6):
        hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV).astype(np.float32)
        hsv[..., 1] = np.clip(hsv[..., 1] * saturation, 0, 255)
        out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB).astype(np.float32)
        out = np.clip((out - 128) * contrast + 128, 0, 255).astype(np.uint8)
        if sharpen > 0:
            blur = cv2.GaussianBlur(out, (0, 0), 1.0)
            out = np.clip(out.astype(np.float32) + sharpen *
                          (out.astype(np.float32) - blur), 0, 255).astype(np.uint8)
        return out

    @staticmethod
    def _downscale_dominant(img, n):
        h, w, _ = img.shape
        out = np.zeros((n, n, 3), np.uint8)
        ys = np.linspace(0, h, n + 1).astype(int)
        xs = np.linspace(0, w, n + 1).astype(int)
        for i in range(n):
            for j in range(n):
                cell = img[ys[i]:ys[i + 1], xs[j]:xs[j + 1]].reshape(-1, 3).astype(np.float32)
                if len(cell) == 0:
                    continue
                k = min(3, len(np.unique(cell, axis=0)))
                if k <= 1:
                    out[i, j] = cell[0]
                    continue
                criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.2)
                _, labels, centers = cv2.kmeans(cell, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
                labels = labels.flatten()
                biggest = np.argmax(np.bincount(labels))
                out[i, j] = np.round(centers[biggest]).astype(np.uint8)
        return out

    @staticmethod
    def _quantize_palette(small, n_colors=16):
        flat = small.reshape(-1, 3)
        lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
        k = min(n_colors, len(np.unique(flat, axis=0)))
        km = KMeans(n_clusters=k, n_init=4, random_state=0).fit(lab)
        labels = km.labels_
        palette = np.zeros((k, 3), np.uint8)
        for c in range(k):
            palette[c] = np.round(flat[labels == c].mean(0)).astype(np.uint8)
        return palette[labels].reshape(small.shape)

    @staticmethod
    def _row_uniform(line, tol):
        med = np.median(line, axis=0)
        dist = np.sqrt(((line.astype(np.float32) - med) ** 2).sum(1))
        return (dist < tol).mean() > 0.92

    def _trim_border(self, img, tol=22, max_ratio=0.42):
        h, w, _ = img.shape
        top, bot, left, right = 0, h, 0, w
        limH, limW = int(h * max_ratio), int(w * max_ratio)
        while top < limH and self._row_uniform(img[top], tol):
            top += 1
        while bot > h - limH and self._row_uniform(img[bot - 1], tol):
            bot -= 1
        while left < limW and self._row_uniform(img[:, left], tol):
            left += 1
        while right > w - limW and self._row_uniform(img[:, right - 1], tol):
            right -= 1
        return img[top:bot, left:right]

    def _trim_uniform_border(self, pil, tol=22, max_ratio=0.42):
        arr = np.asarray(pil).astype(np.float32)
        h, w = arr.shape[:2]
        def uniform(line):
            med = np.median(line, axis=0)
            return (np.sqrt(((line - med) ** 2).sum(1)) < tol).mean() > 0.92
        t, b, l, r = 0, h, 0, w
        limH, limW = int(h * max_ratio), int(w * max_ratio)
        while t < limH and uniform(arr[t]):
            t += 1
        while b > h - limH and uniform(arr[b - 1]):
            b -= 1
        while l < limW and uniform(arr[:, l]):
            l += 1
        while r > w - limW and uniform(arr[:, r - 1]):
            r -= 1
        return pil.crop((l, t, r, b))

    @staticmethod
    def _center_square(pil, up_bias=0.0):
        w, h = pil.size
        side = min(w, h)
        cx, cy = w / 2, h / 2 - side * up_bias
        x0 = int(np.clip(cx - side / 2, 0, w - side))
        y0 = int(np.clip(cy - side / 2, 0, h - side))
        return pil.crop((x0, y0, x0 + side, y0 + side))

    def _face_square(self, pil, expand=1.7, up_bias=0.12):
        arr = np.asarray(pil)
        w, h = pil.size
        faces = []
        if _FACE_DETECTOR is not None:
            try:
                gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
                faces = _FACE_DETECTOR.detectMultiScale(gray, 1.1, 4, minSize=(60, 60))
            except Exception:
                pass
        if len(faces):
            x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            side = int(max(fw, fh) * expand)
            cx, cy = x + fw / 2, y + fh / 2 - side * up_bias
        else:
            side = min(w, h)
            cx, cy = w / 2, h * 0.42
        side = min(side, w, h)
        x0 = int(np.clip(cx - side / 2, 0, w - side))
        y0 = int(np.clip(cy - side / 2, 0, h - side))
        return pil.crop((x0, y0, x0 + side, y0 + side))

    @staticmethod
    def _enhance(pil, brightness, contrast, saturation, sharpen):
        if brightness:
            pil = ImageEnhance.Brightness(pil).enhance(1.0 + brightness / 100.0)
        if contrast:
            pil = ImageEnhance.Contrast(pil).enhance(1.0 + contrast / 100.0)
        if saturation:
            pil = ImageEnhance.Color(pil).enhance(1.0 + saturation / 100.0)
        if sharpen:
            pil = pil.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=2))
        return pil

    @staticmethod
    def _dominant_downscale(pil, N):
        arr = np.asarray(pil)
        H, W = arr.shape[:2]
        out = np.zeros((N, N, 3), np.uint8)
        ey = np.linspace(0, H, N + 1).astype(int)
        ex = np.linspace(0, W, N + 1).astype(int)
        for i in range(N):
            for j in range(N):
                cell = arr[ey[i]:ey[i + 1], ex[j]:ex[j + 1]].reshape(-1, 3)
                if len(cell) == 0:
                    continue
                key = (cell // 24).astype(np.int32)
                k1d = key[:, 0] * 10000 + key[:, 1] * 100 + key[:, 2]
                vals, cnt = np.unique(k1d, return_counts=True)
                out[i, j] = np.round(cell[k1d == vals[cnt.argmax()]].mean(0)).astype(np.uint8)
        return Image.fromarray(out)

    @staticmethod
    def _quantize(pil, max_colors):
        if max_colors and max_colors < 256:
            pil = pil.quantize(colors=max_colors, method=Image.MEDIANCUT,
                               dither=Image.NONE).convert("RGB")
        return pil

    @staticmethod
    def _crop_subject(img, tol=40, pad=0.04, patch=6):
        h, w, _ = img.shape
        f = img.astype(np.float32)
        bgs = [np.median(img[:patch, :patch].reshape(-1, 3), 0),
               np.median(img[:patch, -patch:].reshape(-1, 3), 0),
               np.median(img[-patch:, :patch].reshape(-1, 3), 0),
               np.median(img[-patch:, -patch:].reshape(-1, 3), 0)]
        dmin = np.full((h, w), 1e9, np.float32)
        for bg in bgs:
            dmin = np.minimum(dmin, np.sqrt(((f - bg) ** 2).sum(2)))
        mask = dmin > tol
        mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN,
                                np.ones((3, 3), np.uint8))
        ys, xs = np.where(mask > 0)
        if len(ys) == 0:
            return img
        y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
        py, px = int((y1 - y0) * pad), int((x1 - x0) * pad)
        y0, y1 = max(0, y0 - py), min(h, y1 + py)
        x0, x1 = max(0, x0 - px), min(w, x1 + px)
        return img[y0:y1, x0:x1]

    def _auto_crop(self, img, mode="border", tol=22, pad=0.04):
        if mode == "none":
            return img
        img = self._trim_border(img, tol=tol)
        if mode == "subject":
            img = self._crop_subject(img, tol=tol + 18, pad=pad)
        return img


# ───────────────────────── 公开接口 ─────────────────────────

_ENGINE = BeadEngine()


def bead_render_precise(path, n=DEFAULT_N, palette_size=40, crop="border"):
    """精准模式：加权全图 KMeans（palette_optimize）→ 固定 40 色板兜底。

    尺寸铁律：返回 pix 长度恒为 n*n；输出颜色严格 ⊆ PERLER_PALETTE。
    """
    if n < 1:
        raise ValueError(f"n 必须为正整数: {n}")
    pix = _ENGINE.palette_optimize(path, n=n, palette_size=palette_size)
    pix = quantize_to_palette(pix)
    return pix, n


def bead_render_photo(path, n=DEFAULT_N, max_colors=40):
    """photo 模式：pro1（border 裁 + 预处理 + 主色法 + 自适应量化）→ 固定 40 色板"""
    pix = _ENGINE.pro1(path, n=n, palette=max_colors)
    return quantize_to_palette(pix), n


def bead_render_illustration(path, n=DEFAULT_N, max_colors=20):
    """illustration 模式：pro2（中心方形 + 增强 + 主色 + mediancut）→ 固定 40 色板"""
    pix = _ENGINE.pro2(path, n=n, max_colors=max_colors)
    return quantize_to_palette(pix), n


def bead_render_edge(path, n=DEFAULT_N, palette_size=16):
    """edge 模式：palette_optimize（加权调色板优化）→ 固定 40 色板"""
    pix = _ENGINE.palette_optimize(path, n=n, palette_size=palette_size)
    return quantize_to_palette(pix), n


def bead_render_dither(path, n=DEFAULT_N, palette_size=40):
    """dither 模式：pro1 结果 + 蛇形 FS 抖动 → 固定 40 色板"""
    from .palette import _get_palette_lab as get_palette_lab  # 桌面版 palette.py 为私有命名
    pix = _ENGINE.pro1(path, n=n, palette=palette_size)
    arr = np.asarray(pix, np.uint8).reshape(n, n, 3)
    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB).astype(np.float32)
    pal_lab = get_palette_lab()
    pal_rgb = np.asarray(PERLER_PALETTE, np.uint8)
    out = np.zeros((n, n, 3), np.uint8)
    cur = lab.copy()
    for y in range(n):
        row = range(n) if y % 2 == 0 else range(n - 1, -1, -1)
        for x in row:
            dist = np.sqrt(((cur[y, x] - pal_lab) ** 2).sum(1))
            k = int(np.argmin(dist))
            out[y, x] = pal_rgb[k]
            err = cur[y, x] - pal_lab[k]
            if y % 2 == 0:
                neighbors = [(y, x + 1, 7 / 16), (y + 1, x - 1, 3 / 16),
                             (y + 1, x, 5 / 16), (y + 1, x + 1, 1 / 16)]
            else:
                neighbors = [(y, x - 1, 7 / 16), (y + 1, x + 1, 3 / 16),
                             (y + 1, x, 5 / 16), (y + 1, x - 1, 1 / 16)]
            for ny, nx, w in neighbors:
                if 0 <= ny < n and 0 <= nx < n:
                    cur[ny, nx] += err * w
    pix_out = [tuple(int(v) for v in out[r, c]) for r in range(n) for c in range(n)]
    return pix_out, n


def save_raw_png(pix, n, out_path):
    """把 n×n 像素列表保存为 n×n 像素的 raw PNG（每格一像素、不放大、无网格线）。"""
    img = Image.new("RGB", (n, n))
    img.putdata(list(pix))
    img.save(out_path)
    return out_path
