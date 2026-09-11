<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { useAuthStore } from "../stores/auth";
import AdminDashboard from "./AdminDashboard.vue";

type Alert = { id: string; severity: "critical" | "warning"; title: string; detail: string };
type Worker = { name: string; label: string; status: string; last_seen_at: string | null; total_processed: number };
type Device = { device_id: string; device_name: string; status: string; last_seen_at: string | null };
type Binding = { recruiter_id: string; boss_accounts: string[]; bound: boolean; feishu_display_name: string | null; active_device_count: number; source_count: number; last_checkpoint_at: string | null; devices: Device[] };
type Failure = { kind: string; id: string; status: string; retry_count: number; last_error: string | null; updated_at: string };
type Operations = { generated_at: string; overall_status: "healthy" | "warning" | "critical"; alerts: Alert[]; metrics: Record<string, number>; workers: Worker[]; queues: Record<string, Record<string, number>>; bindings: Binding[]; recent_failures: Failure[] };
type Overview = { candidate_queries: number; engagements: number; open_conflicts: number; interviews: number; unmapped_jobs: number; notification_failures: number; message_sent?: number };
type Candidate = { id: string; candidate_display_name: string; candidate_age: number | null; candidate_experience: string | null; candidate_education: string | null; raw_job_name: string; recruitment_status: string; conversation_started_at: string | null; conversation_updated_at: string | null; last_seen_at: string | null; created_at: string | null; updated_at: string | null };
type Interview = { id: string; candidate_source_id: string; scheduled_at: string; duration_minutes: number; location_type: string; location_text: string | null; status: string };
type ExtensionRelease = { version: string; published_at: string; sha256: string; file_name: string; size_bytes: number };
type ActionItem = { id: string; tone: "danger" | "warning" | "info"; title: string; detail: string; route?: string; failure?: Failure };

const auth = useAuthStore();
const router = useRouter();
const operations = ref<Operations | null>(null);
const overview = ref<Overview | null>(null);
const candidates = ref<Candidate[]>([]);
const interviews = ref<Interview[]>([]);
const extensionRelease = ref<ExtensionRelease | null>(null);
const loading = ref(true);
const extensionLoading = ref(false);
const showInstallGuide = ref(false);
const lastError = ref("");
const timer = ref<number>();

// Keep the company-wide administrator cockpit separate from the recruiter
// workspace.  Both views use the same route, but an admin should never have
// to infer whether a card is personal or company-wide.
const isAdmin = computed(() => auth.user?.workspace?.status === "ADMIN");

const canManage = computed(() => !!auth.user?.capabilities?.can_manage_team);
const onboarding = computed(() => ["UNASSIGNED", "EXTENSION_REQUIRED"].includes(auth.user?.workspace?.status || ""));
const needsAssignment = computed(() => auth.user?.workspace?.status === "UNASSIGNED");
const scopeLabel = computed(() => auth.user?.capabilities?.data_scope === "COMPANY" ? "团队招聘" : "我的招聘");
const greeting = computed(() => {
  const hour = new Date().getHours();
  if (hour < 11) return "早上好";
  if (hour < 14) return "中午好";
  if (hour < 18) return "下午好";
  return "晚上好";
});
const generated = computed(() => operations.value ? formatTime(operations.value.generated_at) : "—");
const totalCandidates = computed(() => overview.value?.candidate_queries ?? candidates.value.length);
const todayNew = computed(() => candidates.value.filter((row) => isToday(row.conversation_started_at || row.created_at)).length);
const activeCandidates = computed(() => candidates.value.filter((row) => !["已拒绝", "已入职", "已淘汰", "已结束"].includes(row.recruitment_status)).length);
const pendingSync = computed(() => (operations.value?.metrics.candidate_sync_pending ?? 0) + (operations.value?.metrics.candidate_sync_failed ?? 0));
const attentionCount = computed(() => pendingSync.value + (overview.value?.open_conflicts ?? 0));
const scheduledInterviews = computed(() => overview.value?.interviews ?? interviews.value.filter((row) => row.status === "SCHEDULED").length);
const messageSent = computed(() => operations.value?.metrics.message_sent_total ?? overview.value?.message_sent ?? 0);
const syncSent = computed(() => operations.value?.metrics.candidate_sync_sent ?? 0);
const syncRate = computed(() => totalCandidates.value ? Math.min(100, Math.round((syncSent.value / totalCandidates.value) * 100)) : 100);
const activeDevices = computed(() => operations.value?.metrics.active_devices ?? 0);
const recentCandidates = computed(() => [...candidates.value].sort((a, b) => candidateTimestamp(b) - candidateTimestamp(a)).slice(0, 6));
const candidateById = computed(() => new Map(candidates.value.map((row) => [row.id, row])));
const upcomingInterviews = computed(() => interviews.value.filter((row) => row.status === "SCHEDULED").sort((a, b) => new Date(a.scheduled_at).getTime() - new Date(b.scheduled_at).getTime()).slice(0, 4));
const funnelStages = computed(() => {
  const definitions = [
    { key: "沟通中", label: "沟通中", tone: "blue" },
    { key: "已获取简历", label: "已获取简历", tone: "cyan" },
    { key: "待约面", label: "待约面", tone: "violet" },
    { key: "已约面", label: "已约面", tone: "amber" },
  ];
  const counts = new Map<string, number>();
  candidates.value.forEach((row) => counts.set(row.recruitment_status, (counts.get(row.recruitment_status) ?? 0) + 1));
  const max = Math.max(...definitions.map((item) => counts.get(item.key) ?? 0), 1);
  return definitions.map((item) => ({ ...item, count: counts.get(item.key) ?? 0, width: `${Math.max(((counts.get(item.key) ?? 0) / max) * 100, 2)}%` }));
});
const actionItems = computed<ActionItem[]>(() => {
  const items: ActionItem[] = [];
  const failures = operations.value?.recent_failures.filter((row) => row.kind === "candidate_sync") ?? [];
  failures.slice(0, 2).forEach((failure) => items.push({ id: failure.id, tone: "danger", title: "有候选人尚未同步到飞书", detail: failure.last_error || "同步任务失败，可以立即重试。", failure }));
  if (overview.value?.open_conflicts) items.push({ id: "conflicts", tone: "warning", title: `${overview.value.open_conflicts} 位候选人可能被重复跟进`, detail: "请先确认归属，避免多人重复联系。", route: "/recruitment/candidates" });
  // A raw job name from the BOSS communication list is already usable. Any
  // pending normalization is informational and must not look like a blocker.
  return items.slice(0, 5);
});

function isToday(value: string | null) {
  if (!value) return false;
  const date = new Date(value), today = new Date();
  return date.getFullYear() === today.getFullYear() && date.getMonth() === today.getMonth() && date.getDate() === today.getDate();
}
function candidateTimestamp(row: Candidate) { return new Date(row.conversation_updated_at || row.updated_at || row.last_seen_at || 0).getTime(); }
function formatTime(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}
function formatRelative(value: string | null) {
  if (!value) return "暂无更新时间";
  const diff = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(diff)) return formatTime(value);
  if (diff < 60_000) return "刚刚更新";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前更新`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前更新`;
  return formatTime(value);
}
function candidateMeta(row: Candidate) {
  return [row.candidate_age ? `${row.candidate_age}岁` : "", row.candidate_experience || "", row.candidate_education || ""].filter(Boolean).join(" · ") || "资料待补充";
}
function statusTone(status: string) {
  if (["已入职", "已约面"].includes(status)) return "success";
  if (["待约面", "已获取简历"].includes(status)) return "warning";
  if (["已拒绝", "已淘汰"].includes(status)) return "muted";
  return "primary";
}
function overallCopy() {
  if (operations.value?.overall_status === "critical") return { label: "数据同步异常", detail: "有数据未完成同步，请查看待办。", tone: "danger" };
  if (operations.value?.overall_status === "warning") return { label: "有事项待处理", detail: "招聘数据已更新，部分事项需要关注。", tone: "warning" };
  return { label: "数据更新正常", detail: "候选人和飞书数据正在正常同步。", tone: "success" };
}
function interviewCandidate(row: Interview) { return candidateById.value.get(row.candidate_source_id); }
function locationText(row: Interview) { return row.location_text || (row.location_type === "ONLINE" ? "线上面试" : "面试地点待确认"); }
function donutStyle() { return { "--sync-rate": `${syncRate.value * 3.6}deg` }; }

async function load(silent = false) {
  if (!silent) loading.value = true;
  try {
    await auth.refreshIdentity();
    if (onboarding.value) {
      operations.value = null; overview.value = null; candidates.value = []; interviews.value = []; lastError.value = "";
      return;
    }
    const [operationData, overviewData, candidateRows, interviewRows] = await Promise.all([
      auth.client.request<Operations>("/admin/operations"), auth.client.request<Overview>("/admin/overview"), auth.client.list<Candidate>("candidate-sources"), auth.client.list<Interview>("interviews"),
    ]);
    operations.value = operationData; overview.value = overviewData; candidates.value = candidateRows; interviews.value = interviewRows; lastError.value = "";
  } catch (error) {
    lastError.value = error instanceof Error ? error.message : "招聘数据加载失败";
    if (!silent) ElMessage.error(lastError.value);
  } finally { loading.value = false; }
}
async function loadExtensionRelease() {
  try { extensionRelease.value = await auth.client.request<ExtensionRelease>(`/admin/extension-release?ts=${Date.now()}`); } catch { extensionRelease.value = null; }
}
async function downloadExtension() {
  extensionLoading.value = true;
  try {
    const apiBase = import.meta.env.VITE_API_BASE_URL ?? (import.meta.env.DEV ? "http://localhost:8000/api/v1" : "/api/v1");
    const response = await fetch(`${apiBase}/admin/extension-release/download?ts=${Date.now()}`, { credentials: "include", cache: "no-store" });
    if (!response.ok) throw new Error("扩展下载失败");
    const blob = await response.blob(), url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = extensionRelease.value?.file_name ?? "recruitment-collab-extension.zip"; link.click(); URL.revokeObjectURL(url); showInstallGuide.value = true;
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : "扩展下载失败"); }
  finally { extensionLoading.value = false; }
}
async function retry(failure: Failure) {
  try { await auth.client.request(`/admin/candidate-sync/${failure.id}/retry`, { method: "POST" }); ElMessage.success("已重新同步"); await load(true); }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : "重试失败"); }
}
async function revoke(device: Device, binding: Binding) {
  try {
    await ElMessageBox.confirm(`撤销 ${binding.boss_accounts.join(" / ")} 的扩展设备“${device.device_name}”？`, "撤销扩展设备", { type: "warning", confirmButtonText: "确认撤销", cancelButtonText: "取消" });
    await auth.client.request(`/admin/devices/${encodeURIComponent(device.device_id)}/revoke`, { method: "POST" }); ElMessage.success("设备已撤销"); await load(true);
  } catch (error) { if (error !== "cancel" && error !== "close") ElMessage.error(error instanceof Error ? error.message : "撤销失败"); }
}
onMounted(() => { void load(); void loadExtensionRelease(); timer.value = window.setInterval(() => void load(true), 15_000); });
onBeforeUnmount(() => { if (timer.value) window.clearInterval(timer.value); });
</script>

<template>
  <AdminDashboard v-if="isAdmin" />
  <div v-else v-loading="loading" class="recruitment-dashboard">
    <section class="dashboard-hero">
      <div class="hero-copy">
        <div class="hero-kicker"><span>{{ scopeLabel }}</span><i></i><span>数据每 15 秒更新</span></div>
        <h1>{{ greeting }}，{{ auth.user?.display_name || "招聘伙伴" }}</h1>
        <p>{{ onboarding ? "飞书登录成功，完成下方步骤即可查看自己的招聘数据。" : "今天的候选人进展、面试安排和待处理事项都在这里。" }}</p>
        <div v-if="!onboarding" class="hero-status" :class="overallCopy().tone"><span class="status-dot"></span><strong>{{ overallCopy().label }}</strong><span>{{ overallCopy().detail }}</span><button type="button" @click="load()">刷新</button></div>
      </div>
      <aside class="extension-quick-card">
        <div class="extension-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M8 3v4M16 3v4M6.5 7h11A2.5 2.5 0 0 1 20 9.5v1A6.5 6.5 0 0 1 13.5 17H13v4h-2v-4h-.5A6.5 6.5 0 0 1 4 10.5v-1A2.5 2.5 0 0 1 6.5 7Z" /></svg></div>
        <div class="extension-copy"><span>招聘协同助手</span><strong v-if="extensionRelease">最新版 v{{ extensionRelease.version }}</strong><strong v-else>安装包暂不可用</strong><small>用于同步 BOSS 候选人数据</small></div>
        <div class="extension-actions"><el-button type="primary" :loading="extensionLoading" :disabled="!extensionRelease" @click="downloadExtension">下载扩展</el-button><button type="button" class="guide-link" @click="showInstallGuide = true">安装说明</button></div>
      </aside>
    </section>

    <el-alert v-if="lastError" type="error" :title="lastError" show-icon :closable="false" />
    <el-alert v-if="onboarding" type="info" :title="needsAssignment ? '飞书登录成功，请安装扩展并绑定 BOSS 账号' : `已分配 BOSS 账号：${auth.user?.workspace?.boss_account_name}，请连接扩展`" description="飞书账号代表你本人，BOSS 账号承载招聘数据。首次在未被占用的 BOSS 账号页面登录扩展会自动完成绑定；已属于其他人的账号只能由管理员调整。" show-icon :closable="false" />

    <section v-if="onboarding" class="dashboard-panel pending-panel">
      <div class="pending-steps">
        <article><span class="step-num">1</span><div><strong>下载并安装扩展</strong><small>下载右上角的“招聘协同助手”，解压后在 Chrome 扩展页加载。</small></div></article>
        <article><span class="step-num">2</span><div><strong>打开要负责的 BOSS 账号</strong><small>{{ needsAssignment ? '登录你要负责的 BOSS 招聘账号并进入沟通页。' : `进入 BOSS 账号“${auth.user?.workspace?.boss_account_name}”的沟通页。` }}</small></div></article>
        <article><span class="step-num">3</span><div><strong>用当前飞书账号登录扩展</strong><small>{{ needsAssignment ? '首次登录会自动建立飞书与这个 BOSS 账号的绑定，以后无需重复操作。' : '连接成功后会继承这个 BOSS 账号已有的候选人和同步历史。' }}</small></div></article>
      </div>
      <el-button @click="load()">刷新连接状态</el-button>
    </section>

    <template v-if="!onboarding">
    <section class="recruitment-kpis" aria-label="招聘数据概览">
      <article class="kpi-card kpi-primary"><div class="kpi-icon">人</div><div class="kpi-label">候选人总数</div><strong>{{ totalCandidates }}</strong><p>当前权限范围内全部候选人</p></article>
      <article class="kpi-card kpi-blue"><div class="kpi-icon">今</div><div class="kpi-label">今日新增</div><strong>{{ todayNew }}</strong><p>今天开始沟通的候选人</p></article>
      <article class="kpi-card kpi-violet"><div class="kpi-icon">进</div><div class="kpi-label">正在推进</div><strong>{{ activeCandidates }}</strong><p>仍在沟通或安排面试</p></article>
      <article class="kpi-card kpi-amber"><div class="kpi-icon">面</div><div class="kpi-label">待进行面试</div><strong>{{ scheduledInterviews }}</strong><p>已安排、等待进行的面试</p></article>
      <article class="kpi-card kpi-violet"><div class="kpi-icon">✉</div><div class="kpi-label">主动沟通</div><strong>{{ messageSent }}</strong><p>成功发出的候选人消息</p></article>
      <article class="kpi-card" :class="attentionCount ? 'kpi-red' : 'kpi-green'"><div class="kpi-icon">{{ attentionCount ? "!" : "✓" }}</div><div class="kpi-label">需要处理</div><strong>{{ attentionCount }}</strong><p>{{ attentionCount ? "同步失败或重复候选人" : "当前没有阻塞事项" }}</p></article>
    </section>

    <div class="dashboard-main-grid">
      <section class="dashboard-panel funnel-panel">
        <div class="panel-heading"><div><span class="section-kicker">RECRUITMENT PIPELINE</span><h2>候选人推进情况</h2><p>从首次沟通到面试、入职，快速看清当前招聘进度。</p></div><el-button text type="primary" @click="router.push('/recruitment/candidates')">查看全部候选人 →</el-button></div>
        <div class="funnel-list"><div v-for="stage in funnelStages" :key="stage.key" class="funnel-row"><span>{{ stage.label }}</span><div class="funnel-track"><i :class="stage.tone" :style="{ width: stage.width }"></i></div><strong>{{ stage.count }}</strong></div></div>
        <div class="funnel-footnote"><span>已拒绝或结束</span><strong>{{ candidates.filter((row) => ['已拒绝', '已淘汰', '已结束'].includes(row.recruitment_status)).length }}</strong><small>这些候选人不计入“正在推进”</small></div>
      </section>
      <section class="dashboard-panel sync-panel">
        <div class="panel-heading compact"><div><span class="section-kicker">DATA SYNC</span><h2>数据同步</h2><p>候选人先进入后台；飞书表是可选共享出口。</p></div></div>
        <div class="sync-visual"><div class="sync-donut" :style="donutStyle()"><div><strong>{{ syncRate }}%</strong><span>已同步</span></div></div><div class="sync-summary"><div><i class="sent"></i><span>已同步飞书</span><strong>{{ syncSent }}</strong></div><div><i class="pending"></i><span>等待同步</span><strong>{{ operations?.metrics.candidate_sync_pending ?? 0 }}</strong></div><div><i class="failed"></i><span>同步失败</span><strong>{{ operations?.metrics.candidate_sync_failed ?? 0 }}</strong></div></div></div>
        <div class="connection-strip"><span class="connection-icon">↗</span><div><strong>{{ activeDevices ? `${activeDevices} 个扩展在线` : "扩展当前未在线" }}</strong><small>{{ operations?.metrics.bound_recruiters ?? 0 }} 个飞书账号已分配</small></div></div>
      </section>
    </div>

    <div class="dashboard-content-grid">
      <section class="dashboard-panel recent-panel">
        <div class="panel-heading"><div><span class="section-kicker">RECENT CANDIDATES</span><h2>最近跟进的候选人</h2><p>按最近聊天或资料更新时间排列。</p></div><el-button text type="primary" @click="router.push('/recruitment/candidates')">候选人协同 →</el-button></div>
        <div v-if="recentCandidates.length" class="candidate-list"><article v-for="candidate in recentCandidates" :key="candidate.id" class="candidate-row"><div class="candidate-avatar">{{ candidate.candidate_display_name?.slice(0, 1) || "候" }}</div><div class="candidate-name"><strong :title="candidate.candidate_display_name">{{ candidate.candidate_display_name }}</strong><small :title="candidateMeta(candidate)">{{ candidateMeta(candidate) }}</small></div><div class="candidate-job"><span>应聘岗位</span><strong :title="candidate.raw_job_name">{{ candidate.raw_job_name || "岗位待确认" }}</strong></div><span class="candidate-status" :class="statusTone(candidate.recruitment_status)">{{ candidate.recruitment_status || "沟通中" }}</span><time>{{ formatRelative(candidate.conversation_updated_at || candidate.updated_at || candidate.last_seen_at) }}</time></article></div>
        <div v-else class="friendly-empty"><span>⌁</span><strong>还没有候选人数据</strong><p>安装扩展并打开 BOSS 沟通页面后，候选人会自动出现在这里。</p></div>
      </section>
      <div class="dashboard-side-stack">
        <section class="dashboard-panel action-panel"><div class="panel-heading compact"><div><span class="section-kicker">TO DO</span><h2>现在要处理</h2><p>优先处理会影响招聘推进的事项。</p></div><span v-if="actionItems.length" class="count-badge">{{ actionItems.length }}</span></div><div v-if="actionItems.length" class="action-list"><article v-for="item in actionItems" :key="item.id" :class="['action-row', item.tone]"><i></i><div><strong>{{ item.title }}</strong><small>{{ item.detail }}</small></div><el-button v-if="item.failure" text type="primary" @click="retry(item.failure)">重试</el-button><el-button v-else-if="item.route" text type="primary" @click="router.push(item.route)">查看</el-button></article></div><div v-else class="clear-state"><span>✓</span><div><strong>今天很顺畅</strong><small>暂无需要立即处理的事项</small></div></div></section>
        <section class="dashboard-panel interview-panel"><div class="panel-heading compact"><div><span class="section-kicker">INTERVIEWS</span><h2>接下来的面试</h2></div><el-button text type="primary" @click="router.push('/recruitment/jobs-workspace')">全部 →</el-button></div><div v-if="upcomingInterviews.length" class="interview-list"><article v-for="item in upcomingInterviews" :key="item.id"><time><strong>{{ new Date(item.scheduled_at).getDate() }}</strong><span>{{ new Date(item.scheduled_at).toLocaleString('zh-CN', { month: 'short' }) }}</span></time><div><strong>{{ interviewCandidate(item)?.candidate_display_name || "候选人" }}</strong><small>{{ formatTime(item.scheduled_at) }} · {{ locationText(item) }}</small></div></article></div><div v-else class="mini-empty">暂无待进行的面试</div></section>
      </div>
    </div>

    <section class="dashboard-panel account-panel">
      <div class="panel-heading"><div><span class="section-kicker">ACCOUNT & EXTENSION</span><h2>{{ canManage ? "账号与扩展概况" : "我的 BOSS 账号与扩展" }}</h2><p>查看账号归属、候选人数和扩展最近连接情况。</p></div></div>
      <div v-if="operations?.bindings.length" class="binding-grid"><article v-for="binding in operations.bindings" :key="binding.recruiter_id" class="binding-card"><div class="binding-top"><span class="boss-mark">B</span><div><span>BOSS 招聘账号</span><strong :title="binding.boss_accounts.join(' / ')">{{ binding.boss_accounts.join(" / ") || "待识别" }}</strong></div><span :class="['online-pill', binding.active_device_count ? 'online' : 'offline']">{{ binding.active_device_count ? "扩展在线" : "扩展离线" }}</span></div><div class="binding-facts"><div><span>飞书负责人</span><strong>{{ binding.feishu_display_name || "未分配" }}</strong></div><div><span>候选人数</span><strong>{{ binding.source_count }}</strong></div><div><span>最近同步</span><strong>{{ formatRelative(binding.last_checkpoint_at) }}</strong></div></div><div v-if="binding.devices.length" class="device-chips"><div v-for="device in binding.devices" :key="device.device_id"><span>{{ device.device_name }}</span><small>{{ formatRelative(device.last_seen_at) }}</small><el-button v-if="device.status === 'ACTIVE' && canManage" text type="danger" @click="revoke(device, binding)">撤销</el-button></div></div></article></div>
      <div v-else class="friendly-empty compact-empty"><strong>尚未连接 BOSS 账号</strong><p>请在要负责的 BOSS 沟通页打开扩展，并使用当前飞书账号登录。首次绑定成功后，候选人数据会进入你的工作台。</p></div>
    </section>
    </template>

    <details v-if="canManage" class="system-details"><summary><span><strong>管理员：系统运行明细</strong><small>服务心跳和数据通道状态</small></span><span>展开查看</span></summary><div class="system-detail-body"><div class="worker-grid"><article v-for="worker in operations?.workers ?? []" :key="worker.name"><i :class="worker.status === 'HEALTHY' ? 'healthy' : 'unhealthy'"></i><div><strong>{{ worker.label }}</strong><small>最近心跳 {{ formatTime(worker.last_seen_at) }}</small></div><span>{{ worker.total_processed }}</span></article></div><div v-if="operations?.alerts.length" class="system-alerts"><article v-for="alert in operations.alerts" :key="alert.id"><strong>{{ alert.title }}</strong><span>{{ alert.detail }}</span></article></div></div></details>
    <footer class="dashboard-footer">最近更新 {{ generated }}</footer>

    <el-dialog v-model="showInstallGuide" title="扩展安装步骤" width="min(560px, 92vw)"><p>下载 ZIP 后请按以下步骤安装：</p><ol class="install-steps"><li>下载并解压 ZIP 文件。</li><li>打开 Chrome 地址：<code>chrome://extensions</code>。</li><li>开启右上角“开发者模式”。</li><li>点击“加载已解压的扩展程序”，选择解压后的目录。</li><li>打开 BOSS 直聘并刷新页面。</li><li>点击插件，使用自己的飞书账号完成登录。</li></ol><el-alert type="info" :closable="false" title="ZIP 需要先解压。插件更新后重新下载最新版，并在 Chrome 扩展页点击“重新加载”。" /></el-dialog>
  </div>
</template>

<style scoped>
.recruitment-dashboard {
  --ink: #17233a;
  --muted: #758198;
  --line: #e8edf4;
  max-width: 1500px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 18px;
  color: var(--ink);
}
.dashboard-hero {
  position: relative;
  overflow: hidden;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(360px, 430px);
  gap: 28px;
  align-items: stretch;
  padding: 30px 32px;
  border-radius: 22px;
  background: radial-gradient(circle at 70% -30%, #6f8cff4d, transparent 44%), linear-gradient(135deg, #17254b, #1f3470 55%, #294895);
  box-shadow: 0 18px 44px #2039782e;
  color: #fff;
}
.dashboard-hero::after { content: ""; position: absolute; width: 240px; height: 240px; border: 1px solid #ffffff14; border-radius: 50%; right: 28%; top: -160px; }
.hero-copy { min-width: 0; position: relative; z-index: 1; }
.hero-kicker { display: flex; align-items: center; gap: 9px; color: #c8d5ff; font-size: 12px; font-weight: 650; }
.hero-kicker i { width: 3px; height: 3px; border-radius: 50%; background: #84a0ff; }
.hero-copy h1 { margin: 13px 0 7px; font-size: clamp(28px, 3vw, 38px); letter-spacing: -.04em; line-height: 1.15; }
.hero-copy > p { margin: 0; color: #c9d4ec; font-size: 14px; }
.hero-status { width: fit-content; max-width: 100%; margin-top: 24px; display: flex; align-items: center; gap: 9px; padding: 9px 10px 9px 12px; border: 1px solid #ffffff21; border-radius: 999px; background: #08112b47; font-size: 12px; color: #d5def1; }
.hero-status strong { color: #fff; white-space: nowrap; }
.hero-status button { border: 0; border-left: 1px solid #ffffff24; padding: 0 3px 0 10px; background: transparent; color: #fff; cursor: pointer; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 auto; }
.hero-status.success .status-dot { background: #51dfa6; box-shadow: 0 0 0 4px #51dfa621; }
.hero-status.warning .status-dot { background: #ffcb67; box-shadow: 0 0 0 4px #ffcb6721; }
.hero-status.danger .status-dot { background: #ff7d8f; box-shadow: 0 0 0 4px #ff7d8f21; }
.extension-quick-card { position: relative; z-index: 1; min-width: 0; display: grid; grid-template-columns: 52px minmax(0, 1fr) auto; align-items: center; gap: 14px; padding: 17px; border: 1px solid #ffffff30; border-radius: 17px; background: #ffffff1c; box-shadow: inset 0 1px 0 #ffffff14; backdrop-filter: blur(16px); }
.extension-icon { width: 52px; height: 52px; display: grid; place-items: center; border-radius: 15px; background: linear-gradient(145deg, #fff, #dce6ff); color: #3157d5; box-shadow: 0 8px 20px #040f2d33; }
.extension-icon svg { width: 27px; height: 27px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.extension-copy { min-width: 0; }
.extension-copy span, .extension-copy strong, .extension-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.extension-copy span { color: #bdccef; font-size: 11px; }
.extension-copy strong { margin: 3px 0; color: #fff; font-size: 15px; }
.extension-copy small { color: #bdc9e3; font-size: 11px; }
.extension-actions { display: flex; flex-direction: column; align-items: stretch; gap: 7px; }
.extension-actions :deep(.el-button) { margin: 0; border: 0; background: #fff; color: #274bba; font-weight: 700; }
.guide-link { border: 0; background: transparent; color: #dbe4fb; font-size: 11px; cursor: pointer; }
.recruitment-kpis { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 14px; }
.kpi-card { position: relative; min-width: 0; overflow: hidden; padding: 20px 20px 18px; border: 1px solid var(--line); border-radius: 17px; background: #fff; box-shadow: 0 7px 24px #1e32500e; }
.kpi-card::before { content: ""; position: absolute; inset: 0 0 auto; height: 3px; background: var(--accent, #dbe4ff); }
.kpi-icon { width: 34px; height: 34px; display: grid; place-items: center; margin-bottom: 15px; border-radius: 10px; background: var(--accent-soft, #f1f4f8); color: var(--accent, #526078); font-size: 13px; font-weight: 800; }
.kpi-label { color: #58657a; font-size: 13px; font-weight: 650; }
.kpi-card > strong { display: block; margin: 4px 0; color: var(--ink); font-size: 32px; line-height: 1.1; letter-spacing: -.045em; }
.kpi-card p { margin: 0; overflow: hidden; color: #929bad; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.kpi-primary { --accent: #3157d5; --accent-soft: #edf1ff; }
.kpi-blue { --accent: #198bd1; --accent-soft: #eaf7ff; }
.kpi-violet { --accent: #7456d8; --accent-soft: #f1edff; }
.kpi-amber { --accent: #b97818; --accent-soft: #fff5df; }
.kpi-red { --accent: #cf485d; --accent-soft: #fff0f2; }
.kpi-green { --accent: #17956a; --accent-soft: #eaf9f2; }
.dashboard-main-grid, .dashboard-content-grid { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(310px, .55fr); gap: 18px; align-items: start; }
.dashboard-content-grid { grid-template-columns: minmax(0, 1.45fr) minmax(330px, .55fr); }
.dashboard-side-stack { display: flex; flex-direction: column; gap: 18px; min-width: 0; }
.dashboard-panel { min-width: 0; padding: 23px 24px; border: 1px solid var(--line); border-radius: 18px; background: #fff; box-shadow: 0 8px 26px #1b2d500b; }
.panel-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; margin-bottom: 24px; }
.panel-heading.compact { margin-bottom: 18px; }
.panel-heading > div { min-width: 0; }
.section-kicker { color: #8b97aa; font-size: 10px; font-weight: 800; letter-spacing: .12em; }
.panel-heading h2 { margin: 5px 0 4px; color: var(--ink); font-size: 19px; letter-spacing: -.025em; }
.panel-heading p { margin: 0; color: #8a95a8; font-size: 12px; line-height: 1.55; }
.funnel-list { display: flex; flex-direction: column; gap: 15px; }
.funnel-row { display: grid; grid-template-columns: 88px minmax(0, 1fr) 32px; align-items: center; gap: 12px; }
.funnel-row > span { color: #5d697c; font-size: 12px; }
.funnel-row > strong { text-align: right; font-size: 13px; }
.funnel-track { height: 11px; overflow: hidden; border-radius: 999px; background: #f0f3f8; }
.funnel-track i { display: block; min-width: 5px; height: 100%; border-radius: inherit; transition: width .35s ease; }
.funnel-track i.blue { background: linear-gradient(90deg, #4368df, #6f8cf1); }
.funnel-track i.cyan { background: linear-gradient(90deg, #2e9fca, #5fc4df); }
.funnel-track i.violet { background: linear-gradient(90deg, #7456d8, #9b83ea); }
.funnel-track i.amber { background: linear-gradient(90deg, #e19a32, #f0be61); }
.funnel-track i.green { background: linear-gradient(90deg, #20a978, #54c99f); }
.funnel-footnote { display: flex; align-items: center; gap: 9px; margin-top: 21px; padding-top: 16px; border-top: 1px solid #eef1f5; color: #7a8597; font-size: 11px; }
.funnel-footnote strong { color: #3f4b5f; font-size: 14px; }
.funnel-footnote small { margin-left: auto; color: #a0a8b6; }
.sync-visual { display: grid; grid-template-columns: 124px minmax(0, 1fr); align-items: center; gap: 23px; padding: 4px 0 21px; }
.sync-donut { width: 124px; height: 124px; display: grid; place-items: center; border-radius: 50%; background: conic-gradient(#3c65df 0 var(--sync-rate), #edf1f7 var(--sync-rate) 360deg); position: relative; }
.sync-donut::after { content: ""; position: absolute; inset: 12px; border-radius: 50%; background: #fff; box-shadow: inset 0 0 0 1px #edf0f5; }
.sync-donut > div { position: relative; z-index: 1; text-align: center; }
.sync-donut strong, .sync-donut span { display: block; }
.sync-donut strong { font-size: 24px; letter-spacing: -.04em; }
.sync-donut span { margin-top: 2px; color: #8a95a8; font-size: 10px; }
.sync-summary { display: flex; flex-direction: column; gap: 11px; }
.sync-summary > div { display: grid; grid-template-columns: 7px minmax(0, 1fr) auto; align-items: center; gap: 8px; font-size: 11px; color: #748095; }
.sync-summary i { width: 7px; height: 7px; border-radius: 50%; }
.sync-summary i.sent { background: #3c65df; } .sync-summary i.pending { background: #e9a23b; } .sync-summary i.failed { background: #d85668; }
.sync-summary strong { color: var(--ink); font-size: 13px; }
.connection-strip { display: flex; align-items: center; gap: 11px; padding: 13px; border-radius: 12px; background: #f6f8fc; }
.connection-icon { width: 32px; height: 32px; display: grid; place-items: center; border-radius: 9px; background: #e8edfc; color: #3157d5; font-weight: 800; }
.connection-strip strong, .connection-strip small { display: block; } .connection-strip strong { font-size: 12px; } .connection-strip small { margin-top: 3px; color: #8d97a8; font-size: 10px; }
.candidate-list { display: flex; flex-direction: column; }
.candidate-row { min-width: 0; display: grid; grid-template-columns: 42px minmax(120px, 1fr) minmax(120px, .9fr) auto 92px; align-items: center; gap: 13px; padding: 13px 2px; border-top: 1px solid #eef1f5; }
.candidate-row:first-child { border-top: 0; padding-top: 2px; }
.candidate-avatar { width: 39px; height: 39px; display: grid; place-items: center; border-radius: 12px; background: linear-gradient(145deg, #e9eeff, #f6f8ff); color: #3157d5; font-size: 14px; font-weight: 800; }
.candidate-name, .candidate-job { min-width: 0; }
.candidate-name strong, .candidate-name small, .candidate-job strong, .candidate-job span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.candidate-name strong { font-size: 13px; } .candidate-name small { margin-top: 4px; color: #8d97a8; font-size: 10px; }
.candidate-job span { color: #9aa3b2; font-size: 9px; } .candidate-job strong { margin-top: 3px; color: #536075; font-size: 11px; font-weight: 600; }
.candidate-status { padding: 5px 9px; border-radius: 999px; font-size: 10px; font-weight: 700; white-space: nowrap; }
.candidate-status.primary { background: #edf1ff; color: #3659c6; } .candidate-status.warning { background: #fff3dc; color: #9b6819; } .candidate-status.success { background: #e9f8f1; color: #167c5b; } .candidate-status.muted { background: #f1f3f6; color: #788294; }
.candidate-row time { color: #9aa3b2; font-size: 9px; text-align: right; }
.action-list { display: flex; flex-direction: column; gap: 9px; }
.action-row { display: grid; grid-template-columns: 8px minmax(0, 1fr) auto; align-items: center; gap: 11px; padding: 12px; border: 1px solid #edf0f5; border-radius: 12px; }
.action-row > i { width: 8px; height: 8px; border-radius: 50%; }
.action-row.danger > i { background: #d85668; box-shadow: 0 0 0 4px #fff0f2; } .action-row.warning > i { background: #df982e; box-shadow: 0 0 0 4px #fff5e4; } .action-row.info > i { background: #4d70dc; box-shadow: 0 0 0 4px #edf1ff; }
.action-row strong, .action-row small { display: block; } .action-row strong { font-size: 11px; line-height: 1.35; } .action-row small { display: -webkit-box; overflow: hidden; margin-top: 4px; color: #9099a9; font-size: 9px; line-height: 1.4; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.count-badge { min-width: 24px; height: 24px; display: grid; place-items: center; border-radius: 8px; background: #fff0f2; color: #c94257; font-size: 11px; font-weight: 800; }
.clear-state { display: flex; align-items: center; gap: 11px; padding: 15px; border-radius: 12px; background: #effaf5; }
.clear-state > span { width: 31px; height: 31px; display: grid; place-items: center; border-radius: 50%; background: #28a779; color: #fff; }
.clear-state strong, .clear-state small { display: block; } .clear-state strong { font-size: 12px; } .clear-state small { margin-top: 3px; color: #739385; font-size: 10px; }
.interview-list { display: flex; flex-direction: column; gap: 9px; }
.interview-list article { display: flex; align-items: center; gap: 11px; padding: 9px 0; border-top: 1px solid #eef1f5; }
.interview-list article:first-child { border-top: 0; padding-top: 0; }
.interview-list time { width: 42px; height: 44px; display: grid; place-content: center; text-align: center; border-radius: 11px; background: #f0f3fb; color: #3157d5; }
.interview-list time strong, .interview-list time span { display: block; } .interview-list time strong { font-size: 16px; line-height: 1; } .interview-list time span { margin-top: 3px; font-size: 8px; }
.interview-list article > div { min-width: 0; } .interview-list article > div strong, .interview-list article > div small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } .interview-list article > div strong { font-size: 11px; } .interview-list article > div small { margin-top: 5px; color: #8c96a8; font-size: 9px; }
.mini-empty { padding: 22px 0; color: #9aa3b2; font-size: 11px; text-align: center; }
.pending-panel { padding: 26px 28px; }
.pending-steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.pending-steps article { display: flex; gap: 13px; align-items: flex-start; padding: 18px; border: 1px dashed #cdd9ee; border-radius: 14px; background: #f7faff; }
.pending-steps .step-num { flex: 0 0 auto; width: 30px; height: 30px; display: grid; place-items: center; border-radius: 50%; background: #274bba; color: #fff; font-size: 14px; font-weight: 700; }
.pending-steps strong, .pending-steps small { display: block; }
.pending-steps strong { color: #263248; font-size: 14px; }
.pending-steps small { margin-top: 6px; color: #7a8699; font-size: 12px; line-height: 1.7; }
@media (max-width: 900px) { .pending-steps { grid-template-columns: 1fr; } }
.friendly-empty { padding: 34px 20px; text-align: center; color: #8994a7; } .friendly-empty > span { display: block; margin-bottom: 8px; color: #6f7e99; font-size: 27px; } .friendly-empty strong { color: #526075; font-size: 13px; } .friendly-empty p { margin: 6px 0 0; font-size: 11px; }
.compact-empty { padding: 22px; border-radius: 12px; background: #f7f9fc; }
.binding-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 13px; }
.binding-card { min-width: 0; padding: 17px; border: 1px solid #e8ecf3; border-radius: 14px; background: linear-gradient(145deg, #fff, #fbfcff); }
.binding-top { display: grid; grid-template-columns: 38px minmax(0, 1fr) auto; align-items: center; gap: 11px; }
.boss-mark { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 11px; background: #17233a; color: #62ddb0; font-weight: 800; }
.binding-top > div { min-width: 0; } .binding-top > div span, .binding-top > div strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } .binding-top > div span { color: #929cad; font-size: 9px; } .binding-top > div strong { margin-top: 4px; font-size: 13px; }
.online-pill { padding: 5px 8px; border-radius: 999px; font-size: 9px; font-weight: 700; white-space: nowrap; } .online-pill.online { background: #eaf9f2; color: #167f5c; } .online-pill.offline { background: #f1f3f6; color: #7c8696; }
.binding-facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 15px; padding-top: 14px; border-top: 1px solid #edf0f5; }
.binding-facts span, .binding-facts strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } .binding-facts span { color: #9aa3b2; font-size: 9px; } .binding-facts strong { margin-top: 4px; color: #536075; font-size: 11px; }
.device-chips { margin-top: 13px; display: flex; flex-direction: column; gap: 7px; }
.device-chips > div { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 9px; background: #f6f8fb; font-size: 10px; }
.device-chips span { font-weight: 650; } .device-chips small { flex: 1; color: #939cac; text-align: right; }
.system-details { overflow: hidden; border: 1px solid #e4e9f1; border-radius: 15px; background: #fff; }
.system-details summary { display: flex; justify-content: space-between; align-items: center; padding: 16px 20px; cursor: pointer; list-style: none; }
.system-details summary::-webkit-details-marker { display: none; }
.system-details summary > span:first-child strong, .system-details summary > span:first-child small { display: block; } .system-details summary strong { font-size: 12px; } .system-details summary small, .system-details summary > span:last-child { margin-top: 3px; color: #939cad; font-size: 9px; }
.system-detail-body { padding: 0 20px 20px; }
.worker-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 9px; }
.worker-grid article { display: grid; grid-template-columns: 8px minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 11px; border-radius: 10px; background: #f7f9fc; }
.worker-grid i { width: 8px; height: 8px; border-radius: 50%; } .worker-grid i.healthy { background: #25a97a; } .worker-grid i.unhealthy { background: #d75368; }
.worker-grid strong, .worker-grid small { display: block; } .worker-grid strong { font-size: 10px; } .worker-grid small { margin-top: 3px; color: #909aab; font-size: 8px; } .worker-grid article > span { color: #667287; font-size: 11px; font-weight: 700; }
.system-alerts { display: grid; gap: 7px; margin-top: 12px; } .system-alerts article { padding: 10px 12px; border-radius: 9px; background: #fff6f0; } .system-alerts strong, .system-alerts span { display: block; font-size: 10px; } .system-alerts span { margin-top: 3px; color: #8c7568; font-size: 9px; }
.dashboard-footer { padding: 0 4px 14px; color: #a0a8b6; font-size: 10px; text-align: right; }
.install-steps { line-height: 2; padding-left: 22px; }
@media (max-width: 1220px) {
  .recruitment-kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .dashboard-content-grid { grid-template-columns: 1fr; }
  .dashboard-side-stack { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 1020px) {
  .dashboard-hero { grid-template-columns: 1fr; }
  .extension-quick-card { max-width: 620px; }
  .dashboard-main-grid { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .recruitment-dashboard { gap: 12px; }
  .dashboard-hero { padding: 23px 20px; border-radius: 17px; }
  .hero-status { width: 100%; flex-wrap: wrap; border-radius: 13px; }
  .hero-status > span:last-of-type { width: calc(100% - 22px); }
  .hero-status button { margin-left: 17px; }
  .extension-quick-card { grid-template-columns: 44px minmax(0, 1fr); }
  .extension-icon { width: 44px; height: 44px; }
  .extension-actions { grid-column: 1 / -1; flex-direction: row; align-items: stretch; }
  .extension-actions :deep(.el-button) { flex: 1; }
  .recruitment-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .dashboard-panel { padding: 21px 18px; }
  .dashboard-side-stack { display: flex; }
  .candidate-row { grid-template-columns: 42px minmax(0, 1fr) auto; }
  .candidate-job { grid-column: 2 / 3; }
  .candidate-status { grid-column: 3; grid-row: 1; }
  .candidate-row time { grid-column: 2 / 4; text-align: left; }
  .funnel-footnote { flex-wrap: wrap; }
  .funnel-footnote small { width: 100%; margin-left: 0; }
}
@media (max-width: 520px) {
  .recruitment-kpis { grid-template-columns: 1fr; }
  .kpi-card { display: grid; grid-template-columns: 36px minmax(0, 1fr) auto; align-items: center; column-gap: 10px; }
  .kpi-icon { grid-row: 1 / 3; margin: 0; }
  .kpi-card > strong { grid-column: 3; grid-row: 1 / 3; margin: 0; }
  .sync-visual { grid-template-columns: 110px minmax(0, 1fr); gap: 15px; }
  .sync-donut { width: 110px; height: 110px; }
  .binding-grid { grid-template-columns: 1fr; }
  .binding-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
