<template>
  <el-card shadow="never">
    <template #header>
      <div class="toolbar">
        <span>FAQ 精确层</span>
        <span class="actions">
          <el-button size="small" @click="reload">刷新</el-button>
          <el-button size="small" type="primary" @click="openEdit()">新建 FAQ</el-button>
        </span>
      </div>
    </template>
    <el-table :data="faqs" size="small">
      <el-table-column prop="question" label="标准问题" min-width="220" show-overflow-tooltip />
      <el-table-column prop="answer" label="标准答案" min-width="280" show-overflow-tooltip />
      <el-table-column prop="category" label="分类" width="110" />
      <el-table-column prop="hit_count" label="命中数" width="80" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="80" fixed="right">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="openEdit(row)">编辑</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!faqs.length" description="暂无 FAQ" />

    <el-dialog v-model="editOpen" :title="editing.faq_id ? '编辑 FAQ' : '新建 FAQ'" width="560px">
      <el-input v-model="editing.question" placeholder="标准问题" class="mb-8" />
      <el-input v-model="editing.answer" type="textarea" :rows="5" placeholder="标准答案" class="mb-8" />
      <el-input v-model="editing.category" placeholder="分类（选填）" />
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

import { listFaqs, upsertFaq, type FaqItem } from "@/api/kb";

const faqs = ref<FaqItem[]>([]);
const editOpen = ref(false);
const saving = ref(false);
const editing = reactive({ faq_id: "", question: "", answer: "", category: "" });

async function reload() {
  try {
    faqs.value = (await listFaqs({})).faqs;
  } catch (err) {
    ElMessage.error(String(err));
  }
}

function openEdit(row?: FaqItem) {
  editing.faq_id = row?.faq_id ?? "";
  editing.question = row?.question ?? "";
  editing.answer = row?.answer ?? "";
  editing.category = row?.category ?? "";
  editOpen.value = true;
}

async function save() {
  if (!editing.question.trim() || !editing.answer.trim()) {
    ElMessage.warning("问题与答案必填");
    return;
  }
  saving.value = true;
  try {
    await upsertFaq({
      question: editing.question,
      answer: editing.answer,
      category: editing.category || undefined,
      faq_id: editing.faq_id || undefined,
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
.mb-8 {
  margin-bottom: 8px;
}
</style>
