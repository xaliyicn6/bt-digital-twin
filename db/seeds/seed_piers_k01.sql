-- =============================================================================
-- 开发联调种子：B001 特大桥 → K01 第1跨 → PIR 1号桥墩 … PIR 10号桥墩
--
-- 目的：为"批量巡检"演示提供 10 个真实构件。资产树刷新后每个桥墩显示
--       各自的 tech_grade 圆点/徽标，批量分析 10 条任务分别落在不同构件。
--
-- 依据：
--   db/schema.sql 5.3 节（P0 冻结）：
--     uq_component_code (project_id, component_code) WHERE deleted_at IS NULL；
--     level CHECK 1..4；component_type/component_name NOT NULL；
--     ck_component_owner：BRIDGE 必须有 bridge_id 且 tunnel_id 为空。
--   层级规则（app/services/component.py）：L3 父节点必须是 L2，且继承项目/结构归属。
--   tech_grade 列为 Alembic 0005 增量列（C1），本文件不修改表结构。
--
-- 幂等：
--   基础对象与 10 个桥墩均 ON CONFLICT DO NOTHING（重复执行不重复插入）；
--   tech_grade 用 UPDATE + IS DISTINCT FROM 写回，仅值变化时才动 updated_at。
--   初值分布覆盖 1–5 类，保证资产树红/黄/绿都有；分析成功后由服务端回写实算等级。
--
-- 注意：本文件只插入演示数据；geom 暂缺实测坐标，置 NULL。
-- =============================================================================

BEGIN;

-- 1) 项目（calc_srid=4547，与 db/schema.sql 的几何列占位 SRID 一致）
INSERT INTO project (id, project_code, project_name, calc_srid)
VALUES (
  '5eed0000-0000-4000-8000-000000000001',
  'DEMO-P001',
  '桥隧数字孪生开发联调项目',
  4547
)
ON CONFLICT DO NOTHING;

-- 2) 结构：B001 特大桥（BEAM，1 跨）
INSERT INTO bridge (
  id, project_id, bridge_code, bridge_name, struct_type, span_count, has_pointcloud
)
SELECT
  '5eed0000-0000-4000-8000-000000000002',
  p.id,
  'B001',
  'B001 特大桥',
  'BEAM',
  1,
  'NO'
FROM project p
WHERE p.project_code = 'DEMO-P001'
  AND p.deleted_at IS NULL
ON CONFLICT DO NOTHING;

-- 3) L1 构件：B001 特大桥
INSERT INTO component (
  id, project_id, struct_kind, bridge_id, tunnel_id, parent_id,
  component_code, component_name, level, component_type, geom
)
SELECT
  '5eed0000-0000-4000-8000-000000000003',
  p.id,
  'BRIDGE',
  b.id,
  NULL,
  NULL,
  'B001 特大桥',
  'B001 特大桥',
  1,
  'STRUCTURE',
  NULL
FROM project p
JOIN bridge b
  ON b.project_id = p.id
 AND b.bridge_code = 'B001'
 AND b.deleted_at IS NULL
WHERE p.project_code = 'DEMO-P001'
  AND p.deleted_at IS NULL
ON CONFLICT DO NOTHING;

-- 4) L2 构件：K01 第1跨
INSERT INTO component (
  id, project_id, struct_kind, bridge_id, tunnel_id, parent_id,
  component_code, component_name, level, component_type, geom
)
SELECT
  '5eed0000-0000-4000-8000-000000000004',
  l1.project_id,
  l1.struct_kind,
  l1.bridge_id,
  l1.tunnel_id,
  l1.id,
  'K01 第1跨',
  'K01 第1跨',
  2,
  'SPAN',
  NULL
FROM component l1
JOIN project p ON p.id = l1.project_id
WHERE l1.component_code = 'B001 特大桥'
  AND l1.level = 1
  AND l1.deleted_at IS NULL
  AND p.project_code = 'DEMO-P001'
  AND p.deleted_at IS NULL
ON CONFLICT DO NOTHING;

-- 5) L3 构件：PIR 1号桥墩 … PIR 10号桥墩（父 = L2，level=3，继承归属）
INSERT INTO component (
  id, project_id, struct_kind, bridge_id, tunnel_id, parent_id,
  component_code, component_name, level, component_type, pier_type, tech_grade, geom
)
SELECT
  v.id::uuid,
  l2.project_id,
  l2.struct_kind,
  l2.bridge_id,
  l2.tunnel_id,
  l2.id,
  v.code,
  v.code,
  3,
  'PIER',
  'RECT',
  v.grade,
  NULL
FROM component l2
JOIN project p ON p.id = l2.project_id
CROSS JOIN (
  VALUES
    ('5eed0000-0000-4000-8000-000000000005', 'PIR 1号桥墩',  1),
    ('5eed0000-0000-4000-8000-000000000006', 'PIR 2号桥墩',  3),
    ('5eed0000-0000-4000-8000-000000000007', 'PIR 3号桥墩',  4),
    ('5eed0000-0000-4000-8000-000000000008', 'PIR 4号桥墩',  5),
    ('5eed0000-0000-4000-8000-000000000009', 'PIR 5号桥墩',  2),
    ('5eed0000-0000-4000-8000-00000000000a', 'PIR 6号桥墩',  1),
    ('5eed0000-0000-4000-8000-00000000000b', 'PIR 7号桥墩',  3),
    ('5eed0000-0000-4000-8000-00000000000c', 'PIR 8号桥墩',  4),
    ('5eed0000-0000-4000-8000-00000000000d', 'PIR 9号桥墩',  5),
    ('5eed0000-0000-4000-8000-00000000000e', 'PIR 10号桥墩', 2)
) AS v(id, code, grade)
WHERE l2.component_code = 'K01 第1跨'
  AND l2.level = 2
  AND l2.deleted_at IS NULL
  AND p.project_code = 'DEMO-P001'
  AND p.deleted_at IS NULL
ON CONFLICT DO NOTHING;

-- 6) 初值 tech_grade（覆盖 1–5 类；重复执行仅修正被改动过的值）
UPDATE component c
SET tech_grade = v.grade,
    updated_at = now()
FROM (
  VALUES
    ('PIR 1号桥墩',  1),
    ('PIR 2号桥墩',  3),
    ('PIR 3号桥墩',  4),
    ('PIR 4号桥墩',  5),
    ('PIR 5号桥墩',  2),
    ('PIR 6号桥墩',  1),
    ('PIR 7号桥墩',  3),
    ('PIR 8号桥墩',  4),
    ('PIR 9号桥墩',  5),
    ('PIR 10号桥墩', 2)
) AS v(code, grade)
WHERE c.project_id = (
    SELECT id FROM project WHERE project_code = 'DEMO-P001' AND deleted_at IS NULL
  )
  AND c.component_code = v.code
  AND c.level = 3
  AND c.component_type = 'PIER'
  AND c.deleted_at IS NULL
  AND c.tech_grade IS DISTINCT FROM v.grade;

COMMIT;

-- 自检：应返回 10 行；tech_grade 覆盖 1–5 类，父节点均为 K01 第1跨。
SELECT
  c.component_code,
  c.component_name,
  c.level,
  c.component_type,
  c.pier_type,
  c.tech_grade,
  parent.component_code AS parent_code
FROM component c
LEFT JOIN component parent ON parent.id = c.parent_id
WHERE c.component_code IN (
    'PIR 1号桥墩', 'PIR 2号桥墩', 'PIR 3号桥墩', 'PIR 4号桥墩', 'PIR 5号桥墩',
    'PIR 6号桥墩', 'PIR 7号桥墩', 'PIR 8号桥墩', 'PIR 9号桥墩', 'PIR 10号桥墩'
  )
  AND c.deleted_at IS NULL
ORDER BY c.component_code;
