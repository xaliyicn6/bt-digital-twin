# 文档信息

## 版本信息

| 项目 | 内容 |
| --- | --- |
| 文档名称 | 公路桥隧结构智能检测与数字孪生运维平台 详细设计说明书 |
| 文档编号 | DDS-BT-2026-V1.2b |
| 密级 | 内部（数据分级见 13.2；是否调整密级待业主/测绘管理口径确认） |
| 版本 | V1.2b |
| 发布日期 | 2026-11-05 |
| 编制 | 平台设计组 |
| 审核 | 待签署 |
| 批准 | 待签署 |
| 适用阶段 | 开发、集成、验收、运维 |
| 配套文档 | 《需求规格说明》《系统安装部署手册》《用户操作手册》《接口契约（OpenAPI 3.1）》《算法精度测试报告》 |

（修订：原文档编号 SRS-BT-2026-V1.0 前缀与本文件类型不符，现改为 DDS 前缀；密级由"商业机密"改为"内部"，最终密级见 13.2 待确认事项。）

## 修订记录

| 版本 | 日期 | 修订人 | 修订类型 | 修订内容摘要 |
| --- | --- | --- | --- | --- |
| V1.0 | 2026-09 | 编制组 | 首次发布 | 八章骨架：概述、架构与数据规范、业务模块、算法、数据库与接口、性能与安全、测试与验收、交付与计划 |
| V1.1 | 2026-11-04 | 设计评审组 | 重大修订 | 重构坐标基准与三层配准链；性能指标改为条件卡片；确定 COPC 至 3D Tiles 可视化路线；桥梁按墩型分支、隧道改为设计断面参数化 + ICP；补齐 14 张核心业务表；AI 改入候选缺陷池并加人工复核；接口补状态码矩阵、双令牌、幂等与分片直传；新增第 9 至 14 章；标准引用按适用性重选；实施计划重排并增加复测窗口 |
| V1.2 | 2026-11-04 | 设计评审组 | 待确认项处置 | 附录 C 20 项按 A/B/C 分类；7 项设计侧结论回写正文（投影带选择规则、推理最低配置、渲染点预算、瓦片参数、日志留存、SLA 计时、灰度策略）；新增"决策会议议程建议"与"附录 D 处置建议表"；补 B 类对外提问清单；附录 A 增补 8 条对照 |
| V1.2a | 2026-11-05 | 平台开发组 | 实现回写 | 按一期代码核实并回写：2.1 部署架构与 9.1 模块清单增"落地状态"列（一期已实现/部分实现/二期规划）；9.3 的 `component_id` 与 `PLUMB/TUNNEL_CLEARANCE` 同步为契约字段 `component_code` 与 `VERTICALITY/CLEARANCE/SECTION_SHAPE`；4.1 补 `cover < 0.7` 四边 RANSAC 降级路径，4.2 补逐片圆弧 RANSAC 与切片降级；4.1.5、7.1 补 ±0.5‰ 物理极限备注；5.4 错误码对齐 `app/core/errors.py` |
| V1.2b | 2026-11-05 | 平台开发组 | 3D Tiles 链路落地回写 | 6.2.2 转换管线由"依赖 PDAL 等第三方工具（选型待定）"改为"一期已实现纯 Python 手写 pnts 打包器，脱离外部二进制依赖"，并给出实测输入输出与产物清单；6.1 补 P-08 实测条目（28 万点单瓦片、Vite 静态挂载、Cesium 加载）与实测口径说明；新增附录 E"3D Tiles 切片踩坑记录"（5 类深层缺陷 + 二期 PDAL 自动化避坑清单） |
| V1.1a | 待定 | 待定 | 待定 | 依据业主确认事项清单（附录 C）定稿 |

## 术语与缩略语

| 术语/缩写 | 全称 | 说明 |
| --- | --- | --- |
| COPC | Cloud Optimized Point Cloud | OGC 社区标准，基于 LAS 1.4 / LAZ 1.4 的分块八叉树点云容器，支持 HTTP Range 按需读取 |
| PDRF | Point Data Record Format | LAS 点记录格式；6 无 RGB、7 带 RGB、8 带 RGB+NIR（允许范围以 COPC 原文为准，条款待核实原文） |
| TTFI | Time To First Image | 从发起请求到首帧可见的时间，本文件给出明确定义见 6.1 |
| LOD | Level of Detail | 细节层次；点云 LOD 由八叉树层级生成，不是固定级数 |
| SSE | Screen Space Error | 屏幕空间误差，Cesium 的瓦片调度阈值 |
| TLS | Total Least Squares | 总体最小二乘（正交距离拟合） |
| MABR | Minimum Area Bounding Rectangle | 最小面积外接矩形（旋转卡壳求解） |
| GSD | Ground Sample Distance | 地面采样距离，即单像素对应的实际尺寸 |
| ICP | Iterative Closest Point | 迭代最近点配准 |
| 三心圆 | Three-center Circle | 隧道衬砌断面由三段不同圆心圆弧组成的常规断面形式 |
| 马蹄形 | Horseshoe Section | 仰拱 + 直边墙 + 拱部的复合断面形式 |
| 七参数 | Bursa-Wolf 七参数 | 两个空间直角坐标系间的平移、旋转、尺度相似变换参数 |
| RBAC | Role-Based Access Control | 基于角色的访问控制 |
| SLO | Service Level Objective | 服务等级目标 |
| RPO / RTO | Recovery Point / Time Objective | 恢复点目标 / 恢复时间目标 |
| 候选缺陷池 | Defect Candidate Pool | AI 输出经人工确认前的暂存区，未确认不得进入正式台账 |
| 待业主/实测确认 | — | 本文件中无法从原始资料推导的数值，均以此标注，不得视为已定值 |

# 第1章 系统概述与建设目标

## 1.1 项目背景

公路网已进入"建养并重"阶段。桥隧结构安全监测与日常养护面临三类问题：一是数据孤岛，点云、影像、检测报告、养护台账各自存放，无法按构件关联；二是检测效率低，墩身垂直度、隧道净空等几何量依赖全站仪人工逐点测量，周期长、覆盖稀；三是病害闭环难追溯，发现、评估、派单、修复、复核之间缺少统一的状态与证据链。

本系统以三维激光扫描点云为几何底座、以影像识别为病害发现手段、以 B/S 三维可视化为人机界面，构建桥隧结构的数字孪生底账，支撑"实景可视、几何可量、病害可查、工单可溯"。

## 1.2 建设目标

| 编号 | 目标 | 可验证的达成标准 |
| --- | --- | --- |
| G1 | 资产数字化 | 单座桥/隧的构件（桥→跨→构件→部位）完成编码入库，编码唯一率 100%，三维可拾取率 ≥95%（待实测确认） |
| G2 | 几何可量 | 墩身垂直度、隧道净空按第 4 章算法输出，精度按第 7 章方法评定合格 |
| G3 | 病害可查 | 表观病害经 AI 发现进入候选缺陷池，人工确认后进入台账，全过程留痕 |
| G4 | 工单可溯 | 工单状态迁移全部写入事件日志，任一工单可回放完整生命周期 |
| G5 | 可私有化 | 局域网/专网内闭环运行，不依赖任何外部云服务与在线底图 |

（修订：原"基于 COPC 实现 TB 级点云 Web 端无损流式加载"属手段而非目标，现改为可验证结果；规模表述统一见 6.1，单座桥隧典型数据集 5–10 GB，全线累计规模以业主提供清单为准，待业主确认。）

## 1.3 系统范围与边界

范围内：桥隧点云与影像的接入、配准、建模、可视化；构件台账；墩身垂直度与隧道净空计算；表观病害识别与人工确认；工单闭环；与既有养护管理系统（PMS）的数据交换。

范围外（本版不设计，需单独立项或另行约定）：结构应力/振动等传感器监测（不在 JT/T 1037 体系内实施）；桥梁荷载试验；隧道机电设施控制；无人机自动机场；结构安全评定结论的法律效力认定。

## 1.4 编制依据

| 序号 | 标准/文件 | 在本系统中的用途 | 适用性说明 |
| --- | --- | --- | --- |
| 1 | JTG H12-2015《公路隧道养护技术规范》 | 隧道病害分类、养护对策、技术状况评定参考 | 运营期主依据 |
| 2 | 《公路桥涵养护规范》 | 在役桥梁技术状况评定与养护对策 | 版本号待核实原文后填入（条款待核实原文） |
| 3 | JT/T 1037《公路桥梁结构监测技术规范》 | 桥梁监测术语与测点布设参考 | 年份与名称需核实（易与 2016 版《公路桥梁结构安全监测系统技术规程》混淆），条款待核实原文 |
| 4 | 设计文件、竣工测量成果、原施工控制网资料 | 垂直度、净空的设计基准与初始值 | 运营期判定的第一依据，优先级高于施工期标准 |
| 5 | JTG F80/1-2017《公路工程质量检验评定标准 第一册 土建工程》 | 仅作施工期容许偏差的参考对照 | 施工期质量评定标准，不直接用于在役结构判定（修订：原将其作为在役桥梁垂直度判定依据，现降为参考） |
| 6 | JTG F90-2015《公路工程施工安全技术规范》 | 现场采集作业安全要求 | 施工安全规范，仅用于外业安全交底（修订：原列为系统编制依据，现限定用途） |
| 7 | GB/T 8567-2006《计算机软件文档编制规范》 | 文档格式 | 仅文档格式依据，不作技术依据 |
| 8 | GB/T 22239 信息安全技术 网络安全等级保护基本要求 | 安全建设与测评 | 等级待业主确认 |

判定原则：凡涉及"在役结构是否超限"的结论，一律以设计文件容许值 + 竣工测量初值为准；标准仅提供方法学，不提供数值；任何引用条文号在未核实原文前一律标注"（条款待核实原文）"。

## 1.5 假设、约束与待确认事项

| 编号 | 类型 | 内容 | 影响 |
| --- | --- | --- | --- |
| A1 | 假设 | 业主可提供设计图纸（含断面参数）与竣工测量成果 | 无此资料则隧道断面只能拟合、桥梁垂直度无设计轴线，算法降级 |
| A2 | 假设 | 现场具备可布设控制点的条件，且控制网可复测 | 无控制点则精度指标不可承诺 |
| A3 | 约束 | 纯内网部署，无外网出口 | 底图、地形、依赖包须离线化 |
| A4 | 约束 | 移动端为 Android 平板/手机，现场常无信号 | 必须支持离线接单与断点续传 |
| C1 | 待确认 | 全线桥隧数量、总点云规模、单座最大规模 | 决定存储与渲染容量 |
| C2 | 待确认 | 目标并发用户数、日工单量、峰值上传量 | 决定部署规格与压测目标 |
| C3 | 待确认 | 精度限值（垂直度、净空）与评定方法 | 决定验收条款 |
| C4 | 待确认 | 数据密级与测绘成果管理要求 | 决定数据分级与交付方式 |

完整清单见附录 C。

### 1.5.1 先行假设条款（A 类结论）

以下 8 项由设计侧先行设定，作为开发与测试的默认值；业主确认后直接替换取值，不改变接口结构与验收方法。

| 编号 | 假设条款 | 取值 | 对应项 |
| --- | --- | --- | --- |
| H-01 | 投影带号按线路平均经度取 3° 带，跨带统一至工程局部系 | 带号由线路坐标代入 2.2.1 规则计算，不预设 | C-05 |
| H-02 | 推理节点最低配置 | 单卡显存 ≥ 8 GB、FP16 算力不低于同级主流推理卡；纯 CPU 方案下 P-05 放宽至 ≤ 20 s | C-08 |
| H-03 | 渲染点预算 | 单帧 ≤ 150 万点，1920×1080，核显档 | C-09 |
| H-04 | 瓦片参数 | 单瓦片 ≤ 50 万点，SSE 默认 16 | C-10 |
| H-05 | 日志与审计留存 | 应用日志 30 天热存 + 180 天冷存；审计 ≥ 3 年 | C-15 |
| H-06 | SLA 计时口径 | 自然日 24 小时连续计时 | C-18 |
| H-07 | 灰度与发布 | 10% → 50% → 100%，每级观察 ≥ 24 小时 | C-19 |
| H-08 | 分页与限流默认参数 | `limit` 默认 20、上限 100；单用户读 20 次/秒、写 5 次/秒，单 IP 100 次/秒，桶容量取速率 2 倍 | C-02 |

以上取值在正文对应处均标注"（按建议值设定，待确认）"。（新增：原 1.5 只有待确认清单，没有"未确认时先按什么做"的默认值，开发与测试无处着手。）

# 第2章 总体架构与数据规范

## 2.1 部署与物理架构

系统采用前后端分离的 B/S 架构，全部组件部署于业主内网。

| 层次 | 组件 | 职责 | 部署形态 | 落地状态（按 2026-11 代码核实） |
| --- | --- | --- | --- | --- |
| 感知层 | 架站式/车载三维激光扫描仪、工业相机、标靶与控制点 | 采集点云与影像 | 外业 | 【一期依赖】设备与数据前提，非软件交付物 |
| 接入层 | 上传网关（Nginx）+ 预签名直传 | 分片上传、限流、校验 | 容器 | 【二期规划】一期由前端/客户端直连 FastAPI，未部署网关 |
| 数据层 | PostgreSQL 15 + PostGIS 3.4 | 业务数据与空间对象 | 单节点（一期）；主备（二期） | 【一期已实现】`db/schema.sql` 与 CI 服务均为 postgis/postgis:15-3.4 |
| 数据层 | MinIO | COPC、影像、3D Tiles、附件 | 单节点（一期）；分布式（二期） | 【一期部分实现】`docker-compose.dev.yml` 已编排并建桶；应用侧仍读本地 Mock，预签名直传与对象生命周期未接 |
| 服务层 | FastAPI 业务服务 | HTTP 编排、校验、结果落库 | 容器 | 【一期已实现】资产台账、点云 Range、几何任务均已上线 |
| 服务层 | 任务队列（Celery/Redis） | 异步任务入队、重试、超时、取消 | 容器 | 【二期规划】一期用 `async_task` + PostgreSQL `FOR UPDATE SKIP LOCKED` 领取，无 Redis/Celery |
| 服务层 | PDAL 处理服务 | COPC 归档、3D Tiles 预转 | 独立 worker 容器 | 【二期规划】一期只做 PLY Mock 解析，未接 PDAL/COPC |
| 服务层 | 推理服务（Triton / 本地 ONNX） | 模型加载与切片推理 | 独立容器 | 【二期规划】仓库内无 onnxruntime/`InferenceSession` 代码，一期未落地 |
| 应用层 | Vue3 + CesiumJS 三维大屏、管理后台、移动端 H5 | 交互与展示 | 静态资源 | 【一期部分实现】Vue3/Vite 骨架与 Cesium 场景封装已存在；大屏业务图层与点云加载未接 |

（修订：原架构仅列技术栈，未给部署形态、主备与任务队列，现补齐；并根据已落地代码新增"落地状态"列。**验收口径**：只有标注【一期已实现】或【一期部分实现】的条目纳入本期验收；标注【二期规划】的组件不在本期交付范围，启用须另行走变更单。一期实际形态是 FastAPI 单体 + PostgreSQL 任务表 + 本地 PLY 解析与几何算法，独立 Triton 推理、Celery/Redis、PDAL worker、Nginx 网关均属二期。）

## 2.2 空间基准与坐标转换规范

### 2.2.1 基准定义

| 项 | 规定 |
| --- | --- |
| 平面基准 | CGCS2000 椭球 |
| 交换/展示表达 | EPSG:4490（经纬度，单位为度），仅用于对外交换、GeoJSON 输出与地图叠加 |
| 计算/入库表达 | CGCS2000 3° 带高斯-克吕格投影（EPSG:4543–4554 区间，具体带号按线路所在经度确定，编号待核实原文）或工程局部坐标系（自定义 proj 参数，SRID 由业主给定） |
| 投影带选择规则（A 类结论） | 取线路起终点平均经度所在的 3° 带中央子午线；跨两条带时按 2.2.3 全线统一至工程局部系。带号由坐标代入规则计算并写入 project.calc_srid，不预设固定带号（按建议值设定，待确认） |
| 高程基准 | 1985 国家高程基准，高程异常改正按项目控制测量成果执行 |
| 长度单位 | 数据库与接口统一为米（m），毫米级成果以数值 + 单位字段表达 |

**强制规则（R-01）**：任何距离、面积、体积、切片、凸包、外接矩形、ICP、KD-Tree 邻域查询，**必须先转换到投影坐标系或工程局部坐标系**后执行。禁止在 EPSG:4490 下进行上述运算。

（修订：原 2.2 规定"统一采用 EPSG:4490 或工程局部坐标系"并直接以 POINTZ/POLYGONZ 4490 入库计算，导致度单位参与毫米级运算、面积单位为度平方、1 米切片在经度方向实际不足 1 米；现改为"双表达 + 单一计算基准"。）

### 2.2.2 几何列约束

| 对象 | 列定义 | 约束 |
| --- | --- | --- |
| 构件空间位置 | geom geometry(PointZ, <计算SRID>) | NOT NULL，GIST 索引，CHECK ST_SRID(geom)=计算SRID |
| 构件轮廓 | geom_solid geometry(PolyhedralSurfaceZ, <计算SRID>) | 可空，GIST 索引 |
| 缺陷几何 | geom_point geometry(PointZ, <计算SRID>) / geom_line geometry(LineStringZ,...) / geom_poly geometry(PolygonZ,...) | 按缺陷类型分行存储，禁止混存 |
| 隧道断面 | geom geometry(PolygonZ, <计算SRID>) | GIST 索引，附 mileage 与断面局部坐标系参数 |
| 对外交换 | 接口输出前 ST_Transform 至 4490 | 只读视图，不落库 |

（修订：原 5.1 全部使用 POINTZ/POLYGONZ 4490 且未给类型与 SRID 约束，现改为按几何维度分列 + SRID 约束 + 交换时转换。）

### 2.2.3 跨带与多坐标系

一条线路跨越两个 3° 带时，按"一线一系"原则处理：全线统一转换至业主指定的工程局部坐标系（以线路中段中央子午线为基准），不允许多带混存。单座桥隧允许使用以结构主轴为 X 轴的局部坐标系，但必须在 `project` 表中登记其 proj 字符串与四参数。

### 2.2.4 七参数的适用前提

七参数（Bursa-Wolf）仅在以下条件同时满足时使用：

1. 源坐标系为参心空间直角坐标系（如北京 54、西安 80）或地心坐标系（WGS84），且目标为 CGCS2000 地心直角坐标；
2. 转换区域足够大（建议不小于 50 km × 50 km，且控制点覆盖完整），使三个旋转角可辨识；
3. 控制点不少于 6 个且分布均匀，具备独立检查点。

不满足上述条件时，一律改用四参数（平面）或 Helmert 相似变换（含尺度），控制点不少于 4 个。

**禁止**：以七参数完成"扫描仪设备坐标系 → 工程局部坐标系"的转换。工程局部坐标系范围小，旋转参数病态不可辨识，强行求解会把误差放大到厘米级。

（修订：原 2.2"通过七参数转换将设备坐标系投影至标准工程坐标系"，模型选择与适用前提均错误，现明确分层转换模型与禁用条件。）

## 2.3 三层配准流程与精度链

配准是全部精度指标的前提，分三层独立控制、逐层验收。

### 2.3.1 L1 扫描站间配准

| 项 | 要求 |
| --- | --- |
| 方法 | 标靶配准为主（每站与相邻站重叠区不少于 4 个公共标靶，其中至少 1 个非共面），点云重叠特征配准为辅 |
| 重叠率 | 相邻站重叠不低于 30% |
| 残差 | 站间配准 RMS ≤ 5 mm（待实测确认，最终以设备标称精度与试验标定结果为准） |
| 记录 | 每站写入 `pointcloud_file` 的 station_count、registration_rms、检查点残差 |

（修订：原文完全缺失站间配准环节。）

### 2.3.2 L2 设备系 → 工程系

| 项 | 要求 |
| --- | --- |
| 控制点 | 每座结构不少于 4 个，沿结构全长均匀分布，且至少 1 个位于结构中部 |
| 转换模型 | 平面四参数 + 高程拟合；大范围成果方可用七参数（见 2.2.4） |
| 检查点 | 独立于控制点，数量不少于控制点的 30%，均匀分布 |
| 残差限值 | 平面 ≤ ±10 mm、高程 ≤ ±10 mm（待业主确认）；检查点残差超限即判该批数据不合格并重测 |
| 记录 | 控制点与检查点全部入 `control_point` 表，含坐标、类型、残差、观测日期 |

### 2.3.3 L3 影像 → 点云

| 情形 | 方法 | 精度要求 |
| --- | --- | --- |
| 有 POS/IMU | 直接解算外方位，自检校光束法平差 | 重投影残差 ≤ 1 px（待实测确认） |
| 无 POS | 与点云做同名点配准，同名点不少于 6 个且分布覆盖画面 | 配准后同名点残差 ≤ 2 px（待实测确认） |
| 单张逆投影 | 共线方程正向投影构造射线，与点云三角网/深度图求交取三维点 | 打点平面误差 ≤ 20 mm（待实测确认） |

**必须说明**：共线方程是"三维物方 → 二维像方"的投影方程；由像素反求三维坐标必须有深度约束，仅凭内参无法完成。原文档"利用相机标定参数与共线方程将 2D 像素反投影至 3D 点云"表述方向错误，现按上表更正。

（修订：原文仅有"相机标定参数 + 共线方程"，缺外方位、缺深度约束、缺配准精度要求，现补全三层流程与残差限值。）

### 2.3.4 精度链合成

单点三维坐标中误差按误差传播合成：

```text
σ_P² = σ_station² + σ_control² + σ_sensor² + σ_model²
其中：
  σ_station  站间配准中误差（L1）
  σ_control  控制点转换与高程拟合中误差（L2）
  σ_sensor   扫描仪测距/测角中误差（随距离增大）
  σ_model    拟合模型自身误差（见 4.5）
```

判定规则：若 σ_P 的合成值大于目标精度限值的 60%，该数据集不得用于精度验收，须重测或补测控制点。示例：目标垂直度 ±5 mm 时，要求 σ_P ≤ 3 mm。

（修订：原文直接给出 ±5 mm / ±10 mm 指标，未给出误差来源与合成方法，指标不可达也不可验。）

## 2.4 点云数据规范

| 项 | 规定 |
| --- | --- |
| 归档与算法源格式 | COPC 1.1（基于 LAS 1.4 / LAZ 1.4），点格式按需选用 PDRF 6/7/8（允许范围以 COPC 原文为准，条款待核实原文） |
| 展示格式 | 3D Tiles 点云瓦片（见 6.2），由 COPC 离线转换生成 |
| 必留属性 | GPS Time、Intensity、Return Number、Number of Returns、Classification、Scan Angle Rank、RGB（PDRF 7/8）、Point Source ID |
| 原始密度 | 有效点密度不低于 500 点/㎡（按结构表面投影面积计，剔除噪点与多次回波冗余） |
| 平均点间距 | 由密度推算约 44.7 mm，作为算法可分辨尺度的下界依据 |
| 抽稀 | 仅用于展示瓦片生成，归档 COPC 保留全密度；禁止对归档数据做不可逆抽稀 |
| 坐标系 | 归档文件内记录计算 SRID；交换副本可转 4490 |
| 分块 | maxNodeSize 建议 128–256（以转换工具实测调优为准），保证单节点响应体积可控 |

**强制规则（R-02）**：几何拟合（4.1、4.2）必须使用最高密度层点云，不得使用展示用抽稀瓦片。

（修订：原 2.3 写"COPC（LAS 1.4 / PDRF 7）"并把 COPC 容器标准等同于单一 PDRF，且"LOD 层级保留 3-5 级"混淆了渲染层级与计算精度；现明确格式、属性、密度口径，并禁止用抽稀数据做拟合。）

## 2.5 影像数据规范

| 项 | 规定 |
| --- | --- |
| 格式 | JPEG（采集）/ TIFF（原始保留），色深 8bit |
| 分辨率 | 裂缝检测用影像 GSD ≤ 0.1 mm/px（推断，需现场标定确认，依据 0.2 mm 裂缝至少 2 像素可分辨） |
| 元数据 | 相机内参、外方位（6 自由度）、采集时间、里程/构件编码、拍摄距离、标尺信息 |
| 命名 | `{结构编码}_{构件编码}_{日期}_{序号}.jpg` |
| 标定 | 每台相机每次外业前用标定板标定，记录焦距、主点、畸变系数 |

（修订：原 3.3 只提"相机标定参数"，未给 GSD、外方位与标定周期。）

## 2.6 元数据与文件组织

所有二进制文件存 MinIO，数据库只存元数据与对象键。对象键规则：`{project}/{struct_type}/{struct_code}/{data_type}/{yyyy}/{mm}/{uuid}.{ext}`。文件与空间对象的关联通过 `file_object_link` 表维护，禁止把对象路径写入业务表。

# 第3章 业务功能模块设计

## 3.1 桥隧资产数字化台账

### 3.1.1 层级与编码

| 层级 | 说明 | 编码段 |
| --- | --- | --- |
| L1 结构 | 一座桥或一座隧道 | `B`/`T` + 3 位序号，如 `B001` |
| L2 跨/段 | 桥梁的第 n 跨、隧道的第 n 段（按里程） | `K`/`S` + 2 位序号，如 `K03` |
| L3 构件 | 主梁、墩身、承台、支座、桥面铺装、衬砌环、仰拱、排水沟 | 类型码 3 位，如 `PIR`（墩身）、`BRG`（支座） |
| L4 部位 | 构件的检测部位（如墩身东侧面、衬砌环拱顶） | `P` + 2 位序号 |

完整编码示例：`B001-K03-PIR-P01-001`。编码一经生成不可变更，构件更换时新建编码并记录前身关系（`superseded_by`）。

（修订：原 3.1 只写"一物一码"，未定义编码规则与层级，无法落地。）

### 3.1.2 语义提取与人工确认

自动提取结果进入"待确认"状态，由作业员在三维界面逐个确认或批量确认后方可转为正式构件。每个构件记录提取置信度、确认人、确认时间。支座等小体积、强遮挡构件不承诺自动提取，改为人工标注。

空间范围以定向包围盒（OBB）表达，不用轴对齐 AABB。

（修订：原"基于语义分割自动提取主梁、墩台、支座、桥面铺装并计算空间包围盒"，把强遮挡小构件纳入自动提取且用 AABB；现改为自动提取 + 人工确认，并改用 OBB。）

### 3.1.3 双向映射

点击三维点云拾取构件返回 `component_code`，由编码反查台账；台账变更通过消息同步至三维索引。拾取实现见 6.2.4。

## 3.2 智能几何分析

| 功能 | 输入 | 输出 | 算法章节 |
| --- | --- | --- | --- |
| 墩身垂直度 | 墩身点云、设计轴线、墩型标识 | 倾斜率（‰）、偏移方向、最大局部偏离、质量等级 | 4.1 |
| 墩身累积位移 | 本期轴线、首次观测轴线（T0） | 指定高程处水平偏移（mm）、时间基准 | 4.1.6 |
| 隧道净空与侵限 | 设计断面参数、断面点云、中轴线 | 最小净空、最大侵入深度、侵限面积、里程区段 | 4.2 |

结果统一写入 `geometry_result` 表，附算法版本、参数快照、输入文件哈希，保证可复算。

（修订：原 3.2 仅一句"PCA 提取中轴线""与设计轴线比对"与 4.1 的凸包法并存冲突，现统一指向 4.1 并补结果落库与可复算要求。）

## 3.3 AI 表观病害识别

### 3.3.1 病害分治原则

| 病害类型 | 主检测通道 | 量化方式 | 明确不承诺 |
| --- | --- | --- | --- |
| 裂缝 | 2D 高分辨率影像 + 像素—毫米标定 | 长度、最大宽度、走向 | 不做 3D 体积量化（点间距 44.7 mm，无法分辨 0.2 mm 裂缝） |
| 渗漏水 | 2D 定位 + 3D 面积计算 | 面积（㎡）、边界 | 不承诺小于 0.5 ㎡ 的渗水面（待实测确认） |
| 剥落/掉块 | 2D 定位 + 3D 体积计算 | 面积、深度、体积 | 不承诺小于 20 mm 的深度（待实测确认） |
| 支座剪切变形 | 专用采集工位（桥检车/无人机仰拍） | 位移量、角度 | 不做车载平视影像识别 |

（修订：原将裂缝、渗水、剥落、支座统一交给 YOLO + PointNet++ 做三维量化，物理上不成立；现按病害类型分通道。）

### 3.3.2 候选缺陷池

AI 输出不直接写台账，流程为：

```text
影像 → 推理 → 结构化结果（含置信度、像素坐标、类别、尺寸）
     → 写入 defect_candidate（状态 = PENDING）
     → 人工确认（通过 / 修改 / 驳回）
     → 通过者写入 defect_ledger（状态 = CONFIRMED）
     → 驳回者写入 defect_review（误报样本，进入模型回流集）
```

置信度策略：置信度 ≥ 高阈值自动预填并置"待确认"；低于低阈值直接标记为"低置信候选"，默认折叠不展示。阈值取值待实测标定后写入配置表（`dict_item`）。

（修订：原 3.4"AI 识别结果自动写入 defect_ledger"并直接派单，缺人工确认与误报处理；现改为候选池 + 人工确认 + 误报回流。）

## 3.4 养护巡检工单闭环

### 3.4.1 状态机

状态：`DRAFT`（草稿）→ `PENDING_CONFIRM`（待确认）→ `CONFIRMED`（已确认）→ `ASSIGNED`（已派发）→ `IN_PROGRESS`（处理中）→ `PENDING_ACCEPT`（待验收）→ `CLOSED`（已闭环）。

旁支状态：`REJECTED`（已驳回）、`SUSPENDED`（挂起）、`OVERDUE`（超期）、`CANCELLED`（作废）。迁移表见 10.2。

### 3.4.2 SLA 与留痕

每张工单按缺陷等级设定响应时限与处置时限，超时自动置 `OVERDUE` 并推送提醒。每次状态迁移写一条 `work_order_event`，记录操作人、前后状态、时间、意见、附件、客户端 IP 与设备指纹。工单不可物理删除，只可作废。

时限具体取值按缺陷等级由业主确认（待业主确认）。

（修订：原状态机仅 0–4 五个数字状态，无驳回/挂起/超期/作废，无 SLA 与日志；现补全并数字化状态码改为语义枚举。）

## 3.5 移动端与离线

移动端提供：工单接单、现场拍照、离线缓存、断点续传、扫码定位构件。离线期间本地以 SQLite 存储，恢复网络后按 `Idempotency-Key` 增量同步，冲突以服务端版本号为准并保留本地副本。详见 13.3。

（修订：原 3.4 仅一句"支持移动端接单与现场照片回传复核"，无离线与冲突处理设计。)

# 第4章 核心算法与模型详细设计

## 4.1 桥梁墩身中轴线与垂直度

### 4.1.1 输入与输出

| 项 | 内容 |
| --- | --- |
| 输入 | 墩身点云（最高密度层）、设计轴线（起点、终点、设计高程区间）、墩型标识（RECT / CIRCLE / ROUND_END）、切片步长（默认 1.0 m，可调 0.5 m） |
| 输出 | 分层截面中心序列、稳健中轴线（点 + 单位方向）、倾斜率（‰）、偏移方位角、最大局部偏离（mm）、累积位移（mm，需 T0）、每层有效点比例与质量等级 |
| 依赖 | 已完成 L1/L2 配准，且 σ_P ≤ 3 mm（见 2.3.4） |

### 4.1.2 预处理

```text
1. 按墩身包围盒（OBB）裁切，Z 范围取设计墩顶至承台顶面；
2. 统计滤波去噪（SOR，k=20，阈值 2σ）；
3. 剔除附属：系梁、盖梁、检修平台、预埋件、植被（按分类码 + 高度带 + 平面距离三重判据）；
4. 计算每层有效点比例：valid_ratio = 有效点数 / 理论点数（理论点数 = 密度 × 截面周长 × 切片厚度）；
5. 若整墩有效点比例 < 60%，直接判为"数据不足"，不输出结论（阈值待实测确认）。
```

**切片污染剔除**：若某层点云在水平投影上的外接圆半径超过相邻层中位数的 1.3 倍，判定该层混入盖梁或系梁，剔除该层并记录。

（修订：原文直接"沿 Z 轴按 1.0m 切片"，未考虑盖梁、系梁污染与遮挡。）

### 4.1.3 分层截面中心估计（按墩型分支）

矩形墩（RECT）：

```text
H = 二维凸包(P_layer)
R = 旋转卡壳最小面积外接矩形(H)
center = R 的中心；width = R 短边；length = R 长边
质量判据：覆盖率 cover = 有效点数 / (密度 × R 周长 × 切片厚度) ≥ 0.7
若 cover < 0.7：改用"四边 RANSAC 直线拟合 + 交点求中心"
```

圆形墩（CIRCLE）：

```text
用 RANSAC 剔除粗差后，做最小二乘圆拟合（几何距离最小化）
输出：圆心 (cx, cy)、半径 r、拟合残差 RMS
质量判据：RMS ≤ 0.01 × r 且 内点数占比 ≥ 0.8
```

圆端形墩（ROUND_END）：

```text
1. 对截面点做主方向估计，投影到 (u, v) 局部坐标；
2. 用 RANSAC 拟合两条平行直边（|v - v1| 与 |v - v2| 分别成线）；
3. 两端分别拟合半圆，得到两个圆心；
4. center = 两圆心连线中点；等效长度 = 圆心距 + 2r
```

**为什么不能一刀切用最小外接矩形**：MABR 只对外包络敏感，单侧遮挡（车载/架站常见的半侧覆盖）时，凸包被可见侧弦线截断，矩形中心会向可见侧系统性偏移，且无法给出误差估计；圆形墩的 MABR 退化为外切正方形，中心虽稳定但对噪声与离群点无鲁棒性；圆端形墩的 MABR 长轴虽正确，但中心误差取决于两端半圆的完整度。

（修订：原 4.1 对所有墩型统一"凸包 → 旋转卡壳最小外接矩形 → 取中心"，未区分墩型、未评估遮挡、无质量判据；现按墩型分支并给出覆盖率与残差判据。）

**降级路径（与一期代码 `backend/app/services/geometry.py` 一致）**

矩形墩的 `cover < 0.7` 不是"直接出数"，而是触发逐层分支替换；以下行为均已实现，不得在报告中省略：

| 触发条件 | 代码行为 | 结果标记 |
| --- | --- | --- |
| `cover < 0.7` | 由 MABR 中心切换到**四边 RANSAC**：方向 RANSAC（90° 折叠）→ u/v 各两次一维 RANSAC 定边 → 四边交点求中心 | `branch="RANSAC"` |
| 缺边且提供设计截面尺寸 | 用设计尺寸补出缺失边（同一轴向不允许两条边都缺） | `reconstructed_from_design=True` |
| 缺边且无设计截面尺寸 | 不做几何外推，只给中心偏移量级估计 `10 mm`（4.1.8 口径） | `center_offset_bound_mm=10.0`，整墩 `degraded=True` |
| 同一轴向两条边均缺失 | 几何上无解，回退 MABR | `branch="MABR(回退)"`，`degraded=True` |
| 有效层数 < 3、有效层占比 < 60%、有效层跨度 < 总高 60%、污染剔除、RANSAC 回退 | 逐条写入降级原因，不静默给结论 | `degraded=True`、`degrade_reason` |

补充口径：未传入点云密度 `density` 时，`cover` 用**四边沿边采样占空比**作代理（`cover_is_proxy=True`），分支判定取 `min(cover, 占空比)`；代理值只表示"四条边是否都被采到"，不等于 4.1.3 的密度公式。`ransac_layers`、`reconstructed_layers`、`cover_is_proxy` 三个计数/标志必须随结果落库，供验收复核。

### 4.1.4 稳健轴线拟合

```text
1. 初值：对分层中心点集做 PCA，取第一主成分作为初始方向 d0 与初始点 p0；
2. 稳健拟合：以正交距离（点到直线垂距）为目标函数，用 IRLS（Huber 权）迭代；
3. 终止：两次迭代方向夹角 < 0.01°，或残差变化 < 1e-4；
4. 输出：p0 + d，及每层点到轴线的垂距残差。
```

**说明**：PCA 仅用于提供初值，不作为最终中轴线；最终结果必须由正交距离（TLS）稳健拟合给出。普通最小二乘（沿某一坐标轴回归）会产生方向偏置，不采用。

（修订：原 3.2"PCA 主成分分析提取中轴线"与 4.1"RANSAC + 最小二乘法拟合"两条路径并存且含义不同，现统一为"PCA 初值 + TLS 稳健拟合"，消除冲突；并纠正"最小二乘拟合三维直线"未定义参数化的问题。）

### 4.1.5 垂直度定义式

以设计高程区间 [z_b, z_t]（默认承台顶面至墩顶）为准：

```text
Δ_h = 拟合轴线在 z = z_t 处的水平坐标 − 在 z = z_b 处的水平坐标
i   = |Δ_h| / (z_t − z_b) × 1000   （单位：‰，即倾斜率）
最大局部偏离 δ_max = max_i( 第 i 层圆心/中心到拟合轴线的水平垂距 )
```

**物理极限备注（验收前提，须写入报告）**：基于 200k 点的点云密度与 20 mm 噪声条件，垂直度验收偏差经验证约为 **±0.5‰**；若需更高精度，须提高硬件扫描密度（更高点密度或更低单点噪声），并重新做重复观测标定。该数值是**当前采集条件下的可达精度**，不是设备或算法的理论极限；投标/合同承诺高于 ±0.5‰ 时，必须同步提高扫描密度并另行验证。

判定：以设计文件容许值为准；若设计文件未给出，参考同类在役桥梁养护规范的容许偏差（条款待核实原文），并在报告中同时给出数值与依据来源。

（修订：原仅输出"垂直度偏差（‰）"与"累积位移（mm）"，未定义测法、基准区间与判定依据；现给出显式公式与优先级。）

### 4.1.6 累积位移的时间基准

```text
disp(t) = 拟合轴线_t 在指定高程 z_ref 处的水平坐标 − 轴线_T0 在 z_ref 处的水平坐标
```

- `T0` 为首期观测（优先取竣工测量或首次扫描成果），写入 `geometry_baseline` 表并锁定，不得随期次覆盖；
- 所有期次必须使用同一控制网、同一计算 SRID；
- 若控制网复测，须同时给出控制网变化量并换算到同一基准，否则不同期次数据不可比。

（修订：原"累积位移量（mm）"无时间基准与控制网一致性要求，数据不可比。）

### 4.1.7 质量判据与边界条件

| 情形 | 处理 |
| --- | --- |
| 墩高 < 5 m | 不出倾斜率，仅出偏移量，标注"样本不足" |
| 有效层数 < 总层数 60% | 判"数据不足"，转人工复测 |
| 相邻墩间距 < 2 m | 扩大裁切盒并做点云分离，分离失败则人工划定 |
| 斜交墩、变截面墩 | 按实测截面分段拟合，不做整体单一轴线 |
| 温度/日照变形明显 | 报告中注明采集时段与温度，不做修正，标注"未修正" |

### 4.1.8 误差来源

| 来源 | 量级 | 是否可控 |
| --- | --- | --- |
| 站间配准 | 3–5 mm | 是，靠标靶数量与重叠率 |
| 控制点转换 | 5–10 mm | 是，靠控制点数量与检查点复核 |
| 扫描仪测距/测角 | 随距离增大（近距 3–5 mm） | 部分，靠缩短站距 |
| 遮挡导致的中心偏移 | 可达 10 mm 以上 | 是，靠多站补扫 + 覆盖率判据 |
| 拟合模型误差 | 亚毫米级 | 是，靠 TLS 与 IRLS |
| 温度与荷载变形 | 未知，需实测 | 否，仅记录 |

## 4.2 隧道断面侵限分析

### 4.2.1 输入与输出

| 项 | 内容 |
| --- | --- |
| 输入 | 设计断面参数化模板、隧道中轴线（优先取设计资料，点云提取仅作校核）、衬砌点云（最高密度层）、限界模型 |
| 输出 | 逐断面最小净空、最大侵入深度、侵入宽度、侵限面积、里程区段、置信度、超限标记 |

### 4.2.2 设计断面参数化

设计断面以"多段圆弧 + 直线段"参数化表达，每段记录：类型（ARC/LINE）、圆心、半径、起止角、连接点。三心圆、马蹄形均由该结构描述，禁止用单一椭圆近似。

```text
section_template = {
  segments: [
    {type: ARC,  center:(0, 0),     r: R1, a0: 0,   a1: 60},
    {type: ARC,  center:(cx2, cy2), r: R2, a0: ..., a1: ...},
    {type: ARC,  center:(cx3, cy3), r: R3, a0: ..., a1: ...},
    {type: LINE, from:(...), to:(...)}
  ],
  invert_elevation: h0,
  design_clearance: c
}
```

（修订：原 4.2"拟合闭合多边形或椭圆"与三心圆、马蹄形断面不匹配，形式误差可达数十毫米并直接混入侵限判定；现改为设计断面参数化 + 实测比对。）

### 4.2.3 纵向采样间距

| 区段 | 间距 |
| --- | --- |
| 一般段 | 0.5–1.0 m |
| 洞口段（洞口前后 50 m） | 0.25–0.5 m |
| 变坡、加宽、紧急停车带、衬砌环缝 | 0.25–0.5 m |
| 最终取值 | 按限界要求与业主要求核定（待业主确认；条款待核实原文） |

**说明**：5 m 等间距会跳过限界最不利位置，无法支撑 ±10 mm 级净空结论；"最大侵入深度"必须是沿纵向连续扫描的最小值，而非抽样断面的最小值。

（修订：原 4.2"按等间距（如每 5 米）提取法平面"，采样密度与精度指标不自洽；现改为自适应间距。）

### 4.2.4 断面配准与法向距离

```text
1. 由中轴线等间距取法平面，建立 (u, v) 断面局部坐标系（u 竖直向上，v 水平）；
2. 断面点投影到 (u, v)；
3. ICP 配准：实测断面对设计断面线做 2D 点到线 ICP，限制旋转 ≤ 2°、平移 ≤ 50 mm，超限则判配准失败；
4. 法向距离：对每个实测点求到设计断面折线的带符号最短距离 d_i（内侧为正）；
5. 净空 = min_i (d_i)；侵入深度 = max(0, c − 净空)；
6. 侵入宽度 = 连续满足 d_i > c 的弧长；
7. 侵限面积 = 沿纵向对侵入深度做梯形积分，输出每延米与区段合计。
```

**椭圆降级说明**：椭圆拟合无设计断面资料时用作粗定位与质量检查（判断断面形状是否被遮挡或混入路面点），不参与侵限判定。

（修订：原 4.2 用"拟合闭合多边形或椭圆 + 空间相交测试 + KD-Tree 最短距离"，形状模型错误、点位求距方法冗余；现改为设计断面参数化 + 2D ICP + 带符号法向距离。）

**降级路径与一期实现现状（`backend/app/services/tunnel.py`、`intrusion.py`）**

1. **一期 API 管线走"逐片局部圆弧 RANSAC"出净空参考**，不是上面的设计断面 ICP 判定：每片取三点定圆 RANSAC（`inlier_band=0.05 m`、300 次迭代），按 95% 分位裁剪内点（裁剪阈值以 `inlier_band` 为上限），再用几何距离做最小二乘精修 3 轮。
2. **切片降级**：切片点数 < 30 或拟合失败 → 跳过该切片并记入 `skipped_reasons`；全部切片失败 → 任务判 `BT-40002`（点云密度不足），不静默出 0。
3. **量纲区分**：一期返回的 `clearance_reference_mm` 是相对**拟合圆弧**的净空参考，`sections[]` 中同名；它不是建筑限界侵限值，报告与前端文案禁止写成"限界侵限"。
4. **设计断面精确判定模块已交付但未接线**：`intrusion.py` 实现三圆心圆弧 + 显式起止角 + 直线底板一元二次方程收口 + `clearance_margin_m=0.05 m` 余量红线 + shapely 精确裁剪面积（`intrusion_depth_mm`/`intrusion_count`/`intrusion_area_sqm`），当前 `execute_analysis` 尚未调用它，`geometry_result.intrusion_area_sqm` 落 `NULL`。接线前不得对外宣称已按设计限界判定；接线后须在报告中标注算法版本，并与拟合圆弧口径分列。
5. **为什么先保留拟合圆弧**：设计断面参数未随台账下发时，ICP 无基准可配；局部圆弧至少能给出可复核的净空趋势，但只能作为参考值，不能作为验收判据。

### 4.2.5 误差来源与边界条件

| 项 | 说明 |
| --- | --- |
| 设计断面数字化误差 | 图纸矢量化误差，需记录，量级待核实 |
| ICP 配准残差 | 建议 ≤ 10 mm，超限判失败 |
| 点云噪声 | 与距离相关，近距 3–5 mm |
| 离散化误差 | 断面折线化精度需 ≤ 1 mm |
| 路面点混入 | 按高程带剔除，剔除失败则该断面作废 |
| 机电设施凸出 | 单独标注，不计入侵限 |

## 4.3 2D 病害识别与像素—毫米标定

| 项 | 内容 |
| --- | --- |
| 模型 | YOLOv8（检测）+ 分割头（裂缝走向），ONNX 导出 |
| 输入 | 切片后影像（640 或 1024，按实测最优选定） |
| 后处理 | NMS（IoU 阈值可配）、按类别阈值过滤、跨切片去重合并 |
| 标定 | 每张影像记录拍摄距离与焦距，或用画面内标尺/已知构件尺寸反算 GSD |
| 宽度换算 | 裂缝宽度(m) = 像素宽度 × GSD；GSD 误差直接传递到宽度结论，必须给出不确定度 |
| 交付口径 | 裂缝只输出 2D 长度/宽度/走向，不输出 3D 体积 |

（修订：原 3.3 用 PointNet++ 对裂缝做"三维体积/面积量化以消除透视误差"，在 44.7 mm 点间距下不成立；现明确裂缝只走 2D 通道并给出标定方法。）

## 4.4 2D—3D 映射

```text
输入：影像像素 (x, y)、相机内参 K、外方位 (R, t)、点云三角网 M
1. 由 (x, y) 与 K 构造相机坐标系下射线 r_cam；
2. 变换到工程坐标系：r_world = R · r_cam，原点 O = t；
3. 求射线与 M 的首个交点 P（无交点则沿射线取最近点并标记"深度不确定"）；
4. 输出 P 及置信标记，写入 defect_candidate.geom_point。
误差：打点误差 ≈ 配准残差 + 射线求交误差，目标 ≤ 20 mm（待实测确认）。
```

（修订：原 3.3"利用共线方程将 2D 像素反投影至 3D 点云"缺深度约束与外方位，方向性错误；现补全射线求交流程。）

## 4.5 精度传递与误差预算

以垂直度 ±5 mm 为目标，反推各环节允许误差（待实测确认后固化）：

| 环节 | 允许中误差 | 依据 |
| --- | --- | --- |
| 站间配准 σ_station | ≤ 3 mm | 标靶 4 个以上、重叠 30% |
| 控制点转换 σ_control | ≤ 3 mm | 控制点 4 个以上 + 检查点复核 |
| 传感器 σ_sensor | ≤ 3 mm | 缩短站距至 30 m 内 |
| 模型 σ_model | ≤ 0.5 mm | TLS 稳健拟合 |
| 合成 σ_P | ≤ 5.3 mm（RSS） | 见 2.3.4 |
| 结论 | 合成值须 ≤ 目标值的 60%，即 ≤ 3 mm 才允许用于 ±5 mm 验收 | 见 2.3.4 判定规则 |

密度论证：500 点/㎡ 对应平均点间距约 44.7 mm；1 m 切片、周长 6 m 的矩形墩约有 3000 个有效点，单个截面中心的理论估计标准差约为单点噪声的 1/√3000，即亚毫米级。因此**限制精度的是配准链与遮挡，不是点密度**；这也正是必须做 L2 控制点与覆盖率判据的原因。

（修订：原文以 500 点/㎡ 直接对 ±5 mm 结论，缺密度与精度的传递论证。）

# 第5章 数据库与接口设计规范

## 5.1 数据模型总览

数据分四域：资产域（project、bridge、tunnel、component）、采集域（pointcloud_file、image_file、control_point、file_object_link）、业务域（defect_candidate、defect_ledger、defect_review、work_order、work_order_event、attachment、geometry_baseline、geometry_result）、支撑域（sys_user、sys_role、sys_permission、sys_user_role、sys_role_permission、sys_role_data_scope、dict_type、dict_item、async_task、notification、audit_log、model_registry、sync_log）。

关系主线：

```text
project 1..n bridge / tunnel
bridge 1..n component（自关联 parent_id，level 1..4）
component 1..n pointcloud_file / image_file / geometry_result / defect_ledger
pointcloud_file / image_file 1..n file_object_link n..1 component
defect_candidate 1..0..1 defect_ledger（确认后生成）
defect_ledger 1..n work_order（重复单合并见 10.5）
work_order 1..n work_order_event
work_order / defect_ledger 1..n attachment
async_task 独立，按 biz_type + biz_id 关联业务对象
```

（修订：原 5.1 仅 4 张表，且 bridge_id、tunnel_id 无对应主表，属悬空外键；现补全四域 29 张表。）

## 5.2 表清单与统一字段

统一审计字段集 A（除字典与关联表外，所有业务表均含）：`id uuid PK`、`project_id uuid`、`created_at timestamptz`、`created_by uuid`、`updated_at timestamptz`、`updated_by uuid`、`deleted_at timestamptz`（软删除）、`version int`（乐观锁）、`remark text`。

| 序号 | 表名 | 说明 | 主键 | 关键索引 |
| --- | --- | --- | --- | --- |
| 1 | project | 项目/合同，承载坐标系与数据范围 | id | uk_project_code |
| 2 | bridge | 桥梁主表 | id | uk_bridge_code、idx_project |
| 3 | tunnel | 隧道主表 | id | uk_tunnel_code、idx_project |
| 4 | component | 构件（四级树，自关联） | id | uk_component_code、idx_parent、idx_struct |
| 5 | pointcloud_file | 点云文件元数据（含配准记录） | id | idx_component、idx_capture_time |
| 6 | image_file | 影像元数据（含内外参） | id | idx_component、idx_capture_time |
| 7 | control_point | 控制点与检查点 | id | idx_project、gist(geom) |
| 8 | file_object_link | 文件与空间对象多对多关联 | id | uk_link(file_id,object_type,object_id) |
| 9 | geometry_baseline | 首期基准（T0 轴线/断面） | id | uk_baseline(component_id,type) |
| 10 | geometry_result | 几何分析结果 | id | idx_component_time、idx_task |
| 11 | defect_candidate | 候选缺陷池 | id | idx_status、gist(geom_point) |
| 12 | defect_ledger | 正式缺陷台账 | id | idx_struct、idx_status、gist |
| 13 | defect_review | 复核记录与误报样本 | id | idx_candidate、idx_result |
| 14 | work_order | 工单主表 | id | uk_order_no、idx_status、idx_handler |
| 15 | work_order_event | 工单状态事件日志 | id | idx_order_time |
| 16 | attachment | 附件（照片、报告、签字件） | id | idx_object |
| 17 | async_task | 异步任务 | id | idx_status、uk_idem |
| 18 | notification | 通知记录 | id | idx_user_status |
| 19 | sys_user | 用户 | id | uk_username |
| 20 | sys_role | 角色 | id | uk_role_code |
| 21 | sys_permission | 权限点 | id | uk_perm_code |
| 22 | sys_user_role | 用户角色关联 | (user_id,role_id) | — |
| 23 | sys_role_permission | 角色权限关联 | (role_id,perm_id) | — |
| 24 | sys_role_data_scope | 角色数据范围（项目/桥/隧/路段） | id | idx_role |
| 25 | dict_type | 字典类型 | id | uk_type_code |
| 26 | dict_item | 字典项 | id | uk_type_value |
| 27 | audit_log | 审计日志 | id | idx_time、idx_user、idx_object |
| 28 | model_registry | 模型版本与上线记录 | id | uk_model_ver |
| 29 | sync_log | PMS 同步与对账日志 | id | idx_time、idx_status |

## 5.3 核心表结构

### 5.3.1 project

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| project_code | varchar(32) | NOT NULL, UNIQUE | 项目编码 |
| project_name | varchar(128) | NOT NULL | 项目名称 |
| calc_srid | int | NOT NULL | 计算坐标系 SRID（见 2.2.1） |
| exchange_srid | int | NOT NULL DEFAULT 4490 | 交换坐标系 |
| proj_string | text | NULL | 工程局部坐标系 proj 参数 |
| vertical_datum | varchar(32) | NOT NULL DEFAULT '1985' | 高程基准 |
| status | varchar(16) | NOT NULL | ACTIVE / ARCHIVED |

### 5.3.2 bridge / tunnel

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| struct_code | varchar(32) | 结构编码（B/T + 3 位），UNIQUE |
| struct_name | varchar(128) | 名称 |
| mileage_start / mileage_end | numeric(12,3) | 起止里程（m） |
| geom_line | geometry(LineStringZ, calc_srid) | 结构中心线 |
| deck_elevation | numeric(8,3) | 桥面/路面设计高程 |
| design_axis_geom | geometry(LineStringZ, calc_srid) | 设计轴线（桥梁） / 设计中轴线（隧道） |
| section_template_id | uuid | 隧道设计断面模板（存 dict_item 扩展） |
| span_count / segment_count | int | 跨数 / 段数 |
| tech_grade | varchar(16) | 技术状况等级（字典） |
| built_year | int | 建成年份 |

### 5.3.3 component

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| component_code | varchar(64) | 编码，规则见 3.1.1，UNIQUE |
| struct_type | varchar(8) | BRIDGE / TUNNEL |
| struct_id | uuid | 指向 bridge 或 tunnel |
| parent_id | uuid | 上级构件（层级 1 时为空） |
| level | smallint | 1 结构 / 2 跨段 / 3 构件 / 4 部位 |
| comp_type | varchar(16) | 类型码（字典） |
| geom | geometry(PointZ, calc_srid) | 代表点 |
| geom_solid | geometry(PolyhedralSurfaceZ, calc_srid) | OBB 或实测轮廓，可空 |
| status | varchar(16) | PENDING / CONFIRMED / MERGED |
| extract_confidence | numeric(5,4) | 自动提取置信度 |
| superseded_by | uuid | 构件更换后的新编码 |
| pier_type | varchar(16) | RECT / CIRCLE / ROUND_END（墩型，驱动 4.1 分支） |

### 5.3.4 pointcloud_file

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| component_id | uuid | 关联构件 |
| object_key | text | MinIO 对象键 |
| format | varchar(16) | COPC / LAS / 其他 |
| pdrf | smallint | 点记录格式 |
| calc_srid | int | 数据坐标系 |
| point_count | bigint | 点数 |
| density | numeric(10,2) | 实测有效密度（点/㎡） |
| bbox | geometry(PolygonZ, calc_srid) | 范围 |
| station_count | int | 扫描站数 |
| registration_rms | numeric(6,2) | L1 站间配准 RMS（mm） |
| control_residual_plan / control_residual_height | numeric(6,2) | L2 检查点残差（mm） |
| scanner_model | varchar(64) | 设备型号 |
| capture_start / capture_end | timestamptz | 采集时间窗 |
| quality_level | varchar(16) | A/B/C/REJECT（见 11.5） |
| file_hash | varchar(64) | SHA-256，用于幂等与复算 |

### 5.3.5 image_file

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| component_id | uuid | 关联构件 |
| object_key | text | MinIO 对象键 |
| width / height | int | 像素尺寸 |
| gsd | numeric(8,4) | 地面采样距离（mm/px） |
| camera_model | varchar(64) | 相机型号 |
| intrinsics | jsonb | 焦距、主点、畸变系数 |
| pose | jsonb | 外方位（x,y,z,rx,ry,rz），无位姿时为空 |
| pose_source | varchar(16) | POS / CONTROL_POINT / NONE |
| mileage | numeric(12,3) | 里程 |
| capture_time | timestamptz | 采集时间 |
| quality_level | varchar(16) | A/B/C/REJECT |

### 5.3.6 defect_candidate

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| struct_type / struct_id | varchar / uuid | 所属结构 |
| component_id | uuid | 构件 |
| defect_type | varchar(16) | CRACK / SEEP / SPALL / BEARING（字典） |
| source | varchar(8) | AI / MANUAL |
| confidence | numeric(5,4) | 置信度 |
| bbox_2d | jsonb | 像素框或分割多边形 |
| geom_point | geometry(PointZ, calc_srid) | 三维定位点（可能为空） |
| quant_value / quant_unit | numeric / varchar | 量化值与单位 |
| status | varchar(16) | PENDING / APPROVED / REJECTED / MERGED |
| model_version | varchar(32) | 产出该候选的模型版本 |
| task_id | uuid | 关联 async_task |

### 5.3.7 defect_ledger

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| defect_no | varchar(32) | 缺陷编号，UNIQUE |
| candidate_id | uuid | 来源候选（人工录入为空） |
| defect_type / severity | varchar | 类型 / 等级（字典，非数字魔法值） |
| geom_point / geom_line / geom_poly | geometry(..., calc_srid) | 点/线/面按类型分列 |
| quant_value / quant_unit | numeric / varchar | 量化值（长度 mm、面积 ㎡、体积 m³） |
| status | varchar(16) | CONFIRMED / ASSIGNED / REPAIRED / VERIFIED / CLOSED / VOID |
| first_found_at | timestamptz | 首次发现时间 |
| last_review_at | timestamptz | 最近复核时间 |
| is_false_positive | boolean | 复核为误报时置真，保留记录不删除 |

### 5.3.8 work_order

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| order_no | varchar(32) | 工单号，UNIQUE |
| defect_id | uuid | 来源缺陷 |
| status | varchar(24) | 见 10.1 枚举 |
| priority | varchar(8) | HIGH / MEDIUM / LOW |
| handler_id | uuid | 处置人 |
| reviewer_id | uuid | 验收人 |
| sla_deadline | timestamptz | 按 10.4 口径计算 |
| sla_paused_at | timestamptz | 挂起开始时间 |
| sla_paused_total | int | 累计暂停秒数 |
| suspend_reason | varchar(128) | 挂起原因 |
| merged_into | uuid | 重复单合并目标 |
| closed_at | timestamptz | 闭环时间 |

### 5.3.9 work_order_event

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| order_id | uuid | 工单 |
| seq | int | 序号（同一工单内递增） |
| from_status / to_status | varchar(24) | 前后状态 |
| event | varchar(32) | 触发事件（见 10.2） |
| operator_id | uuid | 操作人 |
| operator_role | varchar(32) | 操作角色 |
| opinion | text | 意见 |
| attachment_ids | uuid[] | 关联附件 |
| client_ip / device_id | varchar | 来源 |
| occurred_at | timestamptz | 时间 |

### 5.3.10 async_task

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| biz_type | varchar(32) | INFERENCE / GEOMETRY / TILE_BUILD / EXPORT / SYNC |
| biz_id | uuid | 业务对象 |
| status | varchar(16) | QUEUED / RUNNING / SUCCEEDED / FAILED / RETRYING / CANCELLED / TIMEOUT |
| idem_key | varchar(128) | 幂等键，UNIQUE |
| attempt / max_attempt | smallint | 重试次数 |
| timeout_sec | int | 超时预算 |
| priority | smallint | 排队优先级 |
| progress | smallint | 0–100 |
| result_ref | text | 结果存储位置（表名+主键 或 对象键） |
| error_code / error_msg | varchar / text | 失败信息 |
| started_at / finished_at | timestamptz | 时间 |

### 5.3.11 sys_* 与 dict_*

| 表 | 关键字段 |
| --- | --- |
| sys_user | username、display_name、password_hash、status、dept、phone（脱敏存储） |
| sys_role | role_code、role_name、is_builtin |
| sys_permission | perm_code（如 asset:read、defect:confirm）、resource、action |
| sys_role_data_scope | role_id、scope_type（PROJECT/BRIDGE/TUNNEL/SECTION）、scope_id |
| dict_type / dict_item | type_code / item_value、item_label、sort、extra（jsonb，可存断面模板） |

## 5.4 接口契约规范

### 5.4.1 通用约定

| 项 | 规定 |
| --- | --- |
| 协议 | HTTPS（内网亦启用），RESTful，`Content-Type: application/json` |
| 版本 | 路径版本 `/api/v1`；破坏性变更升 `/api/v2`，旧版本保留不少于 6 个月（待业主确认） |
| 时间 | 一律 `timestamptz`，ISO 8601 带时区；服务端存储 UTC，展示按客户端时区 |
| 单位 | 长度 m、精度 mm、面积 ㎡、体积 m³；响应中显式给出 `unit` 字段 |
| 坐标系 | 请求/响应几何默认 4490 GeoJSON；计算类接口接受并返回计算 SRID，需显式声明 `srid` |
| 分页 | `?limit=&cursor=`，`limit` 默认 20、上限 100；响应含 `next_cursor`（按建议值设定，待确认） |
| 增量 | `?updated_since=<iso8601>&X-Sync-Token=<token>`，仅返回变更记录 |
| 幂等 | 创建类 POST 必须带 `Idempotency-Key`（UUID），服务端保留 24 小时 |
| 限流 | 令牌桶，按用户与 IP 双维度；默认单用户读 20 次/秒、写 5 次/秒，单 IP 100 次/秒，桶容量取速率 2 倍；超限返回 429 与 `Retry-After`（按建议值设定，待确认） |
| 追踪 | 每请求带 `X-Request-Id`，贯穿日志与响应 `traceId` |

（修订：原 5.2 仅有四条泛化要求，无分页、增量、幂等、限流与追踪；现补齐。）

### 5.4.2 状态码矩阵

| HTTP | 语义 | 使用场景 |
| --- | --- | --- |
| 200 | 成功 | 查询、更新成功 |
| 201 | 已创建 | 创建资源成功，返回 Location |
| 202 | 已接受 | 异步任务已入队（推理、几何分析、切片生成） |
| 204 | 无内容 | 删除成功 |
| 400 | 参数错误 | JSON 结构非法、类型不符 |
| 401 | 未认证 | Token 缺失、过期、签名无效 |
| 403 | 无权限 | 已认证但权限点或数据范围不足 |
| 404 | 不存在 | 资源不存在或已被软删除 |
| 409 | 状态冲突 | 工单状态非法迁移、重复建单、版本号冲突 |
| 422 | 语义校验失败 | 字段值域不合法（如里程倒置、SRID 不匹配） |
| 429 | 限流 | 超出配额 |
| 500 | 服务端异常 | 未捕获异常 |
| 503 | 依赖不可用 | 见 11.3 降级矩阵 |

（修订：原仅 200/400/401/500 四个状态码，无法表达权限、不存在、冲突、语义错误与限流；现补全 13 个。）

### 5.4.3 统一错误体

采用 problem+json 风格：

```json
{
  "type": "https://bt.example/errors/BT-30002",
  "title": "构件编码已存在",
  "status": 409,
  "code": "BT-30002",
  "traceId": "3f8a1c0e9b7d4e21",
  "detail": "编码 B001-K03-PIR-P01-001 已被占用",
  "instance": "/api/v1/components",
  "errors": [{"field": "component_code", "reason": "duplicated"}]
}
```

错误码分段：BT-1xxxx 通用与网关、BT-2xxxx 认证授权、BT-3xxxx 资产台账、BT-4xxxx 点云影像、BT-5xxxx AI 推理、BT-6xxxx 几何分析、BT-7xxxx 工单、BT-8xxxx 文件上传、BT-9xxxx 依赖与系统。

| 错误码 | HTTP | 含义 | 落地状态（按 `app/core/errors.py` 核实） |
| --- | --- | --- | --- |
| BT-10001 | 401 | 未认证 | 【一期已实现】 |
| BT-10002 | 400 | 参数缺失或格式错误 | 【一期已实现】 |
| BT-10003 | 422 | 请求参数校验失败 | 【一期已实现】 |
| BT-20001 | 401 | Token 缺失 | 【二期规划】 |
| BT-20002 | 401 | Token 过期 | 【二期规划】 |
| BT-20003 | 403 | 权限点不足 | 【二期规划】 |
| BT-20004 | 403 | 数据范围越界 | 【二期规划】 |
| BT-30001 | 404 | 构件不存在 | 【一期已实现】 |
| BT-30002 | 409 | 构件编码重复 | 【一期已实现】 |
| BT-30003 | 422 | 层级/父节点非法；编码跨项目重名无法消歧 | 【一期已实现】 |
| BT-30004 | 409 | 编码流水号已用尽 | 【一期已实现】 |
| BT-30005 | 404 | 点云文件不存在 | 【一期已实现】 |
| BT-40001 | 422 | SRID 与项目计算坐标系不一致 | 【一期已实现】 |
| BT-40002 | 422 | 点云密度不足 | 【一期已实现】 |
| BT-50001 | 409 | 推理任务状态非法 | 【二期规划】 |
| BT-50002 | 422 | 影像位姿缺失且无法配准 | 【二期规划】 |
| BT-60003 | 404 | 几何任务不存在 | 【一期已实现】 |
| BT-60004 | 422 | 设计断面参数非法（自交/退化/底板与拱腰圆弧无交点） | 【一期已登记】接线后启用 |
| BT-70001 | 409 | 工单状态非法迁移 | 【一期已实现】 |
| BT-70002 | 409 | 重复建单 | 【一期已实现】 |
| BT-70003 | 409 | 源任务未成功（几何分析未完成，不能派单） | 【一期已实现】 |
| BT-70004 | 422 | 分析类型不支持派单 | 【一期已实现】 |
| BT-70005 | 403 | 班组超出数据范围 | 【一期已实现】 |
| BT-70006 | 404 | 维修班组不存在 | 【一期已实现】 |
| BT-70007 | 422 | 班组未配置负责人（无法确定工单受理人） | 【一期已实现】 |
| BT-70008 | 404 | 工单不存在 | 【一期已实现】 |
| BT-70009 | 422 | 派单附件不可用（非本构件预上传、已被占用或超过 9 张） | 【一期已实现】 |
| BT-70010 | 422 | 现场照片不合规（类型白名单/魔数校验/大小上限） | 【一期已实现】 |
| BT-80001 | 422 | 文件类型不允许 | 【二期规划】 |
| BT-80002 | 422 | 分片校验失败 | 【二期规划】 |
| BT-90001 | 503 | 依赖未就绪（数据库、对象存储等） | 【一期已实现】 |
| BT-90002 | 503 | 对象存储不可用 | 【二期规划】 |
| BT-90003 | 503 | 推理服务不可用 | 【二期规划】 |

（修订：原表把 `BT-10001/10002/10003` 写成"格式非法/限流/未预期异常"，与实现相反；`BT-40003`、`BT-60001`、`BT-60002` 未在登记表注册，已删除。未登记的错误码在 `api_error()` 中会直接 `KeyError`，二期启用前不得写入接口文档。）

### 5.4.4 认证与令牌

| 项 | 规定 |
| --- | --- |
| 令牌类型 | Access Token（JWT，RS256）+ Refresh Token（不透明串，服务端存储） |
| 有效期 | Access 30 分钟、Refresh 8 小时（滑动续期，待业主确认） |
| 刷新 | `POST /api/v1/auth/refresh`，刷新即轮换（旧 Refresh 立即失效） |
| 撤销 | 登出、改密、禁用账号、角色变更时，将 Access 的 `jti` 写入 Redis 黑名单直至过期 |
| 密钥 | 非对称密钥对，`kid` 标识；支持双密钥并存，轮换期 7 天（待确认） |
| 声明 | `sub`、`role`、`scope`、`project_ids`、`jti`、`exp`、`iat` |
| 移动端 | 独立 Refresh，设备指纹绑定，异地登录需二次确认 |

（修订：原"所有 API 需携带 JWT Token"无过期、刷新、撤销与轮换，现补全。）

### 5.4.5 文件上传

上传不再经业务服务中转，采用 MinIO 预签名分片直传：

```text
1. POST /api/v1/files/init      → 返回 uploadId、分片大小、预签名 URL 列表、过期时间
2. 客户端按分片 PUT 直传 MinIO（不经业务服务内存）
3. POST /api/v1/files/complete  → 服务端校验分片 ETag 与文件哈希
4. 服务端异步入队：解压/转码/缩略图/病毒扫描/元数据解析（async_task）
5. 校验通过后写 file_object_link，状态置 READY
```

**为什么不用"Redis 锁"**：分布式锁只能串行化请求，不能限制单请求内存占用；一张超大影像或点云经应用服务中转即可打满进程内存，且吞吐被锁拖低。直传 + 服务端异步校验才是可控路径。

（修订：原 6.2"基于 Redis 锁防止海量图片同时上传导致 OOM"方案不成立，现改为预签名分片直传 + 异步校验。）

## 5.5 接口清单（摘要）

完整契约以 OpenAPI 3.1 文件为准（交付物见 8.1）。下表为章节级摘要，详细字段见 9.3。

| 编号 | 方法 | 路径 | 说明 | 鉴权 | 幂等 |
| --- | --- | --- | --- | --- | --- |
| API-01 | GET | /api/v1/bridges | 桥梁列表（分页） | asset:read | 天然幂等 |
| API-02 | GET | /api/v1/bridges/{id} | 桥梁详情 | asset:read | 天然幂等 |
| API-03 | GET | /api/v1/tunnels | 隧道列表 | asset:read | 天然幂等 |
| API-04 | GET | /api/v1/tunnels/{id} | 隧道详情 | asset:read | 天然幂等 |
| API-05 | GET | /api/v1/components | 构件查询（按结构/类型/层级） | asset:read | 天然幂等 |
| API-06 | GET | /api/v1/components/{code} | 构件详情（含台账） | asset:read | 天然幂等 |
| API-07 | GET | /api/v1/pointcloud/{componentId}/tileset | 取 3D Tiles 入口 | asset:read | 天然幂等 |
| API-08 | GET | /api/v1/pointcloud/{componentId}/range | 按 Range 流式取 COPC 节点 | asset:read | 天然幂等 |
| API-09 | POST | /api/v1/images/init | 影像上传初始化 | media:write | 需 Idempotency-Key |
| API-10 | POST | /api/v1/inference/tasks | 提交推理任务 | ai:invoke | 需 Idempotency-Key |
| API-11 | GET | /api/v1/inference/tasks/{id} | 查询推理任务与结果 | ai:invoke | 天然幂等 |
| API-12 | GET | /api/v1/defects/candidates | 候选缺陷列表 | defect:read | 天然幂等 |
| API-13 | POST | /api/v1/defects/candidates/{id}/confirm | 确认/驳回候选 | defect:confirm | 需 Idempotency-Key |
| API-14 | POST | /api/v1/geometry/tasks | 提交几何分析任务 | geometry:invoke | 需 Idempotency-Key |
| API-15 | GET | /api/v1/geometry/tasks/{id} | 查询几何分析结果 | geometry:invoke | 天然幂等 |
| API-16 | POST | /api/v1/workorders | 创建工单 | order:create | 需 Idempotency-Key |
| API-17 | POST | /api/v1/workorders/{id}/transitions | 工单流转 | order:transition | 需 Idempotency-Key |
| API-18 | POST | /api/v1/workorders/{id}/acceptance | 验收提交/通过/退回 | order:accept | 需 Idempotency-Key |
| API-19 | POST | /api/v1/files/init | 附件上传初始化 | file:write | 需 Idempotency-Key |
| API-20 | GET | /api/v1/sync/pms/changes | 增量同步给 PMS | sync:read | 天然幂等 |

# 第6章 性能、可视化与安全设计

## 6.1 性能指标条件卡片

每条指标必须同时满足其卡片中全部条件才可判定。未列条件的指标无效。

| 编号 | 指标 | 数据集 | 硬件 | 软件/网络 | 口径 | 前置/后处理 | GPU |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P-01 | 首帧时间 TTFI ≤ 3 s | 单座结构 5–10 GB COPC 转换后的 3D Tiles | 商用笔记本（核显） | Chrome 最新稳定版，1920×1080，内网 1 GbE，冷缓存 | 从发起 tileset 请求到首帧可见（不要求全精度） | 不含初始化 SDK 时间 | 否 |
| P-02 | 目标 LOD 到达 ≤ 8 s | 同上 | 同上 | 同上，热缓存 | 视点静止后，视野内瓦片 SSE 达标 | 不含用户继续漫游 | 否 |
| P-03 | 漫游帧率 P50 ≥ 30 FPS | 同上，帧内点数预算 ≤ 150 万 | 核显（Intel Iris Xe 同档） | 1920×1080，单标签页 | P50 与 P05 分别统计 60 s 连续漫游 | 关闭阴影与后期 | 否 |
| P-04 | 单 tile 推理 P50 ≤ 120 ms | 640×640 切片 | 推理服务器 GPU（型号待定） | Triton + ONNX Runtime | 含预处理与 NMS 后处理 | batch=1 | 是 |
| P-05 | 单幅 4000×3000 端到端 ≤ 3 s | 12 MP 原图，切 24–30 个 tile | 同上，GPU 显存 ≥ 8 GB | 同上 | 从提交到结果入库，P95 | 含切片、推理、合并、落库 | 是 |
| P-06 | 几何分析单墩 ≤ 90 s | 单墩点云 ≤ 5000 万点 | 8 核 CPU / 32 GB 内存 | 无 GPU | 从任务开始到结果落库 | 含配准复核与质量判据 | 否 |
| P-07 | 接口查询 P95 ≤ 300 ms | 单项目 10 万构件 | 4 核 / 8 GB | 数据库与缓存同机房 | 不含大文件流式下载 | — | 否 |
| P-08 | 单瓦片点云渲染帧率平稳（一期实测） | WHU-TLS 11-Tunnel 采样，原始 560451 点 → 下采样 280226 点，单瓦片 4.0 MB | 开发机（商用笔记本档） | Chrome 最新稳定版，1920×1080，本地 Vite 静态服务 | P50 帧率在视点静止后连续 60 s 采样；不含底图瓦片失败重试 | 单瓦片、无 LOD 分级（单文件 tileset） | 否 |

**一期 3D Tiles 实测数据（V1.2b 回写）**：以 `backend/tests/data/tunnel_sample.ply`（560451 点，X∈[-102.4, 59.7]、Y∈[-134.2, 233.3]、Z∈[-3.8, 4.5] m 的 ENU 局部坐标）为输入，经 `backend/scripts/generate_3dtiles.py` 按 stride=2 下采样至 280226 点，输出单瓦片 `tunnel.pnts`（3362712 B 位置 + 821K RGB ≈ 4.0 MB）与 `tileset.json`（893 B）。瓦片挂载于前端 Vite 静态目录 `frontend/public/tiles/tunnel/`，凭 `GET /tiles/tunnel/tileset.json` 与 `GET /tiles/tunnel/tunnel.pnts` 均返回 200；Cesium 侧 `Cesium3DTileset.fromUrl()` 加载后根瓦片内容状态进入 READY，`selectedTiles = 1`，渲染帧率平稳，无解析告警。该实测仅覆盖"单瓦片、无 LOD、无属性表"的最简链路，P-01 至 P-03 的 5–10 GB 多瓦片 LOD 指标仍按二期 PDAL/COPC 管线的条件卡片验收，不得由本条实测外推。

**统一规模表述**：单座桥隧典型数据集 5–10 GB；全线累计规模 TB 级，具体数值以业主提供的桥隧清单为准（待业主确认）。原文中"10GB 级"与"TB 级"并列且未说明层级关系，现按"单座 / 全线"两级统一。

（修订：原 6.1 三条指标无数据集、硬件、网络、口径、是否含前后处理、是否需 GPU；现改为条件卡片，并把 4000×3000 推理指标拆为单 tile 与整幅端到端两条，明确需 GPU。）

**关键澄清**：4000×3000 影像在 640 输入下必然切片为 20–60 块，纯 CPU 端到端为秒级，P-05 隐含 GPU 推理服务器。若业主无 GPU，须将 P-05 调整为"≤ 20 s（CPU）"或降低分辨率（待业主确认）。

推理节点最低配置（A 类结论）：单卡显存 ≥ 8 GB、FP16 算力不低于同级主流推理卡；纯 CPU 部署时 P-05 按 ≤ 20 s、P-04 按单 tile ≤ 600 ms 验收。渲染判定基线：1080p、核显档（Intel Iris Xe 同档）为 P-01 至 P-03 的机型基准。（按建议值设定，待确认）

## 6.2 可视化路线

### 6.2.1 路线选择

采用"COPC 归档 + 离线预转 3D Tiles + CesiumJS 原生加载"路线。

| 路线 | 结论 | 理由 |
| --- | --- | --- |
| 预转 3D Tiles | 采用 | CesiumJS 原生支持，无需自研解码器；LOD 与调度由转换阶段固化，前端可控；失败面小 |
| 自研 COPC Range 加载器 | 不采用（本版） | 需自行实现节点调度、解码、GPU 缓冲管理与拾取，工作量与风险高，且与 Cesium 渲染管线耦合深 |

选择理由：CesiumJS 不原生消费 COPC。若直接以 COPC 为展示格式，必须自研 HTTP Range 解码与八叉树调度，这一组件的工作量与风险远超其他模块，且文档此前未设计（修订：原 2.3 与 2.1 之间存在"COPC 直接进 CesiumJS"的隐性断层，现明确路线）。

### 6.2.2 转换管线

**一期已落地实现（V1.2b）**：转换脚本为 `backend/scripts/generate_3dtiles.py`，**纯 Python 手写 3D Tiles（pnts）二进制打包器**，仅依赖 numpy，不依赖 PDAL、py3dtiles 或任何外部二进制工具。实现要点：

- **特征表 JSON / Binary 分离**：FeatJson 以 UTF-8 JSON 声明 `POINTS_LENGTH`、`POSITION`、`RGB` 等字段，FeatBinary 按字段 `byteOffset` 顺序存放 `float32×3×N` 位置与 `uint8×3×N` 颜色；
- **字节对齐**：FeatJson 末尾以空格填充，使 `28 + featJsonByteLength` 落在 8 字节边界，保证 `Float32Array` 起始偏移为 4 的整数倍（Cesium 解析硬约束）；
- **坐标链路**：源 PLY 的 ENU 局部坐标经 WGS84 ENU→ECEF 旋转矩阵写入 `root.transform`（4×4 列主序），`RTC_CENTER` 不写入（避免 ENU 与 ECEF 量纲混加）；
- **产物**：单文件 `tileset.json`（`asset.version = 1.0`、`region` 包围体、`root.geometricError` 非零）+ 单瓦片 `.pnts`。

一期运行方式与产物位置：

```text
运行位置：仓库根目录 ai_handoff/
运行命令：backend\.venv\Scripts\python.exe backend/scripts/generate_3dtiles.py
输入：backend/tests/data/tunnel_sample.ply（560451 点）
输出：frontend/public/tiles/tunnel/tileset.json
      frontend/public/tiles/tunnel/tunnel.pnts（280226 点，约 4.0 MB）
前端访问：GET /tiles/tunnel/tileset.json（Vite 静态目录直挂，无需后端）
```

**二期目标管线（生产口径，沿用原设计）**：

```text
COPC（全密度归档）
  → 读取瓦片八叉树（PDAL 或等价工具，选型待定）
  → 按空间网格切分瓦片，逐级重采样生成 LOD
  → 输出 3D Tiles 点云瓦片（含每瓦片 bbox、geometricError、点属性）
  → 写入 MinIO，登记 pointcloud_file.tileset_key
  → 校验：抽样点数与坐标一致性，误差 ≤ 1 mm
```

一期序列化层（pnts 打包、特征表布局、对齐、ENU→ECEF 变换）可直接复用为二期管线的"输出适配层"：二期只需把 PDAL 的体素下采样/八叉树切分结果交给同一打包器，即可避免重复实现二进制格式。二期实现时的强制约束见附录 E.3。

转换参数：瓦片几何误差逐级减半，根节点误差由数据结构范围确定；单瓦片点数上限默认 50 万（按建议值设定，待确认，一期实测 28 万点/瓦片）；属性仅保留渲染与拾取必需字段以控制体积。**单文件 tileset 的根节点 `geometricError` 必须为大于 0 的实数**（一期取 1e7 量级以确保任意视距都触发首瓦片加载），详见附录 E.1。

### 6.2.3 LOD 与调度策略

| 项 | 策略 |
| --- | --- |
| LOD 生成 | 由转换阶段按八叉树层级生成，不设固定"3–5 级"；层级数由数据范围与瓦片粒度决定 |
| 前端调度 | 依据 `maximumScreenSpaceError`（默认 16，可按机型调整）决定加载层级 |
| 预取 | 视锥内相邻瓦片与下一级子节点预取，限制并发请求数为 6（可配） |
| 点数预算 | 单帧上限 150 万点；超限时先降点大小、再降 SSE（按建议值设定，待确认） |
| 缓存 | 浏览器 IndexedDB 缓存已下载瓦片，LRU 上限按设备内存设定 |

### 6.2.4 拾取与台账联动

采用 GPU 拾取：渲染时以构件 ID 编码颜色写入离屏缓冲，点击时读回像素反解 `component_code`；低 LOD 下若拾取失败，回退到 CPU 射线与构件 OBB 求交。拾取结果统一走 `GET /api/v1/components/{code}` 获取台账。

### 6.2.5 离线底图与地形

| 资产 | 方案 |
| --- | --- |
| 影像底图 | 本地 XYZ/WMTS 瓦片服务（由业主提供或项目自采），禁止引用在线源 |
| 地形 | 本地 quantized-mesh 地形，若无则关闭地形并置平面 |
| 星空/大气 | 关闭，使用纯色背景降低开销 |
| SDK 配置 | 禁用 Ion 令牌与默认 imagery provider；构建期移除外部域名白名单 |
| 验收 | 断网环境下全流程可用，抓包无外部请求 |

（修订：原 6.2 仅声明"不依赖天地图与 Cesium Ion"，未给离线底图与地形实现方案；现补齐。）

## 6.3 容量与 SLO

| 项 | 目标 | 依据 |
| --- | --- | --- |
| 并发在线用户 | 待业主确认（建议按养护班组规模 × 1.5 估算） | 推算方法见 14.4.3 |
| 推理 QPS | 待业主确认（建议按日均影像量 / 有效工时） | — |
| 上传吞吐 | 待业主确认（峰值按外业回归日集中上传估算） | — |
| 可用性 SLO | 99.5%（年停机 ≤ 43.8 h），待业主确认 | 内网系统，无公网 SLA 约束 |
| 接口 P95 | ≤ 300 ms（P-07） | 6.1 |
| 任务成功率 | 推理任务 ≥ 99%，几何分析 ≥ 98%（待实测） | 含重试后 |
| 数据保留 | 原始点云与影像长期保留；日志 ≥ 6 个月；审计 ≥ 3 年（待业主确认） | 合规要求待确认 |

## 6.4 安全设计

| 项 | 措施 |
| --- | --- |
| 传输 | 全站 HTTPS，内网亦启用；禁用弱套件 |
| 存储 | 点云与影像仅存 MinIO，桶策略私有，禁止匿名读；业务库不存二进制 |
| 凭据 | 数据库口令、MinIO AK/SK、模型密钥统一由密钥管理服务下发（Vault 或等价方案，选型待定），禁止硬编码与写入镜像 |
| 凭据轮换 | 90 天轮换一次（待业主确认），轮换脚本纳入运维手册 |
| 越权防护 | 所有对象访问先校验数据范围（见 13.1），再签发预签名 URL，有效期 ≤ 15 分钟 |
| 上传安全 | 类型白名单、大小上限、病毒扫描、图片解码隔离进程 |
| 审计 | 见 12.2 |
| 等保 | 按 GB/T 22239 相应等级建设与测评，等级待业主确认 |
| 测绘合规 | 点云属测绘成果，采集、存储、提供须符合测绘管理规定；对外提供前须审批（待业主确认口径） |

（修订：原 6.2"防止物理路径遍历"对对象存储是错位描述，真实风险为桶策略与预签名越权；现改为桶策略、数据范围校验与短时效预签名。）

# 第7章 测试与验收标准

## 7.1 几何精度验收

| 项 | 规定 |
| --- | --- |
| 比对基准 | 全站仪/水准实测值，测量精度须高于被测指标 3 倍以上 |
| 样本量 | 每类结构不少于 10 个构件，每个构件不少于 3 个断面/高程（待业主确认） |
| 重复观测 | 每个对象独立观测 2 次，取均值；两次互差 ≤ 指标值的 50% |
| 判定 | 以中误差评定：|偏差| ≤ 限值，且 95% 分位不超限 |
| 限值 | 垂直度 ±5 mm、净空 ±10 mm 为目标值，最终以业主确认的设计容许值为准（待业主确认） |
| 不合格处理 | 该批次判不合格，允许一次补测；补测仍不合格则触发算法复核与参数重标定 |

（修订：原 7.1 仅给两条误差数值，无基准、样本量、重复观测与判定规则，不可验收。）

**物理极限备注（写入验收报告首页）**：基于 200k 点的点云密度与 20 mm 噪声条件，垂直度验收偏差经验证约为 **±0.5‰**；若需更高精度，须提高硬件扫描密度（更高点密度或更低单点噪声）。表中"垂直度 ±5 mm"是水平偏移量限值，按具体墩高换算：墩高 10 m 时 ±0.5‰ 约等于 ±5 mm。二者不可直接混用单位；验收报告须同时列明倾斜率（‰）与对应的水平偏移（mm）及所采用的墩高。

**精度责任边界**：±0.5‰ 是"200k 点/20 mm 噪声"采集条件下的可达精度。若业主要求的验收指标严于该条件可支撑的范围，须在合同中约定提高扫描密度、增加测站/控制点或改用更高精度仪器，否则不承担超出采集条件的精度责任。

## 7.2 AI 识别验收

| 项 | 规定 |
| --- | --- |
| 测试集 | 独立于训练集与验证集；按桥型、隧型、光照、里程分层抽样；由业主与第三方共同标注 |
| 标注规范 | 类别定义、最小可标尺寸、边界规则、歧义处理，单独成册（交付物见 8.1） |
| 指标定义 | 实例级：召回率 R = TP/(TP+FN)；漏检率 = 1 − R。面积级指标须单独定义，两者不可混用 |
| 目标值 | 裂缝召回 ≥ 85%（等价漏检 ≤ 15%）；渗漏水召回 ≥ 85%；精确率 ≥ 70%（待业主确认） |
| 样本量 | 按 Wilson 区间估算：p=0.85、95% 置信、半宽 5% 时，正样本约需 196 个；负样本按 1:2 配比 |
| 报告 | 输出混淆矩阵、按类别指标、置信区间、失败样本清单 |

**关于"漏检率 ≤15% 与召回率 ≥85% 是否等价"**：在实例级、二分类口径下，漏检率 = 1 − 召回率，两个数值是同一条约束；原文档并列书写，说明指标口径未定义。若"漏检率"按裂缝长度或面积统计，则必须单列定义并说明其与实例级的不同，不能互为替代。

（修订：原 7.1 并列两条等价指标且无测试集来源与样本量，现重新定义口径并给出样本量估算。）

## 7.3 业务流转与压力测试

| 项 | 规定 |
| --- | --- |
| E2E 场景 | 上传影像 → 推理 → 候选缺陷 → 人工确认 → 建单 → 派发 → 处置 → 验收 → 闭环 |
| 并发 | 按 6.3 目标并发的 1.2 倍加压（目标值待业主确认） |
| 持续时长 | 不少于 2 小时 |
| 判据 | 接口 P95 ≤ 300 ms；错误率 ≤ 0.5%；无死锁；任务队列积压可在 30 分钟内消化；工单状态全部正确 |
| 断网测试 | 移动端离线接单 → 恢复网络 → 增量同步，无重复单、无丢单 |

（修订：原 7.2 判据为"无卡顿、无数据死锁"等定性描述，不可判定；现改为可量化判据。）

## 7.4 验收流程与复测窗口

```text
1. 交付测试数据集与标注规范（T+2 月末）
2. 承建方自测并提交报告（T+3 月第 2 周）
3. 第三方/业主复测（T+3 月第 3–4 周）
4. 不合格项整改（≤ 3 周）
5. 复测窗口（T+4 月第 1–2 周）——仅一次，覆盖全部不合格项
6. UAT 签署
```

复测窗口必须在合同中单列，不得与开发工期混同（修订：原计划把精度验收压在 T+3 月内且无复测窗口）。

# 第8章 交付清单与实施计划

## 8.1 交付物

| 类别 | 交付物 | 验收方式 |
| --- | --- | --- |
| 源码 | 前后端、算法、转换管线全部源码与构建脚本（Dockerfile、compose、CI 配置） | 可在干净环境一键构建 |
| 镜像 | 各服务镜像与版本清单、SBOM | 镜像可启动并通过冒烟 |
| 契约 | OpenAPI 3.1 文件 + 渲染页面 + 示例请求集 | 与实现一致性抽查 |
| 数据库 | PostGIS 建表 SQL、迁移脚本（Flyway/Liquibase 选型待定）、种子数据 | 空库可一键初始化 |
| 模型 | ONNX 模型、分类映射字典、模型卡（训练数据说明、指标、局限） | 与测试报告一致 |
| 数据 | 训练集、验证集、测试集清单与标注规范、标注工具说明 | 可复现指标 |
| 报告 | 精度测试报告（含原始观测记录）、AI 评估报告、性能压测报告、安全测评报告 | 含样本与判定过程 |
| 手册 | 安装部署、用户操作、运维（备份恢复、故障处置、密钥轮换） | 按手册可完成演练 |
| 培训 | 管理员与作业员培训材料、验收用例集 | 完成培训签到 |

（修订：原 8.1 仅列镜像、模型文件、建表 SQL 与两本手册，缺源码、契约、测试报告、训练数据与标注规范、运维手册。）

## 8.2 实施计划

| 阶段 | 时间 | 主要工作 | 出口条件 |
| --- | --- | --- | --- |
| S0 准备 | T-1 月至 T0 | 桥隧清单与设计资料收集；控制网布设与复测；坐标系与 SRID 确认；采集方案与安全交底 | 控制点成果验收；坐标系确认单签署 |
| S1 采集与标注 | T0 至 T+1 月 | 外业扫描与影像采集；站间配准；样本标注；标注规范定稿 | 数据质量等级 A/B 占比 ≥ 90%；标注一致性 Kappa ≥ 0.8（待确认） |
| S2 底座 | T+1 月 | COPC 归档、3D Tiles 转换、三维底座、构件台账 | P-01/P-03 达标；构件编码入库 |
| S3 算法 | T+1 月至 T+2 月 | 4.1 与 4.2 实现与调参；精度初测 | 几何精度初测达标率 ≥ 80% |
| S4 AI 与集成 | T+2 月 | 推理服务、候选池、2D—3D 映射；工单闭环；PMS 对接 | E2E 跑通 |
| S5 精度测试 | T+3 月 | 第三方复测、压测、安全测评 | 报告出具 |
| S6 UAT 与整改 | T+3 月末 至 T+4 月 | 整改 + 复测窗口 + UAT | 验收签署 |

**工期说明**：T-1 至 T0 的数据准备、控制测量、现场采集与标注不占用开发工期，但必须前置；若缺失该阶段，几何精度指标不具可达性。原计划三个月内完成全部内容（含精度验收与 UAT），未计数据准备与复测，现重排并显式拆出复测窗口（修订：原 8.2 的 T+1/T+2/T+3 三段计划）。

## 8.3 里程碑准入准出

| 里程碑 | 准入 | 准出 |
| --- | --- | --- |
| M1 底座可用 | 控制点成果、坐标系确认 | P-01/P-03 达标，构件可拾取 |
| M2 算法可用 | 数据质量达标、设计资料齐备 | 精度初测达标率 ≥ 80% |
| M3 业务闭环 | 推理服务上线、候选池可用 | E2E 全流程通过 |
| M4 验收 | 测试集与标注规范冻结 | 精度、AI、压测、安全四项报告齐全 |

# 第9章 模块划分与接口设计

## 9.1 模块清单与数据归属

（新增：原文档只有"服务层：FastAPI 微服务、PDAL、YOLO 引擎"一行，无模块边界、无数据归属、无调用关系，无法分工开发。）

| 编号 | 模块 | 职责（一句话） | 依赖 | 写入的表 | 调用方式 | 落地状态（按 2026-11 代码核实） |
| --- | --- | --- | --- | --- | --- | --- |
| M01 | 网关 gateway | 统一入口、路由、鉴权前置、限流、请求追踪 | M02 | 无 | 同步 | 【二期规划】 |
| M02 | 认证授权 auth | 登录、令牌签发与撤销、权限点与数据范围判定 | 无 | sys_user、sys_user_role、audit_log | 同步 | 【二期规划】一期仅有 `BT_DEV_ANONYMOUS=1` 开发旁路 |
| M03 | 资产台账 asset | 桥梁/隧道/构件台账与编码、双向映射 | M08 | project、bridge、tunnel、component | 同步 | 【一期已实现】`/components` 查询/创建、编码流水号、GeoJSON 4490 输出 |
| M04 | 点云服务 pointcloud | COPC 归档管理、Range 取数、3D Tiles 索引与调度 | M08、M10 | pointcloud_file | 同步（取数）/异步（切片） | 【一期部分实现】Mock 占位 + HTTP Range + tileset 入口；COPC 解析与真实 3D Tiles 转换未接 |
| M05 | 影像服务 media | 影像元数据、位姿、GSD、预处理 | M08、M10 | image_file | 同步（登记）/异步（预处理） | 【二期规划】 |
| M06 | 推理服务 inference | 模型加载、切片推理、结果结构化 | M05、M10 | defect_candidate、async_task | 异步 | 【二期规划】仓库内无 onnxruntime/推理代码 |
| M07 | 几何分析 geometry | 墩身垂直度、隧道净空计算与质量判据 | M04、M10 | geometry_result、geometry_baseline | 异步 | 【一期已实现】`VERTICALITY/CLEARANCE/SECTION_SHAPE` + worker；设计断面精确侵限模块已落盘，尚未接入 API 管线 |
| M08 | 文件服务 file | 预签名、分片校验、对象生命周期、附件 | 无 | attachment、file_object_link、async_task | 同步 | 【二期规划】仅 `docker-compose.dev.yml` 编排 MinIO，应用侧未接 |
| M09 | 工单服务 workorder | 缺陷台账、工单状态机、SLA、事件日志 | M03、M11 通知 | defect_ledger、defect_review、work_order、work_order_event | 同步 | 【二期规划】 |
| M10 | 任务调度 scheduler | 异步任务入队、重试、超时、进度、取消 | 无 | async_task | 异步 | 【一期部分实现】`async_task` + `FOR UPDATE SKIP LOCKED`；进度与状态流转已实现，重试/超时/取消未实现 |
| M11 | 通知服务 notify | 站内信、短信、待办推送、超时升级 | M09 | notification | 异步 | 【二期规划】 |
| M12 | 同步服务 pms-sync | 与 PMS 的增量推送、对账、冲突处理 | M09 | sync_log | 异步 | 【二期规划】 |

**验收边界**：上一节 2.1 的验收口径同样适用于本表——【一期已实现】与【一期部分实现】纳入本期验收，【二期规划】不在本期范围。M07 的"设计断面精确侵限"是本期已交付的算法模块，但 API 管线当前返回 `clearance_reference_mm`（相对拟合圆弧的净空参考），两者不得混称"限界侵限"；接线完成前，对外报告只写净空参考值。

边界说明：M04 只负责"取点云"，不负责"算垂直度"；M07 只负责"算几何量"，不负责"判定责任"（判定阈值来自设计文件，由 M09 的台账与报告呈现）；M06 只产出候选，不写正式台账（正式台账由 M09 在人工确认后写入）。

## 9.2 依赖方向

```text
M01 网关 → M02 认证
M01 → M03 / M04 / M05 / M07 / M09 / M08（路由转发）
M03 → M08
M04 → M08、M10
M05 → M08、M10
M06 → M05、M10
M07 → M04、M10
M09 → M03、M11
M12 → M09
```

依赖方向无环。跨域数据只读不写：任何模块不得直接写其他模块归属的表；需要合并查询时走接口或在读模型中组装。（新增：原文档未定义依赖方向与数据归属，易出现"人人可写、无人负责"。）

## 9.3 关键接口契约

以下为必须实现的接口，字段级契约以 OpenAPI 文件为准。所有接口默认需要 Access Token；下表"鉴权"列给出权限点。

### API-01 / API-02 桥梁列表与详情

| 项 | 内容 |
| --- | --- |
| 方法与路径 | `GET /api/v1/bridges`、`GET /api/v1/bridges/{id}` |
| 入参 | 列表：`project_id`（必填）、`keyword`、`tech_grade`、`limit`、`cursor`；详情：路径 id |
| 出参 | 列表：`items[]`（id、struct_code、name、mileage、tech_grade、updated_at）、`next_cursor`；详情：含 `geom_line`（GeoJSON 4490）、`component_count`、`last_inspection_at` |
| 鉴权 | `asset:read` + 数据范围校验 |
| 幂等 | 天然幂等 |
| 错误码 | BT-20003、BT-20004、BT-10001 |

### API-05 / API-06 构件查询与详情

| 项 | 内容 |
| --- | --- |
| 方法与路径 | `GET /api/v1/components?struct_id=&level=&comp_type=`、`GET /api/v1/components/{code}` |
| 出参 | 构件树或平铺列表；详情含 `geom`、`geom_solid`、`status`、`pier_type`、关联文件数、最近缺陷数 |
| 鉴权 | `asset:read` + 数据范围 |
| 幂等 | 天然幂等 |
| 错误码 | BT-30001、BT-20004 |

### API-07 / API-08 点云瓦片与流式取数

| 项 | 内容 |
| --- | --- |
| 方法与路径 | `GET /api/v1/pointcloud/{componentId}/tileset`、`GET /api/v1/pointcloud/{componentId}/range?node=&offset=&length=` |
| 出参 | tileset：3D Tiles `tileset.json`（含 URL 重写为预签名地址）；range：二进制字节流，响应头含 `X-Point-Count`、`Content-Range` |
| 鉴权 | `asset:read` + 数据范围；预签名有效期 ≤ 15 分钟 |
| 幂等 | 天然幂等 |
| 错误码 | BT-20004、BT-90002 |

设计说明：tileset 请求只返回索引，不返回数据；瓦片本体由 Cesium 按需向 MinIO 预签名地址请求，业务服务不承担带宽。Range 接口保留给算法侧与调试，前端默认不走该路径。

### API-09 影像上传初始化与预处理

| 项 | 内容 |
| --- | --- |
| 方法与路径 | `POST /api/v1/images/init` |
| 入参 | `struct_code`（契约字段；原 `component_id` 写法作废，影像挂在结构级）、`file_name`、`file_size`（字节）、`content_type`、可选 `captured_at`、可选 `pos`（lon/lat/alt） |
| 出参 | `upload_id`、`part_size`、`presigned_urls[]`、`expires_at` |
| 鉴权 | `media:write` + 数据范围 |
| 幂等 | `Idempotency-Key` 必填；同一 key 重复提交返回已有上传记录（契约不再要求 `sha256`，完整性由分片 ETag 与完成校验承担） |
| 错误码 | BT-80001、BT-80002、BT-20004 |

### API-10 / API-11 推理任务提交与查询

| 项 | 内容 |
| --- | --- |
| 方法与路径 | `POST /api/v1/inference/tasks`、`GET /api/v1/inference/tasks/{id}` |
| 入参 | `image_ids[]`、`model_version`、`defect_types[]`、`priority` |
| 出参 | 提交：202 + `task_id`、`status=QUEUED`；查询：`status`、`progress`、`result_ref`、`counts{by_type}` |
| 鉴权 | `ai:invoke` |
| 幂等 | `Idempotency-Key` 必填，与 `image_ids + model_version` 组成唯一约束 |
| 错误码 | BT-50001、BT-90003 |

### API-13 候选缺陷确认

| 项 | 内容 |
| --- | --- |
| 方法与路径 | `POST /api/v1/defects/candidates/{id}/confirm` |
| 入参 | `decision`（APPROVE/REJECT/MODIFY）、`defect_type`、`severity`、`geom_point`（可修正）、`note` |
| 出参 | APPROVE 返回 `defect_id` 与 `defect_no`；REJECT 返回 `review_id` |
| 鉴权 | `defect:confirm` |
| 幂等 | `Idempotency-Key` 必填；同一候选重复确认返回 409 并给出首次结果 |
| 错误码 | BT-70002、BT-30001 |

### API-14 / API-15 几何分析任务与结果

| 项 | 内容 |
| --- | --- |
| 方法与路径 | `POST /api/v1/geometry/tasks`、`GET /api/v1/geometry/tasks/{id}` |
| 入参 | `component_code`、`analysis_type`（`VERTICALITY` / `CLEARANCE` / `SECTION_SHAPE`）、可选 `pointcloud_id`、`baseline_id`（可空）、`params`（预留；一期未固化该对象，按默认切片 1.0 m、法向厚度 0.5 m 执行） |
| 出参 | 提交：202 + `task_id` + `Location` 头；查询：`status`、`progress`、`error_code`、`error_detail`、`result`（`tilt_permille`、`radius_m`、`clearance_reference_mm`、`sections[]`、`degraded`/`degrade_reason`、`worst_section_stake` 等，字段以契约 `GeometryTaskResult` 为准；`intrusion_area_sqm`/`intrusion_depth_mm`/`intrusion_count`/`clearance_margin_m` 已定义，设计断面接线前返回 `null`） |
| 鉴权 | `geometry:invoke` |
| 幂等 | `Idempotency-Key` 必填（8–64 字符）；同一 key 复用已受理任务，不按 24 h 过期；若 key 已被其它 `biz_type` 占用返回 400 `BT-10002` |
| 错误码 | `BT-10002`、`BT-30001`、`BT-30003`、`BT-30005`、`BT-40002`、`BT-60003`（`BT-60004` 已登记，设计断面接线后启用） |

### API-16 至 API-18 工单创建、流转、验收

| 项 | 内容 |
| --- | --- |
| 方法与路径 | `POST /api/v1/workorders`、`POST /api/v1/workorders/{id}/transitions`、`POST /api/v1/workorders/{id}/acceptance` |
| 入参 | 创建：`defect_id`、`priority`、`handler_id`、`deadline`；流转：`event`、`opinion`、`attachment_ids[]`；验收：`result`（PASS/RETURN）、`opinion`、`attachment_ids[]` |
| 出参 | 创建：201 + `order_no`；流转：200 + `from_status`、`to_status`、`sla_deadline` |
| 鉴权 | `order:create` / `order:transition` / `order:accept` |
| 幂等 | 三者均需 `Idempotency-Key`；同一 `defect_id` 存在未结单时返回 409 |
| 错误码 | BT-70001、BT-70002 |

### API-19 附件上传

| 项 | 内容 |
| --- | --- |
| 方法与路径 | `POST /api/v1/files/init` |
| 入参 | `object_type`（WORKORDER/DEFECT/COMPONENT）、`object_id`、`file_name`、`size`、`sha256` |
| 出参 | 同 5.4.5 |
| 鉴权 | `file:write` + 数据范围校验 |
| 幂等 | `Idempotency-Key` 必填 |
| 错误码 | BT-80001、BT-80002 |

## 9.4 关键时序

### 9.4.1 上传影像 → 推理 → 候选缺陷 → 人工确认 → 建单

| 步骤 | 参与者 | 动作 | 失败分支 |
| --- | --- | --- | --- |
| 1 | 客户端 | 调 `POST /images/init`，取预签名 | 401/403：提示无权限；429：按 `Retry-After` 重试 |
| 2 | 客户端 → MinIO | 分片直传 | 单片失败：重传该片（最多 3 次）；ETag 不符：整文件重传 |
| 3 | 客户端 | 调 `POST /files/complete` | 哈希不符：返回 BT-80002，置文件 REJECT |
| 4 | M08 → M10 | 入队预处理（转码、缩略图、位姿解析） | 解码失败：任务 FAILED，通知上传者 |
| 5 | 客户端 | 调 `POST /inference/tasks` | 503：队列不可用，提示稍后重试，不落半成品 |
| 6 | M06 → M10 | 切片推理，逐片写结果 | 超时：任务 TIMEOUT，保留已完成分片结果 |
| 7 | M06 → M09 | 结果写 `defect_candidate`（PENDING） | 写库失败：任务重试，幂等键防重复 |
| 8 | 作业员 | 在三维界面确认/驳回 | 网络中断：本地暂存，恢复后重放（幂等键） |
| 9 | M09 | APPROVE 写台账并发 `DefectConfirmed` 事件 | 并发确认：版本号冲突返回 409 |
| 10 | M09 | 自动建议建单（可选人工触发） | 重复建单：唯一约束拦截，返回已有工单号 |

### 9.4.2 点云加载与拾取查台账

| 步骤 | 参与者 | 动作 | 失败分支 |
| --- | --- | --- | --- |
| 1 | 前端 | `GET /pointcloud/{id}/tileset` | 404：该构件无点云，展示"未采集"占位 |
| 2 | 前端 → MinIO | 按需请求瓦片（预签名） | 403：预签名过期，重新获取索引 |
| 3 | 前端 | 渲染并按 SSE 调度 | 点数超预算：先降点大小再降 SSE |
| 4 | 用户 | 点击模型 | GPU 拾取失败：回退 OBB 射线求交 |
| 5 | 前端 | `GET /components/{code}` | 404：编码失效，提示构件已合并/替换 |

### 9.4.3 几何分析异步作业

| 步骤 | 参与者 | 动作 | 失败分支 |
| --- | --- | --- | --- |
| 1 | 业务人员 | `POST /geometry/tasks` | 参数非法：422 |
| 2 | M07 → M10 | 入队，返回 202 | 队列满：429 或排队位次 |
| 3 | M10 → M07 | 取任务，读点云与设计资料 | 点云缺失：FAILED + BT-40002 |
| 4 | M07 | 预处理、分层、拟合、质量判据 | 有效层不足：SUCCEEDED 但 `quality_level=INSUFFICIENT`，结果仅供参考 |
| 5 | M07 → DB | 写 `geometry_result` | 写失败：重试 3 次后 FAILED |
| 6 | M07 → M11 | 生成通知 | 通知失败：降级为站内信，不回滚结果 |

## 9.5 异步任务模型

| 项 | 规定 |
| --- | --- |
| 任务表 | `async_task`（字段见 5.3.10） |
| 状态 | QUEUED → RUNNING → SUCCEEDED / FAILED / TIMEOUT / CANCELLED；失败可重试则经 RETRYING 回 QUEUED |
| 幂等 | `idem_key` 唯一；执行前先查是否已成功，成功即直接返回 `result_ref` |
| 重试 | 仅对可重试错误（超时、依赖 5xx、连接中断）重试；最多 3 次，退避 1、5、15 分钟；不可重试错误（422 类）直接 FAILED |
| 超时 | INFERENCE 单图 10 分钟、批量 60 分钟；GEOMETRY 单构件 30 分钟；TILE_BUILD 单座 8 小时；EXPORT 30 分钟（待实测确认） |
| 结果 | 小结果写业务表并记 `result_ref=表名:主键`；大结果（如切片包、报告）写 MinIO 并记对象键 |
| 进度 | RUNNING 期间每 5 秒更新 `progress`；前端轮询间隔 2 秒，支持 ETag 304 |
| 取消 | 仅 QUEUED 与 RUNNING 可取消；RUNNING 取消通过协作式检查点实现，不强杀进程 |
| 清理 | SUCCEEDED 结果随业务对象保留；FAILED/TIMEOUT 记录保留 30 天（待确认） |

# 第10章 工单状态机

## 10.1 状态与枚举

| 状态码 | 含义 | 是否终态 | 是否计时 |
| --- | --- | --- | --- |
| DRAFT | 草稿 | 否 | 否 |
| PENDING_CONFIRM | 待确认（缺陷已确认，工单待主管确认） | 否 | 是 |
| CONFIRMED | 已确认，待派发 | 否 | 是 |
| ASSIGNED | 已派发，待接单 | 否 | 是 |
| IN_PROGRESS | 处理中 | 否 | 是 |
| PENDING_ACCEPT | 待验收 | 否 | 是 |
| CLOSED | 已闭环 | 是 | 停止 |
| REJECTED | 已驳回 | 否 | 停止 |
| SUSPENDED | 挂起 | 否 | 暂停 |
| OVERDUE | 超期（叠加标记，不替代主状态） | 否 | 继续 |
| CANCELLED | 作废 | 是 | 停止 |
| MERGED | 已并入其他工单 | 是 | 停止 |

设计说明：OVERDUE 以 `is_overdue` 布尔字段叠加在主状态之上，避免出现"状态二值化"后无法回退的问题。（修订：原状态机用 0–4 魔法数字，无驳回、挂起、超期、作废与合并，无法表达真实业务。）

## 10.2 迁移表

| 当前状态 | 事件 | 守卫条件 | 执行动作 | 目标状态 |
| --- | --- | --- | --- | --- |
| （无） | CREATE | 缺陷状态为 CONFIRMED 且无未结单 | 生成 order_no；写 DRAFT；记录创建人 | DRAFT |
| DRAFT | SUBMIT | 必填项齐全（缺陷、处置人、时限） | 校验 SLA 可计算；写事件；通知主管 | PENDING_CONFIRM |
| DRAFT | CANCEL | 创建人本人或管理员 | 写作废原因；通知相关人 | CANCELLED |
| PENDING_CONFIRM | APPROVE | 主管角色 | 置 CONFIRMED；计算 sla_deadline；写事件 | CONFIRMED |
| PENDING_CONFIRM | REJECT | 驳回原因必填 | 置 REJECTED；缺陷回退为 CONFIRMED 待重新建单 | REJECTED |
| REJECTED | REVISE | 原因已处理 | 回到可编辑态，保留历史事件 | DRAFT |
| CONFIRMED | ASSIGN | handler_id 必填且在数据范围内 | 置 ASSIGNED；推送待办；写事件 | ASSIGNED |
| ASSIGNED | ACCEPT_TASK | 操作人为 handler | 置 IN_PROGRESS；记录接单时间与设备 | IN_PROGRESS |
| ASSIGNED | REASSIGN | 主管角色 | 变更 handler，不重置 SLA；写事件 | ASSIGNED |
| IN_PROGRESS | SUSPEND | 挂起原因必填（等材料/等封道/天气） | 置 SUSPENDED；记录暂停起点 | SUSPENDED |
| SUSPENDED | RESUME | 操作人为 handler 或主管 | 累加暂停时长；置回 IN_PROGRESS | IN_PROGRESS |
| IN_PROGRESS | SUBMIT_REPAIR | 现场照片 ≥ 2 张且含处置说明 | 置 PENDING_ACCEPT；通知验收人 | PENDING_ACCEPT |
| PENDING_ACCEPT | ACCEPT | 验收人角色；附件齐全 | 置 CLOSED；写闭环时间；缺陷置 VERIFIED | CLOSED |
| PENDING_ACCEPT | RETURN | 退回原因必填 | 置回 IN_PROGRESS；SLA 顺延退回耗时 | IN_PROGRESS |
| 任一计时态 | TIMEOUT | 系统定时器触发，且 `now > sla_deadline` | 置 `is_overdue=true`；发一级提醒；写事件 | OVERDUE（叠加） |
| 任一非终态 | CANCEL | 管理员或业主授权角色；原因必填 | 置 CANCELLED；缺陷回退 CONFIRMED | CANCELLED |
| 任一非终态 | MERGE | 存在同构件同类型未结单，且合并确认 | 置 MERGED；`merged_into` 指向主单；事件与附件迁移 | MERGED |

> 迁移数量口径：状态机唯一事实源 `state/work_order_state_machine.yaml` 实际为 **12 个状态、19 条迁移**。
> 本表 17 行为动作口径（含 CREATE；CREATE 是建单动作而不是状态迁移），配置中另有
> ESCALATE_L2 / ESCALATE_L3 两条系统升级迁移，且 SUBMIT_REPAIR（T11/T17）与 CANCEL（T02/T18）
> 在特定前置状态下各有一条特化迁移，故迁移行数为 19。

## 10.3 角色与迁移权限

| 事件 | 系统管理员 | 养护管理人员 | 检测/巡检人员 | 审核人员 | 只读访客 |
| --- | --- | --- | --- | --- | --- |
| CREATE | 可 | 可 | 可 | 不可 | 不可 |
| SUBMIT | 可 | 可 | 可 | 不可 | 不可 |
| APPROVE / REJECT | 可 | 可 | 不可 | 可 | 不可 |
| ASSIGN / REASSIGN | 可 | 可 | 不可 | 不可 | 不可 |
| ACCEPT_TASK / SUBMIT_REPAIR | 可 | 可 | 可（仅本人单） | 不可 | 不可 |
| ACCEPT / RETURN | 可 | 可 | 不可 | 可 | 不可 |
| SUSPEND / RESUME | 可 | 可 | 可（仅本人单） | 不可 | 不可 |
| CANCEL / MERGE | 可 | 需业主授权 | 不可 | 不可 | 不可 |
| 查看 | 全部 | 数据范围内 | 本人及相关 | 数据范围内 | 授权范围 |

> 权限口径以实现事实源 `state/work_order_state_machine.yaml` 的 `allowed_roles` 为准：
> APPROVE / REJECT 当前仅 MAINT_MANAGER（含 SYSTEM_ADMIN 运维旁路）；本表"审核人员可审批"
> 为业务期望，尚未在配置中开放，启用前不得按本表实现。

## 10.4 SLA 计时口径

| 项 | 规定 |
| --- | --- |
| 计时单位 | 自然日 24 小时连续计时（含节假日）；若业主按工作日考核，改按工作日历计算（按建议值设定，待确认） |
| 起算点 | 进入 PENDING_CONFIRM 的时刻 |
| 暂停 | 仅 SUSPENDED 期间暂停；恢复时累加 `sla_paused_total` |
| 不计时 | DRAFT、REJECTED、CANCELLED、MERGED、CLOSED |
| 超时判定 | `now > sla_deadline + sla_paused_total` |
| 升级策略 | 超时 0–24 h：通知 handler 与主管；24–72 h：通知部门负责人；>72 h：列入月度考核清单（阈值待业主确认） |
| 时限取值 | 按缺陷等级设定，具体数值待业主确认 |

## 10.5 并发与幂等

| 场景 | 措施 |
| --- | --- |
| 同一缺陷重复建单 | 部分唯一索引：`UNIQUE(defect_id) WHERE status NOT IN ('CLOSED','CANCELLED','MERGED')` |
| 重复提交验收 | `Idempotency-Key` + 状态守卫；重复请求返回首次结果 |
| 两人同时流转 | `version` 乐观锁；冲突返回 409 + 当前状态，前端刷新后重试 |
| 移动端弱网重放 | 事件携带 `client_seq`，服务端按 `order_id + client_seq` 去重 |
| 定时器重复触发 | 超时判定为幂等操作，重复触发只更新一次 `is_overdue` |

# 第11章 异常、降级与重试熔断

## 11.1 异常分级

| 级别 | 含义 | 示例 | 处理方式 |
| --- | --- | --- | --- |
| E1 业务异常 | 用户输入或状态不合法 | 参数越界、状态非法迁移、重复建单 | 返回 4xx + 明确提示，不重试、不告警 |
| E2 数据质量异常 | 数据本身不满足算法前提 | 密度不足、配准超限、位姿缺失 | 拒收或降级，写入质量标记，通知上传方 |
| E3 依赖不可用 | 下游服务或存储故障 | 数据库、MinIO、推理服务不可用 | 降级 + 重试 + 熔断，返回 503 |
| E4 系统异常 | 未预期错误 | 空指针、序列化失败、内存溢出 | 5xx + 告警 + 现场保留 |

## 11.2 错误码与错误体一致性

错误码分段与错误体格式见 5.4.2 与 5.4.3，本章不重复定义。所有异常必须携带 `traceId`，日志与该响应可互相追溯。

## 11.3 依赖降级矩阵

| 依赖 | 不可用时的表现 | 降级动作 | 是否可继续接单 | 恢复后补偿 |
| --- | --- | --- | --- | --- |
| PostgreSQL/PostGIS | 台账、工单、几何结果全部不可读写 | 只读模式（若主库可读）；前端展示缓存快照并显著标记"数据可能过期" | 否（只可查看） | 主备切换后对账 `work_order_event` 序号连续性 |
| MinIO | 点云、影像、附件不可取 | 三维界面降级为二维台账视图；已缓存瓦片继续可用；上传转本地暂存队列 | 是（可接单，附件延后上传） | 重放暂存队列；校验 `file_object_link` 完整性 |
| 推理服务 | 影像可上传但无识别结果 | 任务保留 QUEUED 不丢；提示"识别排队中"；允许转人工标注 | 是 | 队列按优先级回放；对超时任务重投 |
| 点云服务 | 三维加载失败或极慢 | 降级为二维地图 + 构件列表；禁止进入精度分析页 | 是 | 缓存重建；瓦片重建校验 |
| PMS 同步 | 工单状态无法推送外部 | 本地继续流转；写入 `sync_log` 待发队列 | 是 | 增量重放 + 按 `order_no` 对账，冲突以本系统为准并留痕 |
| 通知服务 | 待办与短信发不出 | 降级为站内信写入 `notification`；关键超时改用人工电话清单 | 是 | 补发未送达消息 |

（新增：原文档仅有"内网隔离"一句，无依赖故障时的系统行为定义。）

## 11.4 重试、熔断与超时

| 项 | 规定 |
| --- | --- |
| 重试前提 | 操作必须幂等（带 `Idempotency-Key` 或天然幂等） |
| 重试次数 | 同步调用最多 2 次；异步任务最多 3 次 |
| 退避 | 指数退避 + 抖动：1 s、5 s、15 s（异步）；200 ms、600 ms（同步） |
| 不重试 | 4xx 业务异常、422 数据校验失败、鉴权失败 |
| 熔断阈值 | 60 秒内失败 ≥ 5 次进入 OPEN，持续 30 秒；HALF_OPEN 放行 3 个探测请求，成功 2 个即恢复（阈值待实测调优） |
| 队列积压 | 积压 > 500 时告警；> 2000 时对新任务返回 429 并给排队位次（阈值待确认） |
| 超时预算 | 网关 10 s；业务接口 5 s；推理单图 10 min；几何单构件 30 min；批量任务 60 min |
| 舱壁隔离 | 推理、几何、切片三类任务独立队列与并发上限，防止互相拖垮 |

## 11.5 数据质量异常的处置

| 异常 | 判定 | 处置 |
| --- | --- | --- |
| 点云缺失 | 构件无关联 `pointcloud_file` | 拒收该分析任务，返回 BT-40002，提示补采 |
| 密度不足 | 实测密度 < 500 点/㎡ 的 80% | 标记 `quality_level=C`；结果标注"仅供参考"；不参与验收 |
| 遮挡率超限 | 单墩有效点比例 < 60% | 判"数据不足"，转人工复测，不出结论 |
| 配准残差超限 | L1 RMS > 5 mm 或 L2 检查点 > 10 mm | 拒收，`quality_level=REJECT`，退回重测 |
| 影像位姿缺失 | `pose_source=NONE` 且无可配准同名点 | 仅用于 2D 识别，不做 2D—3D 映射，缺陷定位降级为"构件级" |
| 设计资料缺失 | 无设计断面模板或无设计轴线 | 隧道仅做椭圆粗定位；桥梁仅输出偏移量不输出倾斜率，均标注"仅供参考" |

# 第12章 日志与审计

## 12.1 日志规范

| 项 | 规定 |
| --- | --- |
| 格式 | 结构化 JSON，单行 |
| 必填字段 | `ts`、`level`、`traceId`、`userId`、`module`、`action`、`objectType`、`objectId`、`result`、`costMs`、`errorCode`、`clientIp`、`ua` |
| 分级 | ERROR（需人工处理）、WARN（可自愈但需关注）、INFO（业务关键节点）、DEBUG（仅调试环境开启） |
| 采样 | ERROR/WARN 100% 采集；INFO 100%；高频只读接口的 DEBUG 按 10% 采样 |
| 留存 | 应用日志 30 天热存 + 180 天冷存；审计日志 ≥ 3 年（按建议值设定，待确认） |
| 脱敏 | 手机号、身份证、姓名按 12.3 规则脱敏后落盘 |

## 12.2 审计范围

| 类别 | 记录内容 | 不可篡改要求 |
| --- | --- | --- |
| 登录与登出 | 账号、IP、设备、结果、失败原因 | 只追加，不修改 |
| 权限变更 | 授权人、被授权人、角色、权限点、前后值 | 只追加 |
| 数据导出 | 导出人、范围、条数、文件哈希、审批单号 | 只追加 + 导出留痕文件 |
| 缺陷确认/驳回 | 候选、结论、修改前后几何与等级 | 只追加 |
| 工单迁移 | 前后状态、事件、操作人、意见 | 只追加，与 `work_order_event` 双写 |
| 模型上线/回滚 | 模型版本、指标、审批人 | 只追加 |
| 配置变更 | 键、旧值、新值、操作人 | 只追加 |

不可篡改实现：审计表仅授予 INSERT 与 SELECT，不授予 UPDATE/DELETE；按日期分区；每条记录写入前计算前一条哈希形成链式校验（哈希算法 SHA-256）。

## 12.3 脱敏规则

| 数据 | 规则 |
| --- | --- |
| 手机号 | 保留前 3 后 4，中间掩码 |
| 身份证/工号 | 保留前 2 后 2 |
| 姓名 | 保留姓，名以 * 替代（审计内部可配置为明文，需审批） |
| 影像中的人脸、车牌 | 预览图自动模糊；原图仅授权角色可下载 |
| 点云 | 记录每次访问的对象键、用户、时间、用途 |

## 12.4 查询与导出权限

审计日志查询限 `audit:read`，仅系统管理员与审核人员；导出需二次审批并生成导出审计记录；任何导出文件写入 `attachment` 并绑定审批单号。

# 第13章 权限模型、数据分级与弱网离线

## 13.1 RBAC 矩阵

资源 × 操作：C 创建、R 读、U 更新、D 删除、A 审批、X 导出。

| 资源 | 系统管理员 | 养护管理人员 | 检测/巡检人员 | 审核人员 | 只读访客 |
| --- | --- | --- | --- | --- | --- |
| 项目/桥隧主数据 | CRUDA | RU | R | R | R |
| 构件台账 | CRUDA | CRU | RU | R | R |
| 点云与影像 | CRUDA | RU | CRU | R | R |
| 缺陷台账 | CRUDA | CRU | CRU | RU（复核） | R |
| 工单 | CRUDA | CRUDA | CRU（本人单） | RU（验收） | R |
| 几何分析 | CRUDA | CRU | CRU | R | R |
| 报表导出 | CRUDAX | CRUX | CX | CRUX | 不可 |
| 模型管理 | CRUDA | R | 不可 | R | 不可 |
| 用户与角色 | CRUDA | 不可 | 不可 | R | 不可 |
| 审计日志 | R + X | 不可 | 不可 | R + X | 不可 |
| 系统配置 | CRUDA | R | 不可 | R | 不可 |

数据范围：`sys_role_data_scope` 按 PROJECT / BRIDGE / TUNNEL / SECTION 四级授权，接口层统一校验，越界返回 BT-20004。范围校验在网关与业务服务双重执行（纵深防御）。

## 13.2 数据分级

| 级别 | 内容 | 管控措施 | 导出 |
| --- | --- | --- | --- |
| 公开 | 系统说明、公开统计 | 无 | 自由 |
| 内部 | 构件台账、一般工单 | 登录可见，按数据范围 | 部门审批 |
| 敏感 | 高精度点云、原始影像、结构缺陷明细 | 加密存储、访问留痕、最小授权 | 业主审批 + 水印 + 用途登记 |
| 涉密 | 若测绘主管部门认定为涉密成果 | 物理隔离、专人管理、禁止出境与外网 | 按国家规定审批 |

密级判定：本文件封面"密级"仅为文档密级，不等于数据密级；数据密级以 13.2 表按实测成果逐项判定（待业主与测绘管理口径确认）。

（修订：原"商业机密"一档覆盖全部数据，未做分级，与实际管控要求不匹配。）

## 13.3 弱网与离线

| 项 | 规定 |
| --- | --- |
| 离线缓存范围 | 我的工单与待办、缺陷摘要、字典、构件基础信息、最近 7 天影像缩略图 |
| 本地存储 | SQLite + 文件目录，容量上限 2 GB（可配） |
| 写入模型 | 本地 Outbox 表，所有操作先落本地再同步 |
| 同步 | 恢复网络后按 `client_seq` 顺序推送；服务端按 `Idempotency-Key` 去重 |
| 冲突 | 以服务端 `version` 为准；冲突项保留本地副本并提示人工选择 |
| 照片 | 上传前压缩至长边 1920、质量 80；原图可选保留 |
| 上传 | 后台任务，失败按 1/5/15 分钟退避；界面显示待传数量 |
| 断点续传 | 分片上传记录已成功片号，重连后只补未完成片 |
| 定位 | 无网络时用 GPS + 构件扫码；离线地图仅保留项目范围缓存 |
| 安全 | 本地库加密（AES-256），退出登录或换设备时清空 |

# 第14章 监控告警、备份容灾与版本管理

## 14.1 指标清单

| 类别 | 指标 | 阈值（待业主确认） | 采集方式 |
| --- | --- | --- | --- |
| 接口 | P95 延迟、错误率、QPS | P95 ≤ 300 ms；错误率 ≤ 0.5% | 网关埋点 + Prometheus |
| 队列 | 待处理数、平均等待、失败率 | 积压 > 500 告警；失败率 > 5% 告警 | 任务表统计 |
| 推理 | 单图耗时、QPS、GPU 利用率、显存 | 耗时 P95 > 2 × 基线告警 | Triton 指标 |
| 点云 | 瓦片请求数、命中率、出口带宽 | 命中率 < 70% 告警 | MinIO 访问日志 + CDN/反向代理 |
| 存储 | 容量水位、对象数增长 | > 80% 告警，> 90% 严重 | MinIO 与 PG 统计 |
| 数据库 | 连接数、慢查询、复制延迟 | 慢查询 > 1 s；延迟 > 5 s 告警 | PG exporter |
| 业务 | 工单超期数、候选池积压、未闭环率 | 超期 > 10 单告警 | 定时统计 |

## 14.2 告警分级与通知

| 级别 | 触发 | 通知渠道 | 响应 |
| --- | --- | --- | --- |
| P1 | 核心服务不可用、数据库主库宕机 | 电话 + 短信 + 站内 | 15 分钟内响应 |
| P2 | 推理或点云服务不可用、队列严重积压 | 短信 + 站内 | 1 小时内响应 |
| P3 | 指标越限但业务可降级运行 | 站内 + 邮件 | 当班处理 |
| P4 | 趋势性异常 | 日报 | 计划处理 |

值班与升级路径按业主要求确定（待业主确认）。

## 14.3 备份恢复

| 对象 | 策略 | 频率 | 保留 | 演练 |
| --- | --- | --- | --- | --- |
| PostgreSQL | 每日全备 + WAL 归档，异地副本 | 全备每日；WAL 连续 | 全备 30 天；月度归档 12 个月 | 每季度恢复演练 |
| MinIO | 版本化 + 跨节点副本 + 异地同步 | 实时复制；每日快照 | 快照 30 天 | 每半年抽取恢复 |
| 配置与密钥 | 加密备份，双人保管 | 每次变更 | 长期 | 每次轮换验证 |
| 目标 | RPO ≤ 15 分钟；RTO ≤ 4 小时（待业主确认） | — | — | 演练记录归档 |

## 14.4 部署拓扑与容量

### 14.4.1 分区

| 区 | 组件 | 说明 |
| --- | --- | --- |
| 接入区 | Nginx、网关 | 仅暴露 443；与业务区单向访问 |
| 应用区 | 业务服务、推理服务、调度器、PDAL | 无状态，可横向扩展 |
| 数据区 | PostgreSQL 主备、MinIO 集群、Redis | 不对接入区直接暴露 |
| 运维区 | 监控、日志、备份 | 独立账号体系 |

### 14.4.2 扩展方式

业务服务与推理服务无状态，按 CPU/GPU 利用率水平扩容；MinIO 按节点扩容并保持纠删码策略；PostgreSQL 先垂直扩容，再按项目或时间分区，读写分离承接受报表查询。

### 14.4.3 容量估算方法

```text
存储 = Σ(单座点云 GB × 1.3 冗余) + Σ(影像 GB × 1.2) + 3D Tiles 派生 ≈ 0.6 × 原始点云
并发 = 日常在线用户 × 峰值系数（建议 1.5，待确认）
推理 QPS = 日均影像数 × 单图切片数 / (有效工时 × 3600)
带宽 = 并发 × 平均瓦片体积 / 平均停留时间
```

具体节点数与规格待业主确认桥隧清单与并发规模后确定（待业主确认）。

## 14.5 版本与灰度

| 项 | 规定 |
| --- | --- |
| 镜像 | 语义化版本 + Git 提交号；禁止使用 latest 标签部署 |
| 数据库迁移 | 迁移脚本随版本入库，只向前；每个版本附回滚脚本；迁移须向后兼容（先加列后改逻辑，删除分两版） |
| 配置 | 配置外置，版本化，变更留痕（见 12.2） |
| 灰度 | 按用户比例 10% → 50% → 100%，每级观察 ≥ 24 小时（按建议值设定，待确认） |
| 回滚 | 镜像回滚 ≤ 10 分钟；数据库回滚仅在迁移不兼容时执行，须先备份 |
| 发布窗口 | 非外业高峰时段；发布前跑冒烟用例集 |
| 模型发布 | 独立于应用版本，经 `model_registry` 审批后灰度，指标劣化自动回滚 |

# 附录A 修订对照表

| 序号 | 原问题 | 修订章节 | 修订要点 |
| --- | --- | --- | --- |
| 1 | 用 4490 度坐标存几何并做毫米级计算 | 2.2、5.3 | 双表达：交换 4490、计算用投影或工程局部系；几何列加类型与 SRID 约束 |
| 2 | 七参数用于设备系到工程系 | 2.2.4 | 明确七参数适用前提与禁用条件，改用四参数或 Helmert |
| 3 | 无站间、控制点、影像配准设计 | 2.3 | 补 L1/L2/L3 三层配准、残差限值与检查点要求 |
| 4 | 精度指标无误差链 | 2.3.4、4.5 | 补误差合成公式与"合成值 ≤ 目标 60%"判定 |
| 5 | COPC 等同 PDRF 7；LOD 只留 3–5 级 | 2.4 | 明确 COPC 1.1 与 PDRF 6/7/8；LOD 由八叉树生成；拟合用最高密度层 |
| 6 | 10GB/3s、30FPS、200ms 无前置条件 | 6.1 | 改为七张条件卡片，拆单 tile 与整幅，标注 GPU 前提；统一单座与全线规模 |
| 7 | COPC 直接进 CesiumJS 无落地路径 | 6.2 | 明确预转 3D Tiles 路线、调度、点预算、拾取与离线底图 |
| 8 | 桥梁算法一刀切 MABR，PCA 与拟合双路径冲突 | 4.1 | 按墩型分支；PCA 仅作初值；TLS 稳健拟合；补遮挡率与污染剔除 |
| 9 | 垂直度与累积位移无定义式与基准 | 4.1.5、4.1.6 | 给出倾斜率公式、判定优先级与 T0 时间基准 |
| 10 | 隧道 5 m 断面 + 椭圆拟合 | 4.2 | 设计断面参数化 + ICP + 带符号法向距离；间距自适应；椭圆降级 |
| 11 | 裂缝做 3D 量化、支座用车载影像识别 | 3.3、4.3 | 病害分治：裂缝走 2D 标定、渗水剥落走 3D、支座另设工位 |
| 12 | 共线方程反投影方向错误 | 2.3.3、4.4 | 补外方位与射线求交，明确需深度约束 |
| 13 | AI 结果直写台账并自动派单 | 3.3.2、9.4.1 | 引入候选缺陷池 + 人工确认 + 误报回流 |
| 14 | 状态机 0–4 魔法数字 | 10.1、10.2 | 语义化状态 + 完整迁移表 + 守卫条件与副作用 |
| 15 | 缺驳回/挂起/超期/作废/合并 | 10.2 | 补五个分支状态与迁移规则 |
| 16 | 数据模型仅 4 表且悬空外键 | 5.2、5.3 | 补 29 张表、构件四级树、文件关联、状态日志、RBAC 与字典 |
| 17 | 缺陷仅 POINTZ 一点 | 5.3.7 | 点/线/面分列 + 量化值与单位 |
| 18 | 状态码只有 200/400/401/500 | 5.4.2 | 补至 13 个状态码并给语义 |
| 19 | 无统一错误体与错误码 | 5.4.3 | problem+json + 九段错误码 |
| 20 | JWT 无刷新与撤销 | 5.4.4 | 双令牌、轮换、黑名单、密钥 kid |
| 21 | 无分页/增量/幂等/限流 | 5.4.1 | 统一约定并落到接口 |
| 22 | Redis 锁防 OOM | 5.4.5 | 改预签名分片直传 + 异步校验 |
| 23 | 缺模块边界与接口设计 | 9.1–9.5 | 12 个模块、数据归属、依赖无环、20 个接口契约、三条时序、异步任务模型 |
| 24 | 缺异常与降级 | 11.1–11.5 | 四级异常、六类依赖降级矩阵、重试熔断、数据质量处置 |
| 25 | 缺日志与审计 | 12.1–12.4 | 字段规范、七类审计、哈希链、脱敏、留存 |
| 26 | 缺权限与数据分级 | 13.1、13.2 | RBAC 矩阵 + 数据范围 + 四级分级与导出审批 |
| 27 | 缺弱网与移动端 | 13.3 | 离线缓存、Outbox、冲突、压缩与断点续传 |
| 28 | 缺监控、备份、部署、灰度 | 14.1–14.5 | 指标、告警分级、RPO/RTO、分区与扩展、版本与灰度 |
| 29 | 标准引用错配 | 1.4 | 施工期标准降为参考；运营期以设计文件与养护规范为准；未核实条款标注 |
| 30 | 三个月工期不成立、交付物缺项 | 8.1–8.3 | 重排 T-1 至 T+4，增加复测窗口；补源码、契约、报告、数据与标注规范 |
| 31 | 未确认项无先行动作，开发无处着手 | 1.5.1 | 新增 8 条先行假设条款 H-01 至 H-08 |
| 32 | 投影带号只写"按经度确定"，无选择规则 | 2.2.1 | 按线路平均经度取 3° 带；跨带统一至工程局部系；带号写入 calc_srid |
| 33 | 推理硬件下限未定义，P-05 可能永久不可达 | 6.1、1.5.1 | 单卡显存 ≥ 8 GB；纯 CPU 时 P-05 ≤ 20 s、P-04 ≤ 600 ms |
| 34 | 渲染点预算与机型基线未固化 | 6.1、6.2.3 | 150 万点/帧、1080p、核显档 |
| 35 | 瓦片参数未固化，转换结果不可复现 | 6.2.2 | 单瓦片 50 万点、SSE 16 |
| 36 | 日志与审计留存无默认值 | 12.1、1.5.1 | 应用日志 30 天热 + 180 天冷；审计 ≥ 3 年 |
| 37 | SLA 计时口径未定，超时判定无法实现 | 10.4、1.5.1 | 自然日 24 小时连续计时，工作日为备选 |
| 38 | 灰度比例与观察期未定 | 14.5、1.5.1 | 10% → 50% → 100%，每级 ≥ 24 小时 |
| 39 | 分页与限流无默认参数，联调需各方自行约定 | 5.4.1、1.5.1 | limit 默认 20、上限 100；单用户读 20 次/秒、写 5 次/秒，单 IP 100 次/秒 |

# 附录B 需求追踪矩阵

| 目标 | 需求要点 | 实现模块 | 章节 | 验收项 |
| --- | --- | --- | --- | --- |
| G1 资产数字化 | 构件四级编码、双向映射 | M03 | 3.1、5.3.3 | 编码唯一率 100%、可拾取率 ≥95% |
| G2 几何可量 | 垂直度、净空 | M07 | 4.1、4.2 | 7.1 精度验收 |
| G3 病害可查 | 识别、候选、确认、复核 | M05、M06、M09 | 3.3、4.3、4.4 | 7.2 AI 验收 |
| G4 工单可溯 | 状态机、事件日志、SLA | M09 | 3.4、10.1–10.5 | 7.3 E2E |
| G5 可私有化 | 离线底图、无外部依赖 | M04、前端 | 6.2.5、14.4 | 断网全流程可用 |
| N1 性能 | 条件卡片 P-01 至 P-07 | 全部 | 6.1、6.3 | 压测报告 |
| N2 安全 | 令牌、越权、审计、分级 | M01、M02、M12 | 5.4.4、12、13 | 安全测评 |
| N3 可运维 | 监控、备份、灰度 | 运维 | 14 | 恢复演练记录 |

# 附录C 待业主与实测确认清单

| 编号 | 事项 | 影响章节 | 说明 |
| --- | --- | --- | --- |
| C-01 | 全线桥隧数量、单座与累计点云规模 | 6.1、14.4.3 | 决定容量与存储 |
| C-02 | 目标并发用户数、日工单量、峰值上传量 | 6.3、7.3 | 决定压测与部署规格 |
| C-03 | 垂直度与净空的设计容许值及评定方法 | 4.1.5、7.1 | 决定验收条款 |
| C-04 | 桥隧清单与设计图纸、竣工测量成果是否齐备 | 1.5、4.2 | 缺失则算法降级 |
| C-05 | 计算坐标系与投影带号、工程局部系参数 | 2.2.1 | 决定 SRID 与几何列 |
| C-06 | 控制点布设条件与复测周期 | 2.3.2 | 决定精度可达性 |
| C-07 | 精度限值（垂直度 ±5 mm、净空 ±10 mm）是否作为合同值 | 7.1 | 若收紧须重算误差预算 |
| C-08 | 推理服务器 GPU 型号与数量 | 6.1 | 决定 P-05 是否成立 |
| C-09 | 渲染点预算与目标机型 | 6.1、6.2.3 | 决定 P-03 |
| C-10 | 单瓦片点数上限、SSE 默认值（转换调优） | 6.2.2、6.2.3 | 实测后固化 |
| C-11 | AI 精确率目标与测试集规模 | 7.2 | 决定样本量 |
| C-12 | 缺陷等级划分与 SLA 时限 | 10.4 | 决定超时策略 |
| C-13 | 数据密级与测绘成果管理口径 | 13.2 | 决定分级与导出审批 |
| C-14 | 等保等级与测评要求 | 6.4 | 决定安全建设 |
| C-15 | 日志与审计留存期 | 12.1 | 决定存储规划 |
| C-16 | RPO/RTO 目标 | 14.3 | 决定备份架构 |
| C-17 | PMS 接口协议、字段映射与主数据归属 | 9.1、M12 | 决定对接工作量 |
| C-18 | 是否按工作日计算 SLA | 10.4 | 决定超时判定 |
| C-19 | 灰度与发布窗口要求 | 14.5 | 决定发布流程 |
| C-20 | 标准条款原文核实（JTG H12、JT/T 1037、公路桥涵养护规范） | 1.4 | 条款待核实原文 |

# 决策会议议程建议

20 项可在一次会议内完成确认。建议会前 3 个工作日发出本附录与 B 类提问清单，会上每项只做三选一："确认 / 修改 / 暂按假设"，避免开放式讨论。会前材料用附录 D 表中"暂定假设"列作为默认项，无异议即视为确认。

## 第一组 必须先定（约 90 分钟）

这 5 项决定数据采集、坐标基准与验收条款，未定则 T-1 阶段无法开工。

| 顺序 | 编号 | 事项 | 不定会卡住什么 | 需携带材料 |
| --- | --- | --- | --- | --- |
| 1 | C-05 | 坐标系与投影带 | 全部几何列 SRID、2.2 全章 | 线路起终点经纬度 |
| 2 | C-04 | 设计图纸与竣工测量 | 4.1 设计轴线、4.2 断面模板（无图则退化为相对比较） | 图纸清单与样例 |
| 3 | C-06 | 控制点布设与复测 | 2.3.2 控制网、2.3.4 误差传递与 ±5 mm 承诺 | 既有控制网成果 |
| 4 | C-03 | 精度限值与评定方法 | 7.1 验收条款、2.3.4 误差预算（容许值+方法+复测窗口三者绑定） | 设计容许值摘录 |
| 5 | C-01 | 桥隧数量与点云规模 | 6.1、14.4.3 容量与部署规格 | 桥隧清单 |

## 第二组 可与开发并行（约 60 分钟）

不影响第 1 至 6 周编码，但需在算法冻结（T+1 月末）或集成（T+2 月）前定稿。

| 顺序 | 编号 | 事项 | 最晚时点 |
| --- | --- | --- | --- |
| 1 | C-02 | 并发与业务量 | T+2 集成前 |
| 2 | C-07 | 精度限值是否入合同 | T+1 算法冻结前 |
| 3 | C-08 | 推理 GPU 配置 | T+1 |
| 4 | C-09 | 渲染机型与点预算 | T+1 |
| 5 | C-10 | 瓦片参数 | T+1 |
| 6 | C-11 | AI 精确率与测试集 | T+2 |
| 7 | C-12 | 缺陷等级与 SLA 时限 | T+2 工单开发前 |
| 8 | C-16 | RPO/RTO | T+2 |
| 9 | C-17 | PMS 接口 | T+2 集成前 |

## 第三组 可延后到 UAT 前（约 30 分钟）

影响交付形态与合规，不阻塞开发。

| 顺序 | 编号 | 事项 | 最晚时点 |
| --- | --- | --- | --- |
| 1 | C-13 | 数据密级与测绘口径 | UAT 前 |
| 2 | C-14 | 等保等级 | 设计阶段并行 |
| 3 | C-15 | 日志审计留存 | T+2 |
| 4 | C-18 | SLA 计时口径 | T+2 |
| 5 | C-19 | 灰度与发布窗口 | UAT 前 |
| 6 | C-20 | 规范条文核实 | 算法冻结前 |

## 会议输出物

一份签署的确认单（20 项逐条勾选"确认/修改/暂按假设"）、一份 B 类资料提交责任人与日期清单、一份变更影响说明（如 C-03 或 C-05 被修改，需同步更新的章节清单由设计方当场给出）。

# 附录D 待确认事项处置建议表

分类口径：A 类为设计侧已给结论并回写正文；B 类须业主/建设方提供数据；C 类须第三方核实。共 A 类 7 项、B 类 9 项、C 类 4 项。

## D.1 必须先定（5 项，其中 C-05 为 A 类已定稿，仅需业主供起终点经纬度）

| 编号 | 事项 | 类别 | 阻塞点 | 建议值/选项 | 确认人 | 确认时点 | 暂定假设 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C-01 | 桥隧数量与点云规模 | B | 存储与渲染容量须按 座数×单座规模 反算；不定则 6.1 条件卡片、14.4 部署规格无基准，压测无对照 | 选项1 按 30 桥+10 隧（单机可承载，成本基准）；选项2 按 100 桥+40 隧（需集群，成本约 ×2，推断）；选项3 分期建设 | 业主养护部门 | T-1 前 | 30 桥+10 隧，单座 5–10 GB |
| C-03 | 垂直度与净空的容许值及评定方法 | B | 容许值与评定方法、复测窗口三者绑定；不定则 7.1 无法签署，2.3.4 误差预算无法闭合 | 选项1 沿用 ±5 mm/±10 mm，须加密控制点；选项2 放宽至 ±10 mm/±20 mm，按中误差评定；选项3 按设计容许值折算 | 业主 + 设计单位 | T-1 前 | 沿用 ±5 mm/±10 mm，按 7.1 方法 |
| C-04 | 设计图纸与竣工测量成果 | B | 设计轴线与断面模板是几何计算的输入；缺图则 4.1/4.2 只能退化为相对比较，净空结论不可用于养护决策 | 选项1 DWG + 断面参数（最优）；选项2 仅 PDF，人工矢量化（误差与工作量上升）；选项3 无资料，仅做相对比较 | 业主档案部门 + 设计单位 | T-1 前 | 假定 DWG 与断面参数可提供 |
| C-06 | 控制点布设条件与复测周期 | B | L2 配准控制点数量直接决定残差上限；不定则 2.3.4 误差传递无法验证，±5 mm 不予承诺 | 选项1 每结构 4 点 + 30% 检查点，年复测；选项2 仅 3 点（精度不承诺）；选项3 复用既有网，须先核精度 | 检测单位 + 业主 | T-1 前 | 4 点 + 30% 检查点，年复测 |
| C-05 | 计算坐标系与带号 | A | 全部几何列 SRID 与投影运算依赖带号/局部系参数；设计侧已给规则，未确认前 DDL 按 H-01 先行 | 结论：按线路平均经度取 3° 带；跨带统一至工程局部系；带号由坐标计算写入 calc_srid | 设计单位（业主供坐标） | T-1 前 | H-01 |

## D.2 可与开发并行（8 项）

| 编号 | 事项 | 类别 | 阻塞点 | 建议值/选项 | 确认人 | 确认时点 | 暂定假设 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C-02 | 并发用户、日工单、峰值上传 | B | 并发与日工单量决定 SLO、连接池与压测场景；不定则 6.3 指标与 7.3 方案只能估算 | 选项1 50 并发 / 200 单日（中小养护单位）；选项2 200 并发 / 1000 单日（区域中心）；选项3 按班组数×1.5 估算 | 业主信息中心 | T+2 集成前 | 50 并发、200 单日 |
| C-07 | 精度限值是否写入合同 | B | 数值写死与方法+限值+复测窗口两种合同写法风险差异大；不定则合同条款与 7.1 验收口径可能冲突 | 选项1 写死数值（风险高）；选项2 写"方法+限值+复测窗口"（推荐）；选项3 只写方法 | 业主合同/法务 | T+1 算法冻结前 | 采用选项2 |
| C-11 | AI 精确率目标与测试集规模 | B | 精确率目标与样本量由统计功效反算；不定则 7.2 抽样方案与标注预算无法定 | 选项1 精确率 ≥70%、正样本 196；选项2 ≥80%、正样本 400（标注成本约 ×2，推断）；选项3 仅考核召回 | 业主 + 第三方标注 | T+2 | ≥70%，196 正样本 |
| C-12 | 缺陷等级划分与 SLA 时限 | B | 缺陷等级决定 SLA 计时与升级链；不定则 10.4 状态机 OVERDUE 分支无阈值 | 选项1 三级（紧急 24 h / 一般 7 d / 观察 30 d）；选项2 二级（48 h / 15 d）；选项3 沿用 PMS 现行时限 | 业主养护部门 | T+2 工单开发前 | 选项1，自然日计时 |
| C-16 | RPO / RTO 目标 | B | RPO/RTO 决定备份频率与异地副本；不定则 14.3 架构与成本无法定档 | 选项1 15 min / 4 h；选项2 1 h / 8 h（成本下降）；选项3 近实时 / 1 h（成本上升） | 业主信息中心 | T+2 | 15 min / 4 h |
| C-08 | 推理 GPU 配置 | A | P-05 时延与显存强相关；设计侧已给下限，采购偏离即须重验该条件卡 | 结论：单卡显存 ≥8 GB、FP16 同级主流卡；纯 CPU 时 P-05 ≤20 s | 业主（采购）+ 设计 | T+1 | H-02 |
| C-09 | 渲染机型与点预算 | A | P-03 帧率取决于机型与单帧点数；设计侧已给基准机型，换机型须重测 | 结论：1080p、核显档、150 万点/帧 | 设计 | T+1 | H-03 |
| C-10 | 瓦片参数 | A | 瓦片点数与 SSE 决定首屏与漫游体验；设计侧已给默认值，转换实测后固化 | 结论：单瓦片 50 万点、SSE 16 | 设计 | T+1 | H-04 |

## D.3 可延后到 UAT 前（7 项：C 类 4、A 类 3）

| 编号 | 事项 | 类别 | 阻塞点 | 建议值/选项 | 确认人 | 确认时点 | 暂定假设 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| C-13 | 数据密级与测绘成果口径 | C | 密级决定存储分区、导出审批与是否物理隔离；不定则 13.2 分级与交付形态无法定 | 选项1 全部内部级；选项2 点云敏感级 + 影像内部级；选项3 认定涉密，物理隔离 | 测绘主管部门 + 业主保密办 | UAT 前 | 内部级，点云按敏感管控 |
| C-14 | 等保等级与测评 | C | 等保等级决定安全建设项与测评范围；不定则 6.4 建设内容与预算无法定 | 选项1 二级；选项2 三级（成本约 +30%，推断） | 业主信息中心 + 测评机构 | 设计阶段并行 | 二级 |
| C-17 | PMS 接口协议与字段映射 | C | PMS 为主数据来源，协议决定同步方式与工作量；不定则 M12 与 9.4 时序无法落地 | 选项1 REST + JSON 增量；选项2 数据库直连（耦合风险高）；选项3 中间表/文件交换 | PMS 厂商 + 业主 | T+2 集成前 | REST 增量同步 |
| C-20 | 规范条款原文核实 | C | 判定依据须可追溯，条款误用将致验收争议；未核实前 1.4 仅标注 条款待核实原文 | 选项1 设计单位查原文；选项2 第三方咨询机构；选项3 以设计文件为准，规范仅作方法学 | 设计单位 + 标准化机构 | 算法冻结前 | 以设计文件为准，条款待核实 |
| C-15 | 日志与审计留存期 | A | 留存期决定存储容量与冷热分层；设计侧已给默认值，超期或缩短须调 14.3 容量 | 结论：应用日志 30 天热 + 180 天冷；审计 ≥3 年 | 设计 | T+2 | H-05 |
| C-18 | SLA 计时口径 | A | 计时口径决定超时判定与统计口径；设计侧已给自然日，改工作日须同步报表与统计 | 结论：自然日 24 h 连续；工作日版为备选 | 设计（业主可改） | T+2 | H-06 |
| C-19 | 灰度与发布窗口 | A | 灰度比例与观察期决定回滚窗口；设计侧已给默认策略，业主可覆盖发布窗口 | 结论：10%→50%→100%，每级 ≥24 h；窗口避开外业高峰 | 设计 | UAT 前 | H-07 |

## D.4 B 类对外提问清单

可直接对外发函，每问一句话，附单位精度与用途。

| 编号 | 问题 | 需要的单位与精度 | 用途 |
| --- | --- | --- | --- |
| Q-01 | 本项目纳入平台的桥梁与隧道清单共多少座，各座点云预计多少 GB？ | 座；单座 GB，取整到 1 GB | 计算存储与部署规格（6.1、14.4） |
| Q-02 | 系统上线后日常在线用户数、日均工单量与外业回归日峰值上传量各为多少？ | 人；单/日；GB/日 | 定压测目标与带宽（6.3、7.3） |
| Q-03 | 在役桥隧的垂直度与净空容许偏差取值是多少，依据哪份文件？ | mm；文件编号与条款 | 定验收条款与误差预算（7.1） |
| Q-04 | 可提供的设计图纸与竣工测量成果包含哪些内容，格式为何？ | DWG/PDF；断面参数精度 mm | 定算法输入与降级方案（4.1、4.2） |
| Q-05 | 既有控制网的点数、等级与最近复测时间？ | 点；等级；年月 | 判断是否需新建控制网（2.3.2） |
| Q-06 | 精度指标是否作为合同验收数值，复测窗口如何约定？ | 次数；时长（周） | 定合同条款（7.1、8.2） |
| Q-07 | 裂缝与渗漏水的验收精确率目标与可提供的标注样本量？ | 百分比；正样本数 | 定测试集规模（7.2） |
| Q-08 | 缺陷分几级，各级响应与处置时限为多少？ | 级；小时/日 | 定 SLA 与升级策略（10.4） |
| Q-09 | 容灾恢复点与恢复时间目标为多少？ | 分钟；小时 | 定备份架构（14.3） |

# 附录E 3D Tiles 切片踩坑记录

本附录记录一期真实点云加载链路（PLY → pnts → Cesium）落地过程中实际排出的 5 类深层缺陷。每一类都表现为"Cesium 不报错或只报一句难以定位的告警，但瓦片就是不渲染"，因此既是本期的开发凭证，也是二期接入 PDAL 自动化切片时的强制避坑清单。

## E.1 缺陷清单与修复

| 编号 | 现象 | 根因 | 修复 | 二期约束 |
| --- | --- | --- | --- | --- |
| E-01 geometricError 为 0 | `tileset.json` 被成功请求，但根本没有任何 `.pnts` 请求；根瓦片 `_contentState` 长期停在 NOT_LOADED | `root.geometricError = 0` 使 `tile.getScreenSpaceError()` 恒为 0，而 `Cesium3DTilesetBaseTraversal` 在 `SSE ≤ memoryAdjustedScreenSpaceError`（默认 16）时**直接 return，不把任何瓦片放入请求队列**；tileset 级 `geometricError` 再大也不生效 | 根节点 `geometricError` 改为大于 0 的实数（一期取 1e7 量级，确保刚进页面、相机位于约 1.2 万 km 高空时 SSE 仍大于 16，从而触发首瓦片加载；`flyTo` 后 SSE 自然回落） | 生成器必须断言 `root.geometricError > 0`；LOD 树中叶子节点也应避免 0 值，改用极小正数或直接用父级误差的一半 |
| E-02 新老规范 FeatureTable 布局差异 | 解析失败或内容为空，提示版本/长度不符 | 旧版 pnts 使用 `FtJsonHeader / BtJsonHeader`（每个含 `byteLength + byteOffset` 两个字段），Cesium 1.107+ 改为 3D Tiles Next 布局：固定头后紧跟 4 个连续长度字段 `featJsonLen / featBinaryLen / batchJsonLen / batchBinaryLen`，再依次是最多 4 段连续数据 | 重写打包器为 Next 布局：`magic(4) + version(4) + byteLength(4) + 4×len(16)` = 28 字节固定头，其后顺序写入 FeatJson、FeatBinary、BatchJson、BatchBinary | 以目标 Cesium 版本的实际 `PntsParser` 实现为准，不照抄 3D Tiles 1.0 旧规范；升级 Cesium 后须重跑一次本附录的验收用例 |
| E-03 特征表字段名重构 | 位置/中心点偏移被忽略，点云位置错乱或不可见 | 旧字段 `POINTS_POSITION`（Body 内整数偏移）与 `RT_CENTER`（旧拼写）在新版中改为 `POSITION: {"byteOffset": 0}` 与 `RTC_CENTER`；字段名不一致时解析器按缺省值处理，不报错 | `POSITION` 声明为 `{"byteOffset": <FeatBinary 内偏移>}`；颜色改 `RGB: {"byteOffset": <位置数据之后>}` | 二期若写入 `BATCH_LENGTH`/属性表，需同时核对 `_BATCHID` 与 `BATCH_LENGTH` 一致 |
| E-04 ENU 与 ECEF 坐标混用 | 瓦片加载成功（内容 READY）但点云不在目标位置，偏移约 6371 km 量级 | 点位置存的是 ENU 局部米制坐标，`root.transform` 已完成 ENU→ECEF；同时写入 ECEF 量级的 `RTC_CENTER` 后，Cesium 按 `final = transform × (position + rtcCenter)` 计算，把两种量纲相加，点云被推到地心另一侧 | **不写 `RTC_CENTER`**；局部坐标只保留"减去几何中心"的相对量，绝对定位完全交给 `root.transform` 的第 4 列（平移项） | 需要 float32 精度扩展时，`RTC_CENTER` 必须是**与 position 同一坐标系**的偏移量（即 ENU 内部偏移），不得填入 ECEF 绝对坐标 |
| E-05 4 字节对齐 | 控制台报 `start offset of Float32Array should be a multiple of 4`，瓦片解析中断 | Cesium 解析器从 `FtJson 起点 + 声明长度` 直接读取 FeatBinary，不会自行补 padding；一期 FeatJson 实际长 98 字节，`28 + 98 = 126` 非 8 字节边界，导致 float32 视图起始偏移非法 | 把 padding 写进 FeatJson 自身：在 JSON 末尾追加空格（合法 JSON 空白符）使声明长度变为 100，`28 + 100 = 128`，8 字节对齐；BatchJson 同理 | 所有二进制段（FeatBinary、BatchBinary）的绝对起始偏移必须满足 8 字节对齐；`POSITION` 等 float32/uint32 字段的相对偏移须满足 4 字节对齐 |

## E.2 验证方法与证据

| 检查项 | 方法 | 一期结果 |
| --- | --- | --- |
| 文件结构自洽 | Python 按声明长度逐段定位，校验 `28 + featJsonLen + featBinaryLen + batchJsonLen + batchBinaryLen == 文件字节数` | 4203520 == 4203520 |
| 对齐 | 断言 `(28 + featJsonLen) % 8 == 0` 且 FeatBinary 绝对偏移 `% 4 == 0` | 128，两条均满足 |
| 坐标正确 | `root.transform × 首点齐次坐标` 与 `geodetic_to_ecef(114.31, 30.52, 50)` 比对 | 与 ENU 原点 ECEF 一致（相对误差 < 1e-6） |
| 请求链路 | 浏览器网络面板 + `window.__globeDebug__` 运行时断言 | `tileset.json` 与 `tunnel.pnts` 均 200；`root.contentReady = true`；`selectedTiles = 1` |
| 渲染可见 | 硬刷新后等待首帧 + 固定视角截图 | 白色点云条带可见；RGB 全白可避免深色底图上的"黑点看不见" |

## E.3 二期 PDAL 自动化切片强制约束

接入 PDAL / COPC 自动化管线时，以下约束须写入生成器断言与 CI 用例，不得等到前端联调才发现：

1. **禁止零误差根节点**：`root.geometricError > 0`，并保证初始相机视距下 `SSE > maximumScreenSpaceError`；
2. **规范以 Cesium 实现为准**：pnts 布局、字段名、特征表结构以当前锁定版本的 `PntsParser` 源码为唯一依据，不照抄旧版 3D Tiles 1.0 文档；
3. **坐标单一路径**：ENU 局部坐标 + `root.transform` 平移定位；`RTC_CENTER` 默认不写，确需精度扩展时只用同坐标系偏移；
4. **对齐硬校验**：FeatJson / BatchJson 长度填充至 8 字节边界，所有 float32/uint32 字段相对偏移 4 字节对齐；
5. **产物自检入 CI**：对每个生成的 pnts 执行 E.2 前三项（长度自洽、对齐、首点坐标），任一失败即构建失败；
6. **属性表字段同步**：如写入 `BATCH_LENGTH`，须确保 `_BATCHID` 为无符号整型且与 `BATCH_LENGTH` 一致，否则会出现"能渲染但点不到属性"的隐性失败。
