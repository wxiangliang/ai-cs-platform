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
    path: "/handoff",
    meta: { title: "人工坐席", icon: "Service" },
    children: [
      {
        path: "tickets",
        name: "handoff-tickets",
        component: () => import("@/views/PlaceholderView.vue"),
        meta: { title: "工单队列", placeholder: "坐席工作台（Stage 15 遗留前端，排队实化）" },
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
        component: () => import("@/views/PlaceholderView.vue"),
        meta: { title: "文档管理", placeholder: "知识库运营页（Stage 16 遗留前端，排队实化）" },
      },
      {
        path: "faqs",
        name: "kb-faqs",
        component: () => import("@/views/PlaceholderView.vue"),
        meta: { title: "FAQ 管理", placeholder: "FAQ 精确层管理（排队实化）" },
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
        component: () => import("@/views/PlaceholderView.vue"),
        meta: { title: "商品管理", placeholder: "商品库管理（排队实化）" },
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
