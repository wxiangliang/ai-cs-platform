/**
 * 用户端 WebSocket（Stage 15 协议，chat_api.md 第 8 节）。
 *
 * 角色澄清：bot 回复走 HTTP/SSE，本通道只收**服务端主动推送**——
 * 转人工后坐席实时回复（agent_reply）、会话归还（session_resumed）、
 * 主动消息（proactive）。服务端只推不收；断线即弃无离线队列，重连拉历史补齐。
 *
 * 鉴权边界（如实降级）：浏览器 WS 无法带 Authorization 头，
 * 开发模式走 query 参数（tenant_id/user_id）；API Key 鉴权开启时
 * 浏览器端 WS 不可用（后端为服务端对服务端设计）——调用方据
 * canUseWs() 展示「实时通道不可用」，不做无谓重试。
 */
import { useAuthStore } from "@/stores/auth";

export interface WsEvent {
  type: "agent_reply" | "session_resumed" | "proactive";
  content: string;
  message_id?: string;
  created_at?: string;
  ticket_id?: string;
  csat_message_id?: string;
  category?: string;
}

export type WsStatus = "connected" | "closed" | "unavailable";

/** 浏览器 WS 是否可用：仅开发模式（无 API Key）可连 */
export function canUseWs(): boolean {
  return !useAuthStore().apiKey;
}

export function openSessionWs(
  sessionId: string,
  handlers: {
    onEvent: (event: WsEvent) => void;
    onStatus: (status: WsStatus) => void;
  },
): WebSocket | null {
  if (!canUseWs()) {
    handlers.onStatus("unavailable");
    return null;
  }
  const auth = useAuthStore();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const params = new URLSearchParams({ tenant_id: auth.tenantId, user_id: auth.userId });
  const ws = new WebSocket(
    `${proto}://${location.host}/api/chat/sessions/${sessionId}/ws?${params.toString()}`,
  );
  ws.onopen = () => handlers.onStatus("connected");
  // 4403/4404 策略码与普通断开统一按 closed 处理（面板提供手动重连）
  ws.onclose = () => handlers.onStatus("closed");
  ws.onerror = () => handlers.onStatus("closed");
  ws.onmessage = (msg) => {
    try {
      const data = JSON.parse(msg.data as string) as WsEvent;
      if (data && data.type) handlers.onEvent(data);
    } catch {
      /* 非 JSON 推送忽略 */
    }
  };
  return ws;
}
