# Web 测试控制台：部署与运行文档（Stage 28）

> 前端代码在 `web/`，需求见 `docs/requirements/stage-28-web-console/`。
> 架构一句话：**纯静态前端 + 同源 `/api` 反代到后端**；开发用 Vite 代理，
> 生产用 Nginx（或任意网关）。WebSocket 只是 `/api` 下的一条升级路径，随反代走。

---

## 1. 本地开发运行

依赖：Node 16+（Vite 4 兼容 16；Vite 5 需 18+，升级 Node 后可换）、后端可运行。

```bash
# ① 基础设施 + 后端（仓库根目录）
docker compose up -d                       # PG + Redis（--profile kb 加 Milvus）
uv run uvicorn app.main:app --reload       # 后端 :8000

# ② 前端（web/ 目录）
cd web
npm install                                # 首次
npm run dev                                # http://localhost:5173

# 后端不在本机 :8000 时
VITE_API_TARGET=http://192.168.1.10:8000 npm run dev
```

登录页填写：

| 字段 | 开发模式（后端默认 AUTH_ENABLED=false） | 鉴权模式 |
|---|---|---|
| 租户 ID | 必填，如 `t1` | 填写但后端以凭证解析为准 |
| 用户 ID | 必填，任意测试标识 | 必填 |
| API Key | **留空** | `ak_xxx.sk_yyy`（`scripts/manage_api_keys.py` 生成，chat scope） |

## 2. WebSocket 实时通道

**角色**：bot 回复走 HTTP；WS 只收服务端**主动推送**——坐席实时回复
（`agent_reply`）、会话归还（`session_resumed`）、主动消息（`proactive`）。
协议见 `docs/api/chat_api.md` 第 8 节。

- 对话控制台建会话后自动连接（`WS /api/chat/sessions/{id}/ws`），
  顶部标签显示状态：已连接 / 未连接（可手动重连）/ 不可用；
- 断线即弃无离线队列（Stage 15 语义）：重连后点「加载历史消息」补齐；
- **已知边界**：浏览器 WS 无法携带 `Authorization` 头，因此**鉴权模式下
  浏览器端 WS 不可用**（后端该通道按服务端对服务端设计），控制台显示
  「不可用（鉴权模式）」并正常降级——HTTP 功能不受影响。未来若需要，
  方案是后端加短时 WS 票据接口（新 Stage，不改现有鉴权模型）。

推送联调（开发模式触发一条主动消息，WS 面板应实时出现系统气泡）：

```bash
# 后端启动时需配 KB_ADMIN_TOKEN（管理面口径，Stage 13：空 token 不放行）
curl -X POST http://localhost:8000/api/chat/sessions/<session_id>/notify \
  -H "Content-Type: application/json" -H "X-KB-Admin-Token: <token>" \
  -d '{"tenant_id":"t1","content":"您的包裹今日派送","category":"logistics_alert"}'
```

## 3. 生产构建与部署

```bash
cd web && npm run build        # vue-tsc 类型检查 + 打包 → web/dist/
```

**推荐形态：同源反代**（零 CORS、WS 自然透传）。Nginx 示例：

```nginx
server {
    listen 80;
    server_name console.example.com;

    # 前端静态资源
    root /opt/ai-cs/web/dist;
    index index.html;
    location / {
        try_files $uri $uri/ /index.html;   # hash 路由其实用不到回退，保险起见
    }

    # /api 反代到后端（含 WebSocket 升级）
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # WS 升级三件套（用户端实时通道必需）
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;            # WS 长连接空闲超时
    }
}
```

**备选形态：跨域直连**（前端静态托管在别的域）：后端 `.env` 配
`CORS_ORIGINS=["https://console.example.com"]`；此时前端需把 fetch 的
相对路径换成绝对地址（v1 未做该配置项，跨域部署前先提需求）。

与后端容器编排（Stage 24 `docker-compose.prod.yml`）的关系：前端是纯静态
产物，不进后端镜像；由网关层（Nginx/K8s Ingress）同时挂静态目录与 /api
反代即可，无需新增服务容器。

## 4. 故障排查

| 现象 | 原因与处置 |
|---|---|
| 登录提示「后端连接失败」 | 后端未启动或代理目标不对：确认 :8000 可 curl `/api/health`；非本机后端用 `VITE_API_TARGET` |
| 请求全部 401/403 并被踢回登录页 | Key 无效/已吊销/scope 不对（chat 面需 chat scope）；开发模式确认后端 `AUTH_ENABLED=false` |
| WS 标签一直「未连接」 | 看后端日志：4403=开发模式缺 query 凭证（正常不会发生）；4404=会话不存在或租户/用户与会话不匹配（换了登录身份后旧会话连不上，属预期） |
| WS 标签「不可用（鉴权模式）」 | 已知边界（见第 2 节），非故障 |
| notify 返回 403 | 后端未配 `KB_ADMIN_TOKEN` 或头不对（`X-KB-Admin-Token`） |
| `npm run build` 报 vue-tsc 崩溃 | TypeScript 版本漂移：package.json 已钉 `~5.4.5`（vue-tsc 1.8 不兼容 TS 5.5+），勿手动升级 |
| 构建产物 chunk >500KB 告警 | Element Plus 全量引入所致，测试控制台可接受；要优化走按需引入（后续） |

## 5. 版本与升级备忘

- Node 16 → 18+ 后可升 Vite 5 / vue-tsc 2 / TS 最新（一起升，别单升 TS）；
- 新增页面 = `web/src/router/index.ts` 加一条路由记录（菜单自动生成）+
  views 下加组件；API 层新增文件对齐后端 `docs/api/` 契约；
- 前端构建暂未进 CI（stage-28 遗留），改动后本地 `npm run build` 自查。
