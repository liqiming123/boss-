<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";

type Account = {
  id: string;
  boss_account: string;
  platform: string;
  status: "ASSIGNED" | "UNASSIGNED";
  feishu_display_name?: string | null;
  source_count: number;
  active_device_count: number;
  assigned_at?: string | null;
};
type Candidate = {
  id: string;
  platform_account_id?: string | null;
  candidate_display_name: string;
  candidate_age: number | null;
  candidate_experience: string | null;
  candidate_education: string | null;
  raw_job_name: string;
  recruitment_status: string;
  conversation_updated_at: string | null;
  updated_at: string | null;
};
type SyncRow = { id: string; candidate_source_id: string; status: string; last_error: string | null; updated_at: string };
type Interview = { status: string; scheduled_at: string };
type Overview = { open_conflicts: number; interviews: number; unmapped_jobs: number };
type Operations = { generated_at: string; overall_status: "healthy" | "warning" | "critical"; metrics: Record<string, number>; recent_failures: Array<{ kind: string; last_error: string | null }> };

const auth = useAuthStore();
const router = useRouter();
const loading = ref(true);
const lastError = ref("");
const accounts = ref<Account[]>([]);
const candidates = ref<Candidate[]>([]);
const syncRows = ref<SyncRow[]>([]);
const interviews = ref<Interview[]>([]);
const overview = ref<Overview | null>(null);
const operations = ref<Operations | null>(null);
const selectedAccountId = ref("all");
const search = ref("");
const extensionRelease = ref<{ version: string; file_name: string } | null>(null);
const extensionLoading = ref(false);
const showInstallGuide = ref(false);
const timer = ref<number>();

async function listAll<T>(resource: string) {
  const pageSize = 500;
  const rows: T[] = [];
  for (let offset = 0; ; offset += pageSize) {
    const page = await auth.client.request<T[]>(`/admin/${resource}?limit=${pageSize}&offset=${offset}`);
    rows.push(...page);
    if (page.length < pageSize) return rows;
  }
}

const accountMap = computed(() => new Map(accounts.value.map((row) => [row.id, row])));
const syncMap = computed(() => new Map(syncRows.value.map((row) => [row.candidate_source_id, row])));
const candidateRows = computed(() => candidates.value.map((row) => {
  const account = row.platform_account_id ? accountMap.value.get(row.platform_account_id) : undefined;
  const sync = syncMap.value.get(row.id);
  return {
    ...row,
    account_name: account?.boss_account || "未识别账号",
    feishu_owner: account?.feishu_display_name || "未分配",
    sync_status: sync?.status || "—",
  };
}));
const visibleCandidates = computed(() => candidateRows.value
  .filter((row) => selectedAccountId.value === "all" || row.platform_account_id === selectedAccountId.value)
  .filter((row) => !search.value.trim() || `${row.candidate_display_name} ${row.raw_job_name} ${row.account_name} ${row.feishu_owner} ${row.recruitment_status}`.toLowerCase().includes(search.value.trim().toLowerCase()))
  .sort((a, b) => new Date(b.conversation_updated_at || b.updated_at || 0).getTime() - new Date(a.conversation_updated_at || a.updated_at || 0).getTime()));

const accountRows = computed(() => accounts.value.map((account) => {
  const rows = candidateRows.value.filter((row) => row.platform_account_id === account.id);
  const active = rows.filter((row) => !["已拒绝", "已入职", "已淘汰", "已结束"].includes(row.recruitment_status)).length;
  const failed = rows.filter((row) => row.sync_status === "FAILED").length;
  const pending = rows.filter((row) => ["PENDING", "PROCESSING"].includes(row.sync_status)).length;
  const latest = rows.reduce<string | null>((value, row) => {
    const candidate = row.conversation_updated_at || row.updated_at;
    return !value || (candidate && new Date(candidate).getTime() > new Date(value).getTime()) ? candidate : value;
  }, null);
  return {
    ...account,
    total: account.source_count || rows.length,
    active,
    failed,
    pending,
    latest,
    health: failed ? "danger" : pending || (account.source_count > 0 && !account.active_device_count) ? "warning" : "healthy",
  };
}).sort((a, b) => b.total - a.total));

const totalCandidates = computed(() => operations.value?.metrics.candidate_sources ?? candidates.value.length);
const activeCandidates = computed(() => candidateRows.value.filter((row) => !["已拒绝", "已入职", "已淘汰", "已结束"].includes(row.recruitment_status)).length);
const failedSync = computed(() => operations.value?.metrics.candidate_sync_failed ?? syncRows.value.filter((row) => row.status === "FAILED").length);
const pendingSync = computed(() => operations.value?.metrics.candidate_sync_pending ?? syncRows.value.filter((row) => ["PENDING", "PROCESSING"].includes(row.status)).length);
const attentionCount = computed(() => failedSync.value + pendingSync.value + accountRows.value.filter((row) => row.status === "UNASSIGNED").length + (overview.value?.open_conflicts ?? 0));
const selectedAccountLabel = computed(() => selectedAccountId.value === "all" ? "全部 BOSS 账号" : accountMap.value.get(selectedAccountId.value)?.boss_account || "全部 BOSS 账号");
const candidateTableMaxHeight = computed(() => visibleCandidates.value.length > 8 ? 520 : undefined);
const attentionItems = computed(() => {
  const items: Array<{ tone: string; title: string; detail: string; action?: string }> = [];
  if (failedSync.value) items.push({ tone: "danger", title: `${failedSync.value} 条同步失败`, detail: "候选人还没有写入飞书表，需要优先重试。", action: "查看同步" });
  if (pendingSync.value) items.push({ tone: "warning", title: `${pendingSync.value} 条同步等待处理`, detail: "队列仍在处理，持续增加时请检查扩展连接。" });
  const unassigned = accountRows.value.filter((row) => row.status === "UNASSIGNED").length;
  if (unassigned) items.push({ tone: "warning", title: `${unassigned} 个 BOSS 账号未分配`, detail: "未分配的账号不会进入招聘人员工作台。", action: "去分配" });
  const offline = accountRows.value.filter((row) => row.total > 0 && !row.active_device_count).length;
  if (offline) items.push({ tone: "info", title: `${offline} 个账号扩展离线`, detail: "账号有历史数据，但当前没有在线扩展。" });
  if (overview.value?.open_conflicts) items.push({ tone: "info", title: `${overview.value.open_conflicts} 个候选人查重提醒`, detail: "同一候选人可能被多个招聘流程跟进。" });
  return items.slice(0, 4);
});

function formatTime(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}
function statusLabel(status: string) {
  return ({ SENT: "已同步", FAILED: "失败", PENDING: "等待", PROCESSING: "处理中" } as Record<string, string>)[status] || status;
}
function statusClass(status: string) {
  if (status === "FAILED") return "danger";
  if (["PENDING", "PROCESSING"].includes(status)) return "warning";
  return "success";
}
function healthLabel(row: (typeof accountRows.value)[number]) {
  if (row.health === "danger") return "同步异常";
  if (row.health === "warning") return row.status === "UNASSIGNED" ? "待分配" : "需要关注";
  return "运行正常";
}
function selectAccount(id: string) { selectedAccountId.value = id; }

async function load(silent = false) {
  if (!silent) loading.value = true;
  try {
    await auth.refreshIdentity();
    const [accountRowsData, overviewData, operationData, candidateData, syncData, interviewData] = await Promise.all([
      auth.client.request<Account[]>("/admin/boss-accounts"),
      auth.client.request<Overview>("/admin/overview"),
      auth.client.request<Operations>("/admin/operations"),
      auth.client.request<Candidate[]>("/admin/candidate-sources?limit=20&offset=0&sort=latest"),
      listAll<SyncRow>("candidate-sync"),
      listAll<Interview>("interviews"),
    ]);
    accounts.value = accountRowsData;
    overview.value = overviewData;
    operations.value = operationData;
    candidates.value = candidateData;
    syncRows.value = syncData;
    interviews.value = interviewData;
    lastError.value = "";
  } catch (error) {
    lastError.value = error instanceof Error ? error.message : "管理员数据加载失败";
    if (!silent) ElMessage.error(lastError.value);
  } finally { loading.value = false; }
}
async function loadExtensionRelease() {
  try { extensionRelease.value = await auth.client.request(`/admin/extension-release?ts=${Date.now()}`); } catch { extensionRelease.value = null; }
}
async function downloadExtension() {
  extensionLoading.value = true;
  try {
    const apiBase = import.meta.env.VITE_API_BASE_URL ?? (import.meta.env.DEV ? "http://localhost:8000/api/v1" : "/api/v1");
    const response = await fetch(`${apiBase}/admin/extension-release/download?ts=${Date.now()}`, { credentials: "include", cache: "no-store" });
    if (!response.ok) throw new Error("扩展下载失败");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = extensionRelease.value?.file_name || "recruitment-collab-extension.zip";
    link.click();
    URL.revokeObjectURL(url);
    showInstallGuide.value = true;
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : "扩展下载失败"); }
  finally { extensionLoading.value = false; }
}
onMounted(() => { void load(); void loadExtensionRelease(); timer.value = window.setInterval(() => void load(true), 15_000); });
onBeforeUnmount(() => { if (timer.value) window.clearInterval(timer.value); });
</script>

<template>
  <div v-loading="loading" class="admin-dashboard">
    <section class="admin-hero">
      <div>
        <div class="admin-kicker"><span>ADMIN CONTROL ROOM</span><i></i><span>全公司数据</span></div>
        <h1>全公司招聘数据</h1>
        <p>按 BOSS 账号查看招聘进度、主动沟通次数和当前负责人。候选人数据直接来自后台，飞书表只负责共享同步，不影响这里查看。</p>
        <div class="admin-hero-meta"><span class="admin-state-dot"></span><strong>{{ operations?.overall_status === "critical" ? "同步存在异常" : operations?.overall_status === "warning" ? "有事项需要关注" : "数据更新正常" }}</strong><span>每 15 秒自动更新 · {{ formatTime(operations?.generated_at) }}</span></div>
      </div>
      <aside class="admin-extension-card">
        <div class="admin-extension-icon">↗</div>
        <div class="admin-extension-copy"><span>招聘协同助手</span><strong>{{ extensionRelease ? `最新版 v${extensionRelease.version}` : "安装包暂不可用" }}</strong><small>在 BOSS 页面采集并同步候选人</small></div>
        <div class="admin-extension-actions"><el-button type="primary" :disabled="!extensionRelease" :loading="extensionLoading" @click="downloadExtension">下载扩展</el-button><button type="button" @click="showInstallGuide = true">安装说明</button></div>
      </aside>
    </section>

    <el-alert v-if="lastError" type="error" :title="lastError" :closable="false" show-icon />

    <section class="admin-kpis" aria-label="全公司招聘关键指标">
      <article class="kpi-card kpi-account"><div class="kpi-card-top"><span class="kpi-label">BOSS 招聘账号</span><span class="kpi-mark" aria-hidden="true">B</span></div><strong>{{ accounts.length }}</strong><small>{{ accountRows.filter((row) => row.status === "ASSIGNED").length }} 个已分配</small></article>
      <article class="kpi-card kpi-candidates"><div class="kpi-card-top"><span class="kpi-label">候选人总数</span><span class="kpi-mark" aria-hidden="true">人</span></div><strong>{{ totalCandidates }}</strong><small>{{ activeCandidates }} 位正在推进</small></article>
      <article :class="['kpi-card', 'kpi-sync', failedSync ? 'alert-kpi' : '']"><div class="kpi-card-top"><span class="kpi-label">同步待处理</span><span class="kpi-mark" aria-hidden="true">↗</span></div><strong>{{ failedSync + pendingSync }}</strong><small>{{ failedSync ? `${failedSync} 条失败` : "当前无失败" }} · {{ pendingSync }} 条排队</small></article>
      <article :class="['kpi-card', 'kpi-attention', attentionCount ? 'attention-kpi' : '']"><div class="kpi-card-top"><span class="kpi-label">管理员要关注</span><span class="kpi-mark" aria-hidden="true">!</span></div><strong>{{ attentionCount }}</strong><small>{{ overview?.interviews ?? 0 }} 场待面试 · {{ operations?.metrics.active_devices ?? 0 }} 个扩展在线</small></article>
    </section>

    <div class="admin-grid">
      <section class="admin-panel account-health-panel">
        <div class="admin-panel-heading"><div><span class="section-kicker">BOSS ACCOUNT HEALTH</span><h2>账号运行概况</h2><p>先看哪个 BOSS 账号需要管理员介入。</p></div><el-button text type="primary" @click="router.push('/recruitment/accounts')">管理分配 →</el-button></div>
        <div class="account-health-list">
          <button v-for="row in accountRows" :key="row.id" type="button" :class="['account-health-row', { selected: selectedAccountId === row.id }]" @click="selectAccount(row.id)">
            <span class="account-symbol">B</span><span class="account-name"><strong>{{ row.boss_account }}</strong><small>{{ row.feishu_display_name || "尚未分配负责人" }}</small></span><span class="account-health-metric"><strong>{{ row.total }}</strong><small>候选人</small></span><span class="account-health-metric"><strong>{{ row.active }}</strong><small>推进中</small></span><span :class="['account-status', row.health]"><i></i>{{ healthLabel(row) }}</span><span class="account-health-time">{{ row.latest ? formatTime(row.latest) : "暂无同步" }}</span>
          </button>
          <div v-if="!accountRows.length" class="admin-empty">还没有 BOSS 账号数据</div>
        </div>
      </section>

      <section class="admin-panel attention-panel">
        <div class="admin-panel-heading"><div><span class="section-kicker">PRIORITY SIGNALS</span><h2>管理员先看</h2><p>只保留会影响招聘推进的事项。</p></div><span class="signal-count">{{ attentionItems.length }}</span></div>
        <div v-if="attentionItems.length" class="attention-list"><article v-for="item in attentionItems" :key="item.title" :class="item.tone"><i></i><div><strong>{{ item.title }}</strong><small>{{ item.detail }}</small></div><el-button v-if="item.action === '去分配'" text type="primary" @click="router.push('/recruitment/accounts')">处理</el-button><el-button v-else-if="item.action === '查看同步'" text type="primary" @click="router.push('/recruitment/candidates')">查看</el-button></article></div>
        <div v-else class="admin-clear"><span>✓</span><div><strong>当前没有阻塞事项</strong><small>账号、同步和招聘推进都在正常范围内。</small></div></div>
        <div class="decision-strip"><strong>判断重点</strong><span>同步失败影响数据完整性</span><span>推进中人数反映招聘进度</span><span>离线账号需要提醒负责人</span></div>
      </section>
    </div>

    <section class="admin-panel candidate-panel">
      <div class="admin-panel-heading candidate-heading"><div><span class="section-kicker">RECENT BOSS CANDIDATES</span><h2>候选人明细（最新 20 条）</h2><p>管理员可查看最近记录；完整历史数据请打开飞书表查看。</p></div><div class="candidate-tools"><el-select v-model="selectedAccountId" class="account-select" aria-label="筛选 BOSS 账号"><el-option label="全部 BOSS 账号" value="all" /><el-option v-for="row in accounts" :key="row.id" :label="row.boss_account" :value="row.id" /></el-select><el-input v-model="search" clearable placeholder="搜索候选人 / 岗位 / 账号" class="candidate-search" /></div></div>
      <div class="candidate-scope"><span>当前范围</span><strong>{{ selectedAccountLabel }}</strong><span>{{ visibleCandidates.length }} 位候选人</span><span class="scope-note">后台实时数据 · 飞书表为可选共享出口</span><el-button text type="primary" @click="router.push('/recruitment/candidates')">打开协同列表 →</el-button></div>
      <div class="data-explainer"><strong>页面展示说明</strong><span>此处只加载最新 20 条候选人，避免管理员打开页面时一次渲染全公司历史记录。上方统计卡片仍是全量统计；完整候选人明细、字段和截图请打开飞书表查看。</span></div>
      <el-table :data="visibleCandidates" stripe class="admin-candidate-table" :max-height="candidateTableMaxHeight">
        <el-table-column prop="candidate_display_name" label="候选人" min-width="130" fixed="left" />
        <el-table-column label="BOSS 账号" min-width="130"><template #default="scope"><span class="table-account">{{ scope.row.account_name }}</span><small>{{ scope.row.feishu_owner }}</small></template></el-table-column>
        <el-table-column label="画像" min-width="140"><template #default="scope">{{ [scope.row.candidate_age ? `${scope.row.candidate_age}岁` : "", scope.row.candidate_experience || "", scope.row.candidate_education || ""].filter(Boolean).join(" · ") || "资料待补充" }}</template></el-table-column>
        <el-table-column prop="raw_job_name" label="应聘岗位" min-width="160" show-overflow-tooltip />
        <el-table-column prop="recruitment_status" label="招聘状态" width="100" />
        <el-table-column label="主动沟通" width="100"><template #default="scope"><strong class="message-number">{{ scope.row.message_sent_count ?? 0 }}</strong><small> 次</small></template></el-table-column>
        <el-table-column label="同步状态" width="100"><template #default="scope"><span :class="['table-status', statusClass(scope.row.sync_status)]">{{ statusLabel(scope.row.sync_status) }}</span></template></el-table-column>
        <el-table-column label="最近活动" width="125"><template #default="scope">{{ formatTime(scope.row.conversation_updated_at || scope.row.updated_at) }}</template></el-table-column>
      </el-table>
      <div v-if="!visibleCandidates.length" class="admin-empty">当前筛选范围没有候选人数据</div>
    </section>

    <footer class="admin-footer">管理员视图 · 数据属于 BOSS 招聘账号 · 最后刷新 {{ formatTime(operations?.generated_at) }}</footer>
    <el-dialog v-model="showInstallGuide" title="扩展安装步骤" width="min(560px, 92vw)"><p>下载 ZIP 后请解压，在 Chrome 扩展页开启开发者模式并选择“加载已解压的扩展程序”。之后打开 BOSS 沟通页，使用对应飞书账号完成扩展登录。</p></el-dialog>
  </div>
</template>

<style scoped>
.admin-dashboard{--ink:#18243a;--muted:#7b879a;--line:#e7ecf3;max-width:1500px;margin:0 auto;display:flex;flex-direction:column;gap:16px;color:var(--ink)}
.admin-hero{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:26px;align-items:center;padding:28px 30px;border-radius:20px;background:linear-gradient(130deg,#111d38,#233c78 65%,#3154a2);box-shadow:0 16px 40px #2039782b;color:#fff}
.admin-kicker{display:flex;align-items:center;gap:10px;color:#b8c8ee;font-size:10px;font-weight:800;letter-spacing:.12em}.admin-kicker i{width:4px;height:4px;border-radius:50%;background:#55d8ac}.admin-hero h1{margin:10px 0 8px;font-size:30px;letter-spacing:-.04em}.admin-hero p{max-width:760px;margin:0;color:#c3d0ec;font-size:13px;line-height:1.7}.admin-hero-meta{display:flex;align-items:center;gap:9px;margin-top:20px;color:#d8e3fb;font-size:11px}.admin-state-dot{width:8px;height:8px;border-radius:50%;background:#4bd4a2;box-shadow:0 0 0 5px #4bd4a226}.admin-hero-meta span:last-child{color:#aebde0}.admin-extension-card{display:grid;grid-template-columns:42px minmax(0,1fr) auto;gap:11px;align-items:center;padding:14px;border:1px solid #ffffff2c;border-radius:14px;background:#ffffff12}.admin-extension-icon{width:42px;height:42px;display:grid;place-items:center;border-radius:12px;background:#fff;color:#2a4dba;font-size:21px;font-weight:800}.admin-extension-copy{min-width:0}.admin-extension-copy span,.admin-extension-copy strong,.admin-extension-copy small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.admin-extension-copy span{color:#c5d1ee;font-size:11px}.admin-extension-copy strong{margin:3px 0;color:#fff;font-size:14px}.admin-extension-copy small{color:#b8c6e4;font-size:10px}.admin-extension-actions{display:flex;flex-direction:column;gap:6px}.admin-extension-actions :deep(.el-button){margin:0;border:0;background:#fff;color:#284bb7;font-weight:700}.admin-extension-actions button{border:0;background:transparent;color:#dbe5fc;font-size:10px;cursor:pointer}
.admin-kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}.kpi-card{position:relative;min-width:0;overflow:hidden;padding:16px 18px 15px;border:1px solid var(--line);border-radius:15px;background:#fff;box-shadow:0 5px 18px #17243d09}.kpi-card::before{content:"";position:absolute;inset:0 0 auto;height:3px;background:#dbe4ff}.kpi-account::before{background:#5e7ddd}.kpi-candidates::before{background:#36b68a}.kpi-sync::before{background:#6a8ce8}.kpi-attention::before{background:#d8a044}.kpi-communication::before{background:#9c68d9}.kpi-card-top{display:flex;align-items:center;justify-content:space-between;gap:8px}.kpi-mark{width:25px;height:25px;display:grid;place-items:center;border-radius:8px;background:#f0f4ff;color:#4665c7;font-size:11px;font-weight:800}.kpi-candidates .kpi-mark{background:#eaf8f2;color:#218666}.kpi-sync .kpi-mark{background:#eef2ff;color:#5574d0}.kpi-attention .kpi-mark{background:#fff6e5;color:#a46e1d}.kpi-communication .kpi-mark{background:#f3edff;color:#8b5cc7}.admin-kpis .kpi-label,.admin-kpis small,.admin-kpis strong{display:block;position:relative;z-index:1}.admin-kpis .kpi-label{color:#68758b;font-size:12px;font-weight:650}.admin-kpis strong{margin:9px 0 3px;font-size:29px;letter-spacing:-.045em}.admin-kpis small{color:#929cad;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.admin-kpis .alert-kpi{border-color:#f3cbd1;background:#fffafb}.admin-kpis .alert-kpi::before{background:#d55368}.admin-kpis .alert-kpi .kpi-mark{background:#fff0f2;color:#c8475d}.admin-kpis .alert-kpi strong{color:#c8475d}.admin-kpis .attention-kpi:not(.alert-kpi){border-color:#f0dfbe}.admin-kpis .attention-kpi:not(.alert-kpi)::before{background:#d8a044}.admin-kpis .attention-kpi:not(.alert-kpi) strong{color:#a66d18}
.admin-grid{display:grid;grid-template-columns:minmax(320px,.72fr) minmax(0,1.28fr);gap:16px;align-items:start}.admin-panel{min-width:0;padding:21px 22px;border:1px solid var(--line);border-radius:17px;background:#fff;box-shadow:0 6px 21px #17243d09}.admin-panel-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:15px;margin-bottom:17px}.admin-panel-heading h2{margin:5px 0 4px;font-size:18px;letter-spacing:-.025em}.admin-panel-heading p{margin:0;color:#8994a7;font-size:11px}.section-kicker{color:#8b97aa;font-size:9px;font-weight:800;letter-spacing:.13em}.account-health-panel{order:2}.attention-panel{order:1}.account-health-list{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}.account-health-row{display:grid;grid-template-columns:35px minmax(0,1fr);grid-template-areas:"symbol name" "symbol total" "symbol active" "symbol status" "symbol time";align-items:center;gap:7px 9px;padding:13px 11px;border:1px solid #eef1f5;border-radius:12px;background:#fff;text-align:left;color:inherit;cursor:pointer}.account-health-row:first-child{border-top:1px solid #eef1f5}.account-health-row:hover,.account-health-row.selected{background:#f6f8ff}.account-health-row.selected{box-shadow:inset 3px 0 #4b70d7}.account-symbol{grid-area:symbol;width:33px;height:33px;display:grid;place-items:center;border-radius:10px;background:#15233d;color:#5cdbaf;font-weight:800}.account-name{grid-area:name;min-width:0}.account-health-metric:nth-of-type(1){grid-area:total}.account-health-metric:nth-of-type(2){grid-area:active}.account-health-metric{display:flex;align-items:baseline;gap:5px}.account-name strong,.account-name small,.account-health-metric strong,.account-health-metric small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.account-name strong{font-size:12px}.account-name small{margin-top:3px;color:#929cad;font-size:10px}.account-health-metric strong{font-size:14px}.account-health-metric small{color:#9ca5b4;font-size:9px}.account-status{grid-area:status;display:flex;align-items:center;gap:6px;font-size:10px;font-weight:700;white-space:nowrap}.account-status i{width:7px;height:7px;border-radius:50%;background:#24a679}.account-status.warning{color:#a16d1b}.account-status.warning i{background:#d99a31}.account-status.danger{color:#c7475b}.account-status.danger i{background:#d75468}.account-health-time{grid-area:time;color:#9aa3b2;font-size:9px;white-space:nowrap}
.signal-count{min-width:24px;height:24px;display:grid;place-items:center;border-radius:8px;background:#fff0f2;color:#c8475d;font-size:11px;font-weight:800}.attention-list{display:flex;flex-direction:column;gap:8px}.attention-list article{display:grid;grid-template-columns:8px minmax(0,1fr) auto;gap:10px;align-items:center;padding:11px;border-radius:11px;background:#fafbfc}.attention-list article>i{width:8px;height:8px;border-radius:50%;background:#4b70d7}.attention-list article.warning>i{background:#dc982e}.attention-list article.danger>i{background:#d55368}.attention-list strong,.attention-list small{display:block}.attention-list strong{font-size:11px}.attention-list small{margin-top:3px;color:#8e98a8;font-size:9px;line-height:1.4}.admin-clear{display:flex;align-items:center;gap:10px;padding:15px;border-radius:11px;background:#effaf5}.admin-clear>span{width:28px;height:28px;display:grid;place-items:center;border-radius:50%;background:#28a779;color:#fff}.admin-clear strong,.admin-clear small{display:block}.admin-clear strong{font-size:11px}.admin-clear small{margin-top:3px;color:#779385;font-size:9px}.decision-strip{display:flex;flex-wrap:wrap;gap:7px;margin-top:15px;padding-top:13px;border-top:1px solid #eef1f5}.decision-strip strong{width:100%;font-size:10px}.decision-strip span{padding:4px 7px;border-radius:6px;background:#f5f7fa;color:#7d8798;font-size:9px}
.communication-panel{margin-top:0}.communication-count{background:#f3edff;color:#865bc4}.communication-table :deep(.el-table__cell){padding:10px 0}.communication-table strong{font-size:11px}.communication-table small{color:#8d97a7;font-size:10px}.message-number{color:#7050b8;font-size:14px!important}.scope-note{color:#8e98a8;font-size:9px}.data-explainer{display:flex;align-items:flex-start;gap:10px;margin:0 0 15px;padding:11px 13px;border:1px solid #dce8ff;border-radius:10px;background:#f6f9ff;color:#5d6f8e;font-size:11px;line-height:1.65}.data-explainer strong{flex:none;color:#3557a7;font-size:11px}.data-explainer span{min-width:0}
.candidate-panel{padding-bottom:14px;overflow:hidden}.candidate-heading{align-items:end}.candidate-tools{display:flex;gap:8px}.account-select{width:145px}.candidate-search{width:200px}.candidate-scope{display:flex;align-items:center;gap:8px;margin:-3px 0 13px;padding:9px 11px;border-radius:9px;background:#f6f8fb;color:#8a95a7;font-size:10px}.candidate-scope strong{color:#35435a;font-size:11px}.candidate-scope span:nth-child(3){padding-left:7px;border-left:1px solid #dfe5ee}.candidate-scope .el-button{margin-left:auto;font-size:11px}.admin-candidate-table{width:100%;font-size:11px}.admin-candidate-table :deep(.el-table__header-wrapper){background:#f8f9fc}.admin-candidate-table :deep(.el-table__cell){padding:9px 0}.admin-candidate-table :deep(.cell){line-height:1.35}.admin-candidate-table :deep(.el-table__body-wrapper){overscroll-behavior:contain}.table-account,.table-account+small{display:block}.table-account{color:#35435a;font-weight:650}.table-account+small{margin-top:3px;color:#929cac;font-size:9px}.table-status{padding:4px 7px;border-radius:999px;font-size:9px;font-weight:700}.table-status.success{background:#eaf8f1;color:#187d5d}.table-status.warning{background:#fff4df;color:#a16a19}.table-status.danger{background:#fff0f2;color:#c4475c}.admin-empty{padding:28px;text-align:center;color:#98a1af;font-size:11px}.admin-footer{padding:0 3px 6px;color:#a2aab7;font-size:10px;text-align:right}
@media(max-width:1100px){.admin-hero{grid-template-columns:1fr}.admin-extension-card{max-width:560px}.admin-grid{grid-template-columns:1fr}.account-health-list{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:1100px){.admin-kpis{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:760px){.admin-hero{padding:22px 20px}.admin-hero h1{font-size:26px}.admin-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.admin-panel{padding:18px 15px}.candidate-heading{align-items:stretch;flex-direction:column}.candidate-tools{width:100%;flex-direction:column}.account-select,.candidate-search{width:100%}.account-health-list{grid-template-columns:repeat(2,minmax(0,1fr))}.candidate-scope{flex-wrap:wrap}.candidate-scope .el-button{width:100%;margin-left:0;padding-left:0;text-align:left}.scope-note{width:100%;padding-left:0!important;border-left:0!important}}
@media(max-width:480px){.account-health-list{grid-template-columns:1fr}}
@media(max-width:480px){.admin-extension-card{grid-template-columns:38px minmax(0,1fr)}.admin-extension-icon{width:38px;height:38px}.admin-extension-actions{grid-column:1 / -1;flex-direction:row}.admin-extension-actions :deep(.el-button){flex:1}.admin-kpis{grid-template-columns:1fr 1fr}.admin-kpis article{padding:14px}.admin-kpis strong{font-size:25px}.admin-kpis small{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.account-name strong{max-width:125px}}
</style>
