/**
 * 观测查询 API（Stage 29 批 1，对齐 app/api/routes/observe.py）。
 * 全部 admin scope：开发模式需登录页配置管理令牌（X-KB-Admin-Token）。
 */
import { get } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

export interface SessionItem {
  session_id: string;
  user_id: string;
  channel: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ObserveMessage {
  message_id: string;
  role: string;
  content: string;
  intent: string | null;
  status: string | null;
  trace_id: string | null;
  created_at: string;
}

export interface DecisionItem {
  decision_id: string;
  message_id: string | null;
  created_at: string;
  original_text: string;
  normalized_text: string | null;
  intent_result: Record<string, unknown> | null;
  slots: Record<string, unknown> | null;
  selected_skill: string | null;
  status: string | null;
  decision_source: string | null;
  graph_trace: Record<string, unknown> | null;
  retrieval: Record<string, unknown> | null;
  experiment: Record<string, unknown> | null;
  latency: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
}

export interface ToolCallItem {
  call_id: string;
  task_id: string | null;
  tool_id: string;
  ok: boolean;
  error_code: string | null;
  latency_ms: number | null;
  request: Record<string, unknown> | null;
  response: Record<string, unknown> | null;
  created_at: string;
}

function withTenant(params: URLSearchParams): URLSearchParams {
  params.set("tenant_id", useAuthStore().tenantId);
  return params;
}

export function listSessions(opts: {
  userId?: string;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<{ sessions: SessionItem[]; has_more: boolean }> {
  const params = withTenant(new URLSearchParams());
  if (opts.userId) params.set("user_id", opts.userId);
  if (opts.status) params.set("status", opts.status);
  params.set("limit", String(opts.limit ?? 20));
  params.set("offset", String(opts.offset ?? 0));
  return get(`/api/observe/sessions?${params.toString()}`);
}

export function sessionMessages(sessionId: string): Promise<{ messages: ObserveMessage[] }> {
  return get(`/api/observe/sessions/${sessionId}/messages?${withTenant(new URLSearchParams())}`);
}

export function sessionDecisions(sessionId: string): Promise<{ decisions: DecisionItem[] }> {
  return get(`/api/observe/sessions/${sessionId}/decisions?${withTenant(new URLSearchParams())}`);
}

export function sessionToolCalls(sessionId: string): Promise<{ tool_calls: ToolCallItem[] }> {
  return get(`/api/observe/sessions/${sessionId}/tool-calls?${withTenant(new URLSearchParams())}`);
}
