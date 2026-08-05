/**
 * 聊天 API（对齐 app/api/routes/chat.py 与 app/schemas/chat.py）。
 * tenant_id 仅开发模式生效（鉴权开启后后端从凭证解析、忽略体内值）。
 */
import { get, post } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

export interface SessionData {
  session_id: string;
  tenant_id: string;
  user_id: string;
  channel: string;
  status: string;
}

export interface ChatReply {
  message_id: string;
  session_id: string;
  reply: string;
  intent: string | null;
  status: string | null;
  state: string | null;
  trace_id: string | null;
}

export interface HistoryMessage {
  message_id: string;
  role: string;
  content: string;
  intent: string | null;
  status: string | null;
  created_at: string;
  metadata: Record<string, unknown> | null;
}

export function createSession(): Promise<SessionData> {
  const auth = useAuthStore();
  return post<SessionData>("/api/chat/sessions", {
    tenant_id: auth.tenantId,
    user_id: auth.userId,
    channel: "web",
  });
}

export function sendMessage(sessionId: string, message: string): Promise<ChatReply> {
  const auth = useAuthStore();
  return post<ChatReply>(`/api/chat/sessions/${sessionId}/messages`, {
    tenant_id: auth.tenantId,
    user_id: auth.userId,
    message,
    channel: "web",
  });
}

/**
 * SSE 流式发消息（chat_api.md 第 7 节）：delta 渐进渲染、以 done 为准落最终结果。
 * 决策链路完成后分片下发（意图/确认门/工具必须先跑完，Stage 09 语义）。
 */
export async function sendMessageStream(
  sessionId: string,
  message: string,
  handlers: {
    onDelta: (text: string) => void;
    onDone: (data: ChatReply) => void;
    onError: (code: string, message: string) => void;
  },
): Promise<void> {
  const auth = useAuthStore();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (auth.apiKey) headers["Authorization"] = `Bearer ${auth.apiKey}`;
  const resp = await fetch(`/api/chat/sessions/${sessionId}/messages/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      tenant_id: auth.tenantId,
      user_id: auth.userId,
      message,
      channel: "web",
    }),
  });
  if (!resp.ok || !resp.body) {
    handlers.onError(String(resp.status), "流式请求失败");
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE 事件以空行分隔；逐块解析 event/data 行
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = "";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!event || !data) continue;
      try {
        const payload = JSON.parse(data);
        if (event === "delta") handlers.onDelta(payload.text ?? "");
        else if (event === "done") handlers.onDone(payload as ChatReply);
        else if (event === "error") handlers.onError(payload.code ?? "ERROR", payload.message ?? "");
      } catch {
        /* 数据块解析失败忽略该事件 */
      }
    }
  }
}

export function submitFeedback(
  sessionId: string,
  messageId: string,
  rating: "up" | "down",
): Promise<unknown> {
  const auth = useAuthStore();
  return post(`/api/chat/sessions/${sessionId}/feedback`, {
    tenant_id: auth.tenantId,
    user_id: auth.userId,
    message_id: messageId,
    rating,
  });
}

export function listMessages(
  sessionId: string,
  opts: { limit?: number; before?: string } = {},
): Promise<{ messages: HistoryMessage[] }> {
  const auth = useAuthStore();
  const params = new URLSearchParams({
    tenant_id: auth.tenantId,
    user_id: auth.userId,
    limit: String(opts.limit ?? 50),
  });
  if (opts.before) params.set("before", opts.before);
  return get<{ messages: HistoryMessage[] }>(
    `/api/chat/sessions/${sessionId}/messages?${params.toString()}`,
  );
}
