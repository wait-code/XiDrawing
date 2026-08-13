# XiDrawing 性能优化方案

> 状态：实施中
> 目标：在视觉相近的前提下，把典型图（1024×1024、n=48）处理时间减少 2× 以上。
> 度量：`python scripts/benchmark.py --image path/to/image.png --sizes 24 48 96 --modes photo illustration edge dither --repeat 3`，记录 OKLab 转换、色彩映射（聚类/最近邻）、空间平滑/误差扩散、渲染/保存分段耗时。

## 一、代码事实核查（与草案的差异修正）

草案基于「固定 40 色板仍逐像素跑加权 KMeans」的前提，但通读 `core/bead_engine.py` 后确认：

| 草案假设 | 代码事实 |
|---|---|
| 固定色板每张图都跑 KMeans | **不成立**。传入 `catalog`（如 `build_catalog()` 的 40 色板）时走 `select_catalog_subset`（贪心子集选择，纯 numpy），不调 KMeans |
| 加权 KMeans 是固定色板场景热点 | KMeans（`_weighted_kmeans`）只在**无 catalog** 时由 `derive_observed_palette` 调用 |
| 主要耗时：OKLab 转换 | OKLab 转换已向量化，成本低；`representative_grid` 的 `_cell_samples`（illustration/edge，4× 子采样）占比更大 |
| Floyd-Steinberg 误差扩散是热点 | **成立**。`dither_assign` 是纯 Python 双层顺序循环，每格一次 `(P,3)` 距离计算，网格越大越慢 |
| `spatial_refine`（illustration/edge） | **真实热点**。纯 Python 双层循环 × passes，每格重复计算 `exp(target_delta)` 与 `np.arange(P)` |

因此实施重点调整为：
1. **固定色板批量最近邻映射**（cKDTree / numpy 兜底）——替换 `assign_nearest` 的全矩阵 `(N,P,3)` 中间数组，省内存且等效加速；
2. **dither_assign 优化**——预计算 palette 范数、展开距离公式、减少每次循环的中间分配；
3. **spatial_refine 优化**——四方向邻域权重一次性预计算，循环内查表，消除重复 `exp` / `arange`；
4. **无 catalog 路径的 KMeans**——保留（用户已否决纯最近色量化），仅在文档标注可后续用 MiniBatchKMeans。

## 二、实施清单（按优先级）

### P0 快赢（已完成）
- [x] 新建 `bead_pattern_tool/core/fast_palette_map.py`：40 色 OKLab 预计算缓存 + `scipy.spatial.cKDTree` 批量最近邻映射（scipy 不可用时 numpy 全矩阵兜底，结果严格一致）。
- [x] `config.py` 增加 `DEFAULT_USE_FAST_PALETTE = True` 开关。
- [x] `bead_engine.assign_nearest` 接入快速映射路径（保持默认输出等价）。
- [x] `dither_assign` 距离计算优化（展开平方距离公式，减少中间数组）。
- [x] `spatial_refine` 邻域权重预计算（四方向一次 exp，循环查表）。
- [x] `requirements.txt` 增加 `scipy`（cKDTree）。

### P1 下采样与预缩放（待实施/可选）
- `representative_grid` 已按网格尺寸采样；大输入图可在 `prepare_image` 后先 `thumbnail` 到网格 × 4 的尺寸再走子采样，避免超大图内存尖峰。
- GUI「预缩放」开关（`bead_pattern_tool/gui/app.py`）。

### P2 误差扩散 C 实现（待实施/可选）
- 方案 A：Pillow `Image.quantize(dither=...)`——但使用 RGB 距离，与 OKLab 视觉度量不一致，仅在「快速预览」模式启用。
- 方案 B：numba `@njit` 重写 `dither_assign` 与 `spatial_refine` 主循环（保持 OKLab 语义），收益最高，新增可选依赖 `numba`。

### P3 回归度量（可选）
- `scripts/benchmark.py` 增加分段计时（OKLab / 映射 / 平滑 / 渲染）并输出 CSV，供后续回归。

## 三、验收标准（用户指定，当前轮次暂缓执行完整验收）

- [ ] benchmark 上 24× / 48× / 96× 三组 baseline vs 优化后对比，总体 ≥ 2× 加速；
- [ ] 视觉差异小（PSNR / delta-E 小样本确认可接受范围）；
- [ ] 输出结果与优化前**严格一致**（同一色板、同一 OKLab 度量下最近邻结果不变，dither/spatial_refine 仅作性能等价改写）。

## 四、风险与注意

- cKDTree 最近邻与全矩阵 `argmin` 在 OKLab 欧氏度量下结果一致；仅当浮点精度导致边界并列时可能取不同索引，视觉不可见。
- `dither_assign` 为顺序依赖算法，无法完全向量化；优化后仍保持逐像素蛇形扩散语义，仅加速距离计算与剪裁。
- 固定色板场景不跑 KMeans，草案中「KDTree 替换 KMeans 5–20×」的收益预期仅适用于无 catalog 预览模式（不改变算法，仅提供后续优化方向）。
