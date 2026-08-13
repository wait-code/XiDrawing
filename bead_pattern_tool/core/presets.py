"""
拼豆转换 · 双预设
  bead_avatar()   —— 甜区：Q版/游戏头像/图标（大眼、粗线、主体占满）
  bead_portrait() —— 立绘：自动裁脸 + 高分辨率 + 强增强（写实/半写实）
  bead_auto()     —— 自动判断走哪个
依赖：Pillow + numpy（人脸检测需 opencv，缺了会自动退化为中心裁剪）
"""
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter


# ---------- 核心：每格取主色 + 调色板量化 ----------
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
            out[i, j] = cell[k1d == vals[cnt.argmax()]].mean(0)
    return Image.fromarray(out)


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


def _quantize(pil, max_colors):
    if max_colors and max_colors < 256:
        pil = pil.quantize(colors=max_colors, method=Image.MEDIANCUT,
                           dither=Image.NONE).convert("RGB")
    return pil


def _quantize_to_palette_pil(pil):
    """PIL Image -> 色板量化后的 PIL Image"""
    from .palette import quantize_to_palette
    pix = list(pil.getdata())
    qpix = quantize_to_palette(pix)
    out = pil.copy()
    out.putdata(qpix)
    return out


def _preview(tile, cell=12):
    N = tile.size[0]
    return tile.resize((N * cell, N * cell), Image.NEAREST)


# ---------- 裁剪工具 ----------
def _trim_uniform_border(pil, tol=22, max_ratio=0.42):
    """去掉四周纯色边/UI条/留白"""
    arr = np.asarray(pil).astype(np.float32)
    h, w = arr.shape[:2]
    def uniform(line):
        med = np.median(line, axis=0)
        return (np.sqrt(((line - med) ** 2).sum(1)) < tol).mean() > 0.92
    t, b, l, r = 0, h, 0, w
    limH, limW = int(h * max_ratio), int(w * max_ratio)
    while t < limH and uniform(arr[t]): t += 1
    while b > h - limH and uniform(arr[b - 1]): b -= 1
    while l < limW and uniform(arr[:, l]): l += 1
    while r > w - limW and uniform(arr[:, r - 1]): r -= 1
    return pil.crop((l, t, r, b))


def _center_square(pil, up_bias=0.0):
    w, h = pil.size
    side = min(w, h)
    cx, cy = w / 2, h / 2 - side * up_bias
    x0 = int(np.clip(cx - side / 2, 0, w - side))
    y0 = int(np.clip(cy - side / 2, 0, h - side))
    return pil.crop((x0, y0, x0 + side, y0 + side))


def _face_square(pil, expand=1.7, up_bias=0.12):
    """检测人脸并裁成正方形；检测不到就退化为中心略偏上裁剪"""
    try:
        import cv2
        arr = np.asarray(pil)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        cc = cv2.CascadeClassifier(cv2.data.haarcascades +
                                   "haarcascade_frontalface_default.xml")
        faces = cc.detectMultiScale(gray, 1.1, 4, minSize=(60, 60))
    except Exception:
        faces = []
    w, h = pil.size
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


# ================== 预设 A：Q版/头像甜区 ==================
def bead_avatar(path, N=24, max_colors=20, brightness=0, contrast=12, saturation=30,
                sharpen=True, out=None):
    pil = Image.open(path).convert("RGB")
    pil = _trim_uniform_border(pil)          # 去UI边/留白
    pil = _center_square(pil)                # 收成正方形
    pil = _enhance(pil, brightness, contrast, saturation, sharpen)
    tile = _quantize(_dominant_downscale(pil, N), max_colors)
    tile = _quantize_to_palette_pil(tile)
    if out:
        _preview(tile).save(out)
    return tile


# ================== 预设 B：立绘/写实 ==================
def bead_portrait(path, N=64, max_colors=32, brightness=0, contrast=25, saturation=45,
                  sharpen=True, out=None):
    pil = Image.open(path).convert("RGB")
    pil = _face_square(pil)                  # 自动裁脸（占满画面）
    pil = _enhance(pil, brightness, contrast, saturation, sharpen)
    tile = _quantize(_dominant_downscale(pil, N), max_colors)
    tile = _quantize_to_palette_pil(tile)
    if out:
        _preview(tile).save(out)
    return tile


# ================== 预设 B+：立绘进阶（保五官）==================
def bead_portrait_pro(path, N=64, max_colors=40, edge_strength=0.5,
                      clahe=3.0, sat=1.5, out=None):
    """
    写实脸进阶预设。可调参数：
      edge_strength 五官线条压暗程度 0~0.8（脏就调小）
      clahe         局部对比强度 1~5（越大五官越突出，太大出噪点）
      sat           饱和度倍数
    需要 opencv。
    """
    import cv2
    pil = _face_square(Image.open(path).convert("RGB"))
    arr = np.asarray(pil)
    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
    lab[..., 0] = cv2.createCLAHE(clipLimit=clahe, tileGridSize=(8, 8)).apply(lab[..., 0])
    enh = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    enh = np.asarray(ImageEnhance.Color(Image.fromarray(enh)).enhance(sat))
    tile = np.asarray(_dominant_downscale(Image.fromarray(enh), N)).copy()
    edges = cv2.Canny(cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY), 40, 120)
    es = cv2.resize(edges, (N, N), interpolation=cv2.INTER_AREA)
    tile[es > 50] = (tile[es > 50] * (1 - edge_strength)).astype(np.uint8)
    out_tile = _quantize(Image.fromarray(tile), max_colors)
    out_tile = _quantize_to_palette_pil(out_tile)
    if out:
        _preview(out_tile).save(out)
    return out_tile


# ---------- 对比 / 参数扫描 ----------
def _label(pil, text, bar=26):
    from PIL import ImageDraw
    w, h = pil.size
    canvas = Image.new("RGB", (w, h + bar), (28, 28, 28))
    canvas.paste(pil, (0, bar))
    ImageDraw.Draw(canvas).text((6, 7), text, fill=(235, 235, 235))
    return canvas


def _hcat(imgs, gap=8, bg=(28, 28, 28)):
    H = max(i.size[1] for i in imgs)
    W = sum(i.size[0] for i in imgs) + gap * (len(imgs) - 1)
    canvas = Image.new("RGB", (W, H), bg)
    x = 0
    for im in imgs:
        canvas.paste(im, (x, 0)); x += im.size[0] + gap
    return canvas


def compare_portrait(path, N=64, cell=8, out="compare.png", **kw):
    """并排：左=原图(裁脸后)  右=成品，标注参数"""
    face = _face_square(Image.open(path).convert("RGB"))
    side = N * cell
    left = _label(face.resize((side, side), Image.LANCZOS), "原图(裁脸)")
    res = bead_portrait_pro(path, N=N, **kw)
    p = ", ".join(f"{k}={v}" for k, v in kw.items())
    right = _label(_preview(res, cell), f"成品 N={N} {p}")
    _hcat([left, right]).save(out)
    return res


def sweep_portrait(path, param="edge_strength", values=(0.2, 0.4, 0.6),
                   N=64, cell=6, out="sweep.png", **kw):
    """固定其它参数，扫描某一个参数，横向排出来对比"""
    face = _face_square(Image.open(path).convert("RGB"))
    tiles = [_label(face.resize((N * cell, N * cell), Image.LANCZOS), "原图")]
    for v in values:
        res = bead_portrait_pro(path, N=N, **{**kw, param: v})
        tiles.append(_label(_preview(res, cell), f"{param}={v}"))
    _hcat(tiles).save(out)



def bead_auto(path, out=None):
    """
    立绘 vs Q版 无法可靠自动区分，这里只用【宽高比】粗判：
      接近正方形(游戏头像图标) -> avatar(24)
      非正方形(立绘/截图)       -> portrait(64)
    拿不准就直接手动调 bead_avatar / bead_portrait。
    """
    pil = Image.open(path).convert("RGB")
    w, h = pil.size
    aspect = w / h
    if 0.85 <= aspect <= 1.18:
        mode, tile = "avatar", bead_avatar(path, N=24, out=out)
    else:
        mode, tile = "portrait", bead_portrait(path, N=64, out=out)
    print(f"[bead_auto] 宽高比={aspect:.2f} -> {mode}（如不对请手动选预设）")
    return tile


if __name__ == "__main__":
    import argparse, os
    ap = argparse.ArgumentParser(description="拼豆转换 双预设")
    ap.add_argument("image")
    ap.add_argument("--mode", default="auto", choices=["auto", "avatar", "portrait"])
    ap.add_argument("-n", type=int, default=None, help="覆盖默认格子数")
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()
    out = a.out or os.path.splitext(a.image)[0] + f"_{a.mode}.png"
    if a.mode == "avatar":
        bead_avatar(a.image, N=a.n or 24, out=out)
    elif a.mode == "portrait":
        bead_portrait(a.image, N=a.n or 64, out=out)
    else:
        bead_auto(a.image, out=out)
    print("done ->", out)
