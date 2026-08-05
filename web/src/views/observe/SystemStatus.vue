<template>
  <el-card shadow="never">
    <template #header>
      <div class="toolbar">
        <span>系统状态</span>
        <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
      </div>
    </template>
    <el-descriptions :column="1" border>
      <el-descriptions-item label="健康检查 /api/health">
        <el-tag :type="health ? 'success' : 'danger'">{{ health ? "OK" : "异常" }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="就绪检查 /api/health/ready（含 DB/Redis 探活）">
        <el-tag :type="ready ? 'success' : 'danger'">{{ ready ? "READY" : "未就绪" }}</el-tag>
        <pre v-if="readyDetail" class="json">{{ readyDetail }}</pre>
      </el-descriptions-item>
      <el-descriptions-item label="Prometheus 指标">
        <el-link type="primary" href="/api/metrics" target="_blank">/metrics（原始文本，新窗口打开）</el-link>
        <div class="tip">
          聚合看板走 Grafana（deploy/monitoring/，Stage 25）；此处仅原始出口。
          质量看板 SQL 见 docs/ops/quality_queries.md。
        </div>
      </el-descriptions-item>
    </el-descriptions>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";

const health = ref(false);
const ready = ref(false);
const readyDetail = ref("");
const loading = ref(false);

async function reload() {
  loading.value = true;
  try {
    const h = await fetch("/api/health");
    health.value = h.ok;
    const r = await fetch("/api/health/ready");
    ready.value = r.ok;
    try {
      readyDetail.value = JSON.stringify((await r.json()).data ?? {}, null, 2);
    } catch {
      readyDetail.value = "";
    }
  } finally {
    loading.value = false;
  }
}

onMounted(reload);
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.json {
  margin: 6px 0 0;
  font-size: 12px;
}
.tip {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}
</style>
