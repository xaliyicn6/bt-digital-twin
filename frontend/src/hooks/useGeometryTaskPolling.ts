/**
 * 几何分析任务轮询 Hook。
 *
 * 仓库此前没有可复用的轮询 Hook（已核实 frontend/src 下只有 App.vue / client.ts / scene.ts），
 * 这里按最小实现补齐：提交成功后按固定间隔拉取 GET /geometry/tasks/{id}，
 * 直到进入终态（SUCCEEDED / FAILED / CANCELLED / TIMEOUT）或组件卸载自动停止。
 * 使用 setTimeout 串行排程，避免慢请求叠加导致并发轮询。
 */
import { onBeforeUnmount, ref } from "vue";
import {
  fetchGeometryTask,
  GeometryApiError,
  isTerminalStatus,
  type GeometryTaskDetail,
} from "../api/geometry";

export interface UseGeometryTaskPollingOptions {
  /** 轮询间隔，默认 500ms（演示时进度反馈更及时）。 */
  intervalMs?: number;
}

export function useGeometryTaskPolling(
  options: UseGeometryTaskPollingOptions = {},
) {
  const intervalMs = options.intervalMs ?? 500;

  const taskId = ref<string | null>(null);
  const detail = ref<GeometryTaskDetail | null>(null);
  const error = ref<string | null>(null);
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

  const schedule = (id: string): void => {
    clearTimer();
    timer = setTimeout(() => {
      void tick(id);
    }, intervalMs);
  };

  const tick = async (id: string): Promise<void> => {
    if (stopped) return;
    try {
      const next = await fetchGeometryTask(id);
      if (stopped) return;
      detail.value = next;
      if (isTerminalStatus(next.status)) {
        if (next.status !== "SUCCEEDED") {
          const parts = [next.error_code, next.error_detail].filter(
            (v): v is string => Boolean(v),
          );
          error.value = parts.length ? parts.join(" ") : `任务结束：${next.status}`;
        }
        stop();
        return;
      }
      schedule(id);
    } catch (err) {
      if (stopped) return;
      error.value =
        err instanceof GeometryApiError
          ? err.message
          : err instanceof Error
            ? `轮询失败：${err.message}`
            : `轮询失败：${String(err)}`;
      stop();
    }
  };

  /** 发起轮询前先重置上一次任务的状态，避免旧结果串台。 */
  const reset = (): void => {
    stop();
    stopped = false;
    clearTimer();
    taskId.value = null;
    detail.value = null;
    error.value = null;
  };

  const start = (id: string): void => {
    reset();
    taskId.value = id;
    polling.value = true;
    // 提交受理后立刻查一次，不等首个定时器，减少演示时的空窗。
    void tick(id);
  };

  onBeforeUnmount(stop);

  return { taskId, detail, error, polling, start, stop, reset };
}
