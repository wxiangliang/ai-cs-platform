# AI 客服 Web 测试控制台（Stage 28）

Vue 3 + Vite 4 + TypeScript + Pinia + Element Plus。
需求文档：`docs/requirements/stage-28-web-console/`。

## 快速开始

```bash
# 1. 起后端（仓库根目录；需 PG+Redis：docker compose up -d）
uv run uvicorn app.main:app --reload

# 2. 起前端（本目录；Node 16+，Vite 4 兼容 Node 16）
npm install
npm run dev            # http://localhost:5173，/api 经代理转发到 :8000
# 后端不在本机 8000 时：VITE_API_TARGET=http://host:port npm run dev

# 3. 登录页填：租户 ID（如 t1）+ 用户 ID；API Key 开发模式留空
#   （后端 AUTH_ENABLED=true 时填 ak_xxx.sk_yyy，scope 见 Stage 08）
```

## 构建

```bash
npm run build          # vue-tsc 类型检查 + vite 打包 → dist/
```

生产部署：dist/ 静态托管，同域反代 /api 到后端；跨域部署需后端配 `CORS_ORIGINS`。

## 结构

```text
src/
  api/        client.ts（envelope 解析/凭证注入/401 登出）+ chat.ts
  stores/     auth.ts（连接凭证，localStorage 持久化）
  router/     多级菜单由路由 meta 驱动：加页面 = 加一条路由记录
  layouts/    MainLayout.vue（左侧折叠多级菜单 + 顶栏 + 内容区）
  views/      LoginView / chat/ChatConsole（对话+决策标签）/ PlaceholderView
```

占位页（坐席工作台/知识库/商品库）按后续 Stage 逐页实化，见 stage-28 文档第 6 节。
