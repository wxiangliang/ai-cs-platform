/**
 * API 客户端（Stage 28）：统一 envelope 解析 + 凭证注入 + 401 处理。
 *
 * 后端统一响应结构 {code, message, data}（app/core/responses.py）：
 * - code === "OK" → 返回 data；
 * - 401/403 → 清凭证跳登录页；
 * - 其余 → 抛 ApiError（页面层 ElMessage 提示）。
 * 开发走 Vite 代理（相对路径 /api），生产同域反代或 CORS_ORIGINS。
 */
import { useAuthStore } from "@/stores/auth";
import router from "@/router";

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

interface Envelope<T> {
  code: string;
  message: string;
  data: T;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const auth = useAuthStore();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (auth.apiKey) headers["Authorization"] = `Bearer ${auth.apiKey}`;

  const resp = await fetch(path, { ...init, headers });

  if (resp.status === 401 || resp.status === 403) {
    auth.logout();
    router.push({ name: "login" });
    throw new ApiError("UNAUTHORIZED", "凭证无效或已吊销，请重新登录", resp.status);
  }

  let body: Envelope<T>;
  try {
    body = (await resp.json()) as Envelope<T>;
  } catch {
    throw new ApiError("BAD_RESPONSE", `响应解析失败（HTTP ${resp.status}）`, resp.status);
  }
  if (!resp.ok || body.code !== "OK") {
    throw new ApiError(body.code || String(resp.status), body.message || "请求失败", resp.status);
  }
  return body.data;
}

export function get<T>(path: string): Promise<T> {
  return request<T>(path, { method: "GET" });
}

export function post<T>(path: string, payload: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(payload) });
}

/** 连通性检查（登录页用，health 无需鉴权、无 envelope 的 data 也兼容） */
export async function checkHealth(): Promise<boolean> {
  try {
    const resp = await fetch("/api/health");
    return resp.ok;
  } catch {
    return false;
  }
}
