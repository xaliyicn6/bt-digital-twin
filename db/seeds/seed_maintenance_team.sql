-- =============================================================================
-- 开发联调种子：一条可重复执行的维修班组（含负责人与成员）
--
-- 目的：让「一键派发维修工单」在下拉班组时有真实数据；负责人同时作为
--       work_order.handler_id 的默认指派对象（T01 SUBMIT 要求 handler_id 非空）。
--
-- 依赖：db/seeds/seed_pir1_pier.sql 已插入 project_code='DEMO-P001'。
-- 幂等：全部 ON CONFLICT DO NOTHING；重复执行不报错、不产生重复行。
-- 注意：password_hash 为占位值，仅用于开发联调，禁止用于任何真实环境。
-- =============================================================================

BEGIN;

-- 1) 负责人（养护主管角色）
INSERT INTO sys_user (id, username, password_hash, display_name, dept_name, phone_masked, status)
VALUES (
  '5eed0000-0000-4000-8000-000000000011',
  'mt_leader_demo',
  'dev-only-placeholder-hash',
  '张班长',
  '养护一队',
  '138****0001',
  'ACTIVE'
)
ON CONFLICT DO NOTHING;

-- 2) 授予 MAINT_MANAGER 角色（角色由 db/schema.sql 内置种子创建，这里按 role_code 回查）
INSERT INTO sys_user_role (user_id, role_id)
SELECT '5eed0000-0000-4000-8000-000000000011', r.id
FROM sys_role r
WHERE r.role_code = 'MAINT_MANAGER'
ON CONFLICT DO NOTHING;

-- 3) 测试班组：名称 / 负责人 / 联系电话 / 负责线路范围
INSERT INTO maintenance_team (
  id, project_id, team_code, team_name, leader_id, contact_phone, line_scope
)
SELECT
  '5eed0000-0000-4000-8000-000000000012',
  p.id,
  'MT-001',
  '一号桥隧养护班',
  '5eed0000-0000-4000-8000-000000000011',
  '0571-88880000',
  'B001 特大桥 K01 跨（桥墩/桥面）'
FROM project p
WHERE p.project_code = 'DEMO-P001'
  AND p.deleted_at IS NULL
ON CONFLICT DO NOTHING;

-- 4) 班组成员（负责人同时作为成员，role_in_team=LEADER）
INSERT INTO maintenance_team_member (team_id, user_id, role_in_team)
VALUES (
  '5eed0000-0000-4000-8000-000000000012',
  '5eed0000-0000-4000-8000-000000000011',
  'LEADER'
)
ON CONFLICT DO NOTHING;

COMMIT;
