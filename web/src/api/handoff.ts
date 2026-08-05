/**
 * 坐席工单 API（对齐 app/api/routes/handoff.py，全部 admin scope）。
 */
import { get, post } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

export interface TicketBrief {
  ticket_id: string;
  session_id: string;
  user_id: string;
  reason: string;
  source_intent: string | null;
  status: string;
  assignee: string | null;
  created_at: string | null;
  resolved_at: string | null;
}

export interface TicketDetail extends TicketBrief {
  context: Record<string, unknown>;
}

function tenant(): string {
  return useAuthStore().tenantId;
}

export function listTickets(opts: {
  status?: string;
  limit?: number;
  before?: string;
}): Promise<{ tickets: TicketBrief[]; next_before: string | null }> {
  const params = new URLSearchParams({ tenant_id: tenant(), limit: String(opts.limit ?? 20) });
  if (opts.status) params.set("status", opts.status);
  if (opts.before) params.set("before", opts.before);
  return get(`/api/handoff/tickets?${params.toString()}`);
}

export function getTicket(ticketId: string): Promise<TicketDetail> {
  return get(`/api/handoff/tickets/${ticketId}?tenant_id=${encodeURIComponent(tenant())}`);
}

export function claimTicket(ticketId: string, assignee: string): Promise<unknown> {
  return post(`/api/handoff/tickets/${ticketId}/claim`, { tenant_id: tenant(), assignee });
}

export function replyTicket(ticketId: string, content: string): Promise<{ message_id: string }> {
  return post(`/api/handoff/tickets/${ticketId}/reply`, { tenant_id: tenant(), content });
}

export function resolveTicket(ticketId: string): Promise<unknown> {
  return post(`/api/handoff/tickets/${ticketId}/resolve`, { tenant_id: tenant() });
}
