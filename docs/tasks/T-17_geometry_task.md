# T-17 几何分析任务与结果

| 项 | 内容 |
| --- | --- |
| 阶段 | P5 |
| 依赖前置 | T-06 |
| 产出 | API-14/15、geometry_result 落库、geometry_baseline 基线、任务编排与结果版本 |
| 验收门 | 任务可提交可查询；结果含坐标与单位；基线锁定后不可改 |
| 预估工作量 | 5 人日 / 3 卡 |
| 人工主导 | 否 |
| 外部阻塞 | 阻塞于 C-05（SRID）、C-06（控制点） |

## 1. 目标

为垂直度与侵限提供统一的任务入口与结果存储，算法实现留给 T-18/T-19。

## 2. 输入

- API-14、API-15
- geometry_result / geometry_baseline
- pointcloud_file

## 3. 改动范围

- 允许：backend/app/modules/geometry/api/**、models/**
- 禁止：在结果表里存算法中间态大对象

超出上述范围即视为越权改动，即使代码正确也退回。

## 4. 实现要点

- 结果必须带 `srid`、`unit`、`algorithm_version`、`params_hash`，可复现
- 基线 `baseline_type` 取 DESIGN 或 FIRST_OBSERVE（对应 H-10 情形 II）
- 同一构件同一类型重复计算生成新版本，旧版本保留
- 算法失败返回 202 任务 FAILED，接口层不抛 500

## 5. DoD（可验证的完成定义）

- 提交任务返回 202 + Location
- 查询返回结果与版本号
- 基线锁定后修改返回 409
- 结果单位与契约一致（m/mm 不混用）

## 6. 验收用例

| # | 输入 | 操作 | 期望结果 |
| --- | --- | --- | --- |
| 1 | 提交未注册的 componentId | POST /geometry/tasks | 404，code=BT-30001 |
| 2 | 查询 FAILED 任务 | GET /geometry/tasks/{id} | 200，status=FAILED，detail 带失败原因与错误码 |
| 3 | 对已锁定基线再写入 | POST baseline | 409，提示先解锁 |

## 7. 依赖与顺序

T-06 之后；T-18、T-19 在其上实现。

## 8. 常见坑

- 不要把经纬度存进结果表的几何列
- 不要复用旧 params_hash 蒙混
- 不要吞掉算法异常只返回空结果
