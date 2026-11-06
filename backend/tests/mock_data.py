"""M07 几何分析用合成墩身点云（合成数据，不含业主真实点云；AGENTS.md §6）。

坐标系：原点在柱底中心，x/y 水平、z 向上，单位米。截面为 2 m × 2 m 矩形墩，
点只落在四条侧表面上（地面扫描只能看到表面，看不到内部）。

倾斜口径（与设计文档 4.1.5 一致，非本项目另行发明）：
    文档 4.1.5 原文：
        Δ_h = 拟合轴线在 z = z_t 处的水平坐标 − 在 z = z_b 处的水平坐标
        i   = |Δ_h| / (z_t − z_b) × 1000   （单位：‰，即倾斜率）
    本模块按"顶面中心相对底面中心偏移 = tilt_ratio × height"构造整体刚性倾斜，
    即 Δ_h = tilt_ratio × height，i = tilt_ratio × 1000（默认 0.005 → 5‰；
    1 号墩业务演示用 ``DEMO_TILT_RATIO=0.0045``，实测约 4.562‰）。
    倾斜只做刚体平移：截面随高度平移，断面尺寸与形状不变（不缩放、不剪切）。

噪声口径（本模块的工程解释，**不是**文档规定）：
    文档 4.1.2/4.1.8 只给量级（站间配准 3–5 mm、遮挡导致中心偏移可达 10 mm 以上），
    并未定义"1% 噪声"。本模块取

        σ = noise_ratio × section

    即 2 m 截面的 1% = 20 mm，对 x/y/z 三个方向各施加独立高斯噪声（σ 相同）。
    若业主口径是"点位的 1% 测距"或"1% 高程"，改 noise_ratio 或在本文件替换 σ 定义即可，
    下游接口不受影响。

遮挡口径：在 z ∈ occlusion_range（默认 [4, 6] m）内按 occlusion_drop 比例随机丢弃点，
只作用于该区间，不改变可见点位置（即无系统性偏置的单侧遮挡；真实单侧遮挡的偏置
见文档 4.1.3 的说明，本模块不模拟）。
"""

from __future__ import annotations

import hashlib
import os
import re

import numpy as np
import numpy.typing as npt

# 演示/联调固定种子：同一构建下重复调用必须得到完全一致的点云与倾斜率。
# 需要复现其他场景时用 BT_SYNTHETIC_SEED 覆盖（运行期读取），或显式传 seed。
DEFAULT_SEED = 42
# 1 号墩保留原演示口径：固定倾斜率 0.0045 m/m，实测由点云真实计算（约 4.562‰）。
# 其余构件按 component_code 派生的档位倾斜率，避免 10 个结果一模一样。
DEMO_TILT_RATIO = 0.0045
LEGACY_COMPONENT_CODE = "PIR 1号桥墩"
# 演示倾斜档位：按桥墩序号轮转，保证 PIR 2–10 覆盖 1–5 类的全部区间。
# 这些是合成点云的构造参数，不是结果；最终倾斜率由 calculate_plumb() 实算。
TILT_BUCKETS = (0.0015, 0.0025, 0.0035, 0.0045, 0.0060)


def current_base_seed() -> int:
    """运行期基础种子；``BT_SYNTHETIC_SEED`` 可整体覆盖（交付时统一替换）。"""
    raw = os.environ.get("BT_SYNTHETIC_SEED")
    if not raw:
        return DEFAULT_SEED
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError("BT_SYNTHETIC_SEED 必须是整数") from exc


def derive_component_seed(base_seed: int, component_code: str) -> int:
    """按构件编码派生稳定种子：跨进程/跨机器一致（sha256，不用 Python hash()）。"""
    digest = hashlib.sha256(f"{base_seed}:{component_code}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % (2**63 - 1)


def component_seed(component_code: str, base_seed: int | None = None) -> int:
    """构件级种子；1 号墩沿用原基础种子，保持既有 4.562‰ 演示口径。"""
    base = current_base_seed() if base_seed is None else base_seed
    if component_code == LEGACY_COMPONENT_CODE:
        return base
    return derive_component_seed(base, component_code)


def _component_ordinal(component_code: str) -> int:
    match = re.search(r"PIR\s*(\d+)\s*号桥墩", component_code)
    return int(match.group(1)) if match else 0


def component_tilt_ratio(component_code: str, base_seed: int | None = None) -> float:
    """构件级演示倾斜率（构造参数）；未知编码回落到默认 0.0045。"""
    base = current_base_seed() if base_seed is None else base_seed
    ordinal = _component_ordinal(component_code)
    if ordinal <= 0 or component_code == LEGACY_COMPONENT_CODE:
        return DEMO_TILT_RATIO
    bucket = TILT_BUCKETS[(ordinal - 1) % len(TILT_BUCKETS)]
    # ±0.02‰ 抖动让 10 个构件的实测值互不相等，同时不跨越等级边界。
    jitter = (derive_component_seed(base, component_code) % 41 - 20) / 1_000_000.0
    return bucket + jitter


def generate_pier_point_cloud(
    seed: int = DEFAULT_SEED,
    n: int = 200_000,
    height: float = 10.0,
    section: float = 2.0,
    tilt_ratio: float = 0.005,
    noise_ratio: float = 0.01,
    occlusion_range: tuple[float, float] = (4.0, 6.0),
    occlusion_drop: float = 0.3,
) -> npt.NDArray[np.float64]:
    """生成带倾斜、噪声与中部遮挡的矩形墩身表面点云，返回 (N, 3) float64。

    参数
    ----
    seed: 随机种子。同 seed 同参必得同一结果（``np.random.default_rng(seed)``）。
    n: 生成点数（遮挡后实际返回数会略少）。
    height: 墩高（m），点沿 z 均匀分布。
    section: 截面边长（m），正方形截面。
    tilt_ratio: 倾斜率（m/m）。顶面中心相对底面中心偏移 = ``tilt_ratio * height``。
    noise_ratio: 噪声比，**按本模块解释** σ = ``noise_ratio * section``（见模块 docstring）。
    occlusion_range: 遮挡高程区间 (z_lo, z_hi)，闭区间。
    occlusion_drop: 区间内随机丢弃比例，0–1。

    返回
    ----
    (N, 3) float64，列为 x/y/z（m），原点在柱底中心；z 已含噪声，故可能略微超出
    [0, height]（约 ±4σ）。
    """
    if n <= 0:
        raise ValueError("n 必须为正")
    if height <= 0.0 or section <= 0.0:
        raise ValueError("height/section 必须为正")
    if noise_ratio < 0.0:
        raise ValueError("noise_ratio 不能为负")
    if not 0.0 <= occlusion_drop <= 1.0:
        raise ValueError("occlusion_drop 必须在 [0, 1]")
    z_lo, z_hi = occlusion_range
    if z_hi < z_lo:
        raise ValueError("occlusion_range 必须满足 lo <= hi")

    rng = np.random.default_rng(seed)
    count = int(n)
    half = section / 2.0
    sigma = noise_ratio * section

    z_true = rng.uniform(0.0, height, count)
    # 周长参数 s ~ U(0, 4*section)，按四条边分段映射到正方形边界
    s = rng.uniform(0.0, 4.0 * section, count)
    seg = np.minimum((s / section).astype(np.int64), 3)
    t = s / section - seg
    coord = -half + t * section
    x_local = np.where(seg == 0, coord, np.where(seg == 1, half, np.where(seg == 2, -coord, -half)))
    y_local = np.where(seg == 0, -half, np.where(seg == 1, coord, np.where(seg == 2, half, -coord)))

    # 刚性倾斜：截面整体随高度平移（断面不变形）
    x = x_local + tilt_ratio * z_true
    y = y_local

    # 各向独立高斯噪声（σ 相同），含 z 方向
    if sigma > 0.0:
        x = x + rng.normal(0.0, sigma, count)
        y = y + rng.normal(0.0, sigma, count)
        z = z_true + rng.normal(0.0, sigma, count)
    else:
        z = z_true

    # 遮挡：按未加噪的真实高程判定区间，只在该区间内丢弃
    in_band = (z_true >= z_lo) & (z_true <= z_hi)
    dropped = in_band & (rng.random(count) < occlusion_drop)
    keep = ~dropped

    out = np.stack([x[keep], y[keep], z[keep]], axis=1)
    return np.ascontiguousarray(out, dtype=np.float64)


def generate_pier_lattice(
    height: float = 10.0,
    section: float = 2.0,
    spacing: float = 0.02,
    tilt_ratio: float = 0.005,
) -> npt.NDArray[np.float64]:
    """生成**无噪声**的规则格网墩身表面点云（用于 SOR"不误删"单测）。

    与 ``generate_pier_point_cloud`` 不同：点严格落在四条侧表面的等间距格网上，
    没有任何随机抖动，因此每个点的 kNN 平均距离在内部几乎完全一致，
    SOR 的 mean + 2σ 阈值不应删除任何内部点（顶部/底部边界行除外，属物理边界）。
    """
    if height <= 0.0 or section <= 0.0 or spacing <= 0.0:
        raise ValueError("height/section/spacing 必须为正")
    perimeter = 4.0 * section
    n_per = int(round(perimeter / spacing))
    n_z = int(round(height / spacing))
    half = section / 2.0

    s = np.linspace(0.0, perimeter, n_per, endpoint=False)
    seg = np.minimum((s / section).astype(np.int64), 3)
    t = s / section - seg
    coord = -half + t * section
    x_edge = np.where(seg == 0, coord, np.where(seg == 1, half, np.where(seg == 2, -coord, -half)))
    y_edge = np.where(seg == 0, -half, np.where(seg == 1, coord, np.where(seg == 2, half, -coord)))
    z_levels = np.linspace(0.0, height, n_z, endpoint=False)

    xs = (x_edge[None, :] + tilt_ratio * z_levels[:, None]).ravel()
    ys = np.repeat(y_edge[None, :], n_z, axis=0).ravel()
    zs = np.repeat(z_levels[:, None], n_per, axis=1).ravel()
    return np.ascontiguousarray(np.stack([xs, ys, zs], axis=1), dtype=np.float64)


def remove_face(
    points: npt.NDArray[np.float64],
    *,
    axis: str = "y",
    sign: int = -1,
    section: float = 2.0,
    margin: float = 0.15,
    tilt_ratio: float = 0.005,
) -> npt.NDArray[np.float64]:
    """删除某一整面（模拟单侧遮挡），保留其余三面。

    面位置按刚性倾斜几何计算：x 面在 ``sign·section/2 + tilt_ratio·z``，
    y 面在 ``sign·section/2``；``margin`` 是该面法向的容差带（默认 15 cm ≈ 7.5σ，
    σ=20 mm 时足以把该面连噪声尾部一起删干净，避免残留点让"缺边"检测失效）。
    """
    if axis not in ("x", "y"):
        raise ValueError("axis 只能是 'x' 或 'y'")
    if sign not in (-1, 1):
        raise ValueError("sign 只能是 -1 或 1")
    if section <= 0.0 or margin < 0.0:
        raise ValueError("section 必须为正、margin 不能为负")
    src = np.asarray(points, dtype=np.float64)
    half = section / 2.0
    if axis == "x":
        face = sign * half + tilt_ratio * src[:, 2]
        coord = src[:, 0]
    else:
        face = np.full(src.shape[0], sign * half, dtype=np.float64)
        coord = src[:, 1]
    keep = np.abs(coord - face) > margin
    return np.ascontiguousarray(src[keep], dtype=np.float64)


def add_outliers(
    points: npt.NDArray[np.float64],
    ratio: float = 0.01,
    magnitude: float = 0.5,
    seed: int = 0,
) -> npt.NDArray[np.float64]:
    """把 ``ratio`` 比例的点替换为粗差点（沿随机方向偏移 ``magnitude`` 米）。

    模拟真实扫描的粗差/单站配准漂移：位置错但 z 不变。返回值是新数组，不改入参。
    """
    if not 0.0 <= ratio <= 1.0:
        raise ValueError("ratio 必须在 [0, 1]")
    src = np.asarray(points, dtype=np.float64)
    out = src.copy()
    count = int(src.shape[0])
    k = int(round(count * ratio))
    if k <= 0:
        return out
    rng = np.random.default_rng(seed)
    idx = rng.choice(count, size=k, replace=False)
    dirs = rng.normal(0.0, 1.0, (k, 3))
    norms = np.linalg.norm(dirs, axis=1)
    norms[norms == 0.0] = 1.0
    out[idx, :3] = src[idx, :3] + (dirs / norms[:, None]) * float(magnitude)
    return out


def generate_tunnel_section_synthetic(
    radius: float = 3.0,
    length: float = 10.0,
    n: int = 120_000,
    noise: float = 0.02,
    gap_deg: float = 20.0,
    trench_ratio: float = 0.06,
    seed: int = 0,
) -> npt.NDArray[np.float64]:
    """合成隧道断面点云（**半径 3 m 圆 + 缺口 + 电缆沟**，不含任何真实数据）。

    坐标约定：轴向 ``+x``；断面内 ``u = y``（水平）、``v = z``（竖直），圆心在
    ``(u, v) = (0, 0)``（即仰拱中心 / invert）上方 ``radius`` 处的上半圆。

    - 主体：``θ ∈ [0, π]`` 的上半圆弧，在拱顶（``θ = 90°``）留 ``gap_deg`` 的缺口，
      模拟衬砌缺失/遮挡；
    - 电缆沟：``u ∈ [0.8, 1.4] m``、``v ∈ [0.12, 0.40] m`` 的一条凸起带，落在圆内
      （到圆周的距离远大于 ``inlier_band``），是圆弧拟合必须裁掉的外点；
    - 噪声：``x/y/z`` 三向独立高斯，``σ = noise``（默认 2 cm）。

    返回 (N, 3) float64，未抽稀（调用方可直接喂 ``analyze_tunnel``）。
    """
    if radius <= 0.0 or length <= 0.0:
        raise ValueError("radius/length 必须为正")
    if n <= 0:
        raise ValueError("n 必须为正")
    if noise < 0.0:
        raise ValueError("noise 不能为负")
    if not 0.0 <= gap_deg < 180.0:
        raise ValueError("gap_deg 必须在 [0, 180)")
    if not 0.0 <= trench_ratio < 1.0:
        raise ValueError("trench_ratio 必须在 [0, 1)")

    rng = np.random.default_rng(seed)
    count = int(n)
    n_trench = int(round(count * trench_ratio))
    n_arc = count - n_trench

    theta = rng.uniform(0.0, np.pi, n_arc)
    if gap_deg > 0.0:
        half_gap = np.radians(gap_deg) / 2.0
        in_gap = np.abs(theta - np.pi / 2.0) <= half_gap
        # 缺口内的点重抽到缺口两侧，避免直接丢点造成样本量变化
        theta[in_gap] = rng.uniform(0.0, np.pi, int(np.count_nonzero(in_gap)))
        theta = np.where(
            np.abs(theta - np.pi / 2.0) <= half_gap,
            np.where(theta < np.pi / 2.0, np.pi / 2.0 - half_gap, np.pi / 2.0 + half_gap),
            theta,
        )

    u_arc = radius * np.cos(theta)
    v_arc = radius * np.sin(theta)

    u_trench = rng.uniform(0.8, 1.4, n_trench)
    v_trench = rng.uniform(0.12, 0.40, n_trench)

    x = rng.uniform(0.0, length, count)
    u = np.concatenate([u_arc, u_trench])
    v = np.concatenate([v_arc, v_trench])
    if noise > 0.0:
        x = x + rng.normal(0.0, noise, count)
        u = u + rng.normal(0.0, noise, count)
        v = v + rng.normal(0.0, noise, count)

    out = np.column_stack([x, u, v])
    return np.ascontiguousarray(out[rng.permutation(count)], dtype=np.float64)
