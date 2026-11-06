-- =============================================================================
-- 开发联调种子：B001 特大桥 → K01 第1跨 → PIR 1号桥墩
--
-- 目的：让几何任务接口能按 ``component_code = 'PIR 1号桥墩'`` 查到构件，
--       避免前端点击"发起垂直度分析"时报 404 BT-30001。
--
-- 依据：
--   db/schema.sql 5.3 节（P0 冻结）：
--     project(project_code UNIQUE, calc_srid NOT NULL)
--     bridge (uq_bridge_code (project_id, bridge_code) WHERE deleted_at IS NULL)
--     component(
--       ck_component_owner: BRIDGE 必须有 bridge_id 且 tunnel_id 为空；
--       level CHECK 1..4；component_type/component_name NOT NULL；
--       uq_component_code (project_id, component_code) WHERE deleted_at IS NULL；
--       geom geometry(PointZ, 4547) 可空；
--       parent_id 可空，L1 无父节点
--     )
--   层级规则（app/services/component.py 138-171）：
--     L1 parent_id 为空；子节点 level = 父 level + 1；子节点继承父的项目与结构归属。
--
-- 幂等：全部 ON CONFLICT DO NOTHING，并用自然键回查父节点 id；重复执行不报错。
-- 注意：本文件不修改任何表结构，只插入演示数据；geom 暂缺实测坐标，置 NULL。
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

-- 3) L1 构件：B001 特大桥（无父节点，归属 BRIDGE + bridge_id）
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

-- 4) L2 构件：K01 第1跨（父 = L1，level 必须为 2，继承项目/结构归属）
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
WHERE l1.component_code = 'B001 特大桥'
  AND l1.level = 1
  AND l1.deleted_at IS NULL
ON CONFLICT DO NOTHING;

-- 5) L3 构件：PIR 1号桥墩（父 = L2，level 必须为 3，继承项目/结构归属）
INSERT INTO component (
  id, project_id, struct_kind, bridge_id, tunnel_id, parent_id,
  component_code, component_name, level, component_type, pier_type, geom
)
SELECT
  '5eed0000-0000-4000-8000-000000000005',
  l2.project_id,
  l2.struct_kind,
  l2.bridge_id,
  l2.tunnel_id,
  l2.id,
  'PIR 1号桥墩',
  'PIR 1号桥墩',
  3,
  'PIER',
  'RECT',
  NULL
FROM component l2
WHERE l2.component_code = 'K01 第1跨'
  AND l2.level = 2
  AND l2.deleted_at IS NULL
ON CONFLICT DO NOTHING;

COMMIT;

-- 自检：应恰好返回 1 行，且 level=3、parent_id 指向 K01 第1跨。
SELECT
  c.component_code,
  c.component_name,
  c.level,
  c.component_type,
  c.pier_type,
  c.project_id,
  c.struct_kind,
  c.bridge_id,
  c.parent_id,
  parent.component_code AS parent_code
FROM component c
LEFT JOIN component parent ON parent.id = c.parent_id
WHERE c.component_code = 'PIR 1号桥墩'
  AND c.deleted_at IS NULL;
