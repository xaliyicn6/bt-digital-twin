-- =============================================================================
-- 公路桥隧结构智能检测与数字孪生运维平台 数据库结构（V1.2 第 5.3 节落地）
-- 目标库：PostgreSQL 15 + PostGIS 3.4
-- 本文件是 P0 冻结项。任何变更必须新增 db/migrations/Vxxxx__*.sql，不得直接改本文件。
--
-- 执行顺序（空库一次性执行，务必按此顺序）：
--   0) CREATE EXTENSION postgis; CREATE EXTENSION pgcrypto;
--   1) 枚举类型（CREATE TYPE 区）
--   2) 字典与系统表（dict_type / dict_item / sys_*）
--   3) 主数据（project → bridge / tunnel → component）
--   4) 采集数据（pointcloud_file / image_file / control_point）
--   5) 业务数据（defect_candidate → defect_ledger → work_order → work_order_event）
--   6) 支撑数据（attachment / file_object_link / async_task / notification / sync_log）
--   7) 几何与配置（geometry_baseline / geometry_result / model_registry / threshold_config）
--   8) 审计（audit_log + 只追加触发器）
--   9) 索引与视图
--
-- 与假设条款的对应（详见 docs/tasks/_index.md 第 4 节）：
--   H-01 / H-09  C-05 未定：几何列 SRID 统一写占位 4547，运行时由 project.calc_srid 覆盖
--   H-05         C-15 未定：日志留存口径体现在 audit_log 分区与注释，不建 TTL
--   H-06         C-12 未定：work_order.sla_deadline 由应用层按自然日计算后写入
--   H-10         C-04 未定：geometry_baseline.baseline_type 允许 FIRST_OBSERVE
--   H-11         C-06 未定：control_point 不强制最少点数，由应用层校验
--   H-12         C-01 未定：无分区表，容量靠索引与归档策略应对
--
-- SRID 说明：4547 为 CGCS2000 3 度带占位示例，C-05 确认后由迁移脚本统一替换。
-- 禁止在此文件出现 EPSG:4490（经纬度，不可用于毫米级距离运算）。
--
-- 重复执行：本脚本幂等。枚举用 DO 块捕获 duplicate_object，表/索引带 IF NOT EXISTS，
-- 触发器先 DROP 再建，初始数据带 ON CONFLICT DO NOTHING。重复执行不改动已有对象值，
-- 若枚举取值发生漂移，由 T-01 的契约比对拦截，不依赖本脚本静默跳过。
-- 实测：PostgreSQL 17.5 + PostGIS 3.5.2 空库连续执行两次，均 0 错误；
-- CI 目标镜像为 postgis/postgis:15-3.4，语句未使用 16+ 专属语法。
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =============================================================================
-- 1. 枚举类型
-- =============================================================================
DO $bt$ BEGIN
  CREATE TYPE struct_kind AS ENUM ('BRIDGE', 'TUNNEL');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE bridge_struct_type AS ENUM ('BEAM', 'ARCH', 'CABLE_STAYED', 'SUSPENSION', 'COMPOSITE', 'OTHER');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE tunnel_section_type AS ENUM ('HORSESHOE', 'CIRCULAR', 'RECTANGULAR', 'THREE_CENTER', 'OTHER');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE pier_type AS ENUM ('RECT', 'CIRCLE', 'ROUND_END', 'OTHER');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE pointcloud_status AS ENUM ('UPLOADING', 'REGISTERED', 'CONVERTING', 'READY', 'FAILED');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE image_pose_status AS ENUM ('OK', 'MISSING', 'INVALID');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE image_pose_source AS ENUM ('EXIF', 'POS_FILE', 'TARGET');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE defect_type AS ENUM ('CRACK', 'SPALLING', 'SEEPAGE', 'REBAR_EXPOSURE', 'TILT', 'OTHER');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE defect_level AS ENUM ('L1', 'L2', 'L3');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE candidate_status AS ENUM ('PENDING', 'CONFIRMED', 'REJECTED');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE ledger_status AS ENUM ('CONFIRMED', 'ORDER_CREATED', 'CLOSED', 'VOID');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

-- 12 个工单状态；OVERDUE 为叠加标记，不替代主状态（应用层另用 is_overdue 表达）
DO $bt$ BEGIN
  CREATE TYPE workorder_status AS ENUM (
  'DRAFT', 'PENDING_CONFIRM', 'CONFIRMED', 'ASSIGNED', 'IN_PROGRESS',
  'PENDING_ACCEPT', 'CLOSED', 'REJECTED', 'SUSPENDED', 'OVERDUE',
  'CANCELLED', 'MERGED'
);
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE task_status AS ENUM ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'RETRYING', 'CANCELLED', 'TIMEOUT');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE task_biz_type AS ENUM ('INFERENCE', 'PDAL_CONVERT', 'GEOMETRY', 'THUMBNAIL');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE analysis_type AS ENUM ('VERTICALITY', 'CLEARANCE', 'SECTION_SHAPE');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE baseline_type AS ENUM ('DESIGN', 'FIRST_OBSERVE');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE scope_type AS ENUM ('PROJECT', 'BRIDGE', 'TUNNEL', 'SECTION');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE data_class AS ENUM ('PUBLIC', 'INTERNAL', 'SENSITIVE', 'CLASSIFIED');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE notify_level AS ENUM ('P1', 'P2', 'P3');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE user_status AS ENUM ('ACTIVE', 'DISABLED', 'LOCKED');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

DO $bt$ BEGIN
  CREATE TYPE link_object_type AS ENUM ('BRIDGE', 'TUNNEL', 'COMPONENT', 'WORK_ORDER', 'DEFECT', 'POINTCLOUD', 'IMAGE');
EXCEPTION
  WHEN duplicate_object THEN NULL;  -- 幂等：类型已存在则跳过，漂移由 T-01 契约比对拦截
END $bt$;

-- =============================================================================
-- 2. 字典与系统表
-- =============================================================================
CREATE TABLE IF NOT EXISTS dict_type (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  type_code     varchar(64) NOT NULL UNIQUE,
  type_name     varchar(128) NOT NULL,
  remark        text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    uuid,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  updated_by    uuid,
  deleted_at    timestamptz,
  CONSTRAINT ck_dict_type_code CHECK (type_code ~ '^[a-z][a-z0-9_]{1,63}$')
);

CREATE TABLE IF NOT EXISTS dict_item (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  type_code     varchar(64) NOT NULL REFERENCES dict_type(type_code) ON DELETE RESTRICT,
  item_code     varchar(64) NOT NULL,
  item_name     varchar(128) NOT NULL,
  sort_no       integer NOT NULL DEFAULT 0,
  ext_json      jsonb NOT NULL DEFAULT '{}'::jsonb,
  enabled       boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    uuid,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  updated_by    uuid,
  deleted_at    timestamptz,
  CONSTRAINT uq_dict_item UNIQUE (type_code, item_code)
);

COMMENT ON COLUMN dict_item.ext_json IS '断面模板等结构化参数放这里，如 ARC/LINE 分段；C-04 未定时为空';

CREATE TABLE IF NOT EXISTS sys_user (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username      varchar(64) NOT NULL,
  password_hash varchar(255) NOT NULL,
  display_name  varchar(64) NOT NULL,
  dept_name     varchar(128),
  phone_masked  varchar(32),
  status        user_status NOT NULL DEFAULT 'ACTIVE',
  last_login_at timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    uuid,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  updated_by    uuid,
  deleted_at    timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sys_user_username ON sys_user (lower(username)) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS sys_role (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  role_code     varchar(64) NOT NULL UNIQUE,
  role_name     varchar(64) NOT NULL,
  is_builtin    boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz
);
COMMENT ON TABLE sys_role IS '内置 5 角色：SYSTEM_ADMIN / MAINT_MANAGER / INSPECTOR / REVIEWER / GUEST';

CREATE TABLE IF NOT EXISTS sys_permission (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  perm_code     varchar(64) NOT NULL UNIQUE,
  module        varchar(32) NOT NULL,
  action        varchar(32) NOT NULL,
  remark        text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
COMMENT ON COLUMN sys_permission.perm_code IS '形如 asset:read、order:transition，与 openapi.yaml 的 security 描述一致';

CREATE TABLE IF NOT EXISTS sys_user_role (
  user_id       uuid NOT NULL REFERENCES sys_user(id) ON DELETE CASCADE,
  role_id       uuid NOT NULL REFERENCES sys_role(id) ON DELETE CASCADE,
  granted_at    timestamptz NOT NULL DEFAULT now(),
  granted_by    uuid,
  PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS sys_role_permission (
  role_id       uuid NOT NULL REFERENCES sys_role(id) ON DELETE CASCADE,
  perm_id       uuid NOT NULL REFERENCES sys_permission(id) ON DELETE CASCADE,
  PRIMARY KEY (role_id, perm_id)
);

CREATE TABLE IF NOT EXISTS sys_role_data_scope (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  role_id       uuid NOT NULL REFERENCES sys_role(id) ON DELETE CASCADE,
  scope_kind    scope_type NOT NULL,
  scope_value   uuid NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_role_scope ON sys_role_data_scope (role_id, scope_kind, scope_value);

-- =============================================================================
-- 3. 主数据
-- =============================================================================
CREATE TABLE IF NOT EXISTS project (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_code    varchar(32) NOT NULL UNIQUE,
  project_name    varchar(128) NOT NULL,
  calc_srid       integer NOT NULL,
  local_origin_x  numeric(14,3),
  local_origin_y  numeric(14,3),
  rotation_deg    numeric(9,6),
  scale_factor    numeric(12,9) DEFAULT 1.0,
  vertical_datum  varchar(64) NOT NULL DEFAULT '1985 国家高程基准',
  data_class      data_class NOT NULL DEFAULT 'INTERNAL',
  created_at      timestamptz NOT NULL DEFAULT now(),
  created_by      uuid,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  updated_by      uuid,
  deleted_at      timestamptz
);
COMMENT ON COLUMN project.calc_srid IS '计算用 SRID：C-05 确认后写入；未确认前按 H-01 由起终点经纬度计算带号';
COMMENT ON COLUMN project.scale_factor IS '工程局部系比例因子，仅 H-09（单带+局部系）路径使用';

CREATE TABLE IF NOT EXISTS bridge (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        uuid NOT NULL REFERENCES project(id) ON DELETE RESTRICT,
  bridge_code       varchar(32) NOT NULL,
  bridge_name       varchar(128) NOT NULL,
  struct_type       bridge_struct_type NOT NULL,
  span_count        integer CHECK (span_count IS NULL OR span_count BETWEEN 1 AND 200),
  start_stake       varchar(32),
  end_stake         varchar(32),
  length_m          numeric(10,2) CHECK (length_m IS NULL OR length_m >= 0),
  width_m           numeric(8,2)  CHECK (width_m IS NULL OR width_m >= 0),
  max_pier_height_m numeric(8,2)  CHECK (max_pier_height_m IS NULL OR max_pier_height_m >= 0),
  pms_code          varchar(64),
  has_pointcloud    varchar(8) NOT NULL DEFAULT 'NO' CHECK (has_pointcloud IN ('YES', 'NO', 'PARTIAL')),
  design_doc_ref    varchar(255),
  ext_json          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  created_by        uuid,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  updated_by        uuid,
  deleted_at        timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_bridge_code ON bridge (project_id, bridge_code) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_bridge_pms ON bridge (pms_code);
COMMENT ON COLUMN bridge.design_doc_ref IS '设计图纸引用；C-04 未定时为空，判定能力按 H-10 情形 II';

CREATE TABLE IF NOT EXISTS tunnel (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        uuid NOT NULL REFERENCES project(id) ON DELETE RESTRICT,
  tunnel_code       varchar(32) NOT NULL,
  tunnel_name       varchar(128) NOT NULL,
  section_type      tunnel_section_type NOT NULL,
  length_m          numeric(10,2) CHECK (length_m IS NULL OR length_m >= 0),
  start_stake       varchar(32),
  end_stake         varchar(32),
  max_span_m        numeric(8,2),
  max_height_m      numeric(8,2),
  lining_type       varchar(64),
  pms_code          varchar(64),
  has_pointcloud    varchar(8) NOT NULL DEFAULT 'NO' CHECK (has_pointcloud IN ('YES', 'NO', 'PARTIAL')),
  section_template  jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  created_by        uuid,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  updated_by        uuid,
  deleted_at        timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tunnel_code ON tunnel (project_id, tunnel_code) WHERE deleted_at IS NULL;
COMMENT ON COLUMN tunnel.section_template IS '设计断面参数化模板（ARC/LINE 分段）；无资料时为空，侵限降级为相对比较';

CREATE TABLE IF NOT EXISTS component (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        uuid NOT NULL REFERENCES project(id) ON DELETE RESTRICT,
  struct_kind       struct_kind NOT NULL,
  bridge_id         uuid REFERENCES bridge(id) ON DELETE CASCADE,
  tunnel_id         uuid REFERENCES tunnel(id) ON DELETE CASCADE,
  parent_id         uuid REFERENCES component(id) ON DELETE RESTRICT,
  component_code    varchar(64) NOT NULL,
  component_name    varchar(128) NOT NULL,
  level             smallint NOT NULL CHECK (level BETWEEN 1 AND 4),
  component_type    varchar(32) NOT NULL,
  pier_type         pier_type,
  design_axis_ref   varchar(255),
  geom              geometry(PointZ, 4547),
  ext_json          jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at        timestamptz NOT NULL DEFAULT now(),
  created_by        uuid,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  updated_by        uuid,
  deleted_at        timestamptz,
  CONSTRAINT ck_component_owner CHECK (
    (struct_kind = 'BRIDGE' AND bridge_id IS NOT NULL AND tunnel_id IS NULL) OR
    (struct_kind = 'TUNNEL' AND tunnel_id IS NOT NULL AND bridge_id IS NULL)
  )
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_component_code ON component (project_id, component_code) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_component_parent ON component (parent_id);
CREATE INDEX IF NOT EXISTS ix_component_bridge ON component (bridge_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_component_tunnel ON component (tunnel_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_component_geom ON component USING GIST (geom);
COMMENT ON COLUMN component.component_code IS '四级编码：结构码-跨号-构件类型-部位-序号，如 B001-K03-PIR-P01-001';
COMMENT ON COLUMN component.geom IS 'SRID 4547 为占位示例，C-05 确认后由迁移脚本替换；禁止 4490';

-- =============================================================================
-- 4. 采集数据
-- =============================================================================
CREATE TABLE IF NOT EXISTS pointcloud_file (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id              uuid NOT NULL REFERENCES project(id) ON DELETE RESTRICT,
  component_code          varchar(64) NOT NULL,
  bridge_id               uuid REFERENCES bridge(id) ON DELETE SET NULL,
  tunnel_id               uuid REFERENCES tunnel(id) ON DELETE SET NULL,
  object_key              varchar(512) NOT NULL,
  file_format             varchar(16) NOT NULL DEFAULT 'COPC' CHECK (file_format IN ('COPC', 'LAZ', 'LAS')),
  srid                    integer NOT NULL,
  point_count             bigint,
  point_density_per_sqm   numeric(10,2) CHECK (point_density_per_sqm IS NULL OR point_density_per_sqm >= 0),
  registration_rms_mm     numeric(8,2),
  control_residual_mm     numeric(8,2),
  extent                  geometry(PolygonZ, 4547),
  status                  pointcloud_status NOT NULL DEFAULT 'UPLOADING',
  tileset_key             varchar(512),
  captured_at             timestamptz,
  data_class              data_class NOT NULL DEFAULT 'INTERNAL',
  created_at              timestamptz NOT NULL DEFAULT now(),
  created_by              uuid,
  updated_at              timestamptz NOT NULL DEFAULT now(),
  updated_by              uuid,
  deleted_at              timestamptz,
  CONSTRAINT ck_pc_rms CHECK (registration_rms_mm IS NULL OR registration_rms_mm <= 10),
  CONSTRAINT ck_pc_residual CHECK (control_residual_mm IS NULL OR control_residual_mm <= 10)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pc_object_key ON pointcloud_file (object_key);
CREATE INDEX IF NOT EXISTS ix_pc_component ON pointcloud_file (component_code, status);
CREATE INDEX IF NOT EXISTS ix_pc_extent ON pointcloud_file USING GIST (extent);
COMMENT ON CONSTRAINT ck_pc_rms ON pointcloud_file IS '阈值 10 mm 来自 2.3.2；C-06 实测后可能调整，调整须走迁移';

CREATE TABLE IF NOT EXISTS image_file (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    uuid NOT NULL REFERENCES project(id) ON DELETE RESTRICT,
  component_code varchar(64),
  object_key    varchar(512) NOT NULL,
  thumb_key     varchar(512),
  content_type  varchar(64) NOT NULL,
  size_bytes    bigint NOT NULL CHECK (size_bytes >= 0),
  width_px      integer,
  height_px     integer,
  gsd_mm_px     numeric(8,4),
  pose          jsonb,
  pose_source   image_pose_source,
  pose_status   image_pose_status NOT NULL DEFAULT 'MISSING',
  captured_at   timestamptz,
  exif_json     jsonb NOT NULL DEFAULT '{}'::jsonb,
  data_class    data_class NOT NULL DEFAULT 'INTERNAL',
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    uuid,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_image_object_key ON image_file (object_key);
CREATE INDEX IF NOT EXISTS ix_image_component ON image_file (component_code) WHERE deleted_at IS NULL;
COMMENT ON COLUMN image_file.gsd_mm_px IS '无拍摄距离时置空，禁止估算填充';

CREATE TABLE IF NOT EXISTS control_point (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        uuid NOT NULL REFERENCES project(id) ON DELETE RESTRICT,
  point_code        varchar(32) NOT NULL,
  struct_code       varchar(32),
  point_kind        varchar(16) NOT NULL CHECK (point_kind IN ('CONTROL', 'CHECK', 'TARGET')),
  srid              integer NOT NULL,
  geom              geometry(PointZ, 4547) NOT NULL,
  elevation_datum   varchar(64) NOT NULL DEFAULT '1985 国家高程基准',
  plane_acc_mm      numeric(8,2),
  height_acc_mm     numeric(8,2),
  survey_method     varchar(32),
  control_grade     varchar(16),
  surveyed_at       date,
  re_surveyed_at    date,
  is_l2_reference   boolean NOT NULL DEFAULT false,
  remark            text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  created_by        uuid,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cp_code ON control_point (project_id, point_code) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_cp_geom ON control_point USING GIST (geom);
COMMENT ON COLUMN control_point.plane_acc_mm IS '待实测；C-06 未定时为空，不得填默认值';

-- =============================================================================
-- 5. 业务数据
-- =============================================================================
CREATE TABLE IF NOT EXISTS defect_candidate (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id     uuid NOT NULL REFERENCES project(id) ON DELETE RESTRICT,
  component_code varchar(64) NOT NULL,
  image_id       uuid REFERENCES image_file(id) ON DELETE SET NULL,
  pointcloud_id  uuid REFERENCES pointcloud_file(id) ON DELETE SET NULL,
  defect_type    defect_type NOT NULL,
  bbox_x1        integer, bbox_y1 integer, bbox_x2 integer, bbox_y2 integer,
  confidence     numeric(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  model_version  varchar(64) NOT NULL,
  status         candidate_status NOT NULL DEFAULT 'PENDING',
  dedup_key      varchar(128),
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_cand_bbox CHECK (
    (bbox_x1 IS NULL AND bbox_y1 IS NULL AND bbox_x2 IS NULL AND bbox_y2 IS NULL) OR
    (bbox_x1 >= 0 AND bbox_y1 >= 0 AND bbox_x2 > bbox_x1 AND bbox_y2 > bbox_y1)
  )
);
CREATE INDEX IF NOT EXISTS ix_cand_status ON defect_candidate (status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_cand_component ON defect_candidate (component_code);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cand_dedup ON defect_candidate (dedup_key) WHERE dedup_key IS NOT NULL;
COMMENT ON TABLE defect_candidate IS 'AI 只写此表；defect_ledger 只能由人工确认动作写入';

CREATE TABLE IF NOT EXISTS defect_ledger (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id          uuid NOT NULL REFERENCES project(id) ON DELETE RESTRICT,
  defect_code         varchar(32) NOT NULL,
  component_code      varchar(64) NOT NULL,
  defect_type         defect_type NOT NULL,
  level               defect_level NOT NULL,
  status              ledger_status NOT NULL DEFAULT 'CONFIRMED',
  source_candidate_id uuid REFERENCES defect_candidate(id) ON DELETE SET NULL,
  measured_value      numeric(12,3),
  measured_unit       varchar(16),
  position_note       text,
  confirmed_by        uuid REFERENCES sys_user(id) ON DELETE SET NULL,
  confirmed_at        timestamptz,
  closed_at           timestamptz,
  data_class          data_class NOT NULL DEFAULT 'INTERNAL',
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  deleted_at          timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_defect_code ON defect_ledger (project_id, defect_code) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ledger_from_candidate ON defect_ledger (source_candidate_id) WHERE source_candidate_id IS NOT NULL;
COMMENT ON INDEX uq_ledger_from_candidate IS '同一候选只能生成一条台账，保证确认动作幂等';

CREATE TABLE IF NOT EXISTS work_order (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id        uuid NOT NULL REFERENCES project(id) ON DELETE RESTRICT,
  order_no          varchar(32) NOT NULL,
  defect_id         uuid NOT NULL REFERENCES defect_ledger(id) ON DELETE RESTRICT,
  component_code    varchar(64) NOT NULL,
  status            workorder_status NOT NULL DEFAULT 'DRAFT',
  is_overdue        boolean NOT NULL DEFAULT false,
  version           integer NOT NULL DEFAULT 0,
  handler_id        uuid REFERENCES sys_user(id) ON DELETE SET NULL,
  reporter_id       uuid REFERENCES sys_user(id) ON DELETE SET NULL,
  due_date          date,
  sla_deadline      timestamptz,
  sla_paused_total  integer NOT NULL DEFAULT 0,
  suspend_reason    text,
  merge_into_id     uuid REFERENCES work_order(id) ON DELETE SET NULL,
  cross_link_seam   boolean NOT NULL DEFAULT false,
  remark            text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  created_by        uuid,
  updated_at        timestamptz NOT NULL DEFAULT now(),
  updated_by        uuid,
  closed_at         timestamptz,
  deleted_at        timestamptz,
  CONSTRAINT ck_wo_merge CHECK (
    (status = 'MERGED' AND merge_into_id IS NOT NULL) OR (status <> 'MERGED')
  )
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_wo_no ON work_order (project_id, order_no) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_wo_active_defect ON work_order (defect_id)
  WHERE deleted_at IS NULL AND status NOT IN ('CLOSED', 'CANCELLED', 'MERGED', 'REJECTED');
COMMENT ON INDEX uq_wo_active_defect IS '同一缺陷只允许一条未结工单，重复建单由该索引拦截（BT-70002）';
CREATE INDEX IF NOT EXISTS ix_wo_status ON work_order (status, sla_deadline);
CREATE INDEX IF NOT EXISTS ix_wo_handler ON work_order (handler_id, status);
COMMENT ON COLUMN work_order.sla_deadline IS 'H-06：按自然日 24 小时计时；改工作日口径须迁移+配置同步调整';
COMMENT ON COLUMN work_order.is_overdue IS '叠加标记，不等于 status=OVERDUE；暂停期间不计时';

CREATE TABLE IF NOT EXISTS work_order_event (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id      uuid NOT NULL REFERENCES work_order(id) ON DELETE CASCADE,
  event         varchar(32) NOT NULL,
  from_status   workorder_status NOT NULL,
  to_status     workorder_status NOT NULL,
  operator_id   uuid REFERENCES sys_user(id) ON DELETE SET NULL,
  reason        text,
  payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
  occurred_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_woe_order ON work_order_event (order_id, occurred_at);
COMMENT ON TABLE work_order_event IS '只追加；事件名必须存在于 state/work_order_state_machine.yaml';

CREATE TABLE IF NOT EXISTS attachment (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    uuid NOT NULL REFERENCES project(id) ON DELETE RESTRICT,
  object_type   link_object_type NOT NULL,
  object_id     uuid NOT NULL,
  file_name     varchar(255) NOT NULL,
  content_type  varchar(64) NOT NULL,
  size_bytes    bigint NOT NULL CHECK (size_bytes >= 0),
  sha256        char(64),
  object_key    varchar(512) NOT NULL,
  data_class    data_class NOT NULL DEFAULT 'INTERNAL',
  uploaded_by   uuid REFERENCES sys_user(id) ON DELETE SET NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_attachment_key ON attachment (object_key);
CREATE INDEX IF NOT EXISTS ix_attachment_object ON attachment (object_type, object_id) WHERE deleted_at IS NULL;
COMMENT ON COLUMN attachment.content_type IS '白名单：image/jpeg, image/png, application/pdf, video/mp4；以魔数校验为准';

CREATE TABLE IF NOT EXISTS file_object_link (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  file_id       uuid NOT NULL,
  object_type   link_object_type NOT NULL,
  object_id     uuid NOT NULL,
  relation      varchar(16) NOT NULL DEFAULT 'PRIMARY' CHECK (relation IN ('PRIMARY', 'ATTACHMENT', 'DERIVED')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    uuid
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fol ON file_object_link (file_id, object_type, object_id, relation);
CREATE INDEX IF NOT EXISTS ix_fol_object ON file_object_link (object_type, object_id);

CREATE TABLE IF NOT EXISTS async_task (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id    uuid REFERENCES project(id) ON DELETE SET NULL,
  biz_type      task_biz_type NOT NULL,
  status        task_status NOT NULL DEFAULT 'QUEUED',
  idem_key      varchar(128) NOT NULL,
  payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
  result_ref    varchar(512),
  progress      smallint NOT NULL DEFAULT 0
                CONSTRAINT ck_task_progress CHECK (progress BETWEEN 0 AND 100),
  attempt       smallint NOT NULL DEFAULT 0,
  max_attempt   smallint NOT NULL DEFAULT 3,
  timeout_sec   integer NOT NULL DEFAULT 1800,
  error_code    varchar(16),
  error_detail  text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  started_at    timestamptz,
  finished_at   timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_task_idem ON async_task (idem_key);
CREATE INDEX IF NOT EXISTS ix_task_status ON async_task (status, created_at);
COMMENT ON CONSTRAINT ck_task_progress ON async_task IS '进度仅用于展示，不作为状态判据';
COMMENT ON COLUMN async_task.idem_key IS '同一幂等键只允许一个任务，重复提交返回既有任务（202）';

CREATE TABLE IF NOT EXISTS notification (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES sys_user(id) ON DELETE CASCADE,
  order_id      uuid REFERENCES work_order(id) ON DELETE CASCADE,
  title         varchar(128) NOT NULL,
  body          text,
  level         notify_level NOT NULL DEFAULT 'P3',
  dedup_key     varchar(128) NOT NULL,
  channel       varchar(32) NOT NULL DEFAULT 'INAPP',
  status        varchar(16) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SENT', 'FAILED', 'SUPPRESSED')),
  retry_count   smallint NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now(),
  sent_at       timestamptz,
  read_at       timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_notify_dedup ON notification (dedup_key, user_id);
CREATE INDEX IF NOT EXISTS ix_notify_user ON notification (user_id, read_at);

CREATE TABLE IF NOT EXISTS sync_log (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  direction     varchar(16) NOT NULL CHECK (direction IN ('OUTBOUND', 'INBOUND')),
  entity        varchar(32) NOT NULL,
  object_id     uuid NOT NULL,
  op            varchar(16) NOT NULL CHECK (op IN ('UPSERT', 'DELETE')),
  payload       jsonb,
  status        varchar(16) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SUCCESS', 'FAILED')),
  error_detail  text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  synced_at     timestamptz
);
CREATE INDEX IF NOT EXISTS ix_sync_status ON sync_log (status, created_at);
COMMENT ON TABLE sync_log IS 'PMS 对接（C-17）未确认前仅记录，不做真实外发';

-- =============================================================================
-- 6. 几何与配置
-- =============================================================================
CREATE TABLE IF NOT EXISTS geometry_baseline (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id      uuid NOT NULL REFERENCES project(id) ON DELETE RESTRICT,
  component_code  varchar(64) NOT NULL,
  baseline_type   baseline_type NOT NULL DEFAULT 'FIRST_OBSERVE',
  source_ref      varchar(255),
  srid            integer NOT NULL,
  axis            geometry(LineStringZ, 4547),
  section_template jsonb,
  is_locked       boolean NOT NULL DEFAULT false,
  locked_at       timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  created_by      uuid,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  deleted_at      timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_baseline_active ON geometry_baseline (component_code, baseline_type)
  WHERE deleted_at IS NULL AND is_locked = true;
CREATE INDEX IF NOT EXISTS ix_baseline_axis ON geometry_baseline USING GIST (axis);
COMMENT ON COLUMN geometry_baseline.baseline_type IS 'H-10：无竣工测量时取 FIRST_OBSERVE，累计位移自首测起算';

CREATE TABLE IF NOT EXISTS geometry_result (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id          uuid NOT NULL REFERENCES project(id) ON DELETE RESTRICT,
  task_id             uuid NOT NULL REFERENCES async_task(id) ON DELETE CASCADE,
  component_code      varchar(64) NOT NULL,
  pointcloud_id       uuid REFERENCES pointcloud_file(id) ON DELETE SET NULL,
  baseline_id         uuid REFERENCES geometry_baseline(id) ON DELETE SET NULL,
  analysis_type       analysis_type NOT NULL,
  value               numeric(12,3),
  unit                varchar(16) NOT NULL DEFAULT 'mm' CHECK (unit IN ('mm', 'permille', 'sqm')),
  direction_deg       numeric(6,2),
  intrusion_area_sqm  numeric(12,4),
  worst_section_stake varchar(32),
  valid_layers        integer,
  excluded_layers     integer[],
  srid                integer NOT NULL,
  algorithm_version   varchar(64) NOT NULL,
  params_hash         char(64) NOT NULL,
  detail_json         jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_valid            boolean NOT NULL DEFAULT true,
  invalid_reason      text,
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_geo_component ON geometry_result (component_code, analysis_type, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_geo_task ON geometry_result (task_id);
COMMENT ON COLUMN geometry_result.params_hash IS '参数哈希用于复现；同一组参数重算应得到相同 hash';
COMMENT ON COLUMN geometry_result.is_valid IS 'ICP 超限、有效层不足时置 false 并写 invalid_reason';

CREATE TABLE IF NOT EXISTS model_registry (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  model_key     varchar(64) NOT NULL,
  model_version varchar(64) NOT NULL,
  task_type     varchar(32) NOT NULL,
  object_key    varchar(512),
  metrics_json  jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_active     boolean NOT NULL DEFAULT false,
  released_at   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    uuid
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_model_version ON model_registry (model_key, model_version);
CREATE UNIQUE INDEX IF NOT EXISTS uq_model_active ON model_registry (task_type) WHERE is_active = true;
COMMENT ON TABLE model_registry IS '只登记，不存权重；权重文件在内网对象存储，禁止上公有云（AGENTS.md 第 6 节）';

CREATE TABLE IF NOT EXISTS threshold_config (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  config_key    varchar(64) NOT NULL UNIQUE,
  config_value  jsonb NOT NULL,
  unit          varchar(16),
  source_note   varchar(255),
  pending_confirm boolean NOT NULL DEFAULT true,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  updated_by    uuid
);
COMMENT ON COLUMN threshold_config.pending_confirm IS 'TRUE 表示沿用假设条款值（H-xx），业主确认后置 FALSE 并留审计';

-- =============================================================================
-- 7. 审计
-- =============================================================================
CREATE TABLE IF NOT EXISTS audit_log (
  id            bigserial PRIMARY KEY,
  trace_id      varchar(64) NOT NULL,
  user_id       uuid,
  module        varchar(32) NOT NULL,
  action        varchar(64) NOT NULL,
  object_type   varchar(32),
  object_id     varchar(64),
  result        varchar(16) NOT NULL CHECK (result IN ('SUCCESS', 'FAIL', 'DENY')),
  error_code    varchar(16),
  client_ip     inet,
  user_agent    varchar(255),
  detail        jsonb NOT NULL DEFAULT '{}'::jsonb,
  prev_hash     char(64),
  hash          char(64) NOT NULL,
  occurred_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_audit_time ON audit_log (occurred_at);
CREATE INDEX IF NOT EXISTS ix_audit_user ON audit_log (user_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_audit_object ON audit_log (object_type, object_id);
CREATE INDEX IF NOT EXISTS ix_audit_trace ON audit_log (trace_id);
COMMENT ON TABLE audit_log IS '只追加。prev_hash+hash 组成链，任何 UPDATE/DELETE 由触发器拒绝；留存 ≥3 年（H-05）';

CREATE OR REPLACE FUNCTION fn_audit_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only (attempted %)', TG_OP
    USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_no_update ON audit_log;
CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION fn_audit_append_only();
DROP TRIGGER IF EXISTS trg_audit_no_delete ON audit_log;
CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION fn_audit_append_only();
DROP TRIGGER IF EXISTS trg_audit_no_truncate ON audit_log;
CREATE TRIGGER trg_audit_no_truncate BEFORE TRUNCATE ON audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION fn_audit_append_only();

-- updated_at 自动维护
CREATE OR REPLACE FUNCTION fn_touch_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'dict_type','dict_item','sys_user','sys_role','project','bridge','tunnel','component',
    'pointcloud_file','image_file','control_point','defect_candidate','defect_ledger',
    'work_order','threshold_config','geometry_baseline'
  ] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS trg_touch_%s ON %I', t, t);
    EXECUTE format(
      'CREATE TRIGGER trg_touch_%s BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at()',
      t, t);
  END LOOP;
END;
$$;

-- =============================================================================
-- 8. 初始数据（仅字典与角色，不含任何业主业务数据）
-- =============================================================================
INSERT INTO dict_type (type_code, type_name) VALUES
  ('pier_type', '墩型'),
  ('component_type', '构件类型'),
  ('defect_type', '缺陷类型'),
  ('section_type', '隧道断面形式'),
  ('bridge_struct_type', '桥梁结构类型'),
  ('suspend_reason', '挂起原因'),
  ('reject_reason', '驳回原因')
ON CONFLICT DO NOTHING;

INSERT INTO dict_item (type_code, item_code, item_name, sort_no) VALUES
  ('pier_type', 'RECT', '矩形墩', 1),
  ('pier_type', 'CIRCLE', '圆形墩', 2),
  ('pier_type', 'ROUND_END', '圆端形墩', 3),
  ('pier_type', 'OTHER', '其他', 4),
  ('defect_type', 'CRACK', '裂缝', 1),
  ('defect_type', 'SPALLING', '剥落', 2),
  ('defect_type', 'SEEPAGE', '渗漏水', 3),
  ('defect_type', 'REBAR_EXPOSURE', '钢筋外露', 4),
  ('defect_type', 'TILT', '倾斜/变形', 5),
  ('defect_type', 'OTHER', '其他', 6),
  ('suspend_reason', 'WEATHER', '天气不具备作业条件', 1),
  ('suspend_reason', 'TRAFFIC', '交通管制未批复', 2),
  ('suspend_reason', 'DATA', '数据不足待补采', 3),
  ('reject_reason', 'FALSE_POSITIVE', 'AI 误报', 1),
  ('reject_reason', 'DUPLICATE', '重复上报', 2),
  ('reject_reason', 'POSITION_OFFSET', '位置偏移', 3)
ON CONFLICT DO NOTHING;

INSERT INTO sys_role (role_code, role_name, is_builtin) VALUES
  ('SYSTEM_ADMIN', '系统管理员', true),
  ('MAINT_MANAGER', '养护主管', true),
  ('INSPECTOR', '巡检员', true),
  ('REVIEWER', '复核人', true),
  ('GUEST', '只读访客', true)
ON CONFLICT DO NOTHING;

INSERT INTO sys_permission (perm_code, module, action) VALUES
  ('asset:read', 'asset', 'read'),
  ('asset:write', 'asset', 'write'),
  ('media:write', 'media', 'write'),
  ('ai:invoke', 'inference', 'invoke'),
  ('defect:read', 'defect', 'read'),
  ('defect:confirm', 'defect', 'confirm'),
  ('geometry:invoke', 'geometry', 'invoke'),
  ('order:create', 'workorder', 'create'),
  ('order:transition', 'workorder', 'transition'),
  ('order:accept', 'workorder', 'accept'),
  ('file:write', 'file', 'write'),
  ('sync:read', 'sync', 'read'),
  ('audit:read', 'audit', 'read'),
  ('admin:config', 'admin', 'config')
ON CONFLICT DO NOTHING;

INSERT INTO threshold_config (config_key, config_value, unit, source_note, pending_confirm) VALUES
  ('api.rate_limit.per_user_read', '20', '次/秒', 'H-08', true),
  ('api.rate_limit.per_user_write', '5',  '次/秒', 'H-08', true),
  ('api.rate_limit.per_ip',        '100','次/秒', 'H-08', true),
  ('api.page_limit.default',       '20', '条',   'H-08', true),
  ('api.page_limit.max',           '100','条',   'H-08', true),
  ('pointcloud.min_density',       '100','点/㎡','待实测（C-01/C-06）', true),
  ('pointcloud.registration_rms',  '10', 'mm',   '2.3.2；C-06 实测后确认', true),
  ('tile.max_points',              '500000', '点/片', 'H-05', true),
  ('tile.sse',                     '16', '',     'H-05', true),
  ('render.point_budget',          '1500000', '点/帧', 'H-04', true),
  ('verticality.allow_mm',         '5',  'mm',   'H-13；C-03 待确认', true),
  ('clearance.allow_mm',           '10', 'mm',   'H-13；C-03 待确认', true),
  ('sla.natural_day_hours',        '24', 'h',    'H-06；C-12 待确认', true)
ON CONFLICT DO NOTHING;
