# T-01 契约与假设基线冻结

| 项 | 内容 |
| --- | --- |
| 阶段 | P0 |
| 依赖前置 | 无 |
| 产出 | contracts/openapi.yaml、db/schema.sql、state/work_order_state_machine.yaml 三份文件定稿并入库 |
| 验收门 | openapi 校验通过；DDL 在空库可执行；状态机 YAML 可加载且 12 态 19 迁移齐全 |
| 预估工作量 | 3 人日 / 6 卡 |
| 人工主导 | 是（人工主导：契约取舍与 H 编号分配） |
| 外部阻塞 | 无（依赖 C-05 的具体 SRID 值，先用占位 EPSG:4547） |

## 1. 目标

把 V1.2 第 5、9、10 章的内容固化成三份机器可读的事实源，之后所有代码不得绕过它们。

## 2. 输入

- V1.2 第 5.3 节 29 张表结构
- V1.2 第 5.4 节状态码矩阵、problem+json、错误码分段
- V1.2 第 9 章接口清单 API-01 至 API-20
- V1.2 第 10 章 12 状态与 17 条基础迁移；本配置补齐 CANCEL / MERGE 的通配迁移，共 19 条

## 3. 改动范围

- 允许：contracts/**、db/schema.sql、state/**
- 禁止：backend/**、frontend/**（本卡不写业务代码）

超出上述范围即视为越权改动，即使代码正确也退回。

## 4. 实现要点

- 接口路径一律 `/api/v1`，破坏性变更升 v2，不得在同一版本内改语义
- 错误体必须是 problem+json：`type/title/status/code/traceId/detail/instance/errors`，缺 `code` 即校验不过
- DDL 中几何列的 SRID 写 4547 占位并加注释“待 C-05 确认后替换”，不得留空或写 0
- 状态机 OVERDUE 是叠加标记，不替代主状态；配置里必须用 `overlay: true` 表达

## 5. DoD（可验证的完成定义）

- `openapi-spec-validator contracts/openapi.yaml` 返回 OK
- `psql -f db/schema.sql` 在空库一次性成功，29 张表、枚举类型齐全
- `yaml.safe_load(state/work_order_state_machine.yaml)` 成功，states=12、transitions=19，且每个 `to` 均在 `state_enum` 内
- 三份文件与 V1.2 的路径、状态码、表名逐项比对无差异（附比对记录）

## 6. 验收用例

| # | 输入 | 操作 | 期望结果 |
| --- | --- | --- | --- |
| 1 | openapi 含未定义 $ref | 运行校验器 | 报错并列出缺失组件路径，不进入 P1 |
| 2 | DDL 中把 geometry(PointZ, 4326) | 在空库执行 | 报错：SRID 与 calc_srid 约定不符，需改 4547 占位 |
| 3 | 状态机删除 OVERDUE | 加载配置 | 加载器报“12 态缺失 OVERDUE”，拒绝启动 |

## 7. 依赖与顺序

第一张卡，其它卡全部依赖它。合入后打 tag `contract-freeze-v1`。

## 8. 常见坑

- 不要顺手“优化”路径命名，路径已与前端生成客户端绑定
- 不要把 4490 写进几何列，4490 是经纬度
- 不要在契约里写示例业主数据，示例一律用 BT-xxx 占位编码
