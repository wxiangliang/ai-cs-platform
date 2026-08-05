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
        对话控制台
        <el-text size="small" type="info">（AI 回复附链路决策标签，用于测试观察）</el-text>
      </template>
      <div ref="listEl" class="messages">
        <div v-for="msg in messages" :key="msg.key" class="row" :class="msg.role">
          <div class="bubble" :class="msg.role">
            <div class="content">{{ msg.content }}</div>
            <div v-if="msg.role === 'ai' && (msg.intent || msg.status)" class="tags">
              <el-tag v-if="msg.intent" size="small">{{ msg.intent }}</el-tag>
              <el-tag v-if="msg.status" size="small" type="warning">{{ msg.status }}</el-tag>
              <el-tag v-if="msg.state" size="small" type="info">{{ msg.state }}</el-tag>
              <el-tooltip v-if="msg.traceId" :content="`trace: ${msg.traceId}`">
                <el-tag size="small" type="success">trace</el-tag>
              </el-tooltip>
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
        <el-button type="primary" :loading="sending" :disabled="!sessionId" @click="send">
          发送
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { nextTick, ref } from "vue";
import { ElMessage } from "element-plus";

import { createSession, listMessages, sendMessage } from "@/api/chat";
import { ApiError } from "@/api/client";

interface Bubble {
  key: string;
  role: "user" | "ai";
  content: string;
  intent?: string | null;
  status?: string | null;
  state?: string | null;
  traceId?: string | null;
}

const sessionId = ref("");
const messages = ref<Bubble[]>([]);
const draft = ref("");
const creating = ref(false);
const sending = ref(false);
const loadingHistory = ref(false);
const listEl = ref<HTMLElement>();

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
  } catch (err) {
    toast(err);
  } finally {
    sending.value = false;
    await scrollToBottom();
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
