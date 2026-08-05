<template>
  <el-card shadow="never">
    <template #header>
      <div class="toolbar">
        <span>知识库文档
          <el-text size="small" type="info">（生效 = 已发布过且未下线，与 status 不完全等同）</el-text>
        </span>
        <span class="actions">
          <el-select v-model="filterStatus" placeholder="全部状态" clearable size="small" class="w-140" @change="reload">
            <el-option v-for="s in ['draft', 'pending_review', 'published', 'archived']" :key="s" :label="s" :value="s" />
          </el-select>
          <el-button size="small" @click="reload">刷新</el-button>
          <el-button size="small" type="primary" @click="draftOpen = true">新建草稿</el-button>
          <el-button size="small" @click="publishOpen = true">直接发布</el-button>
          <el-upload :show-file-list="false" :before-upload="doUpload" accept=".pdf,.docx,.xlsx,.md,.txt">
            <el-button size="small">上传文件</el-button>
          </el-upload>
        </span>
      </div>
    </template>

    <el-table :data="documents" size="small">
      <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
      <el-table-column label="状态" width="130">
        <template #default="{ row }">
          <el-tag size="small" :type="statusType(row.status)">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="线上版本" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.published_version" size="small" type="success">v{{ row.published_version }}</el-tag>
          <el-text v-else size="small" type="info">未发布</el-text>
        </template>
      </el-table-column>
      <el-table-column prop="source_type" label="类型" width="90" />
      <el-table-column prop="updated_at" label="更新时间" min-width="165" />
      <el-table-column label="操作" min-width="280" fixed="right">
        <template #default="{ row }">
          <el-button v-if="row.status === 'draft'" size="small" text type="primary" @click="act(submitReview, row, '已提交审核')">提交审核</el-button>
          <el-button v-if="row.status === 'pending_review'" size="small" text type="success" @click="act(approveDocument, row, '已通过并发布')">通过发布</el-button>
          <el-button v-if="row.status === 'pending_review'" size="small" text type="danger" @click="doReject(row)">驳回</el-button>
          <el-button v-if="row.status !== 'archived'" size="small" text type="warning" @click="act(archiveDocument, row, '已下线')">下线</el-button>
          <el-button size="small" text @click="openVersions(row)">版本</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!documents.length" description="暂无文档" />

    <!-- 新建草稿 / 直接发布共用表单 -->
    <el-dialog v-model="draftOpen" title="新建草稿（走审核流）" width="620px">
      <DocForm v-model="form" />
      <template #footer>
        <el-button @click="draftOpen = false">取消</el-button>
        <el-button type="primary" :loading="acting" @click="doCreateDraft">创建草稿</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="publishOpen" title="直接发布（跳过审核，立即建索引）" width="620px">
      <DocForm v-model="form" />
      <template #footer>
        <el-button @click="publishOpen = false">取消</el-button>
        <el-button type="primary" :loading="acting" @click="doPublish">发布</el-button>
      </template>
    </el-dialog>

    <!-- 版本历史 -->
    <el-dialog v-model="versionsOpen" title="版本历史" width="560px">
      <el-table :data="versions" size="small">
        <el-table-column prop="version" label="版本" width="70" />
        <el-table-column prop="title" label="标题" min-width="160" show-overflow-tooltip />
        <el-table-column prop="editor" label="编辑人" width="100" />
        <el-table-column prop="note" label="备注" min-width="120" show-overflow-tooltip />
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button size="small" text type="primary" @click="doRollback(row.version)">回滚</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
import { defineComponent, h, onMounted, ref } from "vue";
import { ElInput, ElMessage, ElMessageBox, ElOption, ElSelect } from "element-plus";

import {
  approveDocument,
  archiveDocument,
  createDraft,
  listDocuments,
  listVersions,
  publishDirect,
  rejectDocument,
  rollbackDocument,
  submitReview,
  uploadDocument,
  type KbDocument,
  type KbVersion,
} from "@/api/kb";

// 草稿/发布共用的小表单（标题+类型+正文）
const DocForm = defineComponent({
  props: { modelValue: { type: Object, required: true } },
  emits: ["update:modelValue"],
  setup(props) {
    const m = props.modelValue as { title: string; source_type: string; content: string };
    return () => [
      h(ElInput, {
        modelValue: m.title,
        "onUpdate:modelValue": (v: string) => (m.title = v),
        placeholder: "标题",
        style: "margin-bottom:8px",
      }),
      h(
        ElSelect,
        {
          modelValue: m.source_type,
          "onUpdate:modelValue": (v: string) => (m.source_type = v),
          style: "width:100%;margin-bottom:8px",
        },
        () => ["policy", "faq", "manual", "product"].map((s) => h(ElOption, { label: s, value: s })),
      ),
      h(ElInput, {
        modelValue: m.content,
        "onUpdate:modelValue": (v: string) => (m.content = v),
        type: "textarea",
        rows: 10,
        placeholder: "文档正文（markdown/纯文本）",
      }),
    ];
  },
});

const documents = ref<KbDocument[]>([]);
const filterStatus = ref("");
const draftOpen = ref(false);
const publishOpen = ref(false);
const versionsOpen = ref(false);
const versions = ref<KbVersion[]>([]);
const currentDoc = ref<KbDocument | null>(null);
const acting = ref(false);
const form = ref({ title: "", source_type: "policy", content: "" });

function statusType(status: string) {
  return (
    { draft: "info", pending_review: "warning", published: "success", archived: "danger" }[status] ??
    "info"
  );
}

async function reload() {
  try {
    const data = await listDocuments({ status: filterStatus.value || undefined });
    documents.value = data.documents;
  } catch (err) {
    ElMessage.error(String(err));
  }
}

async function act(fn: (id: string) => Promise<unknown>, row: KbDocument, okMsg: string) {
  try {
    await fn(row.document_id);
    ElMessage.success(okMsg);
    await reload();
  } catch (err) {
    ElMessage.error(String(err));
  }
}

async function doReject(row: KbDocument) {
  const { value } = await ElMessageBox.prompt("驳回意见", "驳回", { inputPattern: /.+/ });
  try {
    await rejectDocument(row.document_id, value);
    ElMessage.success("已驳回");
    await reload();
  } catch (err) {
    ElMessage.error(String(err));
  }
}

async function doCreateDraft() {
  acting.value = true;
  try {
    await createDraft(form.value);
    ElMessage.success("草稿已创建");
    draftOpen.value = false;
    form.value = { title: "", source_type: "policy", content: "" };
    await reload();
  } catch (err) {
    ElMessage.error(String(err));
  } finally {
    acting.value = false;
  }
}

async function doPublish() {
  acting.value = true;
  try {
    await publishDirect(form.value);
    ElMessage.success("已发布并重建索引");
    publishOpen.value = false;
    form.value = { title: "", source_type: "policy", content: "" };
    await reload();
  } catch (err) {
    ElMessage.error(String(err));
  } finally {
    acting.value = false;
  }
}

async function doUpload(file: File) {
  try {
    await uploadDocument(file);
    ElMessage.success("上传入库成功");
    await reload();
  } catch (err) {
    ElMessage.error(String(err));
  }
  return false; // 阻止 el-upload 默认请求
}

async function openVersions(row: KbDocument) {
  currentDoc.value = row;
  try {
    versions.value = (await listVersions(row.document_id)).versions;
    versionsOpen.value = true;
  } catch (err) {
    ElMessage.error(String(err));
  }
}

async function doRollback(version: number) {
  if (!currentDoc.value) return;
  try {
    await rollbackDocument(currentDoc.value.document_id, version);
    ElMessage.success(`已回滚到 v${version}`);
    versionsOpen.value = false;
    await reload();
  } catch (err) {
    ElMessage.error(String(err));
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
  align-items: center;
}
.w-140 {
  width: 140px;
}
</style>
