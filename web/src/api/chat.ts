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
