<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { useAuthStore } from "../stores/auth";

type Candidate = {
  id: string;
  platform_account_id?: string | null;
  candidate_display_name: string;
  candidate_age: number | null;
  candidate_experience: string | null;
  candidate_education: string | null;
  raw_job_name: string;
  recruitment_status: string;
  status_evidence: string | null;
  conversation_started_at: string | null;
  conversation_updated_at: string | null;
  last_seen_at: string | null;
  last_message_sent_at?: string | null;
  message_sent_count?: number | null;
  snapshot_status?: string | null;
};
type Interview = {
  id: string;
  candidate_source_id: string;
  scheduled_at: string;
  duration_minutes?: number;
  location_type?: string | null;
  location_text?: string | null;
  status: string;
  result?: string | null;
  notes?: string | null;
};
type Account = { id: string; account_display_name: string; recruiter_id?: string | null };
type Recruiter = { id: string; display_name: string };
type BossAccount = { id: string; boss_account: string; feishu_display_name?: string | null; status: string };

const auth = useAuthStore();
const loading = ref(true);
const activeTab = ref("invites");
const candidates = ref<Candidate[]>([]);
const interviews = ref<Interview[]>([]);
const accounts = ref<Account[]>([]);
const recruiters = ref<Recruiter[]>([]);
const bossAccounts = ref<BossAccount[]>([]);
const recruiterFilter = ref("all");
const search = ref("");

const UNASSIGNED = "未分配负责人";
const INACTIVE = ["已拒绝", "已入职", "已淘汰", "已结束"];

/** BOSS renders the invitation as the system bubble 「发送了面试邀请」. The
 * evidence tells us which signal proved it, so the table never implies a
 * scheduled interview that the extension did not actually observe. */
function evidenceLabel(evidence?: string | null) {
  if (evidence === "BOSS_INTERVIEW_INVITE") return "BOSS 面试邀约已发送";
  if (evidence === "BOSS_INTERVIEW_MARKER") return "BOSS 已约面标记";
  return evidence || "BOSS 已约面";
}
function snapshotLabel(status?: string | null) {
  if (status === "READY") return "已存档";
  if (status === "FAILED") return "截图失败";
  if (status === "PENDING" || status === "PROCESSING") return "截图中";
  return "待补截图";
}
function snapshotTone(status?: string | null) {
  if (status === "READY") return "success";
  if (status === "FAILED") return "danger";
  return "neutral";
}

const accountMap = computed(() => new Map(accounts.value.map((row) => [row.id, row])));
const bossAccountMap = computed(() => new Map(bossAccounts.value.map((row) => [row.id, row])));
const recruiterMap = computed(() => new Map(recruiters.value.map((row) => [row.id, row])));
const interviewMap = computed(() => {
  const map = new Map<string, Interview>();
  for (const row of interviews.value) {
    const existing = map.get(row.candidate_source_id);
    if (!existing || new Date(row.scheduled_at).getTime() > new Date(existing.scheduled_at).getTime()) map.set(row.candidate_source_id, row);
  }
  return map;
});

function ownerId(accountId?: string | null) {
  return accountId ? accountMap.value.get(accountId)?.recruiter_id || "" : "";
}
function accountLabel(accountId?: string | null) {
  if (!accountId) return "未识别账号";
  return bossAccountMap.value.get(accountId)?.boss_account || accountMap.value.get(accountId)?.account_display_name || "未识别账号";
}
function ownerLabel(accountId?: string | null) {
  if (!accountId) return UNASSIGNED;
  const boss = bossAccountMap.value.get(accountId);
  if (boss?.feishu_display_name) return boss.feishu_display_name;
  const recruiterId = ownerId(accountId);
  return (recruiterId && recruiterMap.value.get(recruiterId)?.display_name) || UNASSIGNED;
}
function latestOf(...values: Array<string | null | undefined>) {
  let best: string | null = null;
  for (const value of values) {
    if (!value) continue;
    if (!best || new Date(value).getTime() > new Date(best).getTime()) best = value;
  }
  return best;
}

const jobRows = computed(() => {
  const groups = new Map<string, { job_name: string; total: number; active: number; invited: number; last_activity: string | null }>();
  for (const row of candidates.value) {
    const name = String(row.raw_job_name || "岗位待确认").trim();
    const entry = groups.get(name) || { job_name: name, total: 0, active: 0, invited: 0, last_activity: null };
    entry.total += 1;
    if (!INACTIVE.includes(row.recruitment_status)) entry.active += 1;
    if (row.recruitment_status === "已约面") entry.invited += 1;
    entry.last_activity = latestOf(entry.last_activity, row.conversation_updated_at, row.last_seen_at);
    groups.set(name, entry);
  }
  return [...groups.values()].sort((a, b) => b.total - a.total || a.job_name.localeCompare(b.job_name, "zh-CN"));
});

const invites = computed(() =>
  candidates.value
    .filter((row) => row.recruitment_status === "已约面")
    .map((row) => ({
      ...row,
      account: accountLabel(row.platform_account_id),
      owner: ownerLabel(row.platform_account_id),
      ownerKey: ownerId(row.platform_account_id) || ownerLabel(row.platform_account_id),
      invited_at: latestOf(row.conversation_updated_at, row.last_message_sent_at, row.last_seen_at),
      interview: interviewMap.value.get(row.id) || null,
      evidence_label: evidenceLabel(row.status_evidence),
      snapshot_label: snapshotLabel(row.snapshot_status),
      snapshot_tone: snapshotTone(row.snapshot_status),
    }))
    .sort((a, b) => new Date(b.invited_at || 0).getTime() - new Date(a.invited_at || 0).getTime()),
);

const filteredInvites = computed(() =>
  invites.value
    .filter((row) => recruiterFilter.value === "all" || row.ownerKey === recruiterFilter.value)
    .filter((row) => {
      const term = search.value.trim().toLowerCase();
      if (!term) return true;
      return `${row.candidate_display_name} ${row.raw_job_name} ${row.account} ${row.owner}`.toLowerCase().includes(term);
    }),
);

const inviteGroups = computed(() => {
  const groups = new Map<string, { key: string; owner: string; rows: typeof invites.value }>();
  for (const row of filteredInvites.value) {
    const entry = groups.get(row.ownerKey) || { key: row.ownerKey, owner: row.owner, rows: [] };
    entry.rows.push(row);
    groups.set(row.ownerKey, entry);
  }
  return [...groups.values()].sort((a, b) => b.rows.length - a.rows.length || a.owner.localeCompare(b.owner, "zh-CN"));
});

const recruiterOptions = computed(() => {
  const groups = new Map<string, { key: string; owner: string; count: number }>();
  for (const row of invites.value) {
    const entry = groups.get(row.ownerKey) || { key: row.ownerKey, owner: row.owner, count: 0 };
    entry.count += 1;
    groups.set(row.ownerKey, entry);
  }
  return [...groups.values()].sort((a, b) => b.count - a.count || a.owner.localeCompare(b.owner, "zh-CN"));
});

const activeCount = computed(() => candidates.value.filter((row) => !INACTIVE.includes(row.recruitment_status)).length);
const recentInvites = computed(() => {
  const since = Date.now() - 7 * 86_400_000;
  return invites.value.filter((row) => row.invited_at && new Date(row.invited_at).getTime() >= since).length;
});

function formatTime(value?: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
}
function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleDateString("zh-CN", { year: "numeric", month: "numeric", day: "numeric" }) : "—";
}
function profile(candidate: { candidate_age: number | null; candidate_education: string | null; candidate_experience: string | null }) {
  return [candidate.candidate_age ? `${candidate.candidate_age} 岁` : "", candidate.candidate_education || "", candidate.candidate_experience || ""].filter(Boolean).join(" · ") || "资料待补充";
}
function interviewLabel(row: { interview: Interview | null }) {
  if (!row.interview) return "BOSS 已识别，具体时间待补充";
  const form = row.interview.location_type === "ONLINE" ? "线上" : row.interview.location_type === "OFFLINE" ? "线下" : row.interview.location_type || "—";
  const place = row.interview.location_text ? ` · ${row.interview.location_text}` : "";
  return `${formatTime(row.interview.scheduled_at)} · ${form}${place}`;
}

async function load() {
  loading.value = true;
  try {
    const [candidateRows, interviewRows, accountRows, recruiterRows] = await Promise.all([
      auth.client.list<Candidate>("candidate-sources"),
      auth.client.list<Interview>("interviews"),
      auth.client.list<Account>("accounts"),
      auth.client.list<Recruiter>("recruiters"),
    ]);
    candidates.value = candidateRows;
    interviews.value = interviewRows;
    accounts.value = accountRows;
    recruiters.value = recruiterRows;
    // The per-account owner labels come from an admin-only endpoint. A normal
    // recruiter still gets the page; the label simply falls back to the
    // account/recruiter resource instead of failing the whole load.
    try {
      bossAccounts.value = await auth.client.request<BossAccount[]>("/admin/boss-accounts");
    } catch {
      bossAccounts.value = [];
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "加载岗位与面试失败");
  } finally {
    loading.value = false;
  }
}
onMounted(load);
</script>

<template>
  <div v-loading="loading" class="workspace-page jobs-workspace">
    <header class="page-heading compact-heading">
      <div>
        <p class="eyebrow">JOBS &amp; INTERVIEWS</p>
        <h1>岗位与面试</h1>
        <p>岗位名称保持 BOSS 原样；面试邀约只统计 BOSS 确认「发送了面试邀请」的候选人。</p>
      </div>
      <div class="hero-actions"><el-button @click="load">刷新</el-button></div>
    </header>

    <section class="summary-grid compact-summary">
      <article class="summary-card"><span>BOSS 岗位</span><strong>{{ jobRows.length }}</strong><small>来自沟通职位</small></article>
      <article class="summary-card"><span>候选人</span><strong>{{ candidates.length }}</strong><small>当前权限范围</small></article>
      <article class="summary-card"><span>推进中</span><strong>{{ activeCount }}</strong><small>未结束的候选人</small></article>
      <article class="summary-card"><span>已约面</span><strong>{{ invites.length }}</strong><small>{{ recruiterOptions.length }} 位招聘者</small></article>
      <article class="summary-card"><span>近 7 天邀约</span><strong>{{ recentInvites }}</strong><small>按邀约时间统计</small></article>
    </section>

    <section v-if="recruiterOptions.length" class="recruiter-strip">
      <span class="strip-label">招聘者</span>
      <button type="button" :class="['recruiter-pill', { active: recruiterFilter === 'all' }]" @click="recruiterFilter = 'all'">
        全部 <b>{{ invites.length }}</b>
      </button>
      <button v-for="item in recruiterOptions" :key="item.key" type="button" :class="['recruiter-pill', { active: recruiterFilter === item.key }]" @click="recruiterFilter = item.key">
        {{ item.owner }} <b>{{ item.count }}</b>
      </button>
    </section>

    <section class="page-card tab-card">
      <el-tabs v-model="activeTab">
        <el-tab-pane name="invites">
          <template #label>面试邀约 <b class="tab-count">{{ filteredInvites.length }}</b></template>
          <div class="section-toolbar">
            <div>
              <h2>面试邀约明细</h2>
              <p>按招聘者分组。数据来自 BOSS 已确认的邀约状态，面试时间以系统中的安排记录为准。</p>
            </div>
            <el-input v-model="search" clearable placeholder="搜索候选人 / 岗位 / 招聘者" class="invite-search" />
          </div>
          <div v-if="inviteGroups.length" class="invite-groups">
            <article v-for="group in inviteGroups" :key="group.key" class="invite-group">
              <div class="group-head">
                <div class="group-owner"><span class="owner-avatar">{{ group.owner.slice(0, 1) }}</span><div><strong>{{ group.owner }}</strong><small>{{ group.rows.length }} 条邀约</small></div></div>
                <div class="group-metrics">
                  <span><small>已存档截图</small><strong>{{ group.rows.filter((row) => row.snapshot_tone === 'success').length }}</strong></span>
                  <span><small>已排期</small><strong>{{ group.rows.filter((row) => row.interview).length }}</strong></span>
                </div>
              </div>
              <el-table :data="group.rows" stripe>
                <el-table-column label="候选人" min-width="150">
                  <template #default="scope"><div class="candidate-cell"><strong>{{ scope.row.candidate_display_name }}</strong><small>{{ profile(scope.row) }}</small></div></template>
                </el-table-column>
                <el-table-column label="BOSS 岗位" min-width="170"><template #default="scope">{{ scope.row.raw_job_name || "岗位待确认" }}</template></el-table-column>
                <el-table-column label="BOSS 账号" min-width="130"><template #default="scope">{{ scope.row.account }}</template></el-table-column>
                <el-table-column label="面试安排" min-width="210"><template #default="scope"><span :class="scope.row.interview ? 'arranged' : 'pending-text'">{{ interviewLabel(scope.row) }}</span></template></el-table-column>
                <el-table-column label="邀约时间" min-width="130"><template #default="scope">{{ formatTime(scope.row.invited_at) }}</template></el-table-column>
                <el-table-column label="开始聊天" min-width="130"><template #default="scope">{{ formatTime(scope.row.conversation_started_at) }}</template></el-table-column>
                <el-table-column label="识别依据" min-width="150"><template #default="scope"><span class="mini-status neutral">{{ scope.row.evidence_label }}</span></template></el-table-column>
                <el-table-column label="截图" width="110"><template #default="scope"><span :class="['mini-status', scope.row.snapshot_tone]">{{ scope.row.snapshot_label }}</span></template></el-table-column>
              </el-table>
            </article>
          </div>
          <el-empty v-else description="还没有识别到面试邀约" :image-size="70" />
        </el-tab-pane>

        <el-tab-pane name="jobs">
          <template #label>岗位视图 <b class="tab-count">{{ jobRows.length }}</b></template>
          <div class="section-toolbar">
            <div>
              <h2>BOSS 实际岗位</h2>
              <p>岗位名称直接取自候选人沟通页，不做标准岗位、归一化或映射。</p>
            </div>
            <span class="compact-count">{{ jobRows.length }} 个</span>
          </div>
          <el-table :data="jobRows" stripe>
            <el-table-column prop="job_name" label="岗位名称（BOSS）" min-width="230" />
            <el-table-column label="候选人" width="100"><template #default="scope">{{ scope.row.total }} 位</template></el-table-column>
            <el-table-column label="推进中" width="100"><template #default="scope">{{ scope.row.active }} 位</template></el-table-column>
            <el-table-column label="已约面" width="110"><template #default="scope"><span :class="scope.row.invited ? 'arranged' : 'pending-text'">{{ scope.row.invited }} 位</span></template></el-table-column>
            <el-table-column label="邀约占比" min-width="160">
              <template #default="scope">
                <div class="ratio-bar"><i :style="{ width: `${scope.row.total ? Math.max(Math.round((scope.row.invited / scope.row.total) * 100), 2) : 2}%` }" /></div>
              </template>
            </el-table-column>
            <el-table-column label="最近活动" min-width="140"><template #default="scope">{{ formatTime(scope.row.last_activity) }}</template></el-table-column>
          </el-table>
          <el-empty v-if="!jobRows.length" description="暂未从 BOSS 读取到岗位" :image-size="70" />
        </el-tab-pane>
      </el-tabs>
    </section>

    <p class="page-footnote">岗位来自 BOSS 沟通列表；面试邀约来自插件确认的「发送了面试邀请」；面试时间/形式/地点只有系统中存在安排记录时才会显示。最后刷新 {{ formatDate(new Date().toISOString()) }}。</p>
  </div>
</template>

<style scoped>
.jobs-workspace{display:flex;flex-direction:column;gap:14px}
.compact-summary{grid-template-columns:repeat(5,minmax(0,1fr));margin:0}
.summary-card small{color:#98a1b0;font-size:9px}
.recruiter-strip{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:11px 13px;border:1px solid var(--line);border-radius:12px;background:#fff}
.strip-label{color:#8b97aa;font-size:10px;font-weight:800;letter-spacing:.12em}
.recruiter-pill{display:inline-flex;align-items:center;gap:6px;padding:6px 11px;border:1px solid #e4e7ec;border-radius:999px;background:#fff;color:#5d6f8e;font-size:11px;font-weight:600;cursor:pointer}
.recruiter-pill:hover{background:#f6f8ff}
.recruiter-pill.active{border-color:#4b70d7;background:#eef3ff;color:#2f4fae}
.recruiter-pill b{color:#101828;font-size:11px}
.tab-card{padding-top:10px}
.tab-card :deep(.el-tabs__header){margin:0 0 16px}
.tab-count{margin-left:4px;color:#8b97aa;font-size:10px}
.section-toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px}
.section-toolbar h2{margin:0 0 3px;font-size:16px}
.section-toolbar p{margin:0;color:#8d97a8;font-size:10px}
.compact-count{padding:5px 8px;border-radius:8px;background:#f1f4f8;color:#667287;font-size:10px;font-weight:700}
.invite-search{width:260px}
.invite-groups{display:flex;flex-direction:column;gap:16px}
.invite-group{border:1px solid #eef1f5;border-radius:14px;overflow:hidden}
.group-head{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 14px;background:#f8faff;border-bottom:1px solid #eef1f5}
.group-owner{display:flex;align-items:center;gap:10px}
.owner-avatar{width:30px;height:30px;display:grid;place-items:center;border-radius:9px;background:#15233d;color:#5cdbaf;font-weight:800;font-size:12px}
.group-owner strong{display:block;font-size:13px}
.group-owner small{display:block;margin-top:2px;color:#98a1b0;font-size:10px}
.group-metrics{display:flex;gap:18px}
.group-metrics span{display:flex;flex-direction:column;align-items:flex-end}
.group-metrics small{color:#98a1b0;font-size:9px}
.group-metrics strong{font-size:14px}
.candidate-cell strong{display:block;font-size:12px}
.candidate-cell small{display:block;margin-top:2px;color:#98a1b0;font-size:10px}
.mini-status{padding:4px 7px;border-radius:999px;font-size:9px;font-weight:700;white-space:nowrap}
.mini-status.success{background:#eaf8f1;color:#177c5b}
.mini-status.neutral{background:#f1f3f7;color:#6e7b8e}
.mini-status.danger{background:#fdeef1;color:#b83b52}
.arranged{color:#177c5b;font-weight:600}
.pending-text{color:#98a1b0}
.ratio-bar{height:7px;border-radius:999px;background:#eef1f5;overflow:hidden}
.ratio-bar i{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,#4b70d7,#5cdbaf)}
.page-footnote{margin:0;color:#98a1b0;font-size:10px}
@media (max-width:1080px){.compact-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.invite-search{width:180px}}
</style>
