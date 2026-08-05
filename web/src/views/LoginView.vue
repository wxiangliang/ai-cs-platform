<template>
  <div class="login-page">
    <el-card class="login-card">
      <template #header>
        <div class="login-title">AI 客服测试控制台</div>
      </template>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="租户 ID" required>
          <el-input v-model="form.tenantId" placeholder="如 t1（开发模式必填）" />
        </el-form-item>
        <el-form-item label="用户 ID" required>
          <el-input v-model="form.userId" placeholder="测试用户标识，如 tester-1" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="form.apiKey"
            type="password"
            show-password
            placeholder="ak_xxx.sk_yyy；后端开发模式（AUTH_ENABLED=false）可留空"
          />
        </el-form-item>
        <el-button
          type="primary"
          class="login-btn"
          :loading="connecting"
          native-type="submit"
          @click="connect"
        >
          连接并进入
        </el-button>
        <div class="login-tip">
          登录即保存连接凭证（无用户名密码体系，与后端 API Key 鉴权模型一致）；
          开发环境请求经 Vite 代理转发到后端 /api。
        </div>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRouter } from "vue-router";

import { checkHealth } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const router = useRouter();
const connecting = ref(false);
const form = reactive({ tenantId: auth.tenantId || "t1", userId: auth.userId, apiKey: auth.apiKey });

async function connect() {
  if (!form.tenantId.trim() || !form.userId.trim()) {
    ElMessage.warning("请填写租户 ID 与用户 ID");
    return;
  }
  connecting.value = true;
  try {
    const ok = await checkHealth();
    if (!ok) {
      ElMessage.error("后端连接失败：请确认服务已启动（uv run uvicorn app.main:app）");
      return;
    }
    auth.save({ tenantId: form.tenantId, userId: form.userId, apiKey: form.apiKey });
    auth.connected = true;
    ElMessage.success("已连接");
    router.push("/");
  } finally {
    connecting.value = false;
  }
}
</script>

<style scoped>
.login-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1f2d3d 0%, #2b3a4d 100%);
}
.login-card {
  width: 400px;
}
.login-title {
  font-size: 18px;
  font-weight: 600;
  text-align: center;
}
.login-btn {
  width: 100%;
}
.login-tip {
  margin-top: 12px;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}
</style>
