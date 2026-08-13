"""
拼豆像素图转换器 —— 核心：每格取"主色"而非平均，避免脏灰
"""
import numpy as np
import cv2
from PIL import Image
from sklearn.cluster import KMeans


def load_rgb(path):
    im = Image.open(path).convert("RGB")
    return np.array(im)


# ---------- 1. 预处理：轻锐化 + 提饱和/对比 ----------
def preprocess(img, saturation=1.25, contrast=1.12, sharpen=0.6):
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * saturation, 0, 255)  # 饱和度
    out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB).astype(np.float32)
    out = np.clip((out - 128) * contrast + 128, 0, 255).astype(np.uint8)  # 对比度
    if sharpen > 0:  # 非锐化掩模
        blur = cv2.GaussianBlur(out, (0, 0), 1.0)
        out = np.clip(out.astype(np.float32) + sharpen * (out.astype(np.float32) - blur), 0, 255).astype(np.uint8)
    return out


# ---------- 2A. 平均法（就是会糊的那种）----------
def downscale_mean(img, n):
    return cv2.resize(img, (n, n), interpolation=cv2.INTER_AREA)


# ---------- 2B. 主色法（关键改进）----------
def downscale_dominant(img, n):
    """每格用 KMeans 找主色簇，取最大簇的中心色，忽略抗锯齿过渡像素"""
    h, w, _ = img.shape
    out = np.zeros((n, n, 3), np.uint8)
    ys = np.linspace(0, h, n + 1).astype(int)
    xs = np.linspace(0, w, n + 1).astype(int)
    for i in range(n):
        for j in range(n):
            cell = img[ys[i]:ys[i+1], xs[j]:xs[j+1]].reshape(-1, 3).astype(np.float32)
            if len(cell) == 0:
                continue
            k = min(3, len(np.unique(cell, axis=0)))
            if k <= 1:
                out[i, j] = cell[0]
                continue
            km = KMeans(n_clusters=k, n_init=3, random_state=0).fit(cell)
            # 取像素数最多的簇中心，而不是所有像素的平均
            biggest = np.argmax(np.bincount(km.labels_))
            out[i, j] = km.cluster_centers_[biggest]
    return out


# ---------- 3. 调色板量化（LAB 空间找最近色）----------
def quantize_palette(small, n_colors=16):
    """把成图颜色压到有限调色板，用感知均匀的 LAB 距离，颜色更干净"""
    flat = small.reshape(-1, 3)
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    k = min(n_colors, len(np.unique(flat, axis=0)))
    km = KMeans(n_clusters=k, n_init=4, random_state=0).fit(lab)
    labels = km.labels_
    # 每个簇取该簇内 RGB 均值作为代表色
    palette = np.zeros((k, 3), np.uint8)
    for c in range(k):
        palette[c] = flat[labels == c].mean(0)
    return palette[labels].reshape(small.shape)


# ---------- 显示放大（最近邻 + 网格 + 象限辅助线）----------
def upscale_grid(small, cell=24, grid=(60, 60, 60)):
    import numpy as np
    from PIL import Image, ImageDraw

    n = small.shape[0]
    big = cv2.resize(small, (n*cell, n*cell), interpolation=cv2.INTER_NEAREST)
    for i in range(n+1):
        big[i*cell:i*cell+1, :] = grid
        big[:, i*cell:i*cell+1] = grid

    # 转为 PIL 添加象限辅助线
    pil_img = Image.fromarray(big)
    draw = ImageDraw.Draw(pil_img)

    # 象限辅助线（白色、稍粗）
    mid = n // 2
    quad_color = (240, 240, 240)
    sz = n * cell
    draw.line([(mid * cell, 0), (mid * cell, sz)], fill=quad_color, width=2)
    draw.line([(0, mid * cell), (sz, mid * cell)], fill=quad_color, width=2)

    return np.array(pil_img)


# ================== 一步到位的入口函数 ==================
def image_to_beads(path, n=24, palette=16, out=None,
                   saturation=1.30, contrast=1.12, sharpen=0.6,
                   auto_resolution=False, crop="border"):
    """
    把任意图片转成 n×n 拼豆像素图。
    crop: 'none' | 'border'(去纯色边,默认) | 'subject'(裁到角色主体)
    auto_resolution: 默认关闭（尺寸铁律 n 即 n）；显式传 True 才允许自动提升
    """
    src = load_rgb(path)
    src = auto_crop(src, mode=crop)          # 先裁剪主体
    pre = preprocess(src, saturation, contrast, sharpen)

    if auto_resolution:
        gray = cv2.cvtColor(pre, cv2.COLOR_RGB2GRAY)
        edge_density = (cv2.Canny(gray, 60, 160) > 0).mean()
        if edge_density > 0.10 and n < 32:   # 细节多 → 提分辨率
            n = 32
        if edge_density > 0.16 and n < 48:
            n = 48

    small = downscale_dominant(pre, n)                  # 主色法
    small = quantize_palette(small, n_colors=palette)   # 调色板量化
    # 二次量化到固定拼豆色板
    pix = [tuple(small[r, c]) for r in range(small.shape[0]) for c in range(small.shape[1])]
    from .palette import quantize_to_palette
    pix = quantize_to_palette(pix)
    small = np.array(pix).reshape(small.shape).astype(np.uint8)
    if out:
        Image.fromarray(upscale_grid(small)).save(out)
    return small, n


# ================== 自动裁剪主体 ==================
def _row_uniform(line, tol):
    """一行/一列像素是否接近纯色：>92% 的像素落在该行中位色的 tol 范围内"""
    med = np.median(line, axis=0)
    dist = np.sqrt(((line.astype(np.float32) - med) ** 2).sum(1))
    return (dist < tol).mean() > 0.92


def _trim_border(img, tol=22, max_ratio=0.42):
    """从四周去掉纯色边（白边/UI条/角色周围空背景），每边最多裁 max_ratio"""
    h, w, _ = img.shape
    top, bot, left, right = 0, h, 0, w
    limH, limW = int(h * max_ratio), int(w * max_ratio)
    while top < limH and _row_uniform(img[top], tol):
        top += 1
    while bot > h - limH and _row_uniform(img[bot - 1], tol):
        bot -= 1
    while left < limW and _row_uniform(img[:, left], tol):
        left += 1
    while right > w - limW and _row_uniform(img[:, right - 1], tol):
        right -= 1
    return img[top:bot, left:right]


def _crop_subject(img, tol=40, pad=0.04, patch=6):
    """把四角背景色(各自独立)当背景，裁到前景(角色)外接框，四周留 pad 边距"""
    h, w, _ = img.shape
    f = img.astype(np.float32)
    # 取四角小块的中位色作为多个背景色，避免单像素噪声
    bgs = [np.median(img[:patch, :patch].reshape(-1, 3), 0),
           np.median(img[:patch, -patch:].reshape(-1, 3), 0),
           np.median(img[-patch:, :patch].reshape(-1, 3), 0),
           np.median(img[-patch:, -patch:].reshape(-1, 3), 0)]
    # 像素到"最近背景色"的距离；只有远离所有背景色才算前景
    dmin = np.full((h, w), 1e9, np.float32)
    for bg in bgs:
        dmin = np.minimum(dmin, np.sqrt(((f - bg) ** 2).sum(2)))
    mask = dmin > tol
    # 去掉零散噪点，让外接框稳定
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


def auto_crop(img, mode="border", tol=22, pad=0.04):
    """
    mode: 'none' 不裁 | 'border' 去纯色边(安全) | 'subject' 裁到主体(激进)
    """
    if mode == "none":
        return img
    img = _trim_border(img, tol=tol)          # 两档都先去纯色边
    if mode == "subject":
        img = _crop_subject(img, tol=tol + 18, pad=pad)
    return img


# ================== 命令行入口 ==================
if __name__ == "__main__":
    import argparse, os
    ap = argparse.ArgumentParser(description="把图片转成拼豆像素图")
    ap.add_argument("image", help="输入图片路径")
    ap.add_argument("-o", "--out", default=None, help="输出路径(默认 原名_bead.png)")
    ap.add_argument("-n", type=int, default=24, help="格子数 n×n (默认24)")
    ap.add_argument("-p", "--palette", type=int, default=16, help="调色板颜色数 (默认16)")
    ap.add_argument("--crop", default="border",
                    choices=["none", "border", "subject"], help="裁剪模式 (默认border)")
    ap.add_argument("--sat", type=float, default=1.30, help="饱和度倍数 (默认1.30)")
    ap.add_argument("--contrast", type=float, default=1.12, help="对比度倍数 (默认1.12)")
    ap.add_argument("--sharpen", type=float, default=0.6, help="锐化强度 (默认0.6)")
    ap.add_argument("--no-auto-res", action="store_true", help="关闭自动提升分辨率")
    args = ap.parse_args()

    out = args.out or os.path.splitext(args.image)[0] + "_bead.png"
    small, n = image_to_beads(
        args.image, n=args.n, palette=args.palette, out=out,
        saturation=args.sat, contrast=args.contrast, sharpen=args.sharpen,
        auto_resolution=not args.no_auto_res, crop=args.crop,
    )
    print(f"完成: {n}x{n} 拼豆图 -> {out}")
