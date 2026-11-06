/**
 * 批量几何分析轮询 Hook（P1 批量流水线）。
 *
 * 与单条 ``useGeometryTaskPolling`` 独立：一次并发拉取本批全部未终态任务，
 * 汇总 ``当前进度 n/total`` 与失败项，不占用单条任务的状态，互不干扰。
 * 使用 setTimeout 串行排程，避免慢请求叠加。
 */
import { computed, onBeforeUnmount, ref } from "vue";
import {
  fetchGeometryTask,
  GeometryApiError,
  isTerminalStatus,
  type GeometryTaskDetail,
  type GeometryTaskStatus,
} from "../api/geometry";

export interface UseGeometryBatchPollingOptions {
  /** 轮询间隔，默认 800ms（10 条并发时减少对后端的压力）。 */
  intervalMs?: number;
}

export interface BatchItemState {
  taskId: string;
  index: number;
  status: GeometryTaskStatus | null;
  detail: GeometryTaskDetail | null;
  /** 请求失败（如 404/网络错误），与任务 FAILED 区分展示。 */
  error: string | null;
}

export function useGeometryBatchPolling(
  options: UseGeometryBatchPollingOptions = {},
) {
  const intervalMs = options.intervalMs ?? 800;

  const taskIds = ref<string[]>([]);
  const details = ref<Record<string, GeometryTaskDetail>>({});
  const errors = ref<Record<string, string>>({});
  const polling = ref(false);

  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  const clearTimer = (): void => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  const stop = (): void => {
    stopped = true;
    clearTimer();
    polling.value = false;
  };

  const items = computed<BatchItemState[]>(() =>
    taskIds.value.map((taskId, index) => {
      const detail = details.value[taskId] ?? null;
      return {
        taskId,
        index,
        status: detail?.status ?? null,
        detail,
        error: errors.value[taskId] ?? null,
      };
    }),
  );

  const total = computed(() => taskIds.value.length);
  const completed = computed(
    () => items.value.filter((item) => isTerminalStatus(item.status) || item.error !== null).length,
  );
  const succeeded = computed(
    () => items.value.filter((item) => item.status === "SUCCEEDED").length,
  );
  const failedItems = computed(() =>
    items.value.filter(
      (item) =>
        (isTerminalStatus(item.status) && item.status !== "SUCCEEDED") || item.error !== null,
    ),
  );
  const failed = computed(() => failedItems.value.length);

  const isFinished = (taskId: string): boolean => {
    const detail = details.value[taskId];
    return (detail !== undefined && isTerminalStatus(detail.status)) || taskId in errors.value;
  };

  const schedule = (): void => {
    clearTimer();
    timer = setTimeout(() => {
      void tick();
    }, intervalMs);
  };

  const tick = async (): Promise<void> => {
    if (stopped) return;
    const pending = taskIds.value.filter((taskId) => !isFinished(taskId));
    if (pending.length === 0) {
      stop();
      return;
    }
    await Promise.all(
      pending.map(async (taskId) => {
        try {
          const next = await fetchGeometryTask(taskId);
          if (stopped) return;
          details.value = { ...details.value, [taskId]: next };
        } catch (err) {
          if (stopped) return;
          const message =
            err instanceof GeometryApiError
              ? err.message
              : err instanceof Error
                ? `轮询失败：${err.message}`
                : `轮询失败：${String(err)}`;
          errors.value = { ...errors.value, [taskId]: message };
        }
      }),
    );
    if (stopped) return;
    if (taskIds.value.every(isFinished)) {
      stop();
      return;
    }
    schedule();
  };

  /** 发起新批次前清空旧状态，避免上一批结果串台。 */
  const reset = (): void => {
    stop();
    stopped = false;
    clearTimer();
    taskIds.value = [];
    details.value = {};
    errors.value = {};
  };

  const start = (ids: string[]): void => {
    reset();
    taskIds.value = [...ids];
    polling.value = true;
    // 受理后立刻查一次，减少演示时的空窗。
    void tick();
  };

  onBeforeUnmount(stop);

  return {
    taskIds,
    items,
    total,
    completed,
    succeeded,
    failed,
    failedItems,
    polling,
    start,
    stop,
    reset,
  };
}
