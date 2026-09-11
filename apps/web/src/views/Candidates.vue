<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const loading = ref(true);
const detailLoading = ref(false);
const detailVisible = ref(false);
const search = ref("");
const candidates = ref<Record<string, any>[]>([]);
const alerts = ref<Record<string, any>[]>([]);
const sync = ref<Record<string, any>[]>([]);
const notifications = ref<Record<string, any>[]>([]);
const accounts = ref<Record<string, any>[]>([]);
const detail = ref<Record<string, any> | null>(null);

const isAdmin = computed(() => auth.user?.workspace?.status === "ADMIN");
const canOpenFeishu = computed(() => !!auth.user?.capabilities?.can_manage_feishu);
const accountMap = computed(() => new Map(accounts.value.map((row) => [row.id, row])));
const enrichedCandidates = computed(() => candidates.value.map((row) => ({
  ...row,
  boss_account: accountMap.value.get(row.platform_account_id)?.boss_account || (isAdmin.value ? "未识别账号" : auth.user?.workspace?.boss_account_name || "我的账号"),
  feishu_owner: accountMap.value.get(row.platform_account_id)?.feishu_display_name || (isAdmin.value ? "未分配" : auth.user?.display_name),
})));
const filtered = computed(() => enrichedCandidates.value.filter((row) => !search.value.trim() || JSON.stringify(row).toLowerCase().includes(search.value.trim().toLowerCase())));
const pendingSync = computed(() => sync.value.filter((row) => ["PENDING", "PROCESSING"].includes(row.status)).length);
const failedSync = computed(() => sync.value.filter((row) => row.status === "FAILED").length);
const profileComplete = computed(() => candidates.value.filter((row) => row.candidate_age && row.candidate_experience && row.candidate_education).length);

async function listAll<T>(resource: string) {
  const pageSize = 500;
  const rows: T[] = [];
  for (let offset = 0; ; offset += pageSize) {
    const page = await auth.client.request<T[]>(`/admin/${resource}?limit=${pageSize}&offset=${offset}`);
    rows.push(...page);
    if (page.length < pageSize) return rows;
  }
}
async function load() {
  loading.value = true;
  try {
    [candidates.value, alerts.value, sync.value, notifications.value, accounts.value] = await Promise.all([
      isAdmin.value
        ? auth.client.request<Record<string, any>[]>("/admin/candidate-sources?limit=10&offset=0&sort=latest")
        : listAll<Record<string, any>>("candidate-sources"),
      auth.client.list<Record<string, any>>("lookup-alerts"),
      listAll<Record<string, any>>("candidate-sync"),
      auth.client.list<Record<string, any>>("notifications"),
      isAdmin.value ? auth.client.request<Record<string, any>[]>("/admin/boss-accounts") : Promise.resolve([]),
    ]);
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : "加载候选人失败"); }
  finally { loading.value = false; }
}
async function openDetail(row: Record<string, any>) {
  detailVisible.value = true;
  detailLoading.value = true;
  detail.value = row;
  try { detail.value = { ...row, ...(await auth.client.request<Record<string, any>>(`/admin/candidate-sources/${row.id}`)) }; }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : "加载候选人详情失败"); }
  finally { detailLoading.value = false; }
}
async function retry(row: Record<string, any>) {
  try { await auth.client.request(`/admin/candidate-sync/${row.id}/retry`, { method: "POST" }); ElMessage.success("已重新排队"); await load(); }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : "重试失败"); }
}
async function openTable() {
  try { const data = await auth.client.request<{ table_url: string }>("/admin/feishu-tables/active/open-url"); window.open(data.table_url, "_blank"); }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : "当前尚未配置飞书表"); }
}
function formatTime(value?: string | null) { return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—"; }
function syncLabel(candidateId: string) {
  const row = sync.value.find((item) => item.candidate_source_id === candidateId);
  if (!row) return "后台已记录";
  return row.status === "FAILED" ? "同步失败" : ["PENDING", "PROCESSING"].includes(row.status) ? "等待同步" : "已同步";
}
function statusTone(status: string) { return status === "同步失败" ? "danger" : status === "等待同步" ? "warning" : status === "后台已记录" ? "neutral" : "success"; }
onMounted(load);
</script>

<template>
  <div class="workspace-page candidate-workspace" v-loading="loading">
    <header class="page-heading compact-heading">
      <div><p class="eyebrow">CANDIDATES</p><h1>{{ isAdmin ? "全公司候选人" : "我的候选人" }}</h1><p>{{ isAdmin ? "这里只展示最新 10 条候选人；完整历史数据请打开飞书表查看。" : "这里只显示分配给你当前 BOSS 账号的候选人；数据直接来自后台，不需要先写入飞书表。" }}</p></div>
      <div class="hero-actions"><el-button v-if="canOpenFeishu" @click="openTable">打开飞书表</el-button><el-button @click="load">刷新</el-button></div>
    </header>

    <div v-if="isAdmin" class="data-explainer"><strong>页面展示说明</strong><span>这里为了加载更快只显示最近同步的 10 条候选人；候选人总数是公司范围统计，不等于当前列表条数。需要查看完整历史、所有字段和聊天截图，请点击“打开飞书表”。</span></div>
    <div v-else class="data-explainer"><strong>页面展示说明</strong><span>这里展示你当前绑定 BOSS 账号的后台记录；完整历史数据和共享字段请在飞书表中查看。</span></div>

    <section class="summary-grid compact-summary">
      <article class="summary-card"><span>候选人</span><strong>{{ candidates.length }}</strong><small>当前权限范围</small></article>
      <article class="summary-card"><span>资料完整</span><strong>{{ profileComplete }}</strong><small>年龄、经验、学历齐全</small></article>
      <article class="summary-card" :class="failedSync ? 'summary-danger' : ''"><span>同步异常</span><strong>{{ failedSync }}</strong><small>{{ pendingSync }} 条等待同步</small></article>
      <article class="summary-card"><span>查重提醒</span><strong>{{ alerts.length }}</strong><small>{{ notifications.filter((row) => row.status === 'FAILED').length }} 条通知失败</small></article>
    </section>

    <section class="page-card candidate-table-card">
      <div class="section-toolbar"><div><h2>候选人列表</h2><p>点击任意候选人查看主动消息、招聘事件和面试详情；飞书表仅是可选共享出口。</p></div><el-input v-model="search" clearable placeholder="搜索姓名、岗位、账号或状态" class="page-search" /></div>
      <el-table :data="filtered" stripe @row-click="openDetail">
        <el-table-column prop="candidate_display_name" label="候选人" min-width="115" fixed="left" />
        <el-table-column v-if="isAdmin" label="BOSS 账号 / 负责人" min-width="150"><template #default="scope"><strong class="table-primary">{{ scope.row.boss_account }}</strong><small class="table-subtext">{{ scope.row.feishu_owner }}</small></template></el-table-column>
        <el-table-column label="基础画像" min-width="170"><template #default="scope">{{ [scope.row.candidate_age ? `${scope.row.candidate_age}岁` : "", scope.row.candidate_experience || "", scope.row.candidate_education || ""].filter(Boolean).join(" · ") || "资料待补充" }}</template></el-table-column>
        <el-table-column prop="raw_job_name" label="应聘岗位" min-width="160" show-overflow-tooltip />
        <el-table-column prop="recruitment_status" label="招聘状态" width="105" />
        <el-table-column label="主动沟通" width="100"><template #default="scope"><strong class="message-number">{{ scope.row.message_sent_count ?? 0 }}</strong><small> 次</small></template></el-table-column>
        <el-table-column label="飞书出口" width="105"><template #default="scope"><span :class="['mini-status', statusTone(syncLabel(scope.row.id))]">{{ syncLabel(scope.row.id) }}</span></template></el-table-column>
        <el-table-column label="最近活动" min-width="150"><template #default="scope">{{ formatTime(scope.row.conversation_updated_at || scope.row.updated_at) }}</template></el-table-column>
      </el-table>
      <el-empty v-if="!filtered.length" description="暂无候选人数据" :image-size="70" />
    </section>

    <section v-if="alerts.length || pendingSync || failedSync" class="page-card issue-card">
      <div class="section-title"><div><h2>需要处理</h2><p>只显示查重提醒和未完成同步。</p></div></div>
      <div class="issue-grid">
        <article v-for="alert in alerts.slice(0, 6)" :key="alert.id"><span class="issue-dot warning"></span><div><strong>候选人可能被重复跟进</strong><small>{{ alert.match_reason || `${alert.matched_recruiter_name || "其他招聘人员"} 已有沟通记录` }}</small></div></article>
        <article v-for="row in sync.filter((item) => ['FAILED', 'PENDING'].includes(item.status)).slice(0, 6)" :key="row.id"><span :class="['issue-dot', row.status === 'FAILED' ? 'danger' : 'warning']"></span><div><strong>{{ row.status === 'FAILED' ? '候选人同步失败' : '候选人等待同步' }}</strong><small>{{ row.last_error || "等待后台处理" }}</small></div><el-button v-if="row.status === 'FAILED'" link type="primary" @click="retry(row)">重试</el-button></article>
      </div>
    </section>

    <el-drawer v-model="detailVisible" title="候选人详情" size="min(520px, 94vw)">
      <div v-loading="detailLoading" class="candidate-detail">
        <div class="detail-identity"><span>{{ detail?.candidate_display_name?.slice(0, 1) || "候" }}</span><div><h2>{{ detail?.candidate_display_name }}</h2><p>{{ detail?.raw_job_name || "岗位待确认" }}</p></div><span class="detail-status">{{ detail?.recruitment_status || "沟通中" }}</span></div>
        <div class="detail-grid"><div><small>年龄</small><strong>{{ detail?.candidate_age ? `${detail.candidate_age} 岁` : "待补充" }}</strong></div><div><small>工作经验</small><strong>{{ detail?.candidate_experience || "待补充" }}</strong></div><div><small>学历</small><strong>{{ detail?.candidate_education || "待补充" }}</strong></div><div><small>主动沟通</small><strong class="message-number">{{ detail?.message_sent_count ?? 0 }} 次</strong></div><div v-if="isAdmin"><small>BOSS 账号</small><strong>{{ detail?.boss_account }}</strong></div></div>
        <section><h3>招聘事件</h3><div v-if="detail?.events?.length" class="detail-list"><article v-for="event in detail.events" :key="event.id"><i></i><div><strong>{{ event.event_type || event.stage || "状态更新" }}</strong><small>{{ formatTime(event.event_time || event.created_at) }}</small></div></article></div><p v-else class="detail-empty">暂无招聘事件</p></section>
        <section><h3>面试记录</h3><div v-if="detail?.interviews?.length" class="detail-list"><article v-for="interview in detail.interviews" :key="interview.id"><i></i><div><strong>{{ interview.status }} · {{ interview.location_text || interview.location_type }}</strong><small>{{ formatTime(interview.scheduled_at) }}</small></div></article></div><p v-else class="detail-empty">暂无面试记录</p></section>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.candidate-workspace{display:flex;flex-direction:column;gap:14px}.data-explainer{display:flex;align-items:flex-start;gap:10px;margin:0;padding:11px 13px;border:1px solid #dce8ff;border-radius:10px;background:#f6f9ff;color:#5d6f8e;font-size:11px;line-height:1.65}.data-explainer strong{flex:none;color:#3557a7;font-size:11px}.data-explainer span{min-width:0}.compact-summary{margin:0}.summary-card small{color:#98a1b0;font-size:9px}.summary-danger strong{color:#c9475c}.candidate-table-card{padding-bottom:12px}.section-toolbar{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:14px}.section-toolbar h2{margin:0 0 3px;font-size:16px}.section-toolbar p{margin:0;color:#8e98a9;font-size:10px}.page-search{width:280px}.candidate-table-card :deep(.el-table__row){cursor:pointer}.candidate-table-card :deep(.el-table__cell){padding:9px 0}.table-primary,.table-subtext{display:block}.table-primary{font-size:11px}.table-subtext{margin-top:3px;color:#929cac;font-size:9px}.message-number{color:#7050b8}.mini-status{display:inline-block;padding:4px 7px;border-radius:999px;font-size:9px;font-weight:700}.mini-status.success{background:#eaf8f1;color:#177c5b}.mini-status.warning{background:#fff4df;color:#9e691c}.mini-status.danger{background:#fff0f2;color:#c4475b}.mini-status.neutral{background:#f1f3f7;color:#6e7b8e}.issue-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.issue-grid article{display:grid;grid-template-columns:8px minmax(0,1fr) auto;align-items:center;gap:9px;padding:11px;border-radius:10px;background:#f8f9fb}.issue-dot{width:8px;height:8px;border-radius:50%}.issue-dot.warning{background:#dc982e}.issue-dot.danger{background:#d55268}.issue-grid strong,.issue-grid small{display:block}.issue-grid strong{font-size:11px}.issue-grid small{margin-top:3px;color:#8e98a8;font-size:9px}.candidate-detail{display:flex;flex-direction:column;gap:22px}.detail-identity{display:grid;grid-template-columns:44px minmax(0,1fr) auto;align-items:center;gap:12px;padding-bottom:17px;border-bottom:1px solid #edf0f4}.detail-identity>span:first-child{width:44px;height:44px;display:grid;place-items:center;border-radius:13px;background:#eaf0ff;color:#3b61c7;font-weight:800}.detail-identity h2{margin:0;font-size:18px}.detail-identity p{margin:4px 0 0;color:#7f8a9d;font-size:11px}.detail-status{padding:5px 8px;border-radius:999px;background:#edf2ff;color:#3f61c1;font-size:9px;font-weight:700}.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px}.detail-grid>div{padding:12px;border-radius:10px;background:#f6f8fb}.detail-grid small,.detail-grid strong{display:block}.detail-grid small{color:#919baa;font-size:9px}.detail-grid strong{margin-top:5px;font-size:11px}.candidate-detail section h3{margin:0 0 10px;font-size:13px}.detail-list{display:flex;flex-direction:column;gap:7px}.detail-list article{display:flex;gap:9px;align-items:center;padding:10px;border-radius:9px;background:#f7f9fb}.detail-list i{width:7px;height:7px;border-radius:50%;background:#5272d4}.detail-list strong,.detail-list small{display:block}.detail-list strong{font-size:10px}.detail-list small{margin-top:3px;color:#929cad;font-size:9px}.detail-empty{padding:18px;border-radius:9px;background:#f7f9fb;color:#99a2b0;font-size:10px;text-align:center}
@media(max-width:720px){.section-toolbar{align-items:stretch;flex-direction:column}.page-search{width:100%}.issue-grid{grid-template-columns:1fr}.detail-grid{grid-template-columns:1fr 1fr}}
</style>
