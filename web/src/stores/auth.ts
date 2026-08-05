/**
 * 连接凭证 store（Stage 28）。
 *
 * 登录 = 保存连接信息（与后端 API Key 鉴权模型一致，不存在用户名密码）：
 * - apiKey 为空 → 开发模式直连（后端 AUTH_ENABLED=false），请求体带 tenant_id；
 * - apiKey 非空 → Authorization: Bearer <key>，租户由后端从凭证解析。
 * 凭证持久化 localStorage；401/403 由 api/client 统一清除并回登录页。
 */
import { defineStore } from "pinia";

const STORAGE_KEY = "ai-cs-console.credentials";

interface Credentials {
  tenantId: string;
  userId: string;
  apiKey: string;
}

function load(): Credentials {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { tenantId: "", userId: "", apiKey: "", ...JSON.parse(raw) };
  } catch {
    /* 损坏即忽略 */
  }
  return { tenantId: "", userId: "", apiKey: "" };
}

export const useAuthStore = defineStore("auth", {
  state: (): Credentials & { connected: boolean } => ({
    ...load(),
    connected: false,
  }),
  getters: {
    /** 是否已「登录」（有租户与用户标识即可进入主界面） */
    loggedIn: (s) => Boolean(s.tenantId && s.userId),
    /** 脱敏展示的 Key（顶栏用） */
    maskedKey: (s) =>
      s.apiKey ? `${s.apiKey.slice(0, 10)}...${s.apiKey.slice(-4)}` : "（开发模式，未配置）",
  },
  actions: {
    save(cred: Credentials) {
      this.tenantId = cred.tenantId.trim();
      this.userId = cred.userId.trim();
      this.apiKey = cred.apiKey.trim();
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ tenantId: this.tenantId, userId: this.userId, apiKey: this.apiKey }),
      );
    },
    logout() {
      this.tenantId = "";
      this.userId = "";
      this.apiKey = "";
      this.connected = false;
      localStorage.removeItem(STORAGE_KEY);
    },
  },
});
