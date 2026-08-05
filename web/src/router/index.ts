/**
 * 路由（Stage 28）：/login + 主布局嵌套路由。
 * 菜单由路由 meta 驱动（MainLayout 读取生成多级 el-menu）——
 * 加页面 = 加一条路由记录，无需改布局代码。
 */
import { createRouter, createWebHashHistory, type RouteRecordRaw } from "vue-router";

import { useAuthStore } from "@/stores/auth";

export const menuRoutes: RouteRecordRaw[] = [
  {
    path: "/chat",
    meta: { title: "聊天测试", icon: "ChatDotRound" },
    children: [
      {
        path: "console",
        name: "chat-console",
        component: () => import("@/views/chat/ChatConsole.vue"),
        meta: { title: "对话控制台" },
      },
    ],
  },
  {
    path: "/observe",
    meta: { title: "观测分析", icon: "DataAnalysis" },
    children: [
      {
        path: "sessions",
        name: "observe-sessions",
        component: () => import("@/views/observe/SessionExplorer.vue"),
        meta: { title: "会话记录" },
      },
      {
        path: "system",
        name: "observe-system",
        component: () => import("@/views/observe/SystemStatus.vue"),
        meta: { title: "系统状态" },
      },
    ],
  },
  {
    path: "/handoff",
    meta: { title: "人工坐席", icon: "Service" },
    children: [
      {
        path: "tickets",
        name: "handoff-tickets",
        component: () => import("@/views/handoff/TicketWorkbench.vue"),
        meta: { title: "坐席工作台" },
      },
    ],
  },
  {
    path: "/kb",
    meta: { title: "知识库", icon: "Collection" },
    children: [
      {
        path: "documents",
        name: "kb-documents",
        component: () => import("@/views/kb/KbDocuments.vue"),
        meta: { title: "文档管理" },
      },
      {
        path: "faqs",
        name: "kb-faqs",
        component: () => import("@/views/kb/KbFaqs.vue"),
        meta: { title: "FAQ 管理" },
      },
    ],
  },
  {
    path: "/products",
    meta: { title: "商品库", icon: "Goods" },
    children: [
      {
        path: "list",
        name: "product-list",
        component: () => import("@/views/product/ProductList.vue"),
        meta: { title: "商品管理" },
      },
    ],
  },
];

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: "/login",
      name: "login",
      component: () => import("@/views/LoginView.vue"),
      meta: { public: true },
    },
    {
      path: "/",
      component: () => import("@/layouts/MainLayout.vue"),
      redirect: "/chat/console",
      children: menuRoutes,
    },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});

// 未登录一律回登录页
router.beforeEach((to) => {
  const auth = useAuthStore();
  if (!to.meta.public && !auth.loggedIn) return { name: "login" };
  if (to.name === "login" && auth.loggedIn) return { path: "/" };
  return true;
});

export default router;
