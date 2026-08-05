<template>
  <div class="workbench">
    <!-- 左：工单队列 -->
    <el-card class="queue" shadow="never">
      <template #header>
        <div class="queue-header">
          <span>工单队列</span>
          <el-tag size="small" :type="wsTagType">{{ wsStatusText }}</el-tag>
        </div>
      </template>
      <div class="queue-toolbar">
        <el-select v-model="filterStatus" placeholder="全部状态" clearable size="small" @change="reload">
          <el-option v-for="s in ['PENDING', 'ASSIGNED', 'RESOLVED', 'CLOSED']" :key="s" :label="s" :value="s" />
        </el-select>
        <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
      </div>
      <div
        v-for="t in tickets"
        :key="t.ticket_id"
        class="ticket-item"
        :class="{ active: t.ticket_id === current?.ticket_id }"
        @click="openTicket(t.ticket_id)"
      >
        <div class="ticket-line">
          <el-tag size="small" :type="ticketType(t.status)">{{ t.status }}</el-tag>
          <span class="ticket-reason">{{ t.reason }}</span>
        </div>
        <div class="ticket-sub">
          用户 {{ t.user_id }}<span v-if="t.assignee"> · 坐席 {{ t.assignee }}</span>
          <span v-if="t.source_intent"> · {{ t.source_intent }}</span>
        </div>
      </div>
      <el-button v-if="nextBefore" size="small" text class="w-full" @click="loadMore">加载更多</el-button>
      <el-empty v-if="!tickets.length" description="暂无工单" :image-size="60" />
    </el-card>

    <!-- 右：工单处理 -->
    <el-card class="detail" shadow="never" body-class="detail-body">
      <template #header>
        <div class="detail-header">
          <span v-if="current">
            工单 {{ current.ticket_id.slice(0, 8) }}…
            <el-tag size="small" :type="ticketType(current.status)">{{ current.status }}</el-tag>
          </span>
          <span v-else>选择左侧工单开始处理</span>
          <span class="agent-box">
            坐席工号
            <el-input v-model="assignee" size="small" class="agent-input" placeholder="agent-1" />
          </span>
        </div>
      </template>

      <template v-if="current">
        <el-collapse v-model="ctxOpen" class="ctx">
          <el-collapse-item title="上下文移交包（任务栈/槽位/近况快照）" name="ctx">
            <pre class="json">{{ JSON.stringify(current.context, null, 2) }}</pre>
          </el-collapse-item>
        </el-collapse>

        <div ref="msgEl" class="detail-messages">
          <div v-for="m in messages" :key="m.message_id" class="msg-row" :class="roleClass(m.role)">
            <div class="msg-bubble" :class="roleClass(m.role)">
              <div class="msg-role">{{ roleLabel(m.role) }}</div>
              {{ m.content }}
            </div>
          </div>
        </div>

        <div class="actions">
          <el-button
            v-if="current.status === 'PENDING'"
            type="primary"
            :loading="acting"
            @click="doClaim"
          >
            认领工单
          </el-button>
          <template v-if="current.status === 'ASSIGNED'">
            <el-input
              v-model="replyDraft"
              type="textarea"
              :rows="2"
              resize="none"
              placeholder="以人工坐席身份回复用户（用户端 WS 实时可见）"
              class="reply-input"
              @keydown.ctrl.enter.prevent="doReply"
            />
            <el-button type="primary" :loading="acting" @click="doReply">回复</el-button>
            <el-button type="success" :loading="acting" @click="doResolve">解决并归还</el-button>
          </template>
          <el-text v-if="['RESOLVED', 'CLOSED'].includes(current.status)" type="info">
            工单已关闭，会话已归还智能助手。
          </el-text>
        </div>
      </template>
      <el-empty v-else description="左侧队列点击工单查看详情" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage, ElNotification } from "element-plus";

import {
  claimTicket,
  getTicket,
  listTickets,
  replyTicket,
  resolveTicket,
  type TicketBrief,
  type TicketDetail,
} from "@/api/handoff";
import { sessionMessages, type ObserveMessage } from "@/api/observe";
import { ApiError } from "@/api/client";
import { openHandoffWs, type AgentWsEvent, type WsStatus } from "@/api/ws";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const tickets = ref<TicketBrief[]>([]);
const nextBefore = ref<string | null>(null);
const filterStatus = ref("");
const loading = ref(false);
const current = ref<TicketDetail | null>(null);
const messages = ref<ObserveMessage[]>([]);
const assignee = ref(`agent-${auth.userId || "1"}`);
const replyDraft = ref("");
const acting = ref(false);
const ctxOpen = ref<string[]>([]);
const msgEl = ref<HTMLElement>();

// —— 坐席端 WS：新工单 / 接管期间用户新消息 ——
const wsStatus = ref<WsStatus>("closed");
let socket: WebSocket | null = null;
const wsStatusText = computed(
  () =>
    ({ connected: "实时已连", closed: "实时未连", unavailable: "实时不可用（缺管理令牌/鉴权模式）" })[
      wsStatus.value
    ],
);
const wsTagType = computed(
  () => ({ connected: "success", closed: "info", unavailable: "warning" })[wsStatus.value],
);

function toast(err: unknown) {
  ElMessage.error(err instanceof ApiError ? `${err.code}: ${err.message}` : String(err));
}

function ticketType(status: string) {
  return (
    { PENDING: "danger", ASSIGNED: "warning", RESOLVED: "success", CLOSED: "info" }[status] ?? "info"
  );
}

function roleClass(role: string) {
  return role === "user" ? "user" : role === "agent" ? "agent" : "ai";
}

function roleLabel(role: string) {
  return { user: "用户", agent: "坐席", assistant: "AI" }[role] ?? role;
}

async function reload() {
  loading.value = true;
  try {
    const data = await listTickets({ status: filterStatus.value || undefined });
    tickets.value = data.tickets;
    nextBefore.value = data.next_before;
  } catch (err) {
    toast(err);
  } finally {
    loading.value = false;
  }
}

async function loadMore() {
  if (!nextBefore.value) return;
  try {
    const data = await listTickets({ status: filterStatus.value || undefined, before: nextBefore.value });
    tickets.value.push(...data.tickets);
    nextBefore.value = data.next_before;
  } catch (err) {
    toast(err);
  }
}

async function openTicket(ticketId: string) {
  try {
    current.value = await getTicket(ticketId);
    await refreshMessages();
  } catch (err) {
    toast(err);
  }
}

async function refreshMessages() {
  if (!current.value) return;
  const data = await sessionMessages(current.value.session_id);
  messages.value = data.messages;
  await nextTick();
  msgEl.value?.scrollTo({ top: msgEl.value.scrollHeight });
}

async function doClaim() {
  if (!current.value) return;
  if (!assignee.value.trim()) {
    ElMessage.warning("请先填写坐席工号");
    return;
  }
  acting.value = true;
  try {
    await claimTicket(current.value.ticket_id, assignee.value.trim());
    ElMessage.success("已认领");
    await openTicket(current.value.ticket_id);
    await reload();
  } catch (err) {
    toast(err);
  } finally {
    acting.value = false;
  }
}

async function doReply() {
  if (!current.value || !replyDraft.value.trim() || acting.value) return;
  acting.value = true;
  try {
    await replyTicket(current.value.ticket_id, replyDraft.value.trim());
    replyDraft.value = "";
    await refreshMessages();
  } catch (err) {
    toast(err);
  } finally {
    acting.value = false;
  }
}

async function doResolve() {
  if (!current.value) return;
  acting.value = true;
  try {
    await resolveTicket(current.value.ticket_id);
    ElMessage.success("已解决并归还会话");
    await openTicket(current.value.ticket_id);
    await reload();
  } catch (err) {
    toast(err);
  } finally {
    acting.value = false;
  }
}

function connectWs() {
  socket?.close();
  socket = openHandoffWs({
    onStatus: (s) => (wsStatus.value = s),
    onEvent: async (event: AgentWsEvent) => {
      if (event.type === "ticket_created") {
        ElNotification({
          title: "新工单",
          message: `${event.reason || ""} · 会话 ${event.session_id?.slice(0, 8)}…`,
          type: "warning",
        });
        await reload();
      } else if (event.type === "user_message") {
        // 正在处理的会话有新用户消息 → 刷新对话流
        if (current.value?.session_id === event.session_id) await refreshMessages();
      }
    },
  });
}

onMounted(() => {
  reload();
  connectWs();
});
onBeforeUnmount(() => socket?.close());
</script>

<style scoped>
.workbench {
  display: flex;
  gap: 12px;
  height: calc(100vh - 120px);
}
.queue {
  width: 320px;
  flex-shrink: 0;
  overflow-y: auto;
}
.queue-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.queue-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.ticket-item {
  padding: 8px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid transparent;
}
.ticket-item:hover {
  background: #f5f7fa;
}
.ticket-item.active {
  border-color: #409eff;
  background: #ecf5ff;
}
.ticket-line {
  display: flex;
  align-items: center;
  gap: 6px;
}
.ticket-reason {
  font-size: 13px;
  font-weight: 500;
}
.ticket-sub {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
.detail {
  flex: 1;
  display: flex;
  flex-direction: column;
}
.detail :deep(.detail-body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.agent-box {
  font-size: 12px;
  color: #909399;
  display: flex;
  align-items: center;
  gap: 6px;
}
.agent-input {
  width: 140px;
}
.ctx {
  margin-bottom: 8px;
}
.json {
  margin: 0;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
}
.detail-messages {
  flex: 1;
  overflow-y: auto;
  padding: 4px;
}
.msg-row {
  display: flex;
  margin-bottom: 8px;
}
.msg-row.user {
  justify-content: flex-start;
}
.msg-row.ai,
.msg-row.agent {
  justify-content: flex-end;
}
.msg-bubble {
  max-width: 70%;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg-bubble.user {
  background: #f0f2f5;
}
.msg-bubble.ai {
  background: #ecf5ff;
}
.msg-bubble.agent {
  background: #e7f6ec;
}
.msg-role {
  font-size: 11px;
  color: #909399;
  margin-bottom: 2px;
}
.actions {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  padding-top: 8px;
  border-top: 1px solid #e4e7ed;
}
.reply-input {
  flex: 1;
}
.w-full {
  width: 100%;
}
</style>
