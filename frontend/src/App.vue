<script setup lang="ts">
import { onMounted, ref } from "vue";
import { fetchReadiness, type Readiness } from "./api/client";

const readiness = ref<Readiness | null>(null);
const error = ref<string>("");

onMounted(async () => {
  try {
    readiness.value = await fetchReadiness();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "未知错误";
  }
});
</script>

<template>
  <main class="shell">
    <h1>桥隧结构智能检测与数字孪生运维平台</h1>
    <p class="sub">骨架页面（T-02）。业务视图按 docs/tasks 逐卡接入。</p>
    <section class="card">
      <h2>服务就绪状态</h2>
      <p v-if="error" class="bad">{{ error }}</p>
      <ul v-else-if="readiness">
        <li>status: {{ readiness.status }}</li>
        <li v-for="(v, k) in readiness.checks" :key="k">{{ k }}: {{ v }}</li>
        <li>traceId: {{ readiness.traceId }}</li>
      </ul>
      <p v-else>检查中…</p>
    </section>
  </main>
</template>

<style scoped>
.shell {
  max-width: 720px;
  margin: 48px auto;
  padding: 0 20px;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  color: #1f2933;
}
.sub { color: #52606d; }
.card {
  margin-top: 24px;
  padding: 16px 20px;
  border: 1px solid #d9e2ec;
  border-radius: 8px;
  background: #f8fafc;
}
.bad { color: #b91c1c; }
</style>
