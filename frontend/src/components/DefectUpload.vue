<script setup lang="ts">
/**
 * 表观病害上传（界面先行版）。
 *
 * 真实链路：POST /attachments 落盘与登记 → 调用 YOLO 推理服务 → 候选缺陷入库/三维打点。
 * 当前缺口：推理服务地址、模型版本、类别字典、照片 POS/逆投影均未接入（清单 C5）。
 * 因此：**照片确实会上传**，但红框/类别/置信度是**示例数据 · 算法待接入**，
 * 界面必须持续展示该标注，禁止把它当成真实识别结果。
 */
import { onBeforeUnmount, ref } from "vue";
import type { UploadFile } from "element-plus";
import { uploadAttachment } from "../api/workorder";

const props = defineProps<{ componentCode: string }>();

const DEMO_NOTE = "示例数据 · 算法待接入";
const MAX_BYTES = 10 * 1024 * 1024;

interface DemoFinding {
  category: string;
  confidence: number;
  /** 相对比例坐标 [x1, y1, x2, y2]，0-1；仅用于示例红框。 */
  bbox: [number, number, number, number];
  createdAt: string;
  attachmentId: string;
  fileName: string;
}

const file = ref<File | null>(null);
const previewUrl = ref<string | null>(null);
const busy = ref(false);
const error = ref<string | null>(null);
const finding = ref<DemoFinding | null>(null);
const drawerVisible = ref(false);

function releasePreview(): void {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value);
    previewUrl.value = null;
  }
}

function onFileChange(uploadFile: UploadFile): void {
  error.value = null;
  const raw = uploadFile.raw;
  if (!raw) return;
  if (raw.size > MAX_BYTES) {
    error.value = "单张照片不得超过 10 MB";
    return;
  }
  if (!/^image\/(jpeg|png)$/.test(raw.type)) {
    error.value = "仅支持 JPG / PNG";
    return;
  }
  releasePreview();
  file.value = raw;
  previewUrl.value = URL.createObjectURL(raw);
}

function onFileRemove(): void {
  file.value = null;
  finding.value = null;
  releasePreview();
}

async function analyze(): Promise<void> {
  const chosen = file.value;
  if (!chosen || busy.value) return;
  busy.value = true;
  error.value = null;
  try {
    const uploaded = await uploadAttachment(props.componentCode, chosen);
    // 占位延时：模拟推理排队；YOLO 接入后替换为真实推理结果。
    await new Promise((resolve) => setTimeout(resolve, 900));
    finding.value = {
      category: "结构性裂缝（示例）",
      confidence: 0.92,
      bbox: [0.2, 0.32, 0.62, 0.6],
      createdAt: new Date().toLocaleString("zh-CN"),
      attachmentId: uploaded.attachment_id,
      fileName: chosen.name,
    };
    drawerVisible.value = true;
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = false;
  }
}

onBeforeUnmount(releasePreview);
</script>

<template>
  <div class="defect-block">
    <div class="defect-head">
      <span class="defect-title">表观病害上传</span>
      <span class="defect-demo">{{ DEMO_NOTE }}</span>
    </div>
    <div class="defect-sub">照片真实入库；红框/类别/置信度为示例结果</div>

    <el-upload
      :auto-upload="false"
      :limit="1"
      accept="image/jpeg,image/png"
      :on-change="onFileChange"
      :on-remove="onFileRemove"
      class="defect-upload"
    >
      <button type="button" class="task-btn">选择现场照片</button>
    </el-upload>

    <button
      type="button"
      class="task-btn defect-analyze"
      :disabled="!file || busy"
      @click="analyze"
    >
      {{ busy ? "识别中…" : "上传并识别" }}
    </button>

    <div v-if="error" class="task-error">{{ error }}</div>

    <el-drawer
      v-model="drawerVisible"
      title="病害识别结果（示例）"
      size="420px"
    >
      <template v-if="finding">
        <div class="defect-preview">
          <img v-if="previewUrl" :src="previewUrl" alt="现场照片" />
          <div
            class="defect-box"
            :style="{
              left: `${finding.bbox[0] * 100}%`,
              top: `${finding.bbox[1] * 100}%`,
              width: `${(finding.bbox[2] - finding.bbox[0]) * 100}%`,
              height: `${(finding.bbox[3] - finding.bbox[1]) * 100}%`,
            }"
          />
        </div>
        <div class="defect-rows">
          <div class="defect-row"><span>病害类别</span><strong>{{ finding.category }}</strong></div>
          <div class="defect-row"><span>置信度</span><strong>{{ finding.confidence.toFixed(2) }}</strong></div>
          <div class="defect-row"><span>识别时间</span><strong>{{ finding.createdAt }}</strong></div>
          <div class="defect-row"><span>附件 id</span><strong class="mono">{{ finding.attachmentId }}</strong></div>
          <div class="defect-row"><span>文件</span><strong>{{ finding.fileName }}</strong></div>
        </div>
        <div class="defect-warn">
          未调用 YOLO 推理服务：红框、类别、置信度均为示例数据，不代表真实识别结果；
          裂缝宽度需量测算法接入。三维模型红旗打点待照片 POS/逆投影数据接入。
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.defect-block {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed rgba(80, 130, 220, 0.25);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.defect-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.defect-title {
  font-size: 12px;
  font-weight: 600;
  color: #e8f1ff;
}

.defect-demo {
  font-size: 10px;
  color: #d9c088;
}

.defect-sub {
  font-size: 10px;
  color: #7f96b6;
}

.defect-upload :deep(.el-upload) {
  width: 100%;
}

.defect-analyze {
  border-color: rgba(120, 220, 160, 0.55);
  background: rgba(32, 110, 78, 0.75);
}

.defect-preview {
  position: relative;
  width: 100%;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  overflow: hidden;
}

.defect-preview img {
  display: block;
  width: 100%;
}

.defect-box {
  position: absolute;
  border: 2px solid #ff4d4f;
  box-shadow: 0 0 8px rgba(255, 77, 79, 0.45);
  pointer-events: none;
}

.defect-rows {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.defect-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  color: #4b5563;
}

.defect-row strong {
  color: #1f2937;
  font-weight: 600;
  text-align: right;
  word-break: break-all;
}

.defect-row .mono {
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 11px;
}

.defect-warn {
  margin-top: 14px;
  padding: 8px 10px;
  border-left: 3px solid #f59e0b;
  background: #fffbeb;
  color: #92400e;
  font-size: 11px;
  line-height: 1.6;
}
</style>
