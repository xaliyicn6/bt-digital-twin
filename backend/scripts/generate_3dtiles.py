"""点云 → 3D Tiles (pnts) 转换脚本。

用标准库 + numpy + struct 手写 pnts 瓦片，不依赖 py3dtiles / PDAL。
pnts 是 3D Tiles 里最简单的点瓦片格式，50~100 万点的单文件渲染完全 OK。

为什么不用 py3dtiles / PDAL？
- py3dtiles 主版本停在 2020 年，5.0.0 源码用了 3.7+ 语法但只有 py3.6 的 wheel，
  装到 Python 3.11 venv 里也会 import 崩掉。
- PDAL 2.6 是 C++ 二进制，AGENTS.md 要求通过 pdal pipeline 子进程调用，
  Windows 上还得自己装 OSGeo4W，开发机没装的话根本跑不起来。
- 本仓库里点云规模都在百万级，手写 pnts 反而更可控、无外部依赖。

运行方式（本脚本只负责 PLY→pnts，LAS 抽稀由 build_local_datasets.py 编排）：
    uv run python scripts/generate_3dtiles.py \
        --input ../backend/var/local_tiles/tunnel.ply \
        --output-dir ../frontend/public/tiles/local/tunnel \
        --dataset-code tunnel --label "隧道（lunxiao-L）" \
        --source-file backend/tests/lunxiao-L.las

输入/输出均为**必填**：旧版默认把 WHU-TLS 演示样本写进 `frontend/public/tiles/tunnel/`，
该关联已作废（旧产物保留但标注 deprecated），脚本不再保留任何写死的旧源路径。

输出目录（本地产物，已在 .gitignore 内）：
    <output-dir>/{tileset.json, <dataset-code>.pnts, cloud-meta.json}
    ↑ Vite dev server 自动把 public/ 挂在根路径 /tiles/local/<dataset-code>/*，
    ↑ 前端 Cesium3DTileset.fromUrl('/tiles/local/<dataset-code>/tileset.json') 直接加载。

可选参数：
    --lon 114.31 --lat 30.52 --height 50    # 局部 ENU 原点的 WGS84 锚点（底图仅作背景）
    --max-points 200000                      # 点太多时按 stride 下采样
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

WGS84_A = 6_378_137.0  # semi-major axis (meter)
WGS84_F = 1 / 298.257_223_563
WGS84_B = WGS84_A * (1 - WGS84_F)  # semi-minor


# ---------------------------------------------------------------------------
# PLY 读取
# ---------------------------------------------------------------------------


def read_ply(path: Path) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    """读 binary_little_endian PLY。

    返回 (xyz, rgb, intensity)：
      xyz       shape=(N, 3) float32
      rgb       shape=(N, 3) uint8（原文件无颜色时为 None）
      intensity shape=(N,)  float32（原文件无 intensity/reflectance 时为 None）
    """
    data = path.read_bytes()
    header_end = data.find(b"end_header\n")
    if header_end < 0:
        raise RuntimeError(f"{path}: 找不到 end_header")

    header = data[:header_end].decode("ascii", errors="strict")
    body = data[header_end + len(b"end_header\n") :]

    # 解析 element / property
    vertex_count = 0
    for line in header.splitlines():
        parts = line.split()
        if parts[0] == "element" and parts[1] == "vertex":
            vertex_count = int(parts[2])

    if vertex_count == 0:
        raise RuntimeError(f"{path}: header 里没有 vertex element")

    # 重新解析：prop_order 存属性名，prop_types 存类型字符串
    type_map = {
        "float": "<f4",
        "float32": "<f4",
        "double": "<f8",
        "float64": "<f8",
        "uchar": "<u1",
        "uint8": "<u1",
        "char": "<i1",
        "int8": "<i1",
        "ushort": "<u2",
        "uint16": "<u2",
        "short": "<i2",
        "uint": "<u4",
        "uint32": "<u4",
        "int": "<i4",
    }
    prop_names: list[str] = []
    prop_types: list[str] = []
    for line in header.splitlines():
        parts = line.split()
        if parts[0] == "property":
            prop_types.append(parts[1])
            prop_names.append(parts[2])
    formats = [type_map[t] for t in prop_types]
    dtype = np.dtype({"names": prop_names, "formats": formats})

    raw = np.frombuffer(body, dtype=dtype, count=vertex_count)
    pts = np.column_stack([raw["x"], raw["y"], raw["z"]]).astype(np.float32)

    # 颜色：PLY 里可能是 red/green/blue，也可能是 r/g/b
    rgb: np.ndarray | None = None
    rgb_names = next(
        (n for n in (("red", "green", "blue"), ("r", "g", "b")) if all(x in prop_names for x in n)),
        None,
    )
    if rgb_names is not None:
        chans = [raw[c].astype(np.float32) for c in rgb_names]
        peak = max(float(ch.max()) for ch in chans)
        # LAS 的 RGB 是 16 位：数值 >255 说明是 0~65535 口径，压到 0~255
        if peak > 255.0:
            chans = [ch / 257.0 for ch in chans]
        elif peak <= 1.0:
            # float 类型可能是 0~1
            chans = [ch * 255.0 for ch in chans]
        # 全 0 视为“无有效颜色”（很多 TLS 数据 RGB 字段是空的），
        # 交给调用方合成基色，避免整份点云渲染成纯黑。
        rgb = None if peak <= 0.0 else np.column_stack(chans).clip(0, 255).astype(np.uint8)

    # 强度：PLY 里可能是 intensity / reflectance（intensity 优先）
    intensity: np.ndarray | None = None
    for key in ("intensity", "reflectance"):
        if key in prop_names:
            intensity = raw[key].astype(np.float32)
            break

    return pts, rgb, intensity


# ---------------------------------------------------------------------------
# 缺失属性的模拟生成（原文件无 RGB / intensity 时使用）
# ---------------------------------------------------------------------------


def _normalize01(values: np.ndarray) -> np.ndarray:
    vmin = float(values.min())
    vmax = float(values.max())
    span = max(vmax - vmin, 1e-6)
    return ((values - vmin) / span).clip(0.0, 1.0).astype(np.float32)


def synthesize_intensity(
    points: np.ndarray,
    seed: int = 20261105,
    axis_span: tuple[float, float] | None = None,
) -> np.ndarray:
    """生成 0~1 的模拟强度：确定性伪随机 + 沿隧道轴向的正弦起伏。

    用固定 seed 保证同一份数据每次切片结果一致（便于对比截图与回归）。
    ``axis_span`` 传入全局 Y 范围时，相位按整条隧道归一化，
    局部精载瓦片与全局分块的颜色才能接得上（不会在小范围内自归一化跳变）。
    """
    rng = np.random.default_rng(seed)
    noise = rng.random(len(points)).astype(np.float32)
    axis = points[:, 1]
    lo, hi = axis_span if axis_span is not None else (float(axis.min()), float(axis.max()))
    span = max(hi - lo, 1e-6)
    wave = 0.5 + 0.5 * np.sin((axis - lo) / span * 6 * math.pi)
    return _normalize01(0.55 * noise + 0.45 * wave.astype(np.float32))


def synthesize_rgb(intensity: np.ndarray) -> np.ndarray:
    """生成"混凝土灰"基色（略偏暖），亮度随强度变化。"""
    base = 105.0 + 100.0 * intensity
    rgb = np.column_stack([base * 1.02, base * 0.99, base * 0.93])
    return rgb.clip(0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# WGS84 → ECEF + ENU 旋转矩阵（纯 numpy 实现）
# ---------------------------------------------------------------------------


def geodetic_to_ecef(lon_deg: float, lat_deg: float, height: float) -> np.ndarray:
    """lon/lat 是度，height 是 WGS84 椭球高（米）。返回 ECEF (3,)。"""
    lon = math.radians(lon_deg)
    lat = math.radians(lat_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)

    # 卯酉圈半径
    n_radius = WGS84_A / math.sqrt(1 - WGS84_F * (2 - WGS84_F) * sin_lat * sin_lat)

    x = (n_radius + height) * cos_lat * cos_lon
    y = (n_radius + height) * cos_lat * sin_lon
    z = (n_radius * (1 - WGS84_F) ** 2 + height) * sin_lat
    return np.array([x, y, z], dtype=np.float64)


def enu_to_ecef_frame(lon_deg: float, lat_deg: float) -> np.ndarray:
    """3x3 ENU→ECEF 旋转矩阵（列向量 = east / north / up）。"""
    lon = math.radians(lon_deg)
    lat = math.radians(lat_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)

    # East, North, Up 三列（ECEF 坐标系下的单位向量）
    east = np.array([-sin_lon, cos_lon, 0.0], dtype=np.float64)
    north = np.array([-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat], dtype=np.float64)
    up = np.array([cos_lat * cos_lon, cos_lat * sin_lon, sin_lat], dtype=np.float64)

    return np.column_stack([east, north, up])  # (3, 3)


def local_to_ecef_transform(
    center_lon: float,
    center_lat: float,
    center_height: float,
) -> np.ndarray:
    """构造 3D Tiles 的 4x4 transform 矩阵（列主序）。

    语义：P_ecef = transform @ P_local（局部坐标是 ENU 坐标）

    transform = [ R  t ]    其中 R = ENU→ECEF 旋转矩阵，t = ECEF_origin
                [ 0  1 ]

    注：局部坐标已在主流程中减去几何中心（对齐到 ENU 原点），
    所以这里直接用 ECEF_origin 作为平移，不要再扣一遍 local_center。
    """
    ecef_origin = geodetic_to_ecef(center_lon, center_lat, center_height)
    rot = enu_to_ecef_frame(center_lon, center_lat)

    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = rot
    transform[:3, 3] = ecef_origin
    return transform


# ---------------------------------------------------------------------------
# pnts 二进制打包
# ---------------------------------------------------------------------------


def _align8(n: int) -> int:
    return (n + 7) & ~7


def write_pnts(
    path: Path,
    points: np.ndarray,  # (N, 3) float32，局部中心已减掉
    colors: np.ndarray,  # (N, 3) uint8，逐点 RGB
    intensity: np.ndarray,  # (N,) float32，归一化 0~1
    ecef_center: np.ndarray,  # (3,) float64
) -> tuple[int, float, float, float]:
    """写 pnts 瓦片（**新版 3D Tiles Next Feature Table Binary 格式**）。

    Cesium 1.107+（我们用 1.119）已经不再识别旧版 FtJsonHeader/BtJsonHeader
    + 统一 Body 的布局。新版格式见 Cesium 源码 PntsParser.parse：

        magic(4)          "pnts"
        version(4)        1
        byteLength(4)     整个文件长度
        FtJsonLen(4)      Feature Table JSON 字节数
        FtBinaryLen(4)    Feature Table Binary 字节数
        BtJsonLen(4)      Batch Table JSON 字节数（可为 0）
        BtBinaryLen(4)    Batch Table Binary 字节数（可为 0）
        FtJson            UTF-8 JSON
        FtBinary          纯二进制，按 JSON 里 byteOffset 访问
        BtJson            UTF-8 JSON
        BtBinary          纯二进制，按 JSON 里 byteOffset 访问

    FeatureTable JSON 字段名（新版）：
        POINTS_LENGTH   整数（直接内联）
        POSITION        {"byteOffset": 0}  → FtBinary 里的 float32*3*N
        RGB             {"byteOffset": 12*N} → FtBinary 里的 uint8*3*N
        BATCH_LENGTH    整数（= POINTS_LENGTH，逐点一个 batch，供样式逐点着色）

    BatchTable（逐点属性，前端按高程/强度着色与点选量测用）：
        X / Y / Z       float32，ENU 局部坐标（米）
        Intensity       float32，0~1

    返回 (POINTS_LENGTH, xmin_local, ymin_local, zmin_local)。
    """
    n_points = len(points)

    # --- Feature Table JSON ---
    # 注意：不要写 RTC_CENTER！
    # Cesium 新版会做 final_pos = tile.transform @ (position + rtcCenter)
    # 我们的 position 已经是 ENU 局部坐标，tile.transform 已经处理了 ENU→ECEF，
    # 再加一个 ECEF 量级的 RTC_CENTER 会把点云渲染到完全错误的位置。
    # 需要精度扩展时才用 RTC_CENTER（position 存相对 RTC_CENTER 的偏移）。
    # POSITION 从 FeatBinary 起始（byteOffset=0）；RGB 紧随其后
    pos_len = 12 * n_points
    rgb_byte_offset = pos_len

    ft_obj = {
        "BATCH_LENGTH": n_points,
        "POINTS_LENGTH": n_points,
        "POSITION": {"byteOffset": 0},
        "RGB": {"byteOffset": rgb_byte_offset},
    }
    ft_json_bytes = json.dumps(ft_obj, separators=(",", ":")).encode("utf-8")
    ft_json_len = len(ft_json_bytes)
    # 3D Tiles 规范：FtJson 后 FtBinary 起始必须 8 字节对齐。
    # Cesium PntsParser 从 FtJson_start + ft_json_len 直接开始读 FtBinary，
    # 不会自己加 padding。所以我们把空格 padding 直接加到 FtJson 末尾，
    # 这样 header 里声明的 ft_json_len 就包含了 padding，
    # FtJson_start + ft_json_len 自然就是 8 对齐的，Cesium 和我们写的文件位置一致。
    header_len = 28
    ft_json_start = header_len
    total_after_ftjson = ft_json_start + ft_json_len
    ft_json_pad = _align8(total_after_ftjson) - total_after_ftjson
    ft_json_bytes += b" " * ft_json_pad  # 空格是合法 JSON 空白符
    ft_json_len = len(ft_json_bytes)

    # --- Feature Table Binary ---
    # POSITION: float32 * 3 * N，byteOffset=0（相对于 FtBinary 起始）
    pos_bytes = points.astype(np.float32).tobytes()
    pos_len = len(pos_bytes)  # = 12 * N
    # RGB: uint8 * 3 * N，紧接在 POSITION 后面
    rgb_bytes = np.ascontiguousarray(colors, dtype=np.uint8).tobytes()
    rgb_byte_offset = pos_len  # RGB 在 FtBinary 内的偏移
    ft_binary = pos_bytes + rgb_bytes
    ft_binary_len = len(ft_binary)

    # --- Batch Table（逐点属性） ---
    # 4 个 float32 属性连续排布；JSON 里用 {byteOffset, componentType, type} 引用
    attr_names = ("X", "Y", "Z", "Intensity")
    attr_arrays = {
        "X": points[:, 0].astype(np.float32),
        "Y": points[:, 1].astype(np.float32),
        "Z": points[:, 2].astype(np.float32),
        "Intensity": np.asarray(intensity, dtype=np.float32),
    }
    bt_obj: dict[str, Any] = {}
    bt_binary_parts: list[bytes] = []
    cursor = 0
    for name in attr_names:
        arr = attr_arrays[name]
        bt_obj[name] = {
            "byteOffset": cursor,
            "componentType": "FLOAT",
            "type": "SCALAR",
        }
        chunk = arr.tobytes()
        bt_binary_parts.append(chunk)
        cursor += len(chunk)
    bt_binary = b"".join(bt_binary_parts)
    bt_binary_len = len(bt_binary)

    bt_json_bytes = json.dumps(bt_obj, separators=(",", ":")).encode("utf-8")
    bt_json_len = len(bt_json_bytes)

    # --- 布局 ---
    ft_binary_start = ft_json_start + ft_json_len
    bt_json_start = ft_binary_start + ft_binary_len
    # BtJson 同样把 padding 写进自身，保证 BtBinary 起始 8 字节对齐
    total_after_btjson = bt_json_start + bt_json_len
    bt_json_pad = _align8(total_after_btjson) - total_after_btjson
    bt_json_bytes += b" " * bt_json_pad
    bt_json_len = len(bt_json_bytes)
    bt_binary_start = bt_json_start + bt_json_len
    total_len = bt_binary_start + bt_binary_len

    # --- 写 ---
    buf = bytearray(total_len)

    # [0:12] Header: magic + version + byteLength
    buf[0:4] = b"pnts"
    struct.pack_into("<II", buf, 4, 1, total_len)

    # [12:28] 四个长度
    struct.pack_into("<IIII", buf, 12, ft_json_len, ft_binary_len, bt_json_len, bt_binary_len)

    # FtJson（末尾已带空格 padding）
    buf[ft_json_start : ft_json_start + ft_json_len] = ft_json_bytes

    # FtBinary（紧接 FtJson 结束，自然 8 对齐）
    buf[ft_binary_start : ft_binary_start + ft_binary_len] = ft_binary

    # BtJson（末尾已带空格 padding）
    buf[bt_json_start : bt_json_start + bt_json_len] = bt_json_bytes

    # BtBinary（紧接 BtJson 结束，8 对齐）
    buf[bt_binary_start : bt_binary_start + bt_binary_len] = bt_binary

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf)
    return (
        n_points,
        float(points[:, 0].min()),
        float(points[:, 1].min()),
        float(points[:, 2].min()),
    )


def _region_from_bounds(
    transform: np.ndarray, pts_min: np.ndarray, pts_max: np.ndarray
) -> list[float]:
    """局部包围盒 → 3D Tiles region（弧度/米）。"""
    pmin = np.asarray(pts_min, dtype=np.float64)
    pmax = np.asarray(pts_max, dtype=np.float64)
    corners = np.array(
        [
            [pmin[0], pmin[1], pmin[2]],
            [pmax[0], pmin[1], pmin[2]],
            [pmin[0], pmax[1], pmin[2]],
            [pmax[0], pmax[1], pmin[2]],
            [pmin[0], pmin[1], pmax[2]],
            [pmax[0], pmin[1], pmax[2]],
            [pmin[0], pmax[1], pmax[2]],
            [pmax[0], pmax[1], pmax[2]],
        ],
        dtype=np.float64,
    )
    ecef = corners @ transform[:3, :3].T + transform[:3, 3]
    geods = np.array([ecef_to_geodetic(p) for p in ecef], dtype=np.float64)
    lons = geods[:, 0]
    lats = geods[:, 1]
    heights = geods[:, 2]
    return [
        float(math.radians(lons.min())),
        float(math.radians(lats.min())),
        float(math.radians(lons.max())),
        float(math.radians(lats.max())),
        float(heights.min() - 2),
        float(heights.max() + 2),
    ]


def write_tileset_json(
    path: Path,
    pnts_uri: str,
    transform: np.ndarray,
    center_lon: float,
    center_lat: float,
    center_height: float,
    pts_min: np.ndarray,
    pts_max: np.ndarray,
    ecef_center: np.ndarray,
) -> None:
    """写单文件 tileset.json（边界体用 region，ECEF bbox 转经纬度）。

    root.geometricError 给 1e7：单瓦片没有子节点，这个大值保证任何视角下
    根瓦片都会被选中/加载（Cesium 的 SSE<=maxSSE 时不会再往下细化）。
    """
    region = _region_from_bounds(transform, pts_min, pts_max)
    transform_list = transform.flatten("F").tolist()
    giant_error = 10_000_000.0

    tileset: dict[str, Any] = {
        "asset": {"version": "1.0", "gltfUpAxis": "Y"},
        "geometricError": giant_error,
        "root": {
            "transform": transform_list,
            "geometricError": giant_error,
            "boundingVolume": {"region": region},
            "content": {"uri": pnts_uri},
            "refine": "ADD",
        },
        "extensionsUsed": [],
        "extensionsRequired": [],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tileset, indent=2), encoding="utf-8")


def write_tileset_json_lod(
    path: Path,
    overview_uri: str,
    children: list[dict[str, Any]],
    transform: np.ndarray,
    pts_min: np.ndarray,
    pts_max: np.ndarray,
    root_geometric_error: float,
) -> None:
    """写「概览根瓦片 + 沿轴分块」的 tileset.json。

    结构（refine=ADD，父层不会在子块加载期间消失，避免黑屏）：

        root  overview.pnts  geometricError = root_geometric_error
        ├── chunk-00.pnts   geometricError = 0（叶子）
        └── ...

    取舍：
    - 根瓦片 geometricError 决定“离多远只显示概览”。取 4 m 时，
      约 200 m 内（SSE 超过 16）才细化到分块；相机在 2 km 外飞入时
      只会下载概览瓦片（约 3 MB），不会一次性拉全部 30 MB。
    - 子块 geometricError = 0，进入隧道后只有视锥内的 1~2 个分块被加载。
    - 子块 boundingVolume 用 box（局部 ENU 米，继承 root.transform）；
      region 是绝对地理坐标，逐块算容易和 transform 叠错，box 更直接。
    - 不写 RTC_CENTER：所有块的 POSITION 都是同一套 ENU 局部坐标，
      由 root.transform 统一映射到 ECEF（RTC_CENTER 与 transform 语义会叠加）。
    """
    region = _region_from_bounds(transform, pts_min, pts_max)
    tileset: dict[str, Any] = {
        "asset": {"version": "1.0", "gltfUpAxis": "Y"},
        # tileset 级 geometricError 需要 >= root.geometricError；
        # 它只在“本 tileset 作为别人的子节点”时参与计算，根节点遍历用 root 的值。
        "geometricError": 10_000_000.0,
        "root": {
            "transform": transform.flatten("F").tolist(),
            "geometricError": float(root_geometric_error),
            "boundingVolume": {"region": region},
            "content": {"uri": overview_uri},
            "refine": "ADD",
            "children": [
                {
                    "boundingVolume": {"box": child["box"]},
                    "geometricError": 0.0,
                    "content": {"uri": child["uri"]},
                }
                for child in children
            ],
        },
        "extensionsUsed": [],
        "extensionsRequired": [],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tileset, indent=2), encoding="utf-8")


def ecef_to_geodetic(ecef: np.ndarray) -> np.ndarray:
    """Bowring 迭代法 ECEF → (lon, lat, height)。

    迭代式：φ_{k+1} = atan2(z + e² N(φ_k) sin φ_k, p)

    注意是 **一次** sin φ，不是 sin²φ！写成 sin²φ 会收敛到完全错误的纬度
    （本工程曾经踩过这个坑——收敛到 30.437° 而不是 30.52°）。
    """
    x, y, z = ecef
    lon = math.atan2(y, x)
    p = math.hypot(x, y)

    phi = math.atan2(z, p)  # 初始猜测
    for _ in range(50):
        sin_phi = math.sin(phi)
        n_radius = WGS84_A / math.sqrt(1 - WGS84_F * (2 - WGS84_F) * sin_phi * sin_phi)
        phi_new = math.atan2(z + WGS84_F * (2 - WGS84_F) * n_radius * sin_phi, p)
        if abs(phi_new - phi) < 1e-14:
            phi = phi_new
            break
        phi = phi_new

    sin_phi = math.sin(phi)
    n_radius = WGS84_A / math.sqrt(1 - WGS84_F * (2 - WGS84_F) * sin_phi * sin_phi)
    height = p / math.cos(phi) - n_radius
    return np.array([math.degrees(lon), math.degrees(phi), height], dtype=np.float64)


# ---------------------------------------------------------------------------
# 元信息（前端着色范围 / 侵限报警点）
# ---------------------------------------------------------------------------


def principal_axes(points: np.ndarray) -> dict[str, Any]:
    """水平 PCA 主轴（漫游入点、报警点分段、分块共用同一套口径）。

    只用轴对齐包围盒会把朝向判错：桥墩/结构面是薄片，主轴常与坐标轴偏 20° 以上，
    沿错误方向走几步就出片、正前方没点。PCA 主轴 = 点云真正延展最长的方向。
    """
    qx2, qx98 = (float(v) for v in np.quantile(points[:, 0], [0.02, 0.98]))
    qy2, qy98 = (float(v) for v in np.quantile(points[:, 1], [0.02, 0.98]))
    cx = (qx2 + qx98) / 2.0
    cy = (qy2 + qy98) / 2.0
    sel = (
        (points[:, 0] >= qx2)
        & (points[:, 0] <= qx98)
        & (points[:, 1] >= qy2)
        & (points[:, 1] <= qy98)
    )
    if not sel.any():
        sel = np.ones(len(points), dtype=bool)
    rel = points[sel][:, :2] - np.array([cx, cy])
    _, eig_vecs = np.linalg.eigh(np.cov(rel.T))
    major = eig_vecs[:, -1]  # eigh 升序，最后一列是能量最大的主轴
    minor = eig_vecs[:, 0]
    heading_deg = math.degrees(math.atan2(float(major[0]), float(major[1]))) % 360
    return {
        "center": np.array([cx, cy], dtype=np.float64),
        "major": major,
        "minor": minor,
        "headingDeg": heading_deg,
        "halfLength": float(np.quantile(np.abs(rel @ major), 0.98)),
        "halfWidth": float(np.quantile(np.abs(rel @ minor), 0.98)),
    }


def alarm_eye(
    points: np.ndarray,
    u_all: np.ndarray,
    v_all: np.ndarray,
    center_xy: np.ndarray,
    major: np.ndarray,
    minor: np.ndarray,
    alarm_xyz: np.ndarray,
    seg_lo: float,
    seg_hi: float,
) -> tuple[dict[str, Any], float]:
    """算报警点的就地漫游入点（ENU 局部米，z 已含 1.6 m 眼高）。

    做法：取报警点 ±6 m 的横断面邻域，底面 = 邻域 Z 5% 分位；横向从报警点
    往该断面的横向中位数挪 1.0~2.5 m（报警点常在壁面/底部，直接站上去会卡墙），
    再检查与最近点的三维距离，不足 0.8 m 就继续往洞内推。朝向取邻域水平主轴。
    """
    rel = np.array([alarm_xyz[0], alarm_xyz[1]], dtype=np.float64) - center_xy
    u_a = float(rel @ major)
    v_a = float(rel @ minor)
    near = (np.abs(u_all - u_a) <= 6.0) & (np.abs(v_all - v_a) <= 12.0)
    if int(near.sum()) < 50:
        near = (u_all >= seg_lo) & (u_all <= seg_hi)
    pts_near = points[near]
    floor_z = float(np.quantile(pts_near[:, 2], 0.05))
    v_center = float(np.median(v_all[near]))
    shift = float(np.clip(v_center - v_a, -2.5, 2.5))
    if abs(shift) < 1.0:
        shift = 1.0 if v_center >= v_a else -1.0

    # 邻域水平主轴作为朝向（符号对齐全局主轴，保证朝隧道延伸方向看）
    xy = pts_near[:, :2] - pts_near[:, :2].mean(axis=0)
    _, vec = np.linalg.eigh(np.cov(xy.T))
    tangent = vec[:, -1]
    if float(tangent @ major) < 0:
        tangent = -tangent
    heading = math.degrees(math.atan2(float(tangent[0]), float(tangent[1]))) % 360

    eye_z = floor_z + 1.6
    direction = 1.0 if shift > 0 else -1.0
    x = y = 0.0
    for extra in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0):
        v_try = v_a + shift + direction * extra
        # 不要越过断面横向中心
        v_try = min(v_try, v_center) if direction > 0 else max(v_try, v_center)
        x = float(center_xy[0] + major[0] * u_a + minor[0] * v_try)
        y = float(center_xy[1] + major[1] * u_a + minor[1] * v_try)
        gap = float(np.min(np.linalg.norm(pts_near - np.array([x, y, eye_z]), axis=1)))
        if gap >= 0.8:
            break
    return (
        {
            "x": round(x, 3),
            "y": round(y, 3),
            "z": round(eye_z, 3),
            "headingDeg": round(heading, 1),
            "floorZ": round(floor_z, 3),
        },
        floor_z,
    )


def build_meta(
    path: Path,
    points: np.ndarray,
    intensity: np.ndarray,
    center_lon: float,
    center_lat: float,
    center_height: float,
    *,
    dataset_code: str,
    label: str,
    source_file: str,
    source_points: int,
    crs: str,
    crs_note: str,
    local_center: np.ndarray,
    bounds_min: np.ndarray,
    bounds_max: np.ndarray,
    alarm_count: int = 3,
) -> dict[str, Any]:
    """写 cloud-meta.json。

    前端着色需要知道高程/强度的真实范围（不能把 -3~5 m 硬编码成 0~1），
    报警点也需要后端算（前端没有原始点云）。两者都放在这个独立小文件里。

    报警点取法：沿点云主轴（PCA）三等分，每段取 Z 最低 1% 点里离中位点最近的真实点，
    属于“区段最低点”示例口径，**不是** CLEARANCE 侵限结论（示例偏移量是写死的）。

    每个报警点额外算一个 `eye`（就地漫游入点）：从报警点沿断面横向往洞内挪 1~2.5 m，
    眼高 = 该处点云底面（邻域 Z 5% 分位）+ 1.6 m，朝向取邻域水平主轴。这样点击
    “就地内部漫游”不会一开始就贴在壁面上或站在结构里。

    viewpoint（第一人称漫游入点）：不能直接用 center —— center 是 min/max 包围盒中心，
    少量离群点散布到几百米外就会把包围盒拉大，原点可能落在点云主体之外的空隙，
    前端进入漫游后只会看到地形和天空。这里用 2%~98% 分位圈出点云主体，
    取主体水平中心、主体底面（2% 分位 Z）与长轴朝向，保证视线前方确实有点。

    注意不用 tileset.json 的 extras 传这些信息：Cesium 1.119 的
    Cesium3DTileset 没有把 tilesetJson.extras 挂到实例上（源码里搜不到
    this.extras = ...），读不到。独立文件最稳。
    """
    z = points[:, 2]
    axes = principal_axes(points)
    major = axes["major"]
    minor = axes["minor"]
    center_xy = axes["center"]
    heading_deg = axes["headingDeg"]

    rel_xy = points[:, :2] - center_xy
    u_all = rel_xy @ major
    v_all = rel_xy @ minor

    # ---- 报警点（示例口径）：沿主轴三等分，取每段最低点 ----
    alarms: list[dict[str, Any]] = []
    u_min = float(u_all.min())
    span_u = max(float(u_all.max()) - u_min, 1e-6)
    for i in range(alarm_count):
        lo = u_min + span_u * i / alarm_count
        hi = u_min + span_u * (i + 1) / alarm_count
        seg = (u_all >= lo) & (u_all <= hi)
        if not seg.any():
            seg = np.ones(len(points), dtype=bool)
        seg_z = z[seg]
        # 该段的 Z 最低 1%，取中位点避免被单点离群值带偏
        bottom = seg & (z <= float(np.quantile(seg_z, 0.01)))
        if not bottom.any():
            bottom = seg
        bx, by, bz = (float(v) for v in np.median(points[bottom], axis=0))
        # 取离中位点最近的真实点，保证报警坐标就是点云里的点（不是插值出来的）
        idxs = np.flatnonzero(bottom)
        d2 = (points[idxs, 0] - bx) ** 2 + (points[idxs, 1] - by) ** 2 + (points[idxs, 2] - bz) ** 2
        alarm_xyz = points[idxs[int(np.argmin(d2))]]
        eye, floor_z = alarm_eye(points, u_all, v_all, center_xy, major, minor, alarm_xyz, lo, hi)
        alarms.append(
            {
                "id": f"alarm-{i + 1}",
                "label": f"疑似侵限点 #{i + 1}",
                "x": round(float(alarm_xyz[0]), 3),
                "y": round(float(alarm_xyz[1]), 3),
                "z": round(float(alarm_xyz[2]), 3),
                "offsetMm": 15,  # 示例偏移量，与弹窗文案一起标注为示例口径
                "source": "SEGMENT_LOW_POINT",
                "eye": eye,
            }
        )
        print(f"       报警点 #{i + 1}: 底面 {floor_z:.2f} m, 入点 {eye}")

    # ---- 漫游入点：按 2%~98% 分位圈出点云主体，再用水平 PCA 求主轴 ----
    # 只用轴对齐包围盒会把朝向判错：桥墩/结构面是薄片，主轴常与坐标轴偏 20° 以上，
    # 沿错误方向走几步就出片、正前方没点。PCA 主轴 = 点云真正延展最长的方向。
    half_len = float(axes["halfLength"])
    half_wid = float(axes["halfWidth"])
    # 入点放在主轴靠后 60% 的位置、朝主轴正向：正前方有整条主体，
    # 同时仍在主体内部（不是站在点云外面的空隙里往里看）。
    start_offset = -0.6 * half_len
    viewpoint = {
        "x": round(float(center_xy[0] + major[0] * start_offset), 2),
        "y": round(float(center_xy[1] + major[1] * start_offset), 2),
        # 主体底面（不是 zMin：zMin 可能来自离群点，会把眼高拉到主体下方）
        "zBottom": round(float(np.quantile(z, 0.02)), 2),
        "headingDeg": round(heading_deg, 1),
        # 漫游边界（椭圆，以主体中心为原点、沿主轴/法向），
        # 圆形容不下一张薄片：横向半径过大会让人 3 m 外就走出点云。
        "centerX": round(float(center_xy[0]), 2),
        "centerY": round(float(center_xy[1]), 2),
        "majorHeadingDeg": round(heading_deg, 1),
        "halfLength": round(max(half_len, 2.0), 2),
        "halfWidth": round(max(half_wid, 1.0), 2),
        # 旧前端兼容字段（无椭圆字段时按圆的 radius 限制）
        "radius": round(max(half_len * 1.2, 4.0), 1),
    }

    meta: dict[str, Any] = {
        "datasetCode": dataset_code,
        "label": label,
        "sourceFile": source_file,
        "sourcePoints": int(source_points),
        "renderedPoints": int(len(points)),
        # 坐标系：用户未提供投影带号/中央子午线，按局部工程坐标处理。
        # TODO(C-05)：拿到真实 CRS 后改这里（以及 build_local_datasets.py 的 ANCHOR_*），
        # 由 pyproj 反算锚点 lon/lat，并把 crs 换成真实投影带（如 CGCS2000 / 3-degree GK）。
        "crs": crs,
        "crsNote": crs_note,
        "deprecated": False,
        # pnts 里的局部坐标 = 源坐标 - localCenter（x/y 为工程坐标，z 为原始高程）
        "localCenter": {
            "x": round(float(local_center[0]), 3),
            "y": round(float(local_center[1]), 3),
            "z": round(float(local_center[2]), 3),
        },
        "bounds": {
            "min": {
                "x": round(float(bounds_min[0]), 3),
                "y": round(float(bounds_min[1]), 3),
                "z": round(float(bounds_min[2]), 3),
            },
            "max": {
                "x": round(float(bounds_max[0]), 3),
                "y": round(float(bounds_max[1]), 3),
                "z": round(float(bounds_max[2]), 3),
            },
        },
        "pointCount": int(len(points)),
        "center": {"lon": center_lon, "lat": center_lat, "height": center_height},
        "viewpoint": viewpoint,
        "zMin": float(z.min()),
        "zMax": float(z.max()),
        "intensityMin": float(np.min(intensity)),
        "intensityMax": float(np.max(intensity)),
        # 报警点按点云最低点派生，是“相对拟合轮廓”的示例点位，
        # 不是设计限界侵限结论；前端弹窗必须保留该口径标注。
        "alarms": alarms,
        "alarmSource": "SEGMENT_LOW_POINT",
        "alarmsNote": (
            "示例口径 · 由各区段点云最低 1% 点派生，非 CLEARANCE 侵限结论；"
            "“就地内部漫游”入点位于该报警点附近的实测点云内（眼高 1.6 m）"
        ),
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="点云 PLY → 3D Tiles (pnts)")
    ap.add_argument("--input", type=Path, required=True, help="输入 PLY 文件路径")
    ap.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="输出目录（写入 tileset.json + <dataset-code>.pnts + cloud-meta.json）",
    )
    ap.add_argument("--dataset-code", required=True, help="数据集标识（tunnel / bridge）")
    ap.add_argument("--label", required=True, help="数据集展示名（中文）")
    ap.add_argument("--source-file", default="", help="源文件路径（写入 meta 供追溯）")
    ap.add_argument(
        "--source-points",
        type=int,
        default=0,
        help="源文件原始点数（写入 meta；0 表示未知）",
    )
    ap.add_argument(
        "--crs",
        default="LOCAL_ENGINEERING",
        help="坐标系标记；默认局部工程坐标（未做大地基准转换）",
    )
    ap.add_argument(
        "--crs-note",
        default="局部工程坐标 · 未做大地基准转换；底图仅作背景，不声明位置精度",
        help="坐标系说明（写入 meta 与前端展示）",
    )
    ap.add_argument("--lon", type=float, default=120.3826, help="局部 ENU 原点的经度锚点")
    ap.add_argument("--lat", type=float, default=36.0671, help="局部 ENU 原点的纬度锚点")
    ap.add_argument("--height", type=float, default=0.0, help="原点 WGS84 椭球高（米）")
    ap.add_argument(
        "--max-points",
        type=int,
        default=1_200_000,
        help="超过这个点数时自动下采样（默认保留约 100~120 万点）",
    )
    ap.add_argument(
        "--chunk-length",
        type=float,
        default=0.0,
        help=(
            "沿主轴分块长度（米）；0 = 单文件瓦片（桥墩用）。"
            "隧道这类 1 km 级狭长点云建议 120~200，配合概览层可显著降低首屏体积"
        ),
    )
    ap.add_argument(
        "--overview-points",
        type=int,
        default=120_000,
        help="分块模式下概览根瓦片的点数（默认 12 万，约 3 MB）",
    )
    ap.add_argument(
        "--root-geometric-error",
        type=float,
        default=4.0,
        help=(
            "分块模式下根瓦片（概览）的 geometricError（米）。"
            "相机离点云约 200 m 内才细化到分块；太小会一进场景就拉全部分块"
        ),
    )
    args = ap.parse_args()

    in_path = args.input.resolve()
    out_dir = args.output_dir.resolve()
    print(f"[1/4] 读取 PLY: {in_path}")
    pts, rgb, intensity = read_ply(in_path)
    print(f"       点数: {len(pts)}")
    print(f"       min: {pts.min(axis=0)}")
    print(f"       max: {pts.max(axis=0)}")

    if rgb is None:
        print("       原文件无有效 RGB → 按强度合成“混凝土灰”基色")
    else:
        print("       原文件含 RGB → 使用真彩")
    if intensity is None:
        print("       原文件无 intensity/reflectance → 生成确定性模拟强度")
        intensity = synthesize_intensity(pts)
    else:
        intensity = _normalize01(intensity)
        if float(intensity.max() - intensity.min()) < 1e-6:
            # 真实 TLS 数据常见 intensity 恒为 0：当作缺失处理，否则强度着色会是纯色
            print("       intensity 恒为常量 → 生成确定性模拟强度")
            intensity = synthesize_intensity(pts)
    if rgb is None:
        rgb = synthesize_rgb(intensity)

    bounds_min = pts.min(axis=0).astype(np.float64)
    bounds_max = pts.max(axis=0).astype(np.float64)
    source_points = args.source_points if args.source_points > 0 else len(pts)

    if len(pts) > args.max_points:
        stride = len(pts) // args.max_points + 1
        pts = pts[::stride]
        rgb = rgb[::stride]
        intensity = intensity[::stride]
        print(f"[2/4] 下采样 stride={stride} → {len(pts)} 点")
    else:
        print("[2/4] 点数未超过阈值，跳过采样")

    # 中心：数据的几何中心（局部原点附近）
    local_center = (pts.min(axis=0) + pts.max(axis=0)) / 2.0
    pts_centered = pts - local_center
    print(f"[3/4] 局部中心 (减去): {local_center}")
    print(f"       变换矩阵 ECEF 原点: ({args.lon}°E, {args.lat}°N, {args.height}m)")

    transform = local_to_ecef_transform(
        center_lon=args.lon,
        center_lat=args.lat,
        center_height=args.height,
    )

    ecef_center = geodetic_to_ecef(args.lon, args.lat, args.height)
    print(f"       ECEF 原点（写入 root.transform，不写 RTC_CENTER）: {ecef_center}")

    out_dir.mkdir(parents=True, exist_ok=True)
    ts_path = out_dir / "tileset.json"
    pts_min = pts_centered.min(axis=0)
    pts_max = pts_centered.max(axis=0)
    # 清掉上一次的 pnts 产物：单文件与分块两种布局不能混在一起（旧的 30 MB 单块
    # 留在目录里会被误当成有效瓦片，也会让“优化前后文件大小”失真）
    for stale in out_dir.glob("*.pnts"):
        stale.unlink()

    if args.chunk_length > 0:
        print(
            f"[4/4] 分块写入：概览 {args.overview_points} 点 + "
            f"沿主轴每 {args.chunk_length:.0f} m 一块"
        )
        axes = principal_axes(pts_centered)
        u = (pts_centered[:, :2] - axes["center"]) @ axes["major"]
        u_min, u_max = float(u.min()), float(u.max())
        n_chunks = max(int(math.ceil((u_max - u_min) / args.chunk_length)), 1)
        edges = np.linspace(u_min, u_max + 1e-6, n_chunks + 1)
        bucket = np.clip(np.searchsorted(edges, u, side="right") - 1, 0, n_chunks - 1)

        step = max(int(math.ceil(len(pts_centered) / max(args.overview_points, 1000))), 1)
        overview_pts = pts_centered[::step]
        write_pnts(
            out_dir / "overview.pnts",
            overview_pts,
            rgb[::step],
            intensity[::step],
            ecef_center,
        )
        print(f"      overview.pnts → {len(overview_pts)} 点")

        children: list[dict[str, Any]] = []
        for i in range(n_chunks):
            mask = bucket == i
            count = int(mask.sum())
            if count < 1000:
                continue
            sub = pts_centered[mask]
            uri = f"chunk-{i:02d}.pnts"
            write_pnts(out_dir / uri, sub, rgb[mask], intensity[mask], ecef_center)
            lo = sub.min(axis=0)
            hi = sub.max(axis=0)
            cx, cy, cz = (lo + hi) / 2.0
            hx, hy, hz = (hi - lo) / 2.0 + 0.5
            children.append(
                {
                    "uri": uri,
                    "box": [
                        round(float(cx), 3),
                        round(float(cy), 3),
                        round(float(cz), 3),
                        round(float(hx), 3),
                        0.0,
                        0.0,
                        0.0,
                        round(float(hy), 3),
                        0.0,
                        0.0,
                        0.0,
                        round(float(hz), 3),
                    ],
                }
            )
            print(f"      {uri} → {count} 点")
        write_tileset_json_lod(
            ts_path,
            overview_uri="overview.pnts",
            children=children,
            transform=transform,
            pts_min=pts_min,
            pts_max=pts_max,
            root_geometric_error=args.root_geometric_error,
        )
        total_size = sum(f.stat().st_size for f in out_dir.glob("*.pnts")) / 1024
        print(
            f"      tileset.json → {ts_path}"
            f"（{len(children)} 个分块，pnts 合计 {total_size:.1f} KB）"
        )
    else:
        pnts_path = out_dir / f"{args.dataset_code}.pnts"
        print(f"[4/4] 写入 pnts → {pnts_path}")
        n_points_written, *_ = write_pnts(pnts_path, pts_centered, rgb, intensity, ecef_center)
        pnts_size = pnts_path.stat().st_size / 1024
        print(f"      → {n_points_written} 点, {pnts_size:.1f} KB")
        write_tileset_json(
            ts_path,
            pnts_uri=pnts_path.name,
            transform=transform,
            center_lon=args.lon,
            center_lat=args.lat,
            center_height=args.height,
            pts_min=pts_min,
            pts_max=pts_max,
            ecef_center=ecef_center,
        )
        print(f"      tileset.json → {ts_path}")

    meta_path = out_dir / "cloud-meta.json"
    meta = build_meta(
        meta_path,
        points=pts_centered,
        intensity=intensity,
        center_lon=args.lon,
        center_lat=args.lat,
        center_height=args.height,
        dataset_code=args.dataset_code,
        label=args.label,
        source_file=args.source_file,
        source_points=source_points,
        crs=args.crs,
        crs_note=args.crs_note,
        local_center=local_center,
        bounds_min=bounds_min,
        bounds_max=bounds_max,
    )
    print(
        f"      cloud-meta.json → {meta_path}"
        f"（Z {meta['zMin']:.2f}~{meta['zMax']:.2f} m, "
        f"Intensity {meta['intensityMin']:.2f}~{meta['intensityMax']:.2f}, "
        f"报警点 {len(meta['alarms'])} 个）"
    )
    print(f"✔ 完成。前端加载路径：/tiles/local/{args.dataset_code}/tileset.json")


if __name__ == "__main__":
    main()
