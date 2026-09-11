<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useAuthStore } from "../stores/auth";

type FeishuUser = { open_id: string; user_id?: string; display_name: string };
const props = defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ (e: "update:modelValue", value: boolean): void; (e: "granted"): void }>();
const auth = useAuthStore();
const loading = ref(false), directorySearch = ref(""), directory = ref<FeishuUser[]>([]), directoryError = ref(""), selectedOpenId = ref("");
const selectedUser = computed(() => directory.value.find((x) => x.open_id === selectedOpenId.value));

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      directorySearch.value = ""; directory.value = []; directoryError.value = ""; selectedOpenId.value = "";
      void loadDirectory();
    }
  },
);

async function loadDirectory() {
  loading.value = true; directoryError.value = "";
  try {
    directory.value = await auth.client.request<FeishuUser[]>(`/admin/feishu/directory?query=${encodeURIComponent(directorySearch.value.trim())}`);
  } catch (error) {
    directory.value = [];
    directoryError.value = error instanceof Error ? error.message : "读取飞书通讯录失败";
    ElMessage.error(directoryError.value);
  } finally { loading.value = false; }
}
async function grant() {
  const person = selectedUser.value;
  if (!person) return ElMessage.warning("请先在通讯录里选择要添加的飞书成员");
  try {
    await ElMessageBox.confirm(`确认把“${person.display_name}”设为管理员？该成员将获得团队、飞书、岗位和系统重置的全部管理权限。`, "添加管理员", { type: "warning", confirmButtonText: "确认添加", cancelButtonText: "取消" });
  } catch { return; }
  try {
    await auth.client.request("/admin/admins", { method: "PUT", body: JSON.stringify({ feishu_open_id: person.open_id, feishu_user_id: person.user_id, feishu_display_name: person.display_name }) });
    ElMessage.success(`已把“${person.display_name}”设为管理员`);
    emit("update:modelValue", false);
    emit("granted");
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : "添加管理员失败"); }
}
</script>

<template>
  <el-dialog :model-value="modelValue" @update:model-value="(value: boolean) => emit('update:modelValue', value)" width="min(560px, 92vw)" title="添加管理员" destroy-on-close>
    <div class="directory-search"><el-input v-model="directorySearch" clearable placeholder="输入姓名搜索飞书应用可见通讯录" @keyup.enter="loadDirectory" /><el-button @click="loadDirectory">搜索</el-button></div>
    <div v-if="!directoryError" class="directory-summary"><span>从飞书应用可见范围内选择成员，无需对方先登录后台</span><strong>{{ loading ? "读取中…" : `${directory.length} 位成员` }}</strong></div>
    <el-alert v-else class="directory-error" type="error" :closable="false" title="通讯录读取失败" :description="directoryError" show-icon />
    <div class="directory-list" v-loading="loading">
      <label v-for="person in directory" :key="person.open_id" class="person" :class="{ selected: selectedOpenId === person.open_id }">
        <el-radio v-model="selectedOpenId" :value="person.open_id"><span class="person-avatar">{{ person.display_name.slice(0, 1) }}</span><span>{{ person.display_name }}</span></el-radio>
      </label>
      <el-empty v-if="!loading && !directoryError && !directory.length" description="应用可见范围内没有找到成员" :image-size="72" />
    </div>
    <template #footer><el-button @click="emit('update:modelValue', false)">取消</el-button><el-button type="primary" :disabled="!selectedUser" @click="grant">设为管理员</el-button></template>
  </el-dialog>
</template>

<style scoped>
.directory-search { display: flex; gap: 10px; margin-bottom: 14px; }
.directory-search .el-input { flex: 1; }
.directory-summary { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; color: #7a8699; font-size: 12px; }
.directory-error { margin-bottom: 12px; }
.directory-list { max-height: 340px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
.person { display: block; padding: 9px 12px; border: 1px solid #e6ebf2; border-radius: 10px; cursor: pointer; }
.person:hover { background: #f5f8fd; }
.person.selected { border-color: #3c65df; background: #eef2ff; }
.person-avatar { display: inline-grid; place-items: center; width: 24px; height: 24px; margin: 0 8px; border-radius: 50%; background: #142033; color: #42d5a5; font-weight: 700; font-size: 12px; vertical-align: middle; }
</style>
