"""点云 → 3D Tiles (pnts) 转换脚本。

用标准库 + numpy + struct 手写 pnts 瓦片，不依赖 py3dtiles / PDAL。
pnts 是 3D Tiles 里最简单的点瓦片格式，50~100 万点的单文件渲染完全 OK。

为什么不用 py3dtiles / PDAL？
- py3dtiles 主版本停在 2020 年，5.0.0 源码用了 3.7+ 语法但只有 py3.6 的 wheel，
  装到 Python 3.11 venv 里也会 import 崩掉。
- PDAL 2.6 是 C++ 二进制，AGENTS.md 要求通过 pdal pipeline 子进程调用，
  Windows 上还得自己装 OSGeo4W，开发机没装的话根本跑不起来。
- 本仓库里点云规模都在百万级，手写 pnts 反而更可控、无外部依赖。

运行方式：
    uv run python scripts/generate_3dtiles.py

默认输入  backend/tests/data/tunnel_sample.ply
默认输出  frontend/public/tiles/tunnel/{tileset.json, tunnel.pnts}
         ↑ Vite dev server 自动把 public/ 挂在根路径 /tiles/tunnel/*，
         ↑ 前端 Cesium3DTileset.fromUrl('/tiles/tunnel/tileset.json') 直接加载。
         ↑ 生产期把 frontend/public/tiles 连同 dist/ 一起托管给 Nginx 即可。

可选参数：
    --lon 114.31 --lat 30.52 --height 50    # 手动指定点云中心经纬度（WGS84）
    --min-xyz -50 -100 -3 --max-xyz 50 100 5  # 覆盖 auto bbox
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


def read_ply(path: Path) -> np.ndarray:
    """读 binary_little_endian PLY，只抽 x/y/z 三列。

    WHU-TLS 隧道 PLY 头里有多个 property，但我们只关心 x/y/z。
    返回 shape=(N, 3) 的 float32 ndarray。
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
    type_map = {"float": "<f4", "uchar": "<u1", "int": "<i4", "double": "<f8"}
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
    return pts


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
    ecef_center: np.ndarray,  # (3,) float64，用于 RTC_CENTER
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
        BtJson            UTF-8 JSON（本项目 = "{}"）
        BtBinary          空

    FeatureTable JSON 字段名（新版）：
        POINTS_LENGTH   整数（直接内联）
        POSITION        {"byteOffset": 0}  → FtBinary 里的 float32*3*N
        RTC_CENTER      [x, y, z]  float64（直接内联）
        BATCH_LENGTH    整数（直接内联）

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
        "BATCH_LENGTH": 0,
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
    colors = np.full((n_points, 3), 255, dtype=np.uint8)
    rgb_bytes = colors.tobytes()
    rgb_byte_offset = pos_len  # RGB 在 FtBinary 内的偏移
    ft_binary = pos_bytes + rgb_bytes
    ft_binary_len = len(ft_binary)

    # --- Batch Table（空） ---
    bt_json_bytes = b"{}"
    bt_json_len = len(bt_json_bytes)
    bt_json_start = ft_json_start + ft_json_len + ft_binary_len
    total_after_btjson = bt_json_start + bt_json_len
    bt_json_pad = _align8(total_after_btjson) - total_after_btjson
    bt_json_bytes += b" " * bt_json_pad
    bt_json_len = len(bt_json_bytes)
    bt_binary_len = 0

    # --- 布局 ---
    ft_binary_start = ft_json_start + ft_json_len
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

    # BtBinary 空

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf)
    return (
        n_points,
        float(points[:, 0].min()),
        float(points[:, 1].min()),
        float(points[:, 2].min()),
    )


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
    """写单文件 tileset.json。

    boundingVolume 用 region（WGS84 ECEF 包围盒转 region）。
    region 格式 [west, south, east, north, minHeight, maxHeight]（弧度/米）。

    但我们的局部坐标 transform 后，ECEF 的 bbox 可以用 transform 算：
      bbox_min_ecef = transform[:3, :3] @ pts_min + transform[:3, 3]
      bbox_max_ecef = transform[:3, :3] @ pts_max + transform[:3, 3]
    然后把两个角转地理坐标，取外包围盒。

    更简单的做法：因为点云尺度很小（几百米），用 transform 的平移部分作为 region 中心，
    加一个保守的半长即可。
    """
    # 算 ECEF bbox（局部坐标的 8 个角都变一下更准确）
    corners = np.array(
        [
            [pts_min[0], pts_min[1], pts_min[2]],
            [pts_max[0], pts_min[1], pts_min[2]],
            [pts_min[0], pts_max[1], pts_min[2]],
            [pts_max[0], pts_max[1], pts_min[2]],
            [pts_min[0], pts_min[1], pts_max[2]],
            [pts_max[0], pts_min[1], pts_max[2]],
            [pts_min[0], pts_max[1], pts_max[2]],
            [pts_max[0], pts_max[1], pts_max[2]],
        ],
        dtype=np.float64,
    )

    t = transform
    ecef = corners @ t[:3, :3].T + t[:3, 3]  # (8, 3)

    # 转地理
    geods = np.array([ecef_to_geodetic(p) for p in ecef], dtype=np.float64)
    lons = geods[:, 0]
    lats = geods[:, 1]
    heights = geods[:, 2]

    region = [
        float(math.radians(lons.min())),
        float(math.radians(lats.min())),
        float(math.radians(lons.max())),
        float(math.radians(lats.max())),
        float(heights.min() - 2),
        float(heights.max() + 2),
    ]

    # transform 转 3D Tiles 规范的 16 个 float（列主序，flat）
    transform_list = t.flatten("F").tolist()

    # root.geometricError 必须足够大，使 getScreenSpaceError() > maximumScreenSpaceError
    # （Cesium3DTilesetBaseTraversal 里 SSE<=maxSSE 时直接 return 不加载任何瓦片！）
    # 相机默认在 ~12000km 高空，我们的点云只有 ~400m 尺寸：
    #   SSE = geometricError * screenHeight / (2 * distance * tan(fovy/2))
    #   ≈ geometricError / 12_000_000 * 500 (近似)
    #   要让 SSE > 16，需要 geometricError > 16 * 24000 ≈ 384_000
    # 保守给 1e7，保证任何视角下都会触发加载；flyTo 之后 SSE 自然会小下来。
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
# 主流程
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="点云 (PLY/LAS) → 3D Tiles (pnts)")
    ap.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "tests" / "data" / "tunnel_sample.ply",
        help="输入 PLY 文件路径（LAS 暂未实现，加 laspy 后可扩展）",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / ".."
        / "frontend"
        / "public"
        / "tiles"
        / "tunnel",
        help="输出目录（将写入 tileset.json + tunnel.pnts）",
    )
    ap.add_argument("--lon", type=float, default=114.31, help="点云中心经度（WGS84 度）")
    ap.add_argument("--lat", type=float, default=30.52, help="点云中心纬度（WGS84 度）")
    ap.add_argument("--height", type=float, default=50.0, help="点云中心 WGS84 椭球高（米）")
    ap.add_argument(
        "--max-points",
        type=int,
        default=500_000,
        help="超过这个点数时自动下采样",
    )
    args = ap.parse_args()

    in_path = args.input.resolve()
    out_dir = args.output_dir.resolve()
    print(f"[1/4] 读取 PLY: {in_path}")
    pts = read_ply(in_path)
    print(f"       点数: {len(pts)}")
    print(f"       min: {pts.min(axis=0)}")
    print(f"       max: {pts.max(axis=0)}")

    if len(pts) > args.max_points:
        stride = len(pts) // args.max_points + 1
        pts = pts[::stride]
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
    pnts_path = out_dir / "tunnel.pnts"
    ts_path = out_dir / "tileset.json"

    print(f"[4/4] 写入 pnts → {pnts_path}")
    n_points_written, *_ = write_pnts(pnts_path, pts_centered, ecef_center)
    pnts_size = pnts_path.stat().st_size / 1024
    print(f"      → {n_points_written} 点, {pnts_size:.1f} KB")

    write_tileset_json(
        ts_path,
        pnts_uri="tunnel.pnts",
        transform=transform,
        center_lon=args.lon,
        center_lat=args.lat,
        center_height=args.height,
        pts_min=pts_centered.min(axis=0),
        pts_max=pts_centered.max(axis=0),
        ecef_center=ecef_center,
    )
    print(f"      tileset.json → {ts_path}")
    print("✔ 完成。前端加载路径：/tiles/tunnel/tileset.json")


if __name__ == "__main__":
    main()
