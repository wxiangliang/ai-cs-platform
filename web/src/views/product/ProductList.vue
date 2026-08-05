<template>
  <el-card shadow="never">
    <template #header>
      <div class="toolbar">
        <span>商品库
          <el-text size="small" type="info">（价格/库存唯一事实源，链路红线禁走 RAG）</el-text>
        </span>
        <span class="actions">
          <el-input v-model="keyword" placeholder="名称/编码搜索" size="small" clearable class="w-180" @keyup.enter="reload" />
          <el-button size="small" @click="reload">查询</el-button>
          <el-button size="small" type="primary" @click="openEdit()">新建商品</el-button>
        </span>
      </div>
    </template>
    <el-table :data="items" size="small">
      <el-table-column prop="product_code" label="编码" width="120" />
      <el-table-column prop="name" label="名称" min-width="200" show-overflow-tooltip />
      <el-table-column prop="category" label="分类" width="110" />
      <el-table-column label="价格" width="110">
        <template #default="{ row }">
          {{ row.price_cents == null ? "—" : `¥${(row.price_cents / 100).toFixed(2)}` }}
        </template>
      </el-table-column>
      <el-table-column prop="stock" label="库存" width="80" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="updated_at" label="更新时间" min-width="165" />
      <el-table-column label="操作" width="80" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="openEdit(row)">编辑</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!items.length" description="暂无商品" />

    <el-dialog v-model="editOpen" :title="editing.product_code ? '编辑商品' : '新建商品'" width="520px">
      <el-form label-width="90px">
        <el-form-item label="名称" required><el-input v-model="editing.name" /></el-form-item>
        <el-form-item label="编码"><el-input v-model="editing.product_code" placeholder="有编码才支持更新" /></el-form-item>
        <el-form-item label="分类"><el-input v-model="editing.category" /></el-form-item>
        <el-form-item label="价格(元)"><el-input-number v-model="editing.price_yuan" :min="0" :precision="2" /></el-form-item>
        <el-form-item label="库存"><el-input-number v-model="editing.stock" :min="0" /></el-form-item>
        <el-form-item label="简介"><el-input v-model="editing.description" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";

import { listProducts, upsertProduct, type ProductItem } from "@/api/product";

const items = ref<ProductItem[]>([]);
const keyword = ref("");
const editOpen = ref(false);
const saving = ref(false);
const editing = reactive({
  name: "",
  product_code: "",
  category: "",
  price_yuan: 0,
  stock: 0,
  description: "",
});

async function reload() {
  try {
    items.value = (await listProducts({ keyword: keyword.value || undefined })).items;
  } catch (err) {
    ElMessage.error(String(err));
  }
}

function openEdit(row?: ProductItem) {
  editing.name = row?.name ?? "";
  editing.product_code = row?.product_code ?? "";
  editing.category = row?.category ?? "";
  editing.price_yuan = row?.price_cents != null ? row.price_cents / 100 : 0;
  editing.stock = row?.stock ?? 0;
  editing.description = row?.description ?? "";
  editOpen.value = true;
}

async function save() {
  if (!editing.name.trim()) {
    ElMessage.warning("名称必填");
    return;
  }
  saving.value = true;
  try {
    await upsertProduct({
      name: editing.name,
      product_code: editing.product_code || undefined,
      category: editing.category || undefined,
      price_cents: Math.round(editing.price_yuan * 100),
      stock: editing.stock,
      description: editing.description || undefined,
    });
    ElMessage.success("已保存");
    editOpen.value = false;
    await reload();
  } catch (err) {
    ElMessage.error(String(err));
  } finally {
    saving.value = false;
  }
}

onMounted(reload);
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.actions {
  display: flex;
  gap: 8px;
}
.w-180 {
  width: 180px;
}
</style>
