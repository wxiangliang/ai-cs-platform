/**
 * 知识库运营 API（对齐 app/api/routes/kb.py，admin scope）。
 * 生效判据（Stage 16）：published_version 非空且未 archived 才在线上服务。
 */
import { get, post } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

export interface KbDocument {
  document_id: string;
  title: string;
  status: string;
  source_type: string;
  published_version: number | null;
  needs_reindex: boolean;
  effective_from: string | null;
  expire_at: string | null;
  updated_at: string;
}

export interface KbVersion {
  version: number;
  title: string;
  editor: string | null;
  note: string | null;
  created_at: string;
}

export interface FaqItem {
  faq_id: string;
  question: string;
  answer: string;
  category: string | null;
  status: string;
  hit_count: number;
  updated_at: string;
}

function tenant(): string {
  return useAuthStore().tenantId;
}

export function listDocuments(opts: {
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<{ documents: KbDocument[]; has_more: boolean }> {
  const params = new URLSearchParams({ tenant_id: tenant(), limit: String(opts.limit ?? 50) });
  if (opts.status) params.set("status", opts.status);
  params.set("offset", String(opts.offset ?? 0));
  return get(`/api/kb/documents?${params.toString()}`);
}

export function createDraft(payload: {
  title: string;
  content: string;
  source_type?: string;
  note?: string;
}): Promise<unknown> {
  return post("/api/kb/documents/draft", { tenant_id: tenant(), editor: useAuthStore().userId, ...payload });
}

export function publishDirect(payload: {
  title: string;
  content: string;
  source_type?: string;
  document_id?: string;
}): Promise<unknown> {
  // upsert = 直接发布并重建索引（Stage 06 老入口，运营快捷通道）
  return post("/api/kb/documents", { tenant_id: tenant(), ...payload });
}

function actorBody(note?: string) {
  return { tenant_id: tenant(), actor: useAuthStore().userId, note };
}

export function submitReview(id: string): Promise<unknown> {
  return post(`/api/kb/documents/${id}/submit`, actorBody());
}
export function approveDocument(id: string): Promise<unknown> {
  return post(`/api/kb/documents/${id}/approve`, actorBody());
}
export function rejectDocument(id: string, note: string): Promise<unknown> {
  return post(`/api/kb/documents/${id}/reject`, actorBody(note));
}
export function archiveDocument(id: string): Promise<unknown> {
  return post(`/api/kb/documents/${id}/archive`, actorBody());
}
export function rollbackDocument(id: string, version: number): Promise<unknown> {
  return post(`/api/kb/documents/${id}/rollback`, {
    tenant_id: tenant(),
    actor: useAuthStore().userId,
    version,
  });
}
export function listVersions(id: string): Promise<{ versions: KbVersion[] }> {
  return get(`/api/kb/documents/${id}/versions?tenant_id=${encodeURIComponent(tenant())}`);
}

export async function uploadDocument(file: File, title?: string): Promise<unknown> {
  const auth = useAuthStore();
  const form = new FormData();
  form.set("tenant_id", auth.tenantId);
  form.set("file", file);
  if (title) form.set("title", title);
  const headers: Record<string, string> = {};
  if (auth.apiKey) headers["Authorization"] = `Bearer ${auth.apiKey}`;
  if (auth.adminToken) headers["X-KB-Admin-Token"] = auth.adminToken;
  const resp = await fetch("/api/kb/documents/upload", { method: "POST", body: form, headers });
  const body = await resp.json();
  if (!resp.ok || body.code !== "OK") throw new Error(body.message || `上传失败（${resp.status}）`);
  return body.data;
}

export function listFaqs(opts: {
  limit?: number;
  offset?: number;
}): Promise<{ faqs: FaqItem[]; has_more: boolean }> {
  const params = new URLSearchParams({
    tenant_id: tenant(),
    limit: String(opts.limit ?? 50),
    offset: String(opts.offset ?? 0),
  });
  return get(`/api/kb/faqs?${params.toString()}`);
}

export function upsertFaq(payload: {
  question: string;
  answer: string;
  category?: string;
  faq_id?: string;
}): Promise<unknown> {
  return post("/api/kb/faqs", { tenant_id: tenant(), ...payload });
}
