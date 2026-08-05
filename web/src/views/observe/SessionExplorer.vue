<template>
  <div>
    <el-alert
      v-if="!auth.adminToken && !auth.apiKey"
      type="warning"
      :closable="false"
      class="mb-12"
      title="观测分析需要管理令牌"
      description="后端配置 KB_ADMIN_TOKEN 后，在登录页「管理令牌」填入同值（鉴权模式则使用 admin scope 的 API Key）。"
    />
    <el-card shadow="never">
      <template #header>
        <div class="toolbar">
          <span>会话记录</span>
          <span class="filters">
            <el-input
              v-model="filterUser"
              placeholder="按用户 ID 过滤"
              clearable
              class="filter-input"
              @keyup.enter="reload"
            />
            <el-select v-model="filterStatus" placeholder="状态" clearable class="filter-select">
              <el-option label="active" value="active" />
              <el-option label="handoff" value="handoff" />
              <el-option label="closed" value="closed" />
            </el-select>
            <el-button type="primary" :loading="loading" @click="reload">查询</el-button>
          </span>
        </div>
      </template>
      <el-table :data="sessions" size="small" @row-click="openDetail">
        <el-table-column prop="session_id" label="会话 ID" min-width="280" show-overflow-tooltip />
        <el-table-column prop="user_id" label="用户" width="140" />
        <el-table-column prop="channel" label="渠道" width="80" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="updated_at" label="最近活跃" min-width="170" />
      </el-table>
      <div class="pager">
        <el-button size="small" :disabled="page === 0 || loading" @click="page--; reload()">
          上一页
        </el-button>
        <span class="page-no">第 {{ page + 1 }} 页</span>
        <el-button size="small" :disabled="!hasMore || loading" @click="page++; reload()">
          下一页
        </el-button>
      </div>
    </el-card>

    <!-- 会话详情：消息流 / 决策日志 / 工具调用 -->
    <el-drawer v-model="drawerOpen" :title="`会话 ${current?.session_id ?? ''}`" size="72%">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="消息流" name="messages">
          <div v-for="m in messages" :key="m.message_id" class="msg-row">
            <el-tag size="small" :type="m.role === 'user' ? 'primary' : m.role === 'agent' ? 'success' : 'info'">
              {{ m.role }}
            </el-tag>
            <span class="msg-content">{{ m.content }}</span>
            <span class="msg-meta">
              <el-tag v-if="m.intent" size="small" effect="plain">{{ m.intent }}</el-tag>
              <el-tag v-if="m.status" size="small" effect="plain" type="warning">{{ m.status }}</el-tag>
              {{ m.created_at }}
            </span>
          </div>
          <el-empty v-if="!messages.length" description="无消息" />
        </el-tab-pane>

        <el-tab-pane label="决策日志" name="decisions">
          <el-collapse>
            <el-collapse-item v-for="d in decisions" :key="d.decision_id" :name="d.decision_id">
              <template #title>
                <span class="decision-title">
                  <span class="decision-text">{{ d.original_text }}</span>
                  <el-tag v-if="intentOf(d)" size="small">{{ intentOf(d) }}</el-tag>
                  <el-tag v-if="d.decision_source" size="small" type="info">{{ d.decision_source }}</el-tag>
                  <el-tag v-if="d.status" size="small" type="warning">{{ d.status }}</el-tag>
                  <el-tag v-if="marginOf(d) !== null" size="small" effect="plain">
                    margin {{ marginOf(d) }}
                  </el-tag>
                </span>
              </template>
              <el-descriptions :column="1" size="small" border>
                <el-descriptions-item label="意图结果">
                  <pre class="json">{{ pretty(d.intent_result) }}</pre>
                </el-descriptions-item>
                <el-descriptions-item label="槽位">
                  <pre class="json">{{ pretty(d.slots) }}</pre>
                </el-descriptions-item>
                <el-descriptions-item label="图轨迹（含护栏/Meta 影子）">
                  <pre class="json">{{ pretty(d.graph_trace) }}</pre>
                </el-descriptions-item>
                <el-descriptions-item v-if="d.retrieval" label="检索轨迹">
                  <pre class="json">{{ pretty(d.retrieval) }}</pre>
                </el-descriptions-item>
                <el-descriptions-item v-if="d.experiment" label="A/B 实验">
                  <pre class="json">{{ pretty(d.experiment) }}</pre>
                </el-descriptions-item>
                <el-descriptions-item v-if="d.latency" label="耗时">
                  <pre class="json">{{ pretty(d.latency) }}</pre>
                </el-descriptions-item>
                <el-descriptions-item v-if="d.error" label="错误">
                  <pre class="json">{{ pretty(d.error) }}</pre>
                </el-descriptions-item>
              </el-descriptions>
            </el-collapse-item>
          </el-collapse>
          <el-empty v-if="!decisions.length" description="无决策日志" />
        </el-tab-pane>

        <el-tab-pane label="工具调用" name="tools">
          <el-table :data="toolCalls" size="small">
            <el-table-column prop="tool_id" label="工具" width="200" />
            <el-table-column label="结果" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.ok ? 'success' : 'danger'">
                  {{ row.ok ? "成功" : row.error_code || "失败" }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="latency_ms" label="耗时(ms)" width="100" />
            <el-table-column label="请求/响应" min-width="300">
              <template #default="{ row }">
                <pre class="json small">{{ pretty(row.request) }} → {{ pretty(row.response) }}</pre>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="时间" min-width="170" />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";

import {
  listSessions,
  sessionDecisions,
  sessionMessages,
  sessionToolCalls,
  type DecisionItem,
  type ObserveMessage,
  type SessionItem,
  type ToolCallItem,
} from "@/api/observe";
import { ApiError } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const sessions = ref<SessionItem[]>([]);
const filterUser = ref("");
const filterStatus = ref("");
const page = ref(0);
const hasMore = ref(false);
const loading = ref(false);

const drawerOpen = ref(false);
const activeTab = ref("messages");
const current = ref<SessionItem | null>(null);
const messages = ref<ObserveMessage[]>([]);
const decisions = ref<DecisionItem[]>([]);
const toolCalls = ref<ToolCallItem[]>([]);

const PAGE_SIZE = 20;

function toast(err: unknown) {
  ElMessage.error(err instanceof ApiError ? `${err.code}: ${err.message}` : String(err));
}

function statusType(status: string) {
  return { active: "success", handoff: "warning", closed: "info" }[status] ?? "info";
}

function pretty(value: unknown): string {
  return value == null ? "—" : JSON.stringify(value, null, 2);
}

function intentOf(d: DecisionItem): string {
  const r = d.intent_result as { final_intent?: string; pred_label?: string } | null;
  return r?.final_intent || r?.pred_label || "";
}

function marginOf(d: DecisionItem): number | null {
  const r = d.intent_result as { margin?: number } | null;
  return typeof r?.margin === "number" ? r.margin : null;
}

async function reload() {
  loading.value = true;
  try {
    const data = await listSessions({
      userId: filterUser.value || undefined,
      status: filterStatus.value || undefined,
      limit: PAGE_SIZE,
      offset: page.value * PAGE_SIZE,
    });
    sessions.value = data.sessions;
    hasMore.value = data.has_more;
  } catch (err) {
    toast(err);
  } finally {
    loading.value = false;
  }
}

async function openDetail(row: SessionItem) {
  current.value = row;
  drawerOpen.value = true;
  activeTab.value = "messages";
  try {
    const [m, d, t] = await Promise.all([
      sessionMessages(row.session_id),
      sessionDecisions(row.session_id),
      sessionToolCalls(row.session_id),
    ]);
    messages.value = m.messages;
    decisions.value = d.decisions;
    toolCalls.value = t.tool_calls;
  } catch (err) {
    toast(err);
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
.filters {
  display: flex;
  gap: 8px;
}
.filter-input {
  width: 180px;
}
.filter-select {
  width: 120px;
}
.pager {
  display: flex;
  align-items: center;
  gap: 12px;
  justify-content: flex-end;
  margin-top: 10px;
}
.page-no {
  font-size: 12px;
  color: #909399;
}
.msg-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px dashed #ebeef5;
}
.msg-content {
  flex: 1;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg-meta {
  font-size: 12px;
  color: #909399;
  display: flex;
  gap: 4px;
  align-items: center;
  flex-shrink: 0;
}
.decision-title {
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
}
.decision-text {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.json {
  margin: 0;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 240px;
  overflow-y: auto;
}
.json.small {
  max-height: 80px;
}
.mb-12 {
  margin-bottom: 12px;
}
</style>
