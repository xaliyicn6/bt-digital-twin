# T-07 工单状态机引擎

| 项 | 内容 |
| --- | --- |
| 阶段 | P2 |
| 依赖前置 | T-06 |
| 产出 | 配置加载器、迁移执行器、守卫求值、事件写入、SLA 计算、幂等与乐观锁 |
| 验收门 | 19 条迁移全部可执行；非法迁移返回 409 + BT-70001 |
| 预估工作量 | 6 人日 / 4 卡 |
| 人工主导 | 是（人工主导：SLA 时限取值待业主定） |
| 外部阻塞 | 阻塞于 C-12（缺陷等级与 SLA 时限，默认 H-06 自然日计时） |

## 1. 目标

把状态机做成配置驱动，代码里不出现任何硬编码状态分支。

## 2. 输入

- state/work_order_state_machine.yaml
- work_order / work_order_event
- V1.2 §10.2–10.5

## 3. 改动范围

- 允许：backend/app/modules/workorder/engine/**
- 禁止：在路由层写 if status == ...；禁止改 yaml 的迁移语义

超出上述范围即视为越权改动，即使代码正确也退回。

## 4. 实现要点

- 加载时校验：状态覆盖 12 个、事件唯一、guard 引用的字段存在，任一不满足拒绝启动
- 禁止迁移返回 409 + BT-70001，响应体带当前状态与允许事件列表
- SLA 起算 PENDING_CONFIRM；`sla_deadline + sla_paused_total` 与 now 比较，超时只置 `is_overdue`
- 并发用 `version` 乐观锁；重复提交按 `Idempotency-Key` 返回首次结果

## 5. DoD（可验证的完成定义）

- 19 条迁移各有单测（正常 + 非法）
- 一次迁移在同一事务内写 work_order 与 work_order_event
- 超时定时器重复触发只产生 1 条事件
- 状态机 yaml 改动后无需改代码即可生效

## 6. 验收用例

| # | 输入 | 操作 | 期望结果 |
| --- | --- | --- | --- |
| 1 | 从 CLOSED 发 ASSIGN | POST /workorders/{id}/transitions | 409，code=BT-70001，body 含 allowed_events[] |
| 2 | 两个会话同时 APPROVE（version=3） | 并发调用 | 一个 200，一个 409 且提示刷新 |
| 3 | SUSPENDED 期间超时定时器触发 | 定时任务 | `is_overdue` 不变，因为暂停不计时 |

## 7. 依赖与顺序

T-06 之后；T-08、T-13 依赖它。

## 8. 常见坑

- 不要把 OVERDUE 当主状态写进 status 字段
- 不要用时间戳差值算 SLA，节假日口径一变就错
- 不要在事务外发通知，通知失败会留下不一致
