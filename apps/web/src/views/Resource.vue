<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useAuthStore } from "../stores/auth";
const props = defineProps<{ resource: string }>(),
  auth = useAuthStore(),
  rows = ref<Record<string, unknown>[]>([]),
  loading = ref(false),
  search = ref("");
const filteredRows = computed(() =>
  rows.value.filter((row) => JSON.stringify(row).includes(search.value)),
);
const visibleColumns = computed(() =>
  Object.keys(rows.value[0] ?? {})
    .filter((key) => !["password_hash", "refresh_token_hash"].includes(key))
    .slice(0, 8),
);
async function load() {
  loading.value = true;
  try {
    rows.value = await auth.client.list(props.resource);
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "加载失败");
  } finally {
    loading.value = false;
  }
}
async function action(row: Record<string, unknown>, name: string) {
  const reason = await ElMessageBox.prompt("请输入操作原因", "确认操作")
    .then((v) => v.value)
    .catch(() => null);
  if (!reason) return;
  await auth.client.request(`/conflicts/${row.id}/${name}`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
  ElMessage.success("操作成功");
  load();
}
onMounted(load);
watch(() => props.resource, load);
</script>
<template>
  <div class="page-card">
    <div
      style="display: flex; justify-content: space-between; margin-bottom: 16px"
    >
      <el-input
        v-model="search"
        clearable
        placeholder="筛选当前页"
        style="width: 280px"
      /><el-button @click="load">刷新</el-button>
    </div>
    <el-table v-loading="loading" :data="filteredRows" stripe
      ><el-table-column
        v-for="key in visibleColumns"
        :key="key"
        :prop="key"
        :label="key"
        show-overflow-tooltip
      /><el-table-column
        v-if="resource === 'conflicts'"
        label="操作"
        width="230"
        ><template #default="scope"
          ><el-button
            link
            type="primary"
            @click="action(scope.row, 'acknowledge')"
            >知悉</el-button
          ><el-button link type="danger" @click="action(scope.row, 'exclude')"
            >排除</el-button
          ><el-button link @click="action(scope.row, 'request-transfer')"
            >申请转交</el-button
          ></template
        ></el-table-column
      ></el-table
    ><el-empty v-if="!loading && !rows.length" description="暂无数据" />
  </div>
</template>
