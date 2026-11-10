# ADR-0002 桥隧近景三维视图迁移到 Potree

- 状态：已接受（PoC 阶段，主工程尚未切换）
- 日期：2026-11-10
- 关联：`frontend/potree.html`、`frontend/src/potree/*`、`backend/scripts/build_potree.py`

## 背景

Cesium 地球仪在微观桥隧点云上有两个硬伤：透视变形明显（椭球面曲率 + 大 FOV）、近景显示不够专业（无 EDL、无剖面、量测手感差）。因此把「中间三维视图」拆成两层：

- 宏观：Cesium 继续负责地球/大范围资产定位（不变）。
- 微观：Potree 负责桥隧内部近景点云查看与量测。

业务外壳（左侧资产树、右侧分析面板、工单弹窗、漫游入口）不动。

## 决策 1：Vue3 集成方式 —— 全局脚本 + public 静态挂载（推荐）

| 维度 | A. 官方发行包（全局脚本） | B. `potree-core` npm（ESM） |
| --- | --- | --- |
| 当前版本 | 1.8.2（Potree 官方 release，2021 后基本停更） | 2.0.17（社区剥离的渲染核心） |
| 是否 ESM | 否，UMD，依赖 `document.currentScript` 推导 `Potree.scriptPath` | 是 |
| 原生量测工具（距离/高度/面积） | ✅ `viewer.measuringTool` | ❌ 只保留渲染，量测/剖面/侧边栏 GUI 全部缺失 |
| 原生剖面工具 | ✅ `viewer.profileTool` | ❌ |
| 与 Vue3/Vite 结合成本 | 需 `<script>` 顺序引入 + 静态资源挂载，无打包 | `import` 即可，但渲染器需自己接 |
| 风险 | 不是模块，不能 tree-shake；`worker` 与 `resources` 必须按 `scriptPath` 目录摆放 | 要自己实现拾取/量测/剖面，工作量等于重写 viewer |

**结论：选 A。** 用户明确要开原生量测（距离/高度/面积）与剖面，这些能力只在官方 viewer 里；`potree-core` 需要自研等价功能，成本远高于集成成本。B 仅适合“只渲染不要工具”的场景。

### 依赖

不新增 npm 依赖（Potree 走静态资源）：

```jsonc
// frontend/package.json —— 保持现状即可
{
  "dependencies": {
    "cesium": "^1.119.0",
    "three": "^0.169.0",
    "vue": "^3.4.38"
  }
}
```

Potree 发行包放在 `frontend/public/vendor/potree/`（已被 `.gitignore` 忽略，不入库），目录约定：

```
frontend/public/vendor/potree/
├── build/potree/
│   ├── potree.js          # 主脚本（UMD，约 2.2MB）
│   ├── potree.css
│   ├── workers/           # BinaryDecoderWorker.js 等，运行时按 scriptPath/workers 加载
│   ├── resources/         # 图标、shader
│   ├── sidebar.html
│   └── profile.html
└── libs/                  # jquery / jquery-ui / spectrum / other(BinaryHeap) / tween / d3 / proj4 / i18next / jstree
```

获取方式：GitHub `potree/potree` release `1.8.2` 的 `Potree_1.8.2.zip`（本机直连 GitHub 会中断，用 `https://ghproxy.net/https://github.com/...` 或 `gh-proxy.com` 代理，并用 `curl -C -` 断点续传），解压后把 `build/` 与 `libs/` 放到上述目录。为省体积可删 `libs/lazylibs`（只有直接加载 LAZ/EPT 才需要，本方案由转换器离线解码）。

### Worker 与 CSS 的处理

Potree 1.8.2 不是模块，**不能**被打包器处理，原因与对策：

- `potree.js` 用 `document.currentScript.src` 推导 `Potree.scriptPath`，进而加载 `workers/` 与 `resources/`；一旦被 Vite 打包，`scriptPath` 变成打包后的 chunk 路径，worker 404。
- 因此用**普通 `<script>` 标签**按固定顺序加载，资源目录原样放在 `public/` 下，路径与源码目录一一对应（不需要 copy 插件、也不需要 alias）。

`frontend/potree.html`（独立入口，已存在）：

```html
<link rel="stylesheet" href="/vendor/potree/libs/jquery-ui/jquery-ui.min.css" />
<link rel="stylesheet" href="/vendor/potree/libs/spectrum/spectrum.css" />
<link rel="stylesheet" href="/vendor/potree/libs/jstree/themes/mixed/style.min.css" />
<link rel="stylesheet" href="/vendor/potree/build/potree/potree.css" />
...
<script src="/vendor/potree/libs/jquery/jquery-3.1.1.min.js"></script>
<script src="/vendor/potree/libs/spectrum/spectrum.js"></script>
<script src="/vendor/potree/libs/jquery-ui/jquery-ui.min.js"></script>
<script src="/vendor/potree/libs/other/BinaryHeap.js"></script>
<script src="/vendor/potree/libs/tween/tween.min.js"></script>
<script src="/vendor/potree/libs/d3/d3.js"></script>
<script src="/vendor/potree/libs/proj4/proj4.js"></script>
<script src="/vendor/potree/libs/i18next/i18next.js"></script>
<script src="/vendor/potree/libs/jstree/jstree.min.js"></script>
<script src="/vendor/potree/build/potree/potree.js"></script>
<script type="module" src="/src/potree/main.ts"></script>
```

`vite.config.ts` 只需加多页入口（主入口不变）：

```ts
export default defineConfig({
  plugins: [vue(), cesium()],
  server: { port: 5173, proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } } },
  build: {
    outDir: "dist",
    rollupOptions: {
      input: { main: "index.html", potree: "potree.html" },
    },
  },
});
```

类型声明 `frontend/src/potree/potree-globals.d.ts`：`declare const Potree: any;`（官方包无 `.d.ts`）。

> 备选（若后续必须打包）：把 `vendor/potree` 放到 `public` 之外，用 `vite-plugin-static-copy` 在 build 时拷贝到 `dist/vendor/potree`，并用 `Potree.scriptPath = "/vendor/potree/build/potree"` 兜底。当前不需要。

## 决策 2：数据转换与流水线

Potree 读不了 3D Tiles 的 `.pnts`，只认 Potree 八叉树目录。链路改为：

```
原始 .las/.laz ──PotreeConverter 2.1.x──▶ frontend/public/tiles/potree/<dataset>/ ──静态挂载──▶ Potree viewer
```

### 转换器

- 用 **PotreeConverter 2.1.5（Windows x64）**，输出 Potree **2.0** 格式；Potree 1.8.2 viewer 可读 2.0 数据（`potree.js` 内含 `octree.bin`/`hierarchy.bin` 加载逻辑）。
- **1.7 在本机不可用**：无论真实 LAS 还是 200 点合成 LAS 都抛 `0xC0000005` 访问冲突（非沙箱同样崩），所以不要回退 1.7。
- 获取：GitHub `potree/potree` release `2.1.5` 的 `PotreeConverter_2.1.5_x64_windows.zip`（走上面同样的代理），解压到 `backend/var/tools/pc215/`（已被 `.gitignore` 覆盖）。

### 脚本

`backend/scripts/build_potree.py`：幂等、纯函数可单测（`backend/tests/test_build_potree.py`）。

```bash
python backend/scripts/build_potree.py --dataset tunnel \
  --input backend/tests/lunxiao-L.las \
  --aabb "226936.188 103223.594 -45.928 228155.516 104149.18 -21.059"
```

- `--aabb`：**本仓库两份 LAS 头里的 min/max 是乱的**，必须显式传源包围盒，否则八叉树范围错乱。取 `cloud-meta.json` 的 `bounds`。
- `--encoding UNCOMPRESSED`：本地演示便于排查；线上可换 `BROTLI` 省带宽（需注意浏览器端解压开销）。
- 2.x **没有** 1.7 的 `-s/--spacing`、`-l/--levels`、`--store-size`；点预算由内部索引策略决定，稀疏化靠 `--aabb` 裁范围或先降采样源数据，不要再用 1.7 参数。
- `--attributes` 传空格式会被忽略，默认输出 `position + intensity`，不要再写 `--attributes POSITION_CARTESIAN INTENSITY`。

### 产物结构

```
frontend/public/tiles/potree/tunnel/
├── metadata.json     # version 2.0、points、spacing、scale、boundingBox、attributes
├── octree.bin        # 所有节点按层级拼接的点数据（UNCOMPRESSED 时体积≈点数×16B）
├── hierarchy.bin     # 八叉树层级/偏移索引
└── log.txt           # 转换器日志（可删）
```

实测：`lunxiao-L.las` 30,636,217 点全精度约 14s；`beam_disc.las` 10,128,486 点。两者都保留原精度，未做稀疏化。

### 落盘与忽略

- 产物根目录 `frontend/public/tiles/potree/`、运行时 `frontend/public/vendor/potree/` 都写进 `.gitignore`；真实业主点云（`*.las/*.laz`）与派生瓦片一律不入库。
- 迁移到主工程时，若改用 `data/` 或对象存储，同样保持“真实数据不进仓库”。

### 静态挂载

- Vite dev：`public/` 默认挂在 `/` 下，直接访问 `/tiles/potree/tunnel/metadata.json`、`/vendor/potree/build/potree/potree.js`，无需额外配置。
- Nginx（生产）：

```nginx
location /tiles/ {
    alias /srv/bt-platform/tiles/;
    gzip on;
    gzip_types application/json;
    add_header Cache-Control "public, max-age=3600";   # octree.bin 可更长
}
location /vendor/potree/ {
    alias /srv/bt-platform/vendor/potree/;
    expires 30d;
}
```

> 若把 `dist/` 整体部署，`vite build` 已把 `public/` 内容拷到 `dist/`，这两条 location 指向 `dist/tiles/`、`dist/vendor/potree/` 即可。

## 决策 3：坐标系统对齐

### 三层坐标

| 名称 | 定义 | 用途 |
| --- | --- | --- |
| 源工程坐标 `P_eng` | 原始 LAS 里的坐标 | 后端、GEOMETRY 计算 |
| 局部工程坐标 `P_local = P_eng − localCenter` | `localCenter` 来自 `cloud-meta.json` | pnts、业务接口、界面标注 |
| Potree 世界坐标 | 节点坐标 + `geometry.offset`，`offset = metadata.boundingBox.min` | Potree 渲染与拾取 |

所以 Potree 拾取到的位置 `hit.location` 与 `P_eng` 同一量级，转局部只需减 `localCenter`。实现见 `frontend/src/potree/coords.ts`：

```ts
export function engToLocal(p: Vec3, localCenter: Vec3): Vec3      // P_eng → P_local
export function localToEng(p: Vec3, localCenter: Vec3): Vec3
export function geodeticToEcef(g: Geodetic): Vec3                 // WGS84
export function enuBasis(origin: Geodetic)                        // 等价 Cesium ENU 旋转部分
export function geodeticToEnu(p, origin): Vec3
export function geodeticToLocal(p, frame: LocalFrame): Vec3       // 业务经纬度 → Potree 局部
export function localToGeodetic(p, frame: LocalFrame): Geodetic   // 反向
```

Cesium 等价写法（主工程里可直接用）：

```ts
const enu = Cesium.Transforms.eastNorthUpToFixedFrame(
  Cesium.Cartesian3.fromDegrees(lon0, lat0, h0),
);
const local = Cesium.Matrix4.multiplyByPoint(
  Cesium.Matrix4.inverse(enu, new Cesium.Matrix4()),
  Cesium.Cartesian3.fromDegrees(lon, lat, h),
  new Cesium.Cartesian3(),
); // local.x=东, local.y=北, local.z=天，与 Potree 局部空间同向
```

### 点云整体如何施加偏移

- 不需要手动改 `pc.position`：PotreeConverter 已把 `offset` 写进 `metadata.json`，`Potree.loadPointCloud` 会自动 `pointcloud.position.copy(geometry.offset)`。
- 若要让点云与“以 `localCenter` 为原点”的业务实体严格同空间，可选两种做法：
  1. 只算差值（推荐）：`P_local = engToLocal(hit.location, localCenter)`，业务实体也用 `P_local`。
  2. 整体平移场景：`pc.position.sub(new THREE.Vector3(lc.x, lc.y, lc.z))` 后，所有业务实体坐标直接就是 `P_local`。注意此改法会影响剖面/量测数值的绝对坐标口径，PoC 不采用。

### 业务实体映射

- 报警点 / 构件 / 工单位置（`cloud-meta.json` 的 `alarms[].x/y/z`、`eye`）本身就是 `P_local`（实测：`alarms[0].eye` 映射进点云后距最近点 1.59m，人眼高度合理）。
- 因此业务实体 → Potree 世界：`pc.position + P_local`；点云拾取 → 业务实体：`hit.location − pc.position`。
- 若实体只有经纬度：必须先把 `LocalFrame.geodeticOrigin` 填上（控制点），再 `geodeticToLocal()`。

### 当前数据走哪条分支

**走「纯平移」分支（`geodeticOrigin = null`）**：当前数据是局部工程坐标，**未做大地基准转换**。不要用 `center.lon/lat`（120.3826, 36.0671）直接反算经纬度——那只是一个展示用的粗定位，不是量测基准。界面与 HUD 统一保留「局部工程坐标 · 未做大地基准转换」。等拿到施工控制点的经纬度/投影参数后，再用 `geodeticToLocal` 分支，并先用 2~3 个控制点校核残差；若残差超阈值，说明工程坐标系不只差平移，需要 7 参数 Helmert，不能硬套 ENU。

实测补充：`tunnel` 的 `cloud-meta.viewpoint` 映射进点云后距最近点约 139m（不在衬砌内），所以 PoC 改用 `alarms[0].eye` 作为近景入点；正式接入时应由后端产出可靠的推荐入点。

## 决策 4：PoC 组件与验证

- 入口：`frontend/potree.html` → `src/potree/main.ts` → `src/potree/PotreeDemo.vue`（独立页面 `/potree.html`，不改 `index.html` / `App.vue` / `src/cesium/*`）。
- 能力：全屏 EDL 渲染、深色背景、数据集切换（tunnel/bridge）、「点位坐标」拾取打印局部坐标、原生距离/高度/面积/剖面、HUD 保留坐标口径标注、量测状态回显。
- 已验证：真实 30.6M 点隧道近景渲染正常；点击拾取返回 `局部 X 298.217 Y -29.354 Z -10.624` 这类局部坐标（控制台同步打印）；量测工具能落点（HUD 显示 `Distance · 3 点`）。
- 已知告警：个别节点解码抛 `Uncaught RangeError: Offset is outside the bounds of the DataView @ workers/2.0/DecoderWorker.js:149`，属 1.8.2 viewer 读 2.1.5 产物时单节点字节范围越界，画面与量测不受影响；若后续出现缺块，改用 Potree 2.0 官方 viewer 或锁定 PotreeConverter 2.0 版本重转。
- 待人工确认：自动化点击下量测的连线/数值标签未在截图中观察到，需用真实鼠标复核（Potree 量测依赖 mousemove 拖动插入点，脚本模拟事件可能不完整）。

## 迁移到主工程（后续，不在本次改动内）

1. 抽接口：把 `App.vue` 内联的 `globe` 调用（`loadTileset`/`flyToTileset`/`onMeasure`/`onAlarm`/`setAlarmZone`/`resetView` 等）收敛成 `Micro3DViewer` 接口，Cesium 与 Potree 各实现一份。
2. 容器化：新增 `Micro3DViewer.vue`（仅容器：`renderArea` + 顶部工具条插槽），`App.vue` 通过 `provide/inject` 或 props 注入实现，外壳 DOM 结构不变。
3. 分阶段：先并行（Cesium 宏观 + Potree 微观双视图切换），再默认 Potree；量测/漫游迁移到 Potree 前必须人工验收手感。
4. 坐标：`localCenter` 由后端 `cloud-meta.json` 下发，前端只用 `coords.ts` 的纯平移分支，不引入经纬度假精度。

## 风险

- Potree 1.8.2 停更、无类型定义，升级路径有限；如需长期维护，考虑锁定版本 + 自建 fork。
- 大坐标（2.3e5）下量测标记的浮点精度需实测；如出现抖动，按“整体平移场景”把 `pc.position` 归零到局部原点。
- 未做大地基准转换前，任何经纬度展示都只能标注为参考位置。
