# 交接说明（README）

本目录把《公路桥隧结构智能检测与数字孪生运维平台详细设计说明书》V1.2 拆成一套可直接派给 AI 编码会话的交接物。所有内容与 V1.2 正文一致：表名与字段见 `db/schema.sql`，接口路径与错误体见 `contracts/openapi.yaml`，工单 12 态与迁移见 `state/work_order_state_machine.yaml`，假设条款 H-01～H-13 与待确认项 C-xx 见说明书 §1.5.1 与附录 D。

## 1. 文件清单

```text
ai_handoff/
├── AGENTS.md                              # 仓库根目录规范（最高约束）
├── README.md                              # 本文件
├── contracts/
│   └── openapi.yaml                       # OpenAPI 3.0.3，29 paths / 30 operations
├── db/
│   └── schema.sql                         # PostgreSQL 15 + PostGIS 3.x，29 张表
├── state/
│   └── work_order_state_machine.yaml      # 工单 12 态、19 条迁移、5 条典型路径
└── docs/
    └── tasks/
        ├── _index.md                      # 任务总表（派活入口）
        └── T-01 … T-24_*.md               # 24 张任务卡
```

## 2. 怎么用这套文件派活

派活顺序固定，不要跳步：

1. **先读 `AGENTS.md`**。它是硬约束：契约优先、禁止编造、不确定就停、每改必测、冻结项不可改、日志带 traceId、密钥不入仓、几何运算不在 4490 下进行、上传不经应用服务中转。AI 会话与 AGENTS.md 冲突的输出一律作废。
2. **再读 `docs/tasks/_index.md`**，确认本次要派哪张卡、它的依赖前置是否已完成、它的验收门是什么。依赖未满足不得开卡。
3. **一张卡一个会话**。把该卡全文、`AGENTS.md`、以及该卡"输入"栏列出的契约/DDL/状态机片段一起贴给会话。不要把多张卡塞进同一会话——上下文一混，越权改动就会出现。
4. **卡与卡之间只通过三个事实源交互**：`contracts/openapi.yaml`、`db/schema.sql`、`state/work_order_state_machine.yaml`。不通过"口头上说好了"、不通过跨卡私有函数、不通过 A 卡调用 B 卡未在契约中出现的内部实现。
5. **交付即验收**。AI 会话交付时必须给出：改动文件清单、跑过的测试命令与输出、对应任务卡 DoD 的逐条比对。缺任何一项按未完成处理。

### 阶段与并行

`P0 契约冻结 → P1 骨架与 CI → P2 台账与工单 → P3 文件与任务队列 → P4 点云可视化 → P5 几何算法 → P6 AI 推理与联调`。

同阶段内的卡可并行，跨阶段必须等前置完成。横切卡（T-05 日志与审计、T-11 异步任务框架、T-16 监控埋点）不占关键路径，但必须早于它们的消费者开工。关键路径：`T-01 → T-02 → T-03 → T-04 → T-06 → T-07 → T-08 →（T-17 → T-18/T-19）→ T-23 → T-24`。

## 3. 验收门定义

### P0（T-01）完成标志

三条同时满足才算 P0 冻结，缺一条不得开 P1：

| 验收项 | 判定方法 | 通过标准 |
| --- | --- | --- |
| OpenAPI 可校验 | `openapi-spec-validator contracts/openapi.yaml` | 输出 `OK`，0 error；path 数与 operation 数与总表一致 |
| DDL 可执行 | 空库执行 `psql -f db/schema.sql` | 0 error；29 张表、扩展 `postgis`/`pgcrypto` 就位；重复执行不报错（幂等） |
| 状态机可加载 | 解析 yaml 并做拓扑校验（见下） | 12 个状态、19 条迁移；每个 `to` 都在 `state_enum` 内；每个非初始态都有入边；无孤儿状态 |

三条门已在空库实测通过（PostgreSQL 17.5 + PostGIS 3.5.2：连续执行两次均 0 错误，29 表 / 21 枚举 / 4 个 GIST 索引 / SRID 4547 三维几何列）。CI 固定使用 `postgis/postgis:15-3.4`，脚本未使用 16+ 专属语法。

状态机自检的最小脚本（可直接粘进 CI）：

```python
import yaml
cfg = yaml.safe_load(open("state/work_order_state_machine.yaml", encoding="utf-8"))
states = set(cfg["state_enum"])
trs = cfg["transitions"]
assert len(states) == 12, len(states)
assert len(trs) == 19, len(trs)
for t in trs:
    assert t["to"] in states, t
    assert t["from"] == "*" or t["from"] in states, t
reach, todo = {cfg["initial_state"]}, [cfg["initial_state"]]
while todo:
    cur = todo.pop()
    for t in trs:
        if t["from"] in (cur, "*") and t["to"] not in reach:
            reach.add(t["to"]); todo.append(t["to"])
assert reach == states, states - reach
```

### 其余阶段的验收门

每张卡的"验收门"列已写成可判定条件，汇总在 `_index.md` 第 1 节。共性要求：
- 所有接口用例必须给出具体输入与期望输出（状态码、错误码 `BT-xxxxx`、落库字段）。
- 涉及异步任务的卡，必须验证"提交幂等"与"进程崩溃后可恢复"。
- 涉及几何算法的卡，必须附基准数据与误差报告；没有报告不算通过（T-18、T-19、T-23）。

## 4. 如何回填业主确认值

V1.2 里所有不可推导的数值都挂在 C-xx（待确认事项）与 H-xx（假设条款）上，二者一一对应。`_index.md` 第 4 节是完整的"阻塞项 → 受影响卡 → 现行假设"映射表。

回填流程：

1. **拿到业主答复** → 先查 `_index.md` 第 4 节，定位该 C 项影响哪些卡、当前按哪条 H 假设在跑。
2. **按"契约优先"改**：涉及字段、枚举、接口的，先改 `contracts/openapi.yaml` 或 `db/schema.sql`；涉及 SLA、角色、迁移条件的，先改 `state/work_order_state_machine.yaml`。三个事实源改完，才允许改业务代码。
3. **同步文档**：在说明书 V1.2 的 §1.5.1 假设条款表把对应 H 项标为"已确认（值/日期/确认人）"，在附录 D 对应行更新"建议值/暂定假设"列，并在附录 A 修订对照表补一条（写清"原假设 → 现取值 → 影响章节"）。
4. **毙掉旧的 TODO**：代码里所有 `TODO(C-xx)` 必须与该确认同步清除；残留的 `TODO` 视为未完成。
5. **重跑受影响指标**：`_index.md` 第 4 节列出的受影响卡，其验收用例必须重跑一遍并在 PR 描述里贴输出。

现行假设一览（不得新造编号，取值以说明书与决策包为准）：

| 编号 | 内容 | 影响卡 |
| --- | --- | --- |
| H-01 | 坐标系与投影带（3° 带 + 工程局部系，SRID 占位 4547，禁用 4490 做距离运算） | T-01、T-06、T-09、T-14、T-17 |
| H-03 | 推理显存 ≥8 GB | T-20 |
| H-04 | 前端按 1080p 核显档 | T-15 |
| H-05 | 瓦片 50 万点/片、SSE 16；留存 30 天热 + 180 天冷、审计 3 年 | T-14、T-05 |
| H-06 | SLA 按自然日 24 h 计时 | T-07、T-08、T-13 |
| H-09 | 局部独立系平移/旋转/尺度参数 | T-01、T-06 |
| H-10 | 设计图纸情形 II：无竣工测量时以首测为基线 | T-06、T-19 |
| H-11 | 控制点 4 点 + 30% 检查点 | T-09、T-17、T-23 |
| H-12 | 规模 30 桥 + 10 隧，单座点云 5–10 GB | T-09、T-20 |
| H-13 | 容许值 ±5 mm（垂直度）/ ±10 mm（侵限） | T-17、T-18、T-19、T-23 |

其余 C 项（C-02 并发、C-08 GPU、C-09 机型、C-12 SLA、C-13 密级、C-14 等保、C-15 留存、C-17 PMS）在 `_index.md` 第 4 节有逐条对应；本表只列已写进正文的 H 条款。

## 5. 变更追溯

- **冻结后只能新增迁移**：`db/migrations/V0001__xxx.sql` 前向迁移，禁止直接改 `db/schema.sql` 后当作无事发生。
- **一次变更三处同步**：契约、DDL、状态机任意一处变更，必须在同一 PR 内同步另外两处与 `_index.md` 的受影响卡清单，否则 CI 的契约漂移检查失败。
- **提交信息**：`<type>(<scope>): <subject>`，正文写"改了什么 / 为什么 / 影响哪张卡 / 对应哪个 C 或 H 编号"。
- **决策留痕**：影响架构或验收口径的取舍写进 `docs/adr/`，文件名 `ADR-xxxx_<标题>.md`，不写在代码注释里。

## 6. 明确禁止

- 编造规范条款号、容许值、业主数据、实测精度。缺数据写 `TODO(C-xx)`，代码里抛 `NotImplementedError` 或返回 `503`。
- 把点云与影像上传公有云或调用在线推理；Cesium 不得使用 Ion 或其他在线底图。
- 用 EPSG:4490 的经纬度直接算距离、面积、凸包、ICP 邻域。
- 让文件流经应用服务中转；上传必须走 MinIO 预签名分片直传。
- 未经人工确认把 AI 候选缺陷直接写入正式台账。
