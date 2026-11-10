/**
 * Potree 局部坐标 ↔ 源工程坐标 ↔ 大地坐标（WGS84 / ENU）换算。
 *
 * 术语与口径（与 cloud-meta.json 对齐）：
 * - 「源工程坐标」P_eng：原始 LAS 里的坐标（本仓库数据是施工独立坐标系，
 *   量级约 2.3e5 / 1.0e5 米，Z 为高程）。
 * - 「局部工程坐标」P_local = P_eng − localCenter：与 pnts / 业务接口一致，
 *   界面标注「局部工程坐标 · 未做大地基准转换」。
 * - Potree 世界坐标 = 八叉树节点坐标 + geometry.offset，而 offset 取源包围盒
 *   最小值，因此 Potree 世界坐标与 P_eng 同一量级，P_local 需要再减 localCenter。
 *
 * 分支：
 * - `landmark === null`（当前默认）：只做纯平移，不给经纬度换算，避免出现
 *   “看着很精确、其实没有基准”的结果。
 * - 提供了控制点/投影参数后：用 ENU（East-North-Up）基把经纬度映射到同一局部
 *   空间。ENU 基与 Cesium `Transforms.eastNorthUpToFixedFrame` 等价，这里用纯
 *   数学实现，避免把 Cesium 拖进 Potree 独立入口。
 */

export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

/** 大地坐标：经纬度（度）+ 椭球高（米） */
export interface Geodetic {
  lon: number;
  lat: number;
  height: number;
}

/** 局部坐标系定义：局部原点在源工程坐标中的位置 + 该原点的大地坐标 */
export interface LocalFrame {
  /** 局部原点对应的源工程坐标，一般直接取 cloud-meta.json 的 localCenter */
  localCenter: Vec3;
  /**
   * 局部原点的大地坐标。为 null 表示「未做大地基准转换」，
   * 此时经纬度换算一律抛错，不返回假精度结果。
   */
  geodeticOrigin: Geodetic | null;
}

const WGS84_A = 6378137.0;
const WGS84_F = 1 / 298.257223563;
const WGS84_E2 = WGS84_F * (2 - WGS84_F);
const DEG = Math.PI / 180;

export class DatumUnavailableError extends Error {
  constructor() {
    super("当前数据为局部工程坐标 · 未做大地基准转换，缺少控制点/投影参数，无法换算经纬度");
    this.name = "DatumUnavailableError";
  }
}

/** 源工程坐标 → 局部工程坐标（纯平移） */
export function engToLocal(p: Vec3, localCenter: Vec3): Vec3 {
  return { x: p.x - localCenter.x, y: p.y - localCenter.y, z: p.z - localCenter.z };
}

/** 局部工程坐标 → 源工程坐标（纯平移的逆） */
export function localToEng(p: Vec3, localCenter: Vec3): Vec3 {
  return { x: p.x + localCenter.x, y: p.y + localCenter.y, z: p.z + localCenter.z };
}

/** 经纬度 + 椭球高 → ECEF（WGS84） */
export function geodeticToEcef(g: Geodetic): Vec3 {
  const lon = g.lon * DEG;
  const lat = g.lat * DEG;
  const sinLat = Math.sin(lat);
  const cosLat = Math.cos(lat);
  const n = WGS84_A / Math.sqrt(1 - WGS84_E2 * sinLat * sinLat);
  return {
    x: (n + g.height) * cosLat * Math.cos(lon),
    y: (n + g.height) * cosLat * Math.sin(lon),
    z: (n * (1 - WGS84_E2) + g.height) * sinLat,
  };
}

/** ECEF → 经纬度 + 椭球高 */
export function ecefToGeodetic(p: Vec3): Geodetic {
  const { x, y, z } = p;
  const lon = Math.atan2(y, x);
  const r = Math.hypot(x, y);
  let lat = Math.atan2(z, r * (1 - WGS84_E2));
  let height = 0;
  for (let i = 0; i < 5; i += 1) {
    const sinLat = Math.sin(lat);
    const n = WGS84_A / Math.sqrt(1 - WGS84_E2 * sinLat * sinLat);
    height = r / Math.cos(lat) - n;
    lat = Math.atan2(z, r * (1 - (WGS84_E2 * n) / (n + height)));
  }
  return { lon: lon / DEG, lat: lat / DEG, height };
}

/**
 * ENU 基向量（ECEF 下）：等价于 Cesium `Transforms.eastNorthUpToFixedFrame`
 * 的旋转部分，列向量依次为东 / 北 / 天。
 */
export function enuBasis(origin: Geodetic): { east: Vec3; north: Vec3; up: Vec3 } {
  const lon = origin.lon * DEG;
  const lat = origin.lat * DEG;
  const sinLon = Math.sin(lon);
  const cosLon = Math.cos(lon);
  const sinLat = Math.sin(lat);
  const cosLat = Math.cos(lat);
  return {
    east: { x: -sinLon, y: cosLon, z: 0 },
    north: { x: -sinLat * cosLon, y: -sinLat * sinLon, z: cosLat },
    up: { x: cosLat * cosLon, y: cosLat * sinLon, z: sinLat },
  };
}

/** 经纬度 → 相对 origin 的 ENU 局部坐标（x=东, y=北, z=天） */
export function geodeticToEnu(p: Geodetic, origin: Geodetic): Vec3 {
  return ecefToEnu(geodeticToEcef(p), origin);
}

/** ECEF → 相对 origin 的 ENU 局部坐标 */
export function ecefToEnu(p: Vec3, origin: Geodetic): Vec3 {
  const base = enuBasis(origin);
  const originEcef = geodeticToEcef(origin);
  const dx = p.x - originEcef.x;
  const dy = p.y - originEcef.y;
  const dz = p.z - originEcef.z;
  return {
    x: base.east.x * dx + base.east.y * dy + base.east.z * dz,
    y: base.north.x * dx + base.north.y * dy + base.north.z * dz,
    z: base.up.x * dx + base.up.y * dy + base.up.z * dz,
  };
}

/** 相对 origin 的 ENU 局部坐标 → ECEF */
export function enuToEcef(p: Vec3, origin: Geodetic): Vec3 {
  const base = enuBasis(origin);
  const originEcef = geodeticToEcef(origin);
  return {
    x: originEcef.x + base.east.x * p.x + base.north.x * p.y + base.up.x * p.z,
    y: originEcef.y + base.east.y * p.x + base.north.y * p.y + base.up.y * p.z,
    z: originEcef.z + base.east.z * p.x + base.north.z * p.y + base.up.z * p.z,
  };
}

/**
 * 业务实体（经纬度）→ Potree 局部坐标。
 *
 * 前提：工程坐标系与 ENU 基只差平移（无旋转、无尺度）。施工独立坐标系通常
 * 满足，但必须用 2~3 个控制点校核残差；若残差超阈值，需要改用 7 参数
 * （Helmert）转换，不能直接套用本函数。
 */
export function geodeticToLocal(p: Geodetic, frame: LocalFrame): Vec3 {
  if (!frame.geodeticOrigin) throw new DatumUnavailableError();
  const enu = geodeticToEnu(p, frame.geodeticOrigin);
  // ENU 的原点对应 frame.geodeticOrigin，其工程坐标为 localCenter：
  // Potree 局部坐标 = 工程坐标 − localCenter = ENU 偏移量（前提成立时）
  return { x: enu.x, y: enu.y, z: enu.z };
}

/** Potree 局部坐标 → 业务经纬度（与 geodeticToLocal 互逆） */
export function localToGeodetic(p: Vec3, frame: LocalFrame): Geodetic {
  if (!frame.geodeticOrigin) throw new DatumUnavailableError();
  return ecefToGeodetic(enuToEcef(p, frame.geodeticOrigin));
}
