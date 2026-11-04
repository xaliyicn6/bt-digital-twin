# AGENTS.md — 仓库根目录规范

本文件是 AI 编码会话的**最高约束**。与其它文档冲突时，以本文件的"强制规则"为准；事实细节以 `contracts/`、`db/`、`state/` 三个目录为准，叙述细节以 `docs/` 为准。

## 1. 项目定位

公路桥隧结构智能检测与数字孪生运维平台的**单体后端 + 单体前端**实现：把激光点云与巡检影像变成可量测的几何结论（垂直度、净空侵限），把 AI 候选缺陷变成人工确认后的正式台账与可追溯工单，全部部署在业主内网。一句话：**点云进、结论出、工单可追溯、无外网依赖**。

## 2. 技术栈与版本锁定

| 层 | 技术 | 锁定版本 | 说明 |
| --- | --- | --- | --- |
| 前端框架 | Vue 3 | 3.4.x | Composition API + `<script setup>`，禁止 Options API 新写法 |
| 前端构建 | Vite | 5.x | 构建产物静态托管，不引入 SSR |
| 三维引擎 | CesiumJS | 1.11x | 必须 `Ion.defaultAccessToken = undefined`，禁用一切 Cesium Ion 网络请求 |
| 前端语言 | TypeScript | 5.4.x | `strict: true`，禁止 `any`（确需时写 `// eslint-disable-next-line` 并注明原因） |
| 后端框架 | FastAPI | 0.11x | 同步路由用 `def`，涉阻塞 IO 用 `async def` + 线程池 |
| 后端语言 | Python | 3.11 | 仅用标准库 3.11 特性，禁止 3.12 专属语法 |
| 点云处理 | PDAL | 2.6.x | 通过 `pdal pipeline` 子进程调用，不手工解析 LAS |
| 数据库 | PostgreSQL + PostGIS | 15 + 3.4 | 几何运算一律在库内或投影系完成 |
| 对象存储 | MinIO | RELEASE.2024-xx | 预签名分片直传，应用服务不中转文件流 |
| 缓存/队列 | Redis | 7.x | 令牌黑名单、限流桶、任务队列、分布式锁 |
| 推理 | YOLO 推理服务（Ultralytics 封装） | 独立容器 | 只暴露 HTTP，不嵌入业务进程 |
| 反向代理 | Nginx | 1.24.x | TLS 终止、上传网关、静态资源 |

版本以 `pyproject.toml` / `package.json` 的 lock 为准；升级任一主版本须走变更单，不在功能卡里顺手升级。

## 3. 目录结构约定

```text
repo/
├── AGENTS.md                  # 本文件
├── README.md                  # 交接说明
├── contracts/
│   ├── openapi.yaml           # 唯一接口事实源（先改这里）
│   └── examples/              # 请求/响应样例，供契约测试使用
├── db/
│   ├── schema.sql             # 全量 DDL（29 张表）
│   └── migrations/            # 前向迁移脚本，按 V0001__xxx.sql 命名
├── state/
│   └── work_order_state_machine.yaml   # 工单状态机配置（唯一事实源）
├── backend/
│   ├── app/
│   │   ├── main.py            # 应用装配，仅做路由注册与中间件挂载
│   │   ├── core/              # 配置、日志、鉴权、限流、异常处理器
│   │   ├── modules/           # 按业务模块分包，一模块一目录
│   │   │   ├── auth/
│   │   │   ├── rbac/
│   │   │   ├── asset/         # 桥梁、隧道、构件
│   │   │   ├── media/         # 点云、影像、上传
│   │   │   ├── inference/     # 推理任务封装
│   │   │   ├── defect/        # 候选池、台账、复核
│   │   │   ├── geometry/      # 几何分析任务与结果
│   │   │   ├── workorder/     # 状态机引擎与流转
│   │   │   ├── file/          # 附件与对象存储
│   │   │   ├── notify/        # 通知与超期升级
│   │   │   ├── audit/         # 审计日志
│   │   │   ├── sync/          # PMS 同步
│   │   │   └── admin/         # 字典、模型版本、系统配置
│   │   ├── workers/           # 异步任务执行器（独立进程）
│   │   └── shared/            # 通用工具，禁止放业务逻辑
│   └── tests/                 # 单元 + 契约 + 集成
├── frontend/
│   ├── src/
│   │   ├── api/               # 由 openapi.yaml 生成的客户端，不手写请求
│   │   ├── views/
│   │   ├── components/
│   │   ├── stores/
│   │   └── cesium/            # 三维场景、瓦片加载、拾取
│   └── tests/
├── tests/
│   ├── contract/              # 契约测试，针对 openapi.yaml
│   ├── integration/           # 起容器跑真实依赖
│   └── fixtures/              # 基准数据（合成，不含业主真实数据）
└── docs/
    ├── tasks/                 # 任务卡与总表
    └── adr/                   # 决策记录
```

模块边界规则：模块之间只允许**单向依赖**（`workorder` 可依赖 `defect`，`defect` 不得依赖 `workorder`）；跨模块调用走各自 `service.py` 暴露的函数，禁止直接读对方表。数据所有权见 `docs/tasks/T-09_*.md` 的归属表。

## 4. 强制规则（不可协商）

1. **契约优先**：改接口先改 `contracts/openapi.yaml`，再改后端实现，最后更新前端生成客户端。三者顺序不可颠倒；只改代码不改契约的提交一律退回。
2. **禁止编造**：不得编造规范条款号、容许值、业主数据、实测精度。缺数据时写 `TODO(C-xx)` 并在代码中用 `raise NotImplementedError` 或返回 `503`，不得填假数字蒙混。
3. **不确定就停**：遇到契约未定义、状态机无对应迁移、字段含义不明，**停止编码并提问**，不要自行"合理推测"后继续。提问须写明：卡号、缺什么、影响哪个验收项。
4. **每改必测**：每个改动附带至少 1 个自动化测试；纯文档改动除外。没有测试的 PR 不予合并。
5. **冻结项不可改**：`db/schema.sql` 的表名与字段、`state/*.yaml` 的状态与迁移、`contracts/openapi.yaml` 的路径与状态码，均为 P0 冻结项。确需变更须新建 `db/migrations/Vxxxx__*.sql` 并同步三方文件，不得直接改原文件后当作无事发生。
6. **日志必带 traceId**：所有日志记录必须含 `traceId`（来自 `X-Request-Id` 或服务端生成），跨服务调用透传。无 traceId 的日志视为无效日志。
7. **密钥不入库不入仓**：数据库口令、MinIO AK/SK、JWT 私钥、推理服务 Key 一律走环境变量或密钥挂载；`.env` 只提交 `.env.example`。提交前自查 `git diff` 中是否含 `password|secret|token|BEGIN PRIVATE KEY`。
8. **几何运算不得在 4490 下进行**：任何距离、面积、体积、切片、凸包、外接矩形、ICP、KD-Tree 邻域查询，必须先转到 `project.calc_srid` 或工程局部系。此条为历史事故条款，见 T-19 常见坑。
9. **上传不得经应用服务中转**：文件流只走 MinIO 预签名直传，应用服务只处理元数据与校验结果。

## 5. 代码规范

### 后端（Python 3.11）

- 格式化与静态检查：`ruff format` + `ruff check`（行宽 100，规则集 `E,F,I,N,UP,B,SIM`）。
- 类型：`mypy --strict` 对 `backend/app/modules/**/service.py` 与 `core/**` 生效；`pydantic` 模型必须标注字段类型。
- 分层：`router.py`（仅参数解析与响应组装）→ `service.py`（业务与事务边界）→ `repository.py`（SQL，禁止在 router 里写 SQL）。
- 异常：业务异常统一抛 `BizError(code, status, detail)`，由 `core/exceptions.py` 转为 problem+json；禁止 `raise HTTPException` 散落各处。
- 事务：一个 service 方法一个事务边界；跨表写入在同一事务内完成，异步任务投递用 outbox，不直接 `enqueue` 后提交。

### 前端（TypeScript）

- `eslint` + `@typescript-eslint/recommended-type-checked`；`prettier` 统一格式。
- 请求只能经 `src/api/` 下的生成客户端，禁止在组件里手写 `fetch`。
- 三维逻辑集中在 `src/cesium/`，组件只调用封装后的方法；Cesium 实体创建与销毁必须成对，避免内存泄漏。

### 提交信息

`<type>(<scope>): <subject>`，type ∈ `feat|fix|refactor|test|docs|chore|perf`，scope 取模块目录名（如 `workorder`、`geometry`），subject 用中文，不超过 50 字。正文写：改了什么、为什么、影响哪张卡。示例：`feat(workorder): 增加驳回后重开迁移 T-13`。

## 6. 数据安全

- 点云与影像**不得上传任何公有云模型或在线服务**；推理、切片、转码全部在本机或内网完成。
- Cesium 场景禁用 Ion 与任何在线底图；底图走本地 WMTS/XYZ 瓦片。
- 影像中的人脸、车牌在对外导出时必须打码；原始影像仅在授权范围内可访问。
- 数据导出（含截图、报表、点云切片）须写审计日志，记录导出人、范围、用途。
- 测试与样例数据一律用合成数据；不得把业主真实点云提交进仓库或 CI。

## 7. 与任务卡的配合方式

每张任务卡的"改动范围"列出了允许修改的目录。超出范围即视为越权改动，即使代码正确也退回。卡与卡之间只通过 `contracts/`、`db/`、`state/` 三个事实源交互，不通过"口头约定"或跨卡私有函数。
