# T-06 桥梁/隧道台账与构件树

| 项 | 内容 |
| --- | --- |
| 阶段 | P2 |
| 依赖前置 | T-04 |
| 产出 | project/bridge/tunnel/component 的增查改、四级构件树、编码生成器、拾取反查 |
| 验收门 | 编码唯一率 100%；树深度与父子关系正确；分页与增量可用 |
| 预估工作量 | 6 人日 / 4 卡 |
| 人工主导 | 否 |
| 外部阻塞 | 阻塞于 C-04（设计图纸与竣工测量成果，用于设计轴线与断面模板字段） |

## 1. 目标

建立资产主数据与四级构件树，成为点云、影像、缺陷、工单的共同挂载点。

## 2. 输入

- API-01 至 API-06
- 表 project/bridge/tunnel/component/geometry_baseline
- dict_type/dict_item（结构类型、墩型、断面形式）

## 3. 改动范围

- 允许：backend/app/modules/asset/**、frontend/src/views/asset/**
- 禁止：修改 component 的编码规则与层级语义

超出上述范围即视为越权改动，即使代码正确也退回。

## 4. 实现要点

- 构件编码 `结构码-跨号-构件类型-部位-序号`，如 B001-K03-PIR-P01-001；生成器加唯一索引兜底
- 四级树用 `parent_id` 自关联 + `level`，禁止用路径字符串查询（性能与一致性都差）
- 几何列 SRID 一律取 `project.calc_srid`，写入前校验，不一致返回 422 + BT-40001
- 列表接口必须支持 `limit/cursor` 与 `updated_since`，默认 limit=20、上限 100

## 5. DoD（可验证的完成定义）

- 同一编码重复创建返回 409 + BT-30002
- 按结构+类型+层级查询返回正确子树
- 桥详情含构件数与点云覆盖状态
- 契约测试覆盖 API-01 至 API-06

## 6. 验收用例

| # | 输入 | 操作 | 期望结果 |
| --- | --- | --- | --- |
| 1 | 创建构件编码已存在 | POST /components | 409，code=BT-30002，errors 指向 component_code |
| 2 | 查询 level=3 且 type=PIR | GET /components?struct=B001&type=PIR&level=3 | 仅返回该层矩形墩，分页 next_cursor 可续 |
| 3 | 写入 SRID=4326 的构件几何 | POST | 422，code=BT-40001 |

## 7. 依赖与顺序

T-04 之后；T-07、T-09、T-17 依赖它。

## 8. 常见坑

- 不要用中文做 code
- 不要在 component 里存桥名，名称走 join
- 增量同步必须包含软删除标记，否则前端删不掉
