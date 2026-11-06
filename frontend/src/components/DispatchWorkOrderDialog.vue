<script setup lang="ts">
/**
 * 一键派发维修工单弹窗。
 *
 * 业务口径（业主决策）：AI 结果不能直接派单。这里提交后生成的是
 * PENDING_CONFIRM 工单，需要主管在后台点「通过」后才真正下发。
 *
 * 阈值来自 GET /dict/defect-level-thresholds（后端 YAML 字典），前端不硬编码。
 */
import { computed, ref, watch } from "vue";
import {
  dispatchWorkOrder,
  fetchDefectLevelThresholds,
  fetchMaintenanceTeams,
  suggestDefectLevel,
  WorkOrderApiError,
  type DefectLevel,
  type DefectLevelThresholds,
  type MaintenanceTeam,
  type WorkOrderPriority,
} from "../api/workorder";

const props = defineProps<{
  visible: boolean;
  componentCode: string;
  taskId: string | null;
  tiltPermille: number | null;
  algorithmVersion?: string | null;
  degraded?: boolean;
  degradeReason?: string | null;
}>();

const emit = defineEmits<{
  (e: "update:visible", value: boolean): void;
  (e: "dispatched", orderNo: string): void;
}>();

const PRIORITY_OPTIONS: Array<{ value: WorkOrderPriority; label: string }> = [
  { value: "P1", label: "P1 紧急" },
  { value: "P2", label: "P2 一般" },
  { value: "P3", label: "P3 计划" },
];

const LEVEL_LABELS: Record<DefectLevel, string> = {
  L1: "L1 严重",
  L2: "L2 较重",
  L3: "L3 一般",
};

const REFERENCE_NOTE = "参考值，以最终人工复核及设计规范为准";

const teams = ref<MaintenanceTeam[]>([]);
const thresholds = ref<DefectLevelThresholds | null>(null);
const loading = ref(false);
const submitting = ref(false);
const loadError = ref<string | null>(null);
const submitError = ref<string | null>(null);

const teamId = ref<string>("");
const priority = ref<WorkOrderPriority>("P2");
const dueDate = ref<string>("");
const remark = ref<string>("");

/** 提交成功后展示的待确认工单信息（不提示“已下发”）。 */
const result = ref<{ orderNo: string; level: string } | null>(null);

const selectedTeam = computed(
  () => teams.value.find((t) => t.team_id === teamId.value) ?? null,
);

const suggestedLevel = computed<DefectLevel | null>(() => {
  if (!thresholds.value || props.tiltPermille === null) return null;
  return suggestDefectLevel(props.tiltPermille, thresholds.value);
});

const thresholdText = computed(() => {
  const t = thresholds.value?.tilt_permille;
  if (!t) return "";
  return `> ${t.L2} ‰ → L2，> ${t.L1} ‰ → L1，≤ ${t.L2} ‰ → ${t.default_level}`;
});

const noteText = computed(() => thresholds.value?.note ?? REFERENCE_NOTE);

const canSubmit = computed(
  () => props.taskId !== null && teamId.value !== "" && !submitting.value && result.value === null,
);

function close(): void {
  if (submitting.value) return;
  emit("update:visible", false);
}

function resetForm(): void {
  teamId.value = "";
  priority.value = "P2";
  dueDate.value = "";
  remark.value = "";
  submitError.value = null;
  loadError.value = null;
  result.value = null;
}

async function load(): Promise<void> {
  loading.value = true;
  loadError.value = null;
  try {
    const [teamList, levelThresholds] = await Promise.all([
      fetchMaintenanceTeams({ componentCode: props.componentCode, limit: 50 }),
      fetchDefectLevelThresholds(),
    ]);
    teams.value = teamList.items;
    thresholds.value = levelThresholds;
    if (teams.value.length > 0 && teamId.value === "") {
      teamId.value = teams.value[0]?.team_id ?? "";
    }
  } catch (err) {
    loadError.value =
      err instanceof WorkOrderApiError
        ? err.message
        : err instanceof Error
          ? `加载失败：${err.message}`
          : `加载失败：${String(err)}`;
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.visible,
  (visible) => {
    if (!visible) return;
    resetForm();
    void load();
  },
);

async function submit(): Promise<void> {
  if (!canSubmit.value || props.taskId === null) return;
  submitting.value = true;
  submitError.value = null;
  try {
    const created = await dispatchWorkOrder({
      source_task_id: props.taskId,
      component_code: props.componentCode,
      priority: priority.value,
      team_id: teamId.value,
      due_date: dueDate.value ? dueDate.value : null,
      remark: remark.value.trim() ? remark.value.trim() : null,
    });
    result.value = { orderNo: created.order_no, level: created.suggested_level };
    emit("dispatched", created.order_no);
  } catch (err) {
    submitError.value =
      err instanceof WorkOrderApiError
        ? err.message
        : err instanceof Error
          ? `派发失败：${err.message}`
          : `派发失败：${String(err)}`;
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div v-if="visible" class="dispatch-mask" @click.self="close">
    <div class="dispatch-dialog" role="dialog" aria-modal="true">
      <header class="dispatch-header">
        <div class="dispatch-title">一键派发维修工单</div>
        <button type="button" class="dispatch-close" @click="close" aria-label="关闭">×</button>
      </header>

      <div class="dispatch-body">
        <div class="summary">
          <div class="summary-row">
            <span>构件</span>
            <code>{{ componentCode }}</code>
          </div>
          <div class="summary-row">
            <span>任务 id</span>
            <code>{{ taskId ?? "—" }}</code>
          </div>
          <div class="summary-row">
            <span>倾斜率</span>
            <code>{{ tiltPermille === null ? "—" : `${tiltPermille.toFixed(3)} ‰` }}</code>
          </div>
          <div class="summary-row">
            <span>算法版本</span>
            <code>{{ algorithmVersion ?? "—" }}</code>
          </div>
          <div v-if="degraded" class="summary-row degraded">
            <span>降级</span>
            <code>{{ degradeReason ?? "结果处于降级状态" }}</code>
          </div>
        </div>

        <div class="suggest-card">
          <div class="suggest-label">按阈值字典给出的建议缺陷等级</div>
          <div class="suggest-value">
            {{ suggestedLevel ? LEVEL_LABELS[suggestedLevel] : "—" }}
          </div>
          <div v-if="thresholdText" class="suggest-threshold">当前阈值：{{ thresholdText }}</div>
          <div class="suggest-note">⚠ {{ noteText }}</div>
        </div>

        <div v-if="result" class="success-card">
          <div class="success-title">已生成待确认工单，等待主管复核</div>
          <div class="success-row">工单号：<code>{{ result.orderNo }}</code></div>
          <div class="success-row">建议等级：<code>{{ result.level }}</code></div>
          <div class="success-hint">
            主管在后台点「通过」后才会流转为 CONFIRMED 并下发班组。
          </div>
        </div>

        <template v-else>
          <div v-if="loading" class="dispatch-loading">加载班组与阈值配置…</div>
          <div v-else-if="loadError" class="dispatch-error">{{ loadError }}</div>

          <div v-else class="form">
            <label class="form-row">
              <span>维修班组</span>
              <select v-model="teamId">
                <option value="" disabled>请选择班组</option>
                <option v-for="team in teams" :key="team.team_id" :value="team.team_id">
                  {{ team.team_name }}（{{ team.team_code }}）
                </option>
              </select>
            </label>
            <div v-if="selectedTeam" class="team-hint">
              负责人：{{ selectedTeam.leader_name ?? "未配置" }} ·
              电话：{{ selectedTeam.contact_phone ?? "—" }} ·
              范围：{{ selectedTeam.line_scope ?? "—" }}
            </div>

            <label class="form-row">
              <span>优先级</span>
              <select v-model="priority">
                <option v-for="opt in PRIORITY_OPTIONS" :key="opt.value" :value="opt.value">
                  {{ opt.label }}
                </option>
              </select>
            </label>

            <label class="form-row">
              <span>要求完成日期</span>
              <input v-model="dueDate" type="date" />
            </label>

            <label class="form-row">
              <span>备注</span>
              <input v-model="remark" type="text" maxlength="200" placeholder="可选，200 字以内" />
            </label>

            <div v-if="teams.length === 0" class="dispatch-error">
              当前数据范围内没有可用维修班组，请先在后台维护班组。
            </div>
          </div>
        </template>

        <div v-if="submitError" class="dispatch-error">{{ submitError }}</div>
      </div>

      <footer class="dispatch-footer">
        <button type="button" class="ghost" @click="close">取消</button>
        <button
          v-if="result === null"
          type="button"
          class="primary"
          :disabled="!canSubmit"
          @click="submit"
        >
          {{ submitting ? "提交中…" : "生成待确认工单" }}
        </button>
        <button v-else type="button" class="primary" @click="close">知道了</button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.dispatch-mask {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(3, 8, 18, 0.62);
  backdrop-filter: blur(3px);
}

.dispatch-dialog {
  width: 460px;
  max-height: calc(100% - 60px);
  overflow-y: auto;
  border-radius: 10px;
  background: rgba(10, 24, 48, 0.92);
  border: 1px solid rgba(80, 130, 220, 0.45);
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(10px);
  color: #cfe2ff;
  font-size: 13px;
}

.dispatch-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid rgba(80, 130, 220, 0.28);
}

.dispatch-title {
  font-size: 15px;
  font-weight: 600;
  color: #e8f1ff;
  letter-spacing: 0.02em;
}

.dispatch-close {
  background: transparent;
  border: none;
  color: #9fb4d0;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  padding: 0 4px;
}

.dispatch-close:hover {
  color: #fff;
}

.dispatch-body {
  padding: 14px 18px 6px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.summary {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border-radius: 6px;
  background: rgba(16, 36, 72, 0.65);
  border: 1px solid rgba(80, 130, 220, 0.24);
}

.summary-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  color: #9fb4d0;
}

.summary-row code {
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  color: #e8f1ff;
  word-break: break-all;
  text-align: right;
}

.summary-row.degraded code {
  color: #ffcece;
}

.suggest-card {
  padding: 12px;
  border-radius: 6px;
  background: rgba(36, 90, 160, 0.24);
  border: 1px solid rgba(130, 180, 255, 0.4);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.suggest-label {
  font-size: 12px;
  color: #9fb4d0;
}

.suggest-value {
  font-size: 18px;
  font-weight: 600;
  color: #cfe2ff;
}

.suggest-threshold {
  font-size: 11px;
  color: #88a4cf;
}

.suggest-note {
  font-size: 12px;
  font-weight: 600;
  color: #ffd28a;
  background: rgba(200, 160, 60, 0.16);
  border-radius: 4px;
  padding: 6px 8px;
}

.success-card {
  padding: 12px;
  border-radius: 6px;
  background: rgba(40, 160, 100, 0.16);
  border: 1px solid rgba(120, 220, 160, 0.4);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.success-title {
  font-size: 14px;
  font-weight: 600;
  color: #7fe0a5;
}

.success-row {
  font-size: 12px;
  color: #b9c9df;
}

.success-row code {
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  color: #c8ffdd;
}

.success-hint {
  font-size: 11px;
  color: #8aa0be;
}

.dispatch-loading {
  font-size: 12px;
  color: #9fb4d0;
}

.dispatch-error {
  padding: 6px 8px;
  border-radius: 4px;
  background: rgba(200, 80, 80, 0.18);
  color: #ff9b9b;
  font-size: 12px;
  word-break: break-all;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  color: #9fb4d0;
}

.form-row select,
.form-row input {
  flex: 1 1 auto;
  max-width: 280px;
  padding: 6px 8px;
  border-radius: 5px;
  border: 1px solid rgba(120, 170, 255, 0.4);
  background: rgba(16, 36, 72, 0.85);
  color: #cfe2ff;
  font-size: 12px;
  outline: none;
}

.form-row select:focus,
.form-row input:focus {
  border-color: rgba(160, 200, 255, 0.7);
}

.team-hint {
  font-size: 11px;
  color: #88a4cf;
  padding-left: 2px;
}

.dispatch-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 18px 16px;
}

.ghost,
.primary {
  padding: 7px 16px;
  border-radius: 5px;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.ghost {
  border: 1px solid rgba(120, 170, 255, 0.35);
  background: transparent;
  color: #9fb4d0;
}

.ghost:hover {
  background: rgba(26, 58, 110, 0.6);
}

.primary {
  border: 1px solid rgba(130, 180, 255, 0.6);
  background: rgba(36, 90, 160, 0.8);
  color: #e8f1ff;
  font-weight: 500;
}

.primary:hover:not(:disabled) {
  background: rgba(46, 110, 200, 0.95);
}

.primary:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
