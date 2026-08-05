<template>
  <div class="console">
    <!-- 左：会话操作 -->
    <el-card class="side" shadow="never">
      <template #header>会话</template>
      <el-button type="primary" class="w-full" :loading="creating" @click="newSession">
        新建会话
      </el-button>
      <el-divider />
      <template v-if="sessionId">
        <div class="field-label">当前会话</div>
        <el-text size="small" class="session-id">{{ sessionId }}</el-text>
        <el-divider />
        <el-button class="w-full" :loading="loadingHistory" @click="loadHistory">
          加载历史消息
        </el-button>
      </template>
      <el-empty v-else description="先新建一个会话" :image-size="60" />
      <el-divider />
      <div class="field-label">测试话术速填</div>
      <div class="quick-fills">
        <el-tag
          v-for="text in quickFills"
          :key="text"
          class="quick-tag"
          @click="draft = text"
        >
          {{ text }}
        </el-tag>
      </div>
    </el-card>

    <!-- 右：对话流 -->
    <el-card class="chat" shadow="never" body-class="chat-body">
      <template #header>
        <div class="chat-header">
          <span>
            对话控制台
            <el-text size="small" type="info">（AI 回复附链路决策标签，用于测试观察）</el-text>
          </span>
          <span class="ws-status">
            <el-tooltip
              content="WS 只收服务端推送（坐席回复/会话归还/主动消息）；bot 回复走 HTTP。鉴权模式下浏览器 WS 不可用（无法携带 Bearer 头）"
              placement="bottom"
            >
              <el-tag size="small" :type="wsTagType">实时通道：{{ wsStatusText }}</el-tag>
            </el-tooltip>
            <el-button
              v-if="wsStatus === 'closed' && sessionId"
              size="small"
              text
              type="primary"
              @click="connectWs"
            >
              重连
            </el-button>
          </span>
        </div>
      </template>
      <div ref="listEl" class="messages">
        <div v-for="msg in messages" :key="msg.key" class="row" :class="msg.role">
          <div class="bubble" :class="msg.role">
            <div v-if="msg.role === 'agent'" class="bubble-label">人工坐席</div>
            <div v-if="msg.role === 'system'" class="bubble-label">系统事件</div>
            <div class="content">{{ msg.content }}</div>
            <div v-if="msg.role === 'ai' && (msg.intent || msg.status)" class="tags">
              <el-tag v-if="msg.intent" size="small">{{ msg.intent }}</el-tag>
              <el-tag v-if="msg.status" size="small" type="warning">{{ msg.status }}</el-tag>
              <el-tag v-if="msg.state" size="small" type="info">{{ msg.state }}</el-tag>
              <el-tooltip v-if="msg.traceId" :content="`trace: ${msg.traceId}`">
                <el-tag size="small" type="success">trace</el-tag>
              </el-tooltip>
              <!-- 反馈（Stage 09 API）：down 评价进回流待审导出 -->
              <span class="vote">
                <el-button
                  size="small"
                  text
                  :type="msg.voted === 'up' ? 'primary' : ''"
                  @click="vote(msg, 'up')"
                >👍</el-button>
                <el-button
                  size="small"
                  text
                  :type="msg.voted === 'down' ? 'danger' : ''"
                  @click="vote(msg, 'down')"
                >👎</el-button>
              </span>
            </div>
          </div>
        </div>
        <el-empty v-if="!messages.length" description="发一条消息试试，例如「我要退款」" />
      </div>
      <div class="composer">
        <el-input
          v-model="draft"
          type="textarea"
          :rows="2"
          resize="none"
          placeholder="输入消息，Ctrl+Enter 发送"
          @keydown.ctrl.enter.prevent="send"
        />
        <div class="composer-side">
          <el-button type="primary" :loading="sending" :disabled="!sessionId" @click="send">
            发送
          </el-button>
          <el-tooltip content="SSE 流式：delta 渐进渲染，done 落最终决策标签">
            <el-checkbox v-model="streamMode" size="small">流式</el-checkbox>
          </el-tooltip>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref } from "vue";
import { ElMessage } from "element-plus";

import {
  createSession,
  listMessages,
  sendMessage,
  sendMessageStream,
  submitFeedback,
} from "@/api/chat";
import { ApiError } from "@/api/client";
import { canUseWs, openSessionWs, type WsEvent, type WsStatus } from "@/api/ws";

interface Bubble {
  key: string;
  role: "user" | "ai" | "agent" | "system";
  content: string;
  intent?: string | null;
  status?: string | null;
  state?: string | null;
  traceId?: string | null;
  voted?: "up" | "down";
}

const sessionId = ref("");
const messages = ref<Bubble[]>([]);
const draft = ref("");
const creating = ref(false);
const sending = ref(false);
const streamMode = ref(false);
const loadingHistory = ref(false);
const listEl = ref<HTMLElement>();

// —— 实时通道（Stage 15 用户端 WS：坐席回复/会话归还/主动消息）——
const wsStatus = ref<WsStatus>(canUseWs() ? "closed" : "unavailable");
let socket: WebSocket | null = null;

const wsStatusText = computed(
  () =>
    ({ connected: "已连接", closed: "未连接", unavailable: "不可用（鉴权模式）" })[
      wsStatus.value
    ],
);
const wsTagType = computed(
  () => ({ connected: "success", closed: "info", unavailable: "warning" })[wsStatus.value],
);

function connectWs() {
  socket?.close();
  socket = openSessionWs(sessionId.value, {
    onStatus: (status) => (wsStatus.value = status),
    onEvent: async (event: WsEvent) => {
      if (event.type === "agent_reply") {
        messages.value.push({
          key: event.message_id || `agent-${Date.now()}`,
          role: "agent",
          content: event.content,
        });
      } else if (event.type === "session_resumed") {
        messages.value.push({
          key: `sys-${Date.now()}`,
          role: "system",
          content: event.content || "人工服务已结束，会话已交还智能助手。",
        });
      } else if (event.type === "proactive") {
        messages.value.push({
          key: event.message_id || `pro-${Date.now()}`,
          role: "system",
          content: `[${event.category || "通知"}] ${event.content}`,
        });
      }
      await scrollToBottom();
    },
  });
}

onBeforeUnmount(() => socket?.close());

// 覆盖主链路关键路径的测试话术（补槽/确认门/切换守护/多意图/澄清）
const quickFills = [
  "我要退款",
  "订单号是12345678",
  "确认",
  "先别退了，帮我查下物流",
  "我要退款，顺便看下快递到哪了",
  "你们支持开发票吗",
];

function toast(err: unknown) {
  ElMessage.error(err instanceof ApiError ? `${err.code}: ${err.message}` : String(err));
}

async function scrollToBottom() {
  await nextTick();
  listEl.value?.scrollTo({ top: listEl.value.scrollHeight, behavior: "smooth" });
}

async function newSession() {
  creating.value = true;
  try {
    const data = await createSession();
    sessionId.value = data.session_id;
    messages.value = [];
    ElMessage.success("会话已创建");
    // 建会话即挂实时通道（开发模式；鉴权模式浏览器 WS 不可用，状态标签如实显示）
    if (canUseWs()) connectWs();
  } catch (err) {
    toast(err);
  } finally {
    creating.value = false;
  }
}

async function send() {
  const text = draft.value.trim();
  if (!text || !sessionId.value || sending.value) return;
  sending.value = true;
  messages.value.push({ key: `u-${Date.now()}`, role: "user", content: text });
  draft.value = "";
  await scrollToBottom();
  try {
    if (streamMode.value) {
      await sendStreaming(text);
    } else {
      const data = await sendMessage(sessionId.value, text);
      messages.value.push({
        key: data.message_id,
        role: "ai",
        content: data.reply,
        intent: data.intent,
        status: data.status,
        state: data.state,
        traceId: data.trace_id,
      });
    }
  } catch (err) {
    toast(err);
  } finally {
    sending.value = false;
    await scrollToBottom();
  }
}

async function sendStreaming(text: string) {
  // 先放一个空 AI 气泡，delta 渐进填充；done 事件补决策标签并换正式 message_id
  const bubble: Bubble = { key: `s-${Date.now()}`, role: "ai", content: "" };
  messages.value.push(bubble);
  await sendMessageStream(sessionId.value, text, {
    onDelta: (piece) => {
      bubble.content += piece;
      void scrollToBottom();
    },
    onDone: (data) => {
      bubble.key = data.message_id;
      bubble.content = data.reply;
      bubble.intent = data.intent;
      bubble.status = data.status;
      bubble.state = data.state;
      bubble.traceId = data.trace_id;
    },
    onError: (code, message) => {
      bubble.content = bubble.content || `[${code}] ${message}`;
      ElMessage.error(`${code}: ${message}`);
    },
  });
}

async function vote(msg: Bubble, rating: "up" | "down") {
  try {
    await submitFeedback(sessionId.value, msg.key, rating);
    msg.voted = rating;
    ElMessage.success(rating === "up" ? "已点赞" : "已记录，感谢反馈");
  } catch (err) {
    toast(err);
  }
}

async function loadHistory() {
  loadingHistory.value = true;
  try {
    const data = await listMessages(sessionId.value);
    // 接口按 created_at 倒序返回，气泡流按时间正序展示
    messages.value = [...data.messages].reverse().map((m) => ({
      key: m.message_id,
      role: m.role === "user" ? "user" : "ai",
      content: m.content,
      intent: m.intent,
      status: m.status,
    }));
    await scrollToBottom();
  } catch (err) {
    toast(err);
  } finally {
    loadingHistory.value = false;
  }
}
</script>

<style scoped>
.console {
  display: flex;
  gap: 12px;
  height: calc(100vh - 120px);
}
.side {
  width: 260px;
  flex-shrink: 0;
  overflow-y: auto;
}
.chat {
  flex: 1;
  display: flex;
  flex-direction: column;
}
.chat :deep(.chat-body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 8px 4px;
}
.row {
  display: flex;
  margin-bottom: 10px;
}
.row.user {
  justify-content: flex-end;
}
.bubble {
  max-width: 72%;
  padding: 10px 14px;
  border-radius: 10px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.bubble.user {
  background: #409eff;
  color: #fff;
}
.bubble.ai {
  background: #f0f2f5;
  color: #303133;
}
.bubble.agent {
  background: #e7f6ec;
  color: #303133;
  border: 1px solid #b3e0c2;
}
.row.system {
  justify-content: center;
}
.bubble.system {
  background: #fdf6ec;
  color: #8a6d3b;
  font-size: 12px;
  max-width: 88%;
}
.bubble-label {
  font-size: 11px;
  color: #67c23a;
  margin-bottom: 2px;
}
.bubble.system .bubble-label {
  color: #e6a23c;
}
.tags {
  margin-top: 6px;
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.composer {
  display: flex;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid #e4e7ed;
}
.composer-side {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.ws-status {
  display: flex;
  align-items: center;
  gap: 4px;
}
.w-full {
  width: 100%;
}
.field-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 6px;
}
.session-id {
  word-break: break-all;
}
.quick-fills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.quick-tag {
  cursor: pointer;
}
</style>
