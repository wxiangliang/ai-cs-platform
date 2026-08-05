# Stage 28：Web 测试控制台（前端地基）

## 1. 阶段目标

后台功能（聊天链路/坐席/知识库/商品/观测）已成型，缺一个前端把它们串起来
做人工测试与演示。本阶段搭**前端地基 + 第一个完整功能页**：

- 技术栈（2026-08-05 用户选型确认）：**Vue 3 + Vite 4**（本机 Node 16，
  Vite 5 需 18+）+ TypeScript + Pinia + Vue Router + **Element Plus**；
- 代码位于**同仓库 `web/`**（测试控制台性质，与后端一起提交/联调）；
- 登录用 **API Key**（与后端鉴权模型一致：`Bearer ak_xxx.sk_yyy`，
  chat/admin scope；开发模式 `AUTH_ENABLED=false` 时 Key 留空直连）——
  **不新建用户名密码体系**（未来真要账号体系是独立后端 Stage）。

## 2. 本阶段要做什么

```text
1. 脚手架：web/（Vite4+Vue3+TS+Pinia+Router+Element Plus），
   开发经 Vite 代理 /api → 后端（零 CORS 依赖；生产另配 CORS_ORIGINS）；
2. 登录页：租户 ID / 用户 ID / API Key（可空=开发模式），
   /api/health 连通性校验后入主界面；凭证存 localStorage，
   401/403 全局拦截清凭证回登录页；
3. 主布局：左侧多级折叠菜单（el-menu）+ 顶栏（连接信息/退出）+ 右侧路由内容；
4. 第一个功能页「对话控制台」：创建会话 → 发消息 → 气泡式对话流，
   AI 回复附决策标签（intent/status/state/trace_id——测试台核心价值：
   一眼看到链路决策）；历史分页加载；
5. 其余菜单挂占位页（坐席工作台/知识库/商品库），后续 Stage 逐页实化。
```

## 3. 本阶段不做什么

```text
1. 不做用户名密码/JWT（API Key 即登录，后端零改动）；
2. 不做 SSE 流式渲染 / WS 实时（对话控制台 v1 用普通接口，流式下一批）；
3. 不做坐席工作台/知识库运营页的实体功能（占位页 + 路由留位）；
4. 前端不进后端 CI 门禁（web/ 独立 npm 工作流，后续再议接入）；
5. 不做多语言 UI（控制台中文即可；后端 locale 参数在发消息面板透出）。
```

## 4. 技术要点

- 统一响应 envelope `{code, message, data}` 在 api/client.ts 收口解析，
  `code != "OK"` 抛业务错误（ElMessage 提示）；
- 凭证注入：有 Key 时带 `Authorization: Bearer <key>`；开发模式请求体带
  tenant_id（后端 `resolve_tenant_id` 语义：鉴权开启后忽略体内租户）；
- 菜单为多级结构声明式配置（router meta 驱动，加页面 = 加一条路由记录）。

## 5. 验收

1. `npm run build` 通过（vue-tsc 类型检查 + vite 构建）；
2. 开发模式（后端 AUTH_ENABLED=false）：登录 → 建会话 → 发「我要退款」
   → 看到追问回复与 intent/status 标签 → 补订单号 → 走到确认门；
3. 401 时自动登出回登录页；
4. 后端零改动、零回归。

## 6. 遗留（排队给后续 Stage）

1. ~~WS 实时通道~~ ✅ 已实现（2026-08-05）：建会话自动连
   `WS /api/chat/sessions/{id}/ws`（Vite 代理 ws:true），坐席回复/会话
   归还/主动消息实时渲染为坐席/系统气泡；状态标签+手动重连；
   **已知边界**：浏览器 WS 无法带 Bearer 头，鉴权模式下不可用（如实
   降级显示，未来方案=后端短时 WS 票据，新 Stage）。e2e 冒烟：代理连
   WS + notify 触发 proactive 实收。SSE 流式仍留；
2. 坐席工作台实体页（Stage 15 遗留的前端部分）；
3. 知识库运营页（Stage 16 遗留）、商品管理页；
4. web/ 构建接入 CI；
5. ~~部署文档~~ ✅ `docs/ops/web_console_deploy.md`（开发运行/Nginx 同域
   反代含 WS 升级配置/跨域备选/故障排查表/版本升级备忘）。
