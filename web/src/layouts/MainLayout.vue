<template>
  <el-container class="layout">
    <!-- 左侧多级菜单（路由 meta 驱动，加页面 = 加路由记录） -->
    <el-aside :width="collapsed ? '64px' : '220px'" class="aside">
      <div class="brand" @click="collapsed = !collapsed">
        <el-icon><Monitor /></el-icon>
        <span v-if="!collapsed" class="brand-text">AI 客服控制台</span>
      </div>
      <el-menu
        :default-active="route.path"
        :collapse="collapsed"
        router
        background-color="#1f2d3d"
        text-color="#c0c4cc"
        active-text-color="#409eff"
      >
        <template v-for="group in menuRoutes" :key="group.path">
          <!-- 多子项 → 折叠子菜单；单子项 → 直接菜单项 -->
          <el-sub-menu v-if="(group.children?.length ?? 0) > 1" :index="group.path">
            <template #title>
              <el-icon><component :is="group.meta?.icon || 'Menu'" /></el-icon>
              <span>{{ group.meta?.title }}</span>
            </template>
            <el-menu-item
              v-for="child in group.children"
              :key="child.path"
              :index="`${group.path}/${child.path}`"
            >
              {{ child.meta?.title }}
            </el-menu-item>
          </el-sub-menu>
          <el-menu-item v-else :index="`${group.path}/${group.children![0].path}`">
            <el-icon><component :is="group.meta?.icon || 'Menu'" /></el-icon>
            <template #title>{{ group.children![0].meta?.title ?? group.meta?.title }}</template>
          </el-menu-item>
        </template>
      </el-menu>
    </el-aside>

    <el-container>
      <!-- 顶栏：面包屑 + 连接信息 + 退出 -->
      <el-header class="header">
        <el-breadcrumb separator="/">
          <el-breadcrumb-item v-for="item in breadcrumb" :key="item">{{ item }}</el-breadcrumb-item>
        </el-breadcrumb>
        <div class="header-right">
          <el-tag size="small" type="info">租户 {{ auth.tenantId }}</el-tag>
          <el-tag size="small" type="info">用户 {{ auth.userId }}</el-tag>
          <el-tooltip :content="`API Key: ${auth.maskedKey}`" placement="bottom">
            <el-tag size="small" :type="auth.apiKey ? 'success' : 'warning'">
              {{ auth.apiKey ? "已鉴权" : "开发模式" }}
            </el-tag>
          </el-tooltip>
          <el-button size="small" text @click="logout">退出</el-button>
        </div>
      </el-header>

      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { menuRoutes } from "@/router";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const collapsed = ref(false);

const breadcrumb = computed(() =>
  route.matched
    .map((r) => r.meta?.title as string | undefined)
    .filter((t): t is string => Boolean(t)),
);

function logout() {
  auth.logout();
  router.push({ name: "login" });
}
</script>

<style scoped>
.layout {
  height: 100%;
}
.aside {
  background-color: #1f2d3d;
  transition: width 0.2s;
  overflow-x: hidden;
}
.aside :deep(.el-menu) {
  border-right: none;
}
.brand {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #fff;
  cursor: pointer;
  user-select: none;
}
.brand-text {
  font-weight: 600;
  white-space: nowrap;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e4e7ed;
  background: #fff;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.main {
  background: #f5f7fa;
}
</style>
