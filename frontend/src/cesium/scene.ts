/**
 * Cesium 场景封装（骨架）：禁用 Ion 与在线底图，后续接本地 WMTS/XYZ 与 3D Tiles。
 * 规则来源：AGENTS.md §4 规则 8、§6 数据安全；点云加载见 T-15。
 */
import { Viewer, Ion } from "cesium";

Ion.defaultAccessToken = undefined as unknown as string;

export function createViewer(container: HTMLElement): Viewer {
  return new Viewer(container, {
    baseLayerPicker: false,
    geocoder: false,
    homeButton: false,
    sceneModePicker: false,
    navigationHelpButton: false,
    timeline: false,
    animation: false,
    infoBox: false,
    selectionIndicator: false,
  });
}
