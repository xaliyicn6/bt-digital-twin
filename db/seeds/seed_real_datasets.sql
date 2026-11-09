-- =============================================================================
-- 开发联调种子：真实点云数据集 → 构件台账
--
-- 目的：让「点云数据集」面板上的两份真实点云（tunnel / bridge）能直接发起
--       几何分析任务（任务按 component_code 定位构件）。
--
-- 登记口径（与 backend/config/pointcloud_sources.yaml 一一对应）：
--   tunnel  → component_code = 'LNX 轮小区间'   （TUNNEL / level 1 / CLEARANCE）
--   bridge  → component_code = 'BD 梁体（beam_disc）'（BRIDGE / level 3 / VERTICALITY）
--
-- 依据：
--   db/schema.sql 5.3 节（P0 冻结）：ck_component_owner（TUNNEL 必须有 tunnel_id
--   且 bridge_id 为空）；uq_component_code (project_id, component_code)。
--   本文件不修改任何表结构，也不写入任何真实坐标（geom 置 NULL）。
--
-- 幂等：全部 ON CONFLICT DO NOTHING；重复执行不重复插入。
-- 依赖：桥侧父节点 K01 第1跨 来自 db/seeds/seed_piers_k01.sql（缺失时该分支跳过）。
-- 数据安全：真实 LAS 与派生瓦片在 .gitignore 内，本文件只登记编码，不含实测值。
-- =============================================================================

BEGIN;

-- 1) 项目（与 seed_piers_k01.sql 一致；重复执行不插入）
INSERT INTO project (id, project_code, project_name, calc_srid)
VALUES (
  '5eed0000-0000-4000-8000-000000000001',
  'DEMO-P001',
  '桥隧数字孪生开发联调项目',
  4547
)
ON CONFLICT DO NOTHING;

-- 2) 隧道结构：轮小区间（lunxiao-L）
INSERT INTO tunnel (
  id, project_id, tunnel_code, tunnel_name, section_type, has_pointcloud
)
SELECT
  '5eed0000-0000-4000-8000-00000000000f',
  p.id,
  'LNX',
  '轮小区间（lunxiao-L）',
  'HORSESHOE',
  'YES'
FROM project p
WHERE p.project_code = 'DEMO-P001'
  AND p.deleted_at IS NULL
ON CONFLICT DO NOTHING;

-- 3) 隧道 L1 构件：LNX 轮小区间
INSERT INTO component (
  id, project_id, struct_kind, bridge_id, tunnel_id, parent_id,
  component_code, component_name, level, component_type, geom
)
SELECT
  '5eed0000-0000-4000-8000-000000000010',
  p.id,
  'TUNNEL',
  NULL,
  t.id,
  NULL,
  'LNX 轮小区间',
  'LNX 轮小区间',
  1,
  'STRUCTURE',
  NULL
FROM project p
JOIN tunnel t
  ON t.project_id = p.id
 AND t.tunnel_code = 'LNX'
 AND t.deleted_at IS NULL
WHERE p.project_code = 'DEMO-P001'
  AND p.deleted_at IS NULL
ON CONFLICT DO NOTHING;

-- 4) 桥梁点云构件：BD 梁体（beam_disc），挂在 K01 第1跨 下（L3）
INSERT INTO component (
  id, project_id, struct_kind, bridge_id, tunnel_id, parent_id,
  component_code, component_name, level, component_type, geom
)
SELECT
  '5eed0000-0000-4000-8000-000000000011',
  l2.project_id,
  l2.struct_kind,
  l2.bridge_id,
  l2.tunnel_id,
  l2.id,
  'BD 梁体（beam_disc）',
  'BD 梁体（beam_disc）',
  3,
  'BEAM',
  NULL
FROM component l2
JOIN project p ON p.id = l2.project_id
WHERE l2.component_code = 'K01 第1跨'
  AND l2.level = 2
  AND l2.deleted_at IS NULL
  AND p.project_code = 'DEMO-P001'
  AND p.deleted_at IS NULL
ON CONFLICT DO NOTHING;

COMMIT;

-- 自检：应返回 2 行（LNX 轮小区间 / BD 梁体（beam_disc）），归属正确。
SELECT
  c.component_code,
  c.level,
  c.component_type,
  c.struct_kind,
  c.tunnel_id,
  c.bridge_id,
  parent.component_code AS parent_code
FROM component c
LEFT JOIN component parent ON parent.id = c.parent_id
WHERE c.component_code IN ('LNX 轮小区间', 'BD 梁体（beam_disc）')
  AND c.deleted_at IS NULL
ORDER BY c.component_code;
