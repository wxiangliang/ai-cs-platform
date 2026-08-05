<template>
  <el-card>
    <el-empty :description="description">
      <template #image>
        <el-icon :size="48" color="#c0c4cc"><Box /></el-icon>
      </template>
    </el-empty>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";

// 占位页：菜单先挂路由留位，后续 Stage 逐页实化（描述来自路由 meta.placeholder）
const route = useRoute();
const description = computed(
  () => (route.meta.placeholder as string) || "该功能页排队实化中",
);
</script>
