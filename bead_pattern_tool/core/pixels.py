"""像素处理：载入、增强、缩放、量化、背景检测、平滑、轮廓描边"""
import sys, os, traceback, datetime
from collections import Counter
from PIL import Image, ImageEnhance, ImageFilter
import numpy as np
from ..config import DEFAULT_N

# ── Pro1 日志 ──
_PRO1_LOG = []
def _pro1_log(msg):
    _PRO1_LOG.append(f"[{datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}")

_PRO1_LOG.append(f"sys.executable={sys.executable}")
_PRO1_LOG.append(f"sys.path[:3]={sys.path[:3]}")
_PRO1_LOG.append(f"__name__={__name__} __package__={__package__}")

try:
    _pro1_log("尝试导入 core.bead_converter...")
    from .bead_converter import auto_crop, preprocess, downscale_dominant, quantize_palette
    _HAS_BC = True
    _pro1_log("导入成功 _HAS_BC=True")
except Exception as _err:
    _pro1_log(f"导入失败: {_err}")
    _pro1_log(traceback.format_exc())
    _HAS_BC = False


def load_pixels(path, brightness=0, contrast=0, saturation=0, n=DEFAULT_N):
    src = Image.open(path).convert("RGB")
    if brightness: src = ImageEnhance.Brightness(src).enhance(1.0 + brightness/100.0)
    if contrast:   src = ImageEnhance.Contrast(src).enhance(1.0 + contrast/100.0)
    if saturation: src = ImageEnhance.Color(src).enhance(1.0 + saturation/100.0)
    w, h = src.size; scale = max(n / w, n / h)
    nw, nh = int(w * scale + 0.5), int(h * scale + 0.5)
    src = src.resize((nw, nh), Image.NEAREST)
    left, top = (nw - n) // 2, (nh - n) // 2
    tile = src.crop((left, top, left + n, top + n))
    pix = [tile.getpixel((c, r)) for r in range(n) for c in range(n)]
    from .palette import quantize_to_palette
    return quantize_to_palette(pix)


def load_pixels_main_color(path, brightness=0, contrast=0, saturation=0, n=DEFAULT_N):
    """
    主色模式算法（Pro4）：先裁后分块，每格取主色而非单点采样
    特点：清晰干净，比快速模式细节更丰富，比 Pro1 更快
    """
    src = Image.open(path).convert("RGB")

    # 增强处理（使用用户参数）
    if brightness:
        src = ImageEnhance.Brightness(src).enhance(1.0 + brightness / 100.0)
    if contrast:
        src = ImageEnhance.Contrast(src).enhance(1.0 + contrast / 100.0)
    if saturation:
        src = ImageEnhance.Color(src).enhance(1.0 + saturation / 100.0)

    # 锐化（内部默认，不暴露给用户）
    src = src.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=2))

    # 在原分辨率上居中裁剪成正方形（先高清裁，再分块）
    w, h = src.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    src = src.crop((left, top, left + side, top + side))

    # 每格取主色——核心算法
    arr = np.asarray(src)
    S = arr.shape[0]
    out = np.zeros((n, n, 3), np.uint8)
    e = np.linspace(0, S, n + 1).astype(int)
    for i in range(n):
        for j in range(n):
            cell = arr[e[i]:e[i + 1], e[j]:e[j + 1]].reshape(-1, 3)
            if len(cell) == 0:
                continue
            # 粗分桶：按 24 阶量化颜色，找出现次数最多的类别
            key = (cell // 24).astype(np.int32)
            k1d = key[:, 0] * 10000 + key[:, 1] * 100 + key[:, 2]
            vals, cnt = np.unique(k1d, return_counts=True)
            dom = vals[cnt.argmax()]
            # 在主色类别内求均值，避免脏灰
            out[i, j] = cell[k1d == dom].mean(0)

    # 返回扁平元组列表，与现有 load_pixels 格式一致
    pix = [tuple(out[r, c]) for r in range(n) for c in range(n)]
    from .palette import quantize_to_palette
    return quantize_to_palette(pix)


def detect_bg(pix, n=DEFAULT_N):
    edges = []
    for c in range(n): edges.append(pix[c]); edges.append(pix[(n-1)*n+c])
    for r in range(1, n-1): edges.append(pix[r*n]); edges.append(pix[r*n+n-1])
    return Counter(edges).most_common(1)[0][0]


def smooth_edges(pix, bg, strength=5, n=DEFAULT_N):
    if strength <= 0: return list(pix)
    res = list(pix); tb = 9-strength; ts = strength
    for r in range(n):
        for c in range(n):
            idx=r*n+c; nbs=[]
            for dr in (-1,0,1):
                for dc in (-1,0,1):
                    if dr==0 and dc==0: continue
                    nr,nc=r+dr,c+dc
                    if 0<=nr<n and 0<=nc<n: nbs.append(pix[nr*n+nc])
            if not nbs: continue
            is_bg=pix[idx]==bg; bg_c=sum(1 for nb in nbs if nb==bg)
            if is_bg and (len(nbs)-bg_c)>tb:
                nb=[nb for nb in nbs if nb!=bg]
                if nb: res[idx]=Counter(nb).most_common(1)[0][0]
            elif not is_bg and bg_c>=ts: res[idx]=bg
    return res


def pro_process(path, bg_hint=None, contour_enabled=True,
                brightness=0, contrast=0, saturation=0, n=DEFAULT_N):
    _pro1_log(f"pro_process 入口: path={path}, _HAS_BC={_HAS_BC}, n={n}")
    if not _HAS_BC:
        _pro1_log("走 fallback 路径")
        pix = load_pixels(path, brightness, contrast, saturation, n=n)
        bg = bg_hint if bg_hint is not None else detect_bg(pix, n)
        pix = smooth_pro(pix, bg, strength=5, n=n)
        if contour_enabled: pix = add_contour(pix, bg, n=n)
        from .render import render_pattern
        _pro1_log(f"fallback 完成, 颜色数={len(set(pix))}")
        result = render_pattern(pix, n=n, show_grid=False), pix
    else:
        _pro1_log("走 bead_converter 路径")
        import numpy as np
        src = np.array(Image.open(path).convert("RGB"))
        _pro1_log(f"原图尺寸: {src.shape}")
        src = auto_crop(src, mode="border")
        _pro1_log(f"auto_crop 后: {src.shape}")
        processed = preprocess(src)
        _pro1_log(f"preprocess 完成, shape={processed.shape}")
        small = downscale_dominant(processed, n)
        _pro1_log(f"downscale_dominant 完成, shape={small.shape}")
        pix = [tuple(int(v) for v in small[r, c]) for r in range(n) for c in range(n)]
        from .palette import quantize_to_palette
        pix = quantize_to_palette(pix)
        _pro1_log(f"quantize_to_palette 完成, 颜色数={len(set(pix))}")
        small = np.array(pix).reshape(n, n, 3).astype(np.uint8)
        if contour_enabled:
            bg = bg_hint if bg_hint is not None else detect_bg(pix, n)
            pix = add_contour(pix, bg, n=n)
        from .render import render_pattern
        _pro1_log("渲染完成")
        result = render_pattern(pix, n=n), pix
    return result


def smooth_pro(pix, bg_hint=None, strength=5, n=DEFAULT_N):
    import random
    if strength <= 0: return list(pix)
    if bg_hint is None: bg_hint = detect_bg(pix, n)
    res = list(pix); uni = list(set(pix)); k = min(2, len(uni))
    cents = random.sample(uni, k) if k >= 2 else [uni[0], uni[0]]
    labels = [0]*len(pix)
    for _ in range(30):
        nl=[]
        for p in pix:
            ds=[sum((a-b)*(a-b) for a,b in zip(p,c)) for c in cents]
            nl.append(ds.index(min(ds)))
        if nl==labels: break
        labels=nl
        for ki in range(k):
            ms=[pix[i] for i,l in enumerate(labels) if l==ki]
            if ms: cents[ki]=tuple(sum(c[j] for c in ms)//len(ms) for j in range(3))
    d0=sum((a-b)*(a-b) for a,b in zip(cents[0],bg_hint))
    d1=sum((a-b)*(a-b) for a,b in zip(cents[1],bg_hint))
    bl=0 if d0<=d1 else 1; bc=cents[bl]; is_bg=[l==bl for l in labels]
    for r in range(n):
        for c in range(n):
            idx=r*n+c; same=[]
            for dr in (-1,0,1):
                for dc in (-1,0,1):
                    nr,nc=r+dr,c+dc
                    if 0<=nr<n and 0<=nc<n and labels[nr*n+nc]==labels[idx]:
                        same.append(pix[nr*n+nc])
            if same: res[idx]=sorted(same,key=lambda x:sum(x))[len(same)//2]
    tb=9-strength; ts=strength
    for r in range(n):
        for c in range(n):
            idx=r*n+c; nbs=[]
            for dr in (-1,0,1):
                for dc in (-1,0,1):
                    if dr==0 and dc==0: continue
                    nr,nc=r+dr,c+dc
                    if 0<=nr<n and 0<=nc<n: nbs.append(res[nr*n+nc])
            if not nbs: continue
            bg_c=sum(1 for nb in nbs if nb==bc)
            if is_bg[idx] and (len(nbs)-bg_c)>tb:
                nb=[nb for nb in nbs if nb!=bc]
                if nb: res[idx]=Counter(nb).most_common(1)[0][0]
            elif not is_bg[idx] and bg_c>=ts: res[idx]=bc
    loops=max(1,min(strength//2,3))
    for _ in range(loops):
        d=list(res)
        for r in range(n):
            for c in range(n):
                idx=r*n+c
                if res[idx]==bc:
                    for dr in (-1,0,1):
                        for dc in (-1,0,1):
                            nr,nc=r+dr,c+dc
                            if 0<=nr<n and 0<=nc<n and res[nr*n+nc]!=bc:
                                d[idx]=res[nr*n+nc]; break
                        else: continue
                        break
        res=d
        for r in range(n):
            for c in range(n):
                idx=r*n+c
                if res[idx]!=bc:
                    b = sum(1 for dr in (-1,0,1) for dc in (-1,0,1)
                            if 0<=r+dr<n and 0<=c+dc<n
                            and res[(r+dr)*n+(c+dc)]==bc)
                    if b>=7: res[idx]=bc
    return res


def add_contour(pix, bg, contour_color=(0,0,0), n=DEFAULT_N):
    res = list(pix)
    for r in range(n):
        for c in range(n):
            idx=r*n+c
            if pix[idx]!=bg: continue
            for dr,dc in ((-1,0),(1,0),(0,-1),(0,1)):
                nr,nc=r+dr,c+dc
                if 0<=nr<n and 0<=nc<n and pix[nr*n+nc]!=bg:
                    res[idx]=contour_color; break
    return res
