/**
 * 3D Tiles 1.0 pnts 轻量解析器（PoC 专用）。
 *
 * 只覆盖本项目自产瓦片的布局（见 backend/scripts/generate_3dtiles.py::write_pnts）：
 *   header(28B) = magic(4) | version(4) | byteLength(4)
 *                 | ftJsonLen(4) | ftBinLen(4) | btJsonLen(4) | btBinLen(4)
 *   body        = FtJson | FtBinary | BtJson | BtBinary
 *   FeatureTable: POINTS_LENGTH, POSITION{byteOffset}(float32×3), RGB{byteOffset}(uint8×3)
 *   BatchTable:   Intensity{byteOffset}(float32), X/Y/Z（与 POSITION 同值的局部 ENU 米）
 *
 * 坐标系：POSITION 已是“源工程坐标 − cloud-meta.localCenter”的局部 ENU（米），
 * 不再乘 root.transform（那是给 Cesium 转到 ECEF 用的，PoC 不进入地球坐标系）。
 */

export interface PntsPayload {
  /** 局部 ENU 坐标（米），长度 3×count。 */
  positions: Float32Array;
  /** 逐点 RGB，长度 3×count。 */
  colors: Uint8Array;
  /** 逐点归一化强度 0~1，长度 count；缺失时全 0。 */
  intensity: Float32Array;
  count: number;
}

export interface PntsFetchResult {
  payload: PntsPayload;
  /** 网络字节数（用于报“下载了多少”）。 */
  bytes: number;
}

interface Accessor {
  byteOffset?: number;
}

interface FeatureTableJson {
  POINTS_LENGTH?: number;
  POSITION?: Accessor;
  RGB?: Accessor;
}

interface BatchTableJson {
  Intensity?: Accessor;
}

const HEADER_BYTES = 28;

/** 解析 pnts 二进制。纯函数，便于单测与复用。 */
export function parsePnts(buffer: ArrayBuffer): PntsPayload {
  if (buffer.byteLength < HEADER_BYTES) {
    throw new Error(`pnts 头部不足 28 字节（实际 ${buffer.byteLength}）`);
  }
  const dv = new DataView(buffer);
  const magic = String.fromCharCode(
    dv.getUint8(0),
    dv.getUint8(1),
    dv.getUint8(2),
    dv.getUint8(3),
  );
  if (magic !== "pnts") throw new Error(`不是 pnts 文件（magic=${magic}）`);

  const ftJsonLen = dv.getUint32(12, true);
  const ftBinLen = dv.getUint32(16, true);
  const btJsonLen = dv.getUint32(20, true);

  const ftJsonStart = HEADER_BYTES;
  const ftBinStart = ftJsonStart + ftJsonLen;
  const btJsonStart = ftBinStart + ftBinLen;
  const btBinStart = btJsonStart + btJsonLen;

  const ft = JSON.parse(
    new TextDecoder().decode(new Uint8Array(buffer, ftJsonStart, ftJsonLen)),
  ) as FeatureTableJson;
  const count = ft.POINTS_LENGTH ?? 0;
  if (count <= 0) throw new Error("pnts 缺少 POINTS_LENGTH");

  const posOffset = ftBinStart + (ft.POSITION?.byteOffset ?? 0);
  // slice 出独立 ArrayBuffer：既避开非 4 字节对齐的 TypedArray 视图限制，
  // 也让解析完的大文件 buffer 可以被 GC 掉。
  const positions = new Float32Array(buffer.slice(posOffset, posOffset + count * 12));

  const rgbOffset = ft.RGB?.byteOffset;
  let colors: Uint8Array;
  if (rgbOffset === undefined) {
    colors = new Uint8Array(count * 3).fill(200);
  } else {
    const start = ftBinStart + rgbOffset;
    colors = new Uint8Array(buffer.slice(start, start + count * 3));
  }

  let intensity = new Float32Array(count);
  if (btJsonLen > 0) {
    const bt = JSON.parse(
      new TextDecoder().decode(new Uint8Array(buffer, btJsonStart, btJsonLen)),
    ) as BatchTableJson;
    const intOffset = bt.Intensity?.byteOffset;
    if (intOffset !== undefined) {
      const start = btBinStart + intOffset;
      intensity = new Float32Array(buffer.slice(start, start + count * 4));
    }
  }

  return { positions, colors, intensity, count };
}

/** 拉取并解析一个 pnts。 */
export async function fetchPnts(url: string): Promise<PntsFetchResult> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`pnts 请求失败 ${res.status}：${url}`);
  const buffer = await res.arrayBuffer();
  return { payload: parsePnts(buffer), bytes: buffer.byteLength };
}
