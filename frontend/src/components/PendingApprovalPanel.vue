<script setup lang="ts">
/**
 * 主管审批入口（『待我审批 (N)』折叠面板）。
 *
 * 数据源：GET /workorders?status=PENDING_CONFIRM&mine=false&limit=20
 * 流转：POST /workorders/{id}/transitions，事件 APPROVE / REJECT。
 *
 * 权限口径以 state/work_order_state_machine.yaml 为准：
 * APPROVE / REJECT 仅 MAINT_MANAGER（SYSTEM_ADMIN 为运维旁路），且审核人不得等于上报人。
 * 前端据此禁用按钮，后端守卫仍是最终兜底。
 */
import { computed, onMounted, ref, watch } from "vue";
import {
  fetchCurrentUser,
  fetchPendingOrders,
  fetchRejectReasons,
  transitionWorkOrder,
  WorkOrderApiError,
  type CurrentUserInfo,
  type DefectLevel,
  type DictEntry,
  type WorkOrder,
} from "../api/workorder";

const props = defineProps<{
  /** 派单成功后由 App.vue 递增，触发列表刷新。 */
  refreshSignal?: number;
}>();

const LEVEL_LABELS: Record<DefectLevel, string> = {
  L1: "L1 严重",
  L2: "L2 较重",
  L3: "L3 一般",
};

const expanded = ref(false);
const loading = ref(false);
const items = ref<WorkOrder[]>([]);
const hasMore = ref(false);
const currentUser = ref<CurrentUserInfo | null>(null);
const rejectReasons = ref<DictEntry[]>([]);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);
const busyId = ref<string | null>(null);

const rejectTarget = ref<WorkOrder | null>(null);
const rejectCode = ref<string>("");
const rejectReason = ref<string>("");
const rejectError = ref<string | null>(null);

const countText = computed(() => `${items.value.length}${hasMore.value ? "+" : ""}`);

const canApprove = computed(() => {
  const roles = currentUser.value?.roles ?? [];
  return roles.includes("MAINT_MANAGER") || roles.includes("SYSTEM_ADMIN");
});

function approvalBlockReason(order: WorkOrder): string | null {
  if (!currentUser.value) return "未获取到当前用户，暂时无法审批";
  if (!canApprove.value) return "当前账号无审批权限（需 MAINT_MANAGER）";
  if (
    currentUser.value.user_id &&
    order.reporter_id &&
    currentUser.value.user_id === order.reporter_id
  ) {
    return "不能审批本人上报的工单";
  }
  return null;
}

function describe(err: unknown): string {
  if (err instanceof WorkOrderApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

function levelText(level: DefectLevel | null | undefined): string {
  return level ? LEVEL_LABELS[level] : "—";
}

function tiltText(order: WorkOrder): string {
  const snapshot = order.source_snapshot ?? {};
  const tilt = snapshot["tilt_permille"];
  if (typeof tilt === "number") return `${tilt.toFixed(3)} ‰`;
  return "—";
}

function timeText(value: string): string {
  return value ? value.replace("T", " ").slice(0, 16) : "—";
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    const list = await fetchPendingOrders(20);
    items.value = list.items;
    hasMore.value = Boolean(list.next_cursor);
    if (!currentUser.value) currentUser.value = await fetchCurrentUser();
  } catch (err) {
    error.value = describe(err);
  } finally {
    loading.value = false;
  }
}

async function refresh(): Promise<void> {
  notice.value = null;
  await load();
}

function toggle(): void {
  expanded.value = !expanded.value;
  if (expanded.value) void refresh();
}

async function ensureRejectReasons(): Promise<void> {
  if (rejectReasons.value.length > 0) return;
  try {
    rejectReasons.value = await fetchRejectReasons();
  } catch (err) {
    rejectError.value = describe(err);
  }
}

async function approve(order: WorkOrder): Promise<void> {
  const blocked = approvalBlockReason(order);
  if (blocked) {
    notice.value = blocked;
    return;
  }
  busyId.value = order.id;
  error.value = null;
  notice.value = null;
  try {
    const updated = await transitionWorkOrder(order.id, {
      event: "APPROVE",
      expectedVersion: order.version,
    });
    notice.value = `${order.order_no} 已通过，状态推进为 ${updated.status}`;
    await load();
  } catch (err) {
    error.value = describe(err);
    // 409 多为 version 过期（别人已处理）或守卫拦截；刷新让界面回到真实状态。
    if (err instanceof WorkOrderApiError && err.status === 409) await load();
  } finally {
    busyId.value = null;
  }
}

async function openReject(order: WorkOrder): Promise<void> {
  const blocked = approvalBlockReason(order);
  if (blocked) {
    notice.value = blocked;
    return;
  }
  rejectTarget.value = order;
  rejectCode.value = "";
  rejectReason.value = "";
  rejectError.value = null;
  await ensureRejectReasons();
}

function cancelReject(): void {
  rejectTarget.value = null;
  rejectError.value = null;
}

async function confirmReject(): Promise<void> {
  const target = rejectTarget.value;
  if (!target) return;
  if (!rejectCode.value) {
    rejectError.value = "请选择驳回原因类型（字典 reject_reason）";
    return;
  }
  if (!rejectReason.value.trim()) {
    rejectError.value = "请填写驳回原因";
    return;
  }
  busyId.value = target.id;
  rejectError.value = null;
  error.value = null;
  notice.value = null;
  try {
    const updated = await transitionWorkOrder(target.id, {
      event: "REJECT",
      expectedVersion: target.version,
      reason: rejectReason.value.trim(),
      rejectReasonCode: rejectCode.value,
    });
    notice.value = `${target.order_no} 已驳回，状态推进为 ${updated.status}`;
    rejectTarget.value = null;
    await load();
  } catch (err) {
    rejectError.value = describe(err);
    if (err instanceof WorkOrderApiError && err.status === 409) await load();
  } finally {
    busyId.value = null;
  }
}

watch(
  () => props.refreshSignal,
  () => {
    if (expanded.value) void refresh();
  },
);

onMounted(() => {
  // 折叠时也要拿到数量，先静默拉一次。
  void load();
});
</script>

<template>
  <section class="approval-panel">
    <button type="button" class="approval-toggle" @click="toggle">
      <span class="approval-title">待我审批</span>
      <span class="approval-count" :class="{ zero: items.length === 0 }">{{ countText }}</span>
      <span class="approval-chevron">{{ expanded ? "▾" : "▸" }}</span>
    </button>

    <div v-if="expanded" class="approval-body">
      <div class="approval-toolbar">
        <span class="approval-note">按状态 PENDING_CONFIRM 拉取（最多 20 条）</span>
        <button type="button" class="approval-refresh" :disabled="loading" @click="refresh">
          {{ loading ? "刷新中…" : "刷新" }}
        </button>
      </div>

      <div v-if="loading && items.length === 0" class="approval-empty">加载待审批列表…</div>
      <div v-else-if="items.length === 0" class="approval-empty">暂无待确认工单</div>

      <ul v-else class="approval-list">
        <li v-for="order in items" :key="order.id" class="approval-item">
          <div class="approval-item-head">
            <code>{{ order.order_no }}</code>
            <span class="approval-priority">{{ order.priority }}</span>
          </div>
          <div class="approval-grid">
            <span>构件</span>
            <strong>{{ order.component_name || order.component_code || "—" }}</strong>
            <span>建议等级</span>
            <strong>{{ levelText(order.suggested_level) }}</strong>
            <span>倾斜率快照</span>
            <strong>{{ tiltText(order) }}</strong>
            <span>班组</span>
            <strong>{{ order.team_name || "—" }}</strong>
            <span>创建时间</span>
            <strong>{{ timeText(order.created_at) }}</strong>
          </div>

          <div v-if="approvalBlockReason(order)" class="approval-blocked">
            {{ approvalBlockReason(order) }}
          </div>

          <div class="approval-actions">
            <button
              type="button"
              class="approve"
              :disabled="Boolean(approvalBlockReason(order)) || busyId !== null"
              :title="approvalBlockReason(order) ?? '通过并推进为 CONFIRMED'"
              @click="approve(order)"
            >
              {{ busyId === order.id ? "处理中…" : "通过" }}
            </button>
            <button
              type="button"
              class="reject"
              :disabled="Boolean(approvalBlockReason(order)) || busyId !== null"
              :title="approvalBlockReason(order) ?? '驳回并推进为 REJECTED'"
              @click="openReject(order)"
            >
              驳回
            </button>
          </div>

          <div v-if="rejectTarget && rejectTarget.id === order.id" class="reject-form">
            <select v-model="rejectCode">
              <option value="" disabled>请选择驳回原因类型</option>
              <option v-for="reason in rejectReasons" :key="reason.code" :value="reason.code">
                {{ reason.name }}（{{ reason.code }}）
              </option>
            </select>
            <input v-model="rejectReason" type="text" maxlength="200" placeholder="驳回原因（必填，200 字以内）" />
            <div v-if="rejectReasons.length === 0" class="reject-hint">
              驳回原因字典为空，请先在后台维护 dict_item（type_code=reject_reason）。
            </div>
            <div v-if="rejectError" class="approval-error">{{ rejectError }}</div>
            <div class="reject-actions">
              <button
                type="button"
                class="approve"
                :disabled="busyId !== null"
                @click="confirmReject"
              >
                {{ busyId === order.id ? "提交中…" : "确认驳回" }}
              </button>
              <button type="button" class="ghost" :disabled="busyId !== null" @click="cancelReject">
                取消
              </button>
            </div>
          </div>
        </li>
      </ul>

      <div v-if="hasMore" class="approval-hint">仅展示前 20 条，处理后将继续加载下一批。</div>
      <div v-if="error" class="approval-error">{{ error }}</div>
      <div v-if="notice" class="approval-notice">{{ notice }}</div>
    </div>
  </section>
</template>

<style scoped>
.approval-panel {
  margin-bottom: 12px;
  border-radius: 6px;
  border: 1px solid rgba(120, 170, 255, 0.3);
  background: rgba(10, 24, 48, 0.55);
  overflow: hidden;
  font-size: 12px;
  color: #cfe2ff;
}

.approval-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  background: rgba(26, 58, 110, 0.55);
  border: none;
  color: #e8f1ff;
  cursor: pointer;
  font-size: 13px;
}

.approval-toggle:hover {
  background: rgba(36, 80, 150, 0.7);
}

.approval-title {
  font-weight: 600;
}

.approval-count {
  min-width: 22px;
  padding: 1px 7px;
  border-radius: 10px;
  background: #ff9d4d;
  color: #1b1200;
  font-weight: 700;
  text-align: center;
}

.approval-count.zero {
  background: rgba(120, 170, 255, 0.25);
  color: #9fb4d0;
}

.approval-chevron {
  margin-left: auto;
  color: #9fb4d0;
}

.approval-body {
  padding: 8px 10px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.approval-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.approval-note {
  font-size: 11px;
  color: #88a4cf;
}

.approval-refresh {
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid rgba(120, 170, 255, 0.4);
  background: transparent;
  color: #9fb4d0;
  font-size: 11px;
  cursor: pointer;
}

.approval-refresh:hover:not(:disabled) {
  color: #e8f1ff;
  background: rgba(26, 58, 110, 0.6);
}

.approval-empty,
.approval-hint {
  font-size: 11px;
  color: #88a4cf;
}

.approval-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 340px;
  overflow-y: auto;
}

.approval-item {
  padding: 8px;
  border-radius: 6px;
  background: rgba(16, 36, 72, 0.7);
  border: 1px solid rgba(80, 130, 220, 0.25);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.approval-item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.approval-item-head code {
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  color: #e8f1ff;
}

.approval-priority {
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(255, 157, 77, 0.2);
  color: #ffc48a;
  font-size: 11px;
}

.approval-grid {
  display: grid;
  grid-template-columns: 74px 1fr;
  row-gap: 3px;
  column-gap: 6px;
  font-size: 11px;
  color: #9fb4d0;
}

.approval-grid strong {
  color: #cfe2ff;
  font-weight: 500;
}

.approval-blocked {
  font-size: 11px;
  color: #ff9b9b;
}

.approval-actions,
.reject-actions {
  display: flex;
  gap: 8px;
}

.approval-actions button,
.reject-actions button {
  flex: 1 1 0;
  padding: 4px 8px;
  border-radius: 5px;
  font-size: 12px;
  cursor: pointer;
}

.approve {
  border: 1px solid rgba(120, 220, 160, 0.5);
  background: rgba(40, 160, 100, 0.35);
  color: #c8ffdd;
}

.approve:hover:not(:disabled) {
  background: rgba(40, 160, 100, 0.55);
}

.reject {
  border: 1px solid rgba(255, 140, 140, 0.5);
  background: rgba(180, 60, 60, 0.3);
  color: #ffc9c9;
}

.reject:hover:not(:disabled) {
  background: rgba(180, 60, 60, 0.5);
}

.approval-actions button:disabled,
.reject-actions button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.ghost {
  border: 1px solid rgba(120, 170, 255, 0.35);
  background: transparent;
  color: #9fb4d0;
}

.reject-form {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 6px;
  border-top: 1px dashed rgba(120, 170, 255, 0.25);
}

.reject-form select,
.reject-form input {
  padding: 4px 6px;
  border-radius: 4px;
  border: 1px solid rgba(120, 170, 255, 0.4);
  background: rgba(16, 36, 72, 0.9);
  color: #cfe2ff;
  font-size: 11px;
  outline: none;
}

.reject-hint {
  font-size: 11px;
  color: #ffd28a;
}

.approval-error {
  padding: 4px 6px;
  border-radius: 4px;
  background: rgba(200, 80, 80, 0.18);
  color: #ff9b9b;
  font-size: 11px;
  word-break: break-all;
}

.approval-notice {
  padding: 4px 6px;
  border-radius: 4px;
  background: rgba(40, 160, 100, 0.16);
  color: #7fe0a5;
  font-size: 11px;
}
</style>
