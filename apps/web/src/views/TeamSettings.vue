<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useAuthStore } from "../stores/auth";
import AdminGrantDialog from "../components/AdminGrantDialog.vue";

type AccessProfile = {
  recruiter_id: string;
  recruiter_name: string;
  role: "ADMIN" | "RECRUITER";
  data_scope: "COMPANY" | "OWN";
  can_manage_team: boolean;
  can_manage_feishu: boolean;
  can_manage_jobs: boolean;
  can_reset_system: boolean;
};

const auth = useAuthStore();
const loading = ref(true);
const saving = ref(false);
const activeTab = ref("members");
const data = ref<Record<string, any>>({});
const tables = ref<Record<string, any>[]>([]);
const profiles = ref<AccessProfile[]>([]);
const tableUrl = ref("");
const pendingTable = ref<Record<string, any> | null>(null);
const resetPreview = ref<Record<string, any> | null>(null);
const adminDialog = ref(false);

const admins = computed(() => profiles.value.filter((profile) => profile.role === "ADMIN" && profile.data_scope === "COMPANY"));
const recruiters = computed(() => profiles.value.filter((profile) => !admins.value.includes(profile)));
const activeTable = computed(() => tables.value.find((table) => table.status === "ACTIVE"));

async function load() {
  loading.value = true;
  try {
    [data.value, tables.value, profiles.value] = await Promise.all([
      auth.client.request<Record<string, any>>("/admin/settings"),
      auth.client.request<Record<string, any>[]>("/admin/feishu-tables"),
      auth.client.request<AccessProfile[]>("/admin/access-profiles"),
    ]);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "加载团队设置失败");
  } finally {
    loading.value = false;
  }
}
async function saveSettings() {
  saving.value = true;
  try {
    await auth.client.request("/admin/settings", {
      method: "PATCH",
      body: JSON.stringify({ notify_on_contact: data.value.notify_on_contact, catchup_enabled: data.value.catchup_enabled }),
    });
    ElMessage.success("同步策略已保存");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "保存失败");
  } finally {
    saving.value = false;
  }
}
async function validateTable() {
  if (!tableUrl.value.trim()) return ElMessage.warning("请先粘贴飞书多维表格链接");
  try {
    pendingTable.value = await auth.client.request("/admin/feishu-tables/validate", {
      method: "POST",
      body: JSON.stringify({ table_url: tableUrl.value.trim() }),
    });
    ElMessage.success("表格验证通过，请核对后再启用");
    await load();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "表格验证失败");
  }
}
async function activateTable() {
  if (!pendingTable.value) return;
  try {
    const name = pendingTable.value.table_name;
    const result = await ElMessageBox.prompt(
      `目标表“${name}”将被清空并设为新的候选人主表。此操作不会删除旧表。`,
      "确认切换飞书表",
      { type: "warning", confirmButtonText: "清空并启用", cancelButtonText: "取消", inputPlaceholder: `清空 ${name}`, inputValidator: (value) => value === `清空 ${name}` ? true : `请输入：清空 ${name}` },
    );
    await auth.client.request(`/admin/feishu-tables/${pendingTable.value.id}/clear-and-activate`, { method: "POST", body: JSON.stringify({ confirmation: result.value }) });
    ElMessage.success("候选人主表已切换");
    pendingTable.value = null;
    tableUrl.value = "";
    await load();
  } catch (error) {
    if (error !== "cancel" && error !== "close") ElMessage.error(error instanceof Error ? error.message : "切换失败");
  }
}
async function previewReset() {
  try {
    resetPreview.value = await auth.client.request("/admin/system-reset/preview");
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "无法生成重置预览");
  }
}
async function resetSystem() {
  if (!resetPreview.value) return;
  try {
    const result = await ElMessageBox.prompt(
      "此操作不可恢复：账号绑定、候选人缓存、同步队列、诊断和审计都会删除；管理员身份、岗位、设置与当前飞书表会保留。",
      "永久重新初始化",
      { type: "error", confirmButtonText: "继续", cancelButtonText: "取消", inputPlaceholder: "重新初始化同步", inputValidator: (value) => value === "重新初始化同步" ? true : "请输入：重新初始化同步" },
    );
    await auth.client.request("/admin/system-reset", { method: "POST", body: JSON.stringify({ preview_version: resetPreview.value.preview_version, confirmation: result.value }) });
    ElMessage.success("后台已完成重新初始化");
    resetPreview.value = null;
    await load();
  } catch (error) {
    if (error !== "cancel" && error !== "close") ElMessage.error(error instanceof Error ? error.message : "重置失败");
  }
}
async function removeAdmin(profile: AccessProfile) {
  if (profile.recruiter_id === auth.user?.id) return ElMessage.warning("不能移除自己的管理员权限，请让其他管理员操作");
  try {
    await ElMessageBox.confirm(`确认移除“${profile.recruiter_name}”的管理员权限？其飞书登录和 BOSS 账号分配会保留。`, "移除管理员", { type: "warning", confirmButtonText: "确认移除", cancelButtonText: "取消" });
  } catch { return; }
  try {
    await auth.client.request(`/admin/admins/${profile.recruiter_id}`, { method: "DELETE" });
    ElMessage.success("管理员权限已移除");
    await load();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "移除失败");
  }
}
function formatTime(value?: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
}
function keptAdminNames() {
  const identities = (resetPreview.value?.keep_identities ?? []) as { display_name?: string; feishu_display_name?: string | null }[];
  return identities.map((item) => item.feishu_display_name || item.display_name || "管理员").join("、") || "当前管理员";
}
onMounted(load);
</script>

<template>
  <div v-loading="loading" class="workspace-page settings-workspace">
    <header class="page-heading compact-heading">
      <div><p class="eyebrow">TEAM & SYSTEM</p><h1>团队设置</h1><p>人员权限、飞书主表和同步策略分区管理，危险操作单独隔离。</p></div>
      <el-button @click="load">刷新</el-button>
    </header>

    <section class="summary-grid compact-summary">
      <article class="summary-card"><span>管理员</span><strong>{{ admins.length }}</strong><small>可管理全公司招聘数据</small></article>
      <article class="summary-card"><span>招聘人员</span><strong>{{ recruiters.length }}</strong><small>仅查看本人 BOSS 数据</small></article>
      <article class="summary-card"><span>当前飞书主表</span><strong class="table-name">{{ activeTable?.table_name || "未配置" }}</strong><small>{{ activeTable ? "同步目标正常" : "候选人暂无法写入飞书" }}</small></article>
      <article class="summary-card"><span>自动补扫</span><strong class="strategy-state">{{ data.catchup_enabled ? "已开启" : "已关闭" }}</strong><small>重新绑定后补扫最近记录</small></article>
    </section>

    <section class="page-card settings-tabs">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="成员与权限" name="members">
          <div class="section-toolbar"><div><h2>管理员</h2><p>管理员可有一位或多位，从飞书应用可见通讯录中添加，不固定到任何账号。</p></div><el-button type="primary" plain @click="adminDialog = true">添加管理员</el-button></div>
          <div class="role-callout"><span class="role-badge admin">管理员</span><strong>全公司数据与全部管理权限</strong><small>可以分配 BOSS 账号、管理飞书表、岗位和系统设置。</small></div>
          <el-table :data="admins" stripe>
            <el-table-column prop="recruiter_name" label="飞书成员" min-width="150" />
            <el-table-column label="角色" width="110"><template #default><span class="mini-status success">管理员</span></template></el-table-column>
            <el-table-column label="数据范围" min-width="180"><template #default>全公司所有 BOSS 账号</template></el-table-column>
            <el-table-column label="操作" width="100"><template #default="scope"><el-button link type="danger" :disabled="scope.row.recruiter_id === auth.user?.id" @click="removeAdmin(scope.row)">{{ scope.row.recruiter_id === auth.user?.id ? "当前账号" : "移除" }}</el-button></template></el-table-column>
          </el-table>
          <el-empty v-if="!admins.length" description="暂无管理员" :image-size="64" />

          <div class="section-toolbar member-heading"><div><h2>招聘人员</h2><p>飞书成员可以直接登录；只有其 BOSS 账号完成扩展绑定或由管理员分配后，才会看到对应招聘数据。</p></div><span class="compact-count">{{ recruiters.length }} 人</span></div>
          <div class="role-callout recruiter"><span class="role-badge recruiter">招聘人员</span><strong>只看本人被分配的 BOSS 账号</strong><small>不允许进入账号分配、团队设置或查看他人候选人。</small></div>
          <el-table :data="recruiters" stripe>
            <el-table-column prop="recruiter_name" label="飞书成员" min-width="150" />
            <el-table-column label="角色" width="110"><template #default><span class="mini-status neutral">招聘人员</span></template></el-table-column>
            <el-table-column label="数据范围" min-width="180"><template #default>本人 BOSS 账号</template></el-table-column>
            <el-table-column label="说明" min-width="220"><template #default>账号分配在“BOSS 账号”页完成</template></el-table-column>
          </el-table>
          <el-empty v-if="!recruiters.length" description="暂无普通招聘人员" :image-size="64" />
        </el-tab-pane>

        <el-tab-pane label="飞书主表" name="feishu">
          <div class="section-toolbar"><div><h2>候选人同步目标</h2><p>新表验证成功且二次确认后才启用；旧表保留归档，不会被删除。</p></div><span :class="['mini-status', activeTable ? 'success' : 'warning']">{{ activeTable ? "已配置" : "待配置" }}</span></div>
          <div v-if="activeTable" class="active-table-card"><div><small>当前使用</small><strong>{{ activeTable.table_name }}</strong><span>{{ formatTime(activeTable.activated_at) }} 启用</span></div><span class="live-dot">正在接收同步</span></div>
          <div class="table-input"><el-input v-model="tableUrl" placeholder="粘贴新的飞书多维表格链接" /><el-button type="primary" @click="validateTable">验证新表</el-button></div>
          <el-alert class="safe-tip" type="info" :closable="false" title="验证只读取表结构；只有点击“清空并启用”后才会变更同步目标。" show-icon />
          <div v-if="pendingTable" class="validation-box"><div><small>已验证的新表</small><strong>{{ pendingTable.table_name }}</strong></div><span>现有记录 {{ pendingTable.record_count }} 条</span><span>缺少字段 {{ pendingTable.missing_fields?.length || 0 }} 个</span><el-button type="danger" @click="activateTable">清空并启用</el-button></div>
          <el-table :data="tables" stripe>
            <el-table-column prop="table_name" label="表格" min-width="180" />
            <el-table-column label="状态" width="110"><template #default="scope"><span :class="['mini-status', scope.row.status === 'ACTIVE' ? 'success' : 'neutral']">{{ scope.row.status === "ACTIVE" ? "当前使用" : "已归档" }}</span></template></el-table-column>
            <el-table-column label="验证时间" min-width="160"><template #default="scope">{{ formatTime(scope.row.validated_at) }}</template></el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="同步策略" name="strategy">
          <div class="section-toolbar"><div><h2>同步与通知</h2><p>这些开关作用于全公司扩展和后台任务。</p></div><el-button type="primary" :loading="saving" @click="saveSettings">保存策略</el-button></div>
          <div class="strategy-list">
            <div><span class="setting-icon">补</span><div><strong>自动补扫</strong><small>扩展重新绑定后，自动补扫最近 48 小时的招聘记录。</small></div><el-switch v-model="data.catchup_enabled" /></div>
            <div><span class="setting-icon">通</span><div><strong>联系后通知</strong><small>候选人联系和招聘状态发生变化时发送飞书提醒。</small></div><el-switch v-model="data.notify_on_contact" /></div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="危险操作" name="danger">
          <div class="danger-zone">
            <div class="danger-heading"><span>!</span><div><h2>重新初始化全公司同步</h2><p>删除招聘账号绑定、候选人缓存、同步队列、诊断与审计；保留管理员身份、岗位、系统开关和当前飞书表。</p></div></div>
            <el-button type="danger" plain @click="previewReset">先生成删除预览</el-button>
          </div>
          <div v-if="resetPreview" class="reset-preview-panel">
            <div class="preview-heading"><div><strong>将永久删除的数据</strong><small>保留管理员：{{ keptAdminNames() }}</small></div><span>不可恢复</span></div>
            <el-descriptions :column="2" border><el-descriptions-item v-for="(value, key) in resetPreview.counts" :key="key" :label="String(key)">{{ value }}</el-descriptions-item></el-descriptions>
            <el-alert type="error" :closable="false" title="请确认已了解影响范围，执行后所有招聘人员需要重新绑定扩展。" show-icon />
            <el-button type="danger" @click="resetSystem">输入确认词并执行</el-button>
          </div>
        </el-tab-pane>
      </el-tabs>
    </section>

    <AdminGrantDialog v-model="adminDialog" @granted="load" />
  </div>
</template>

<style scoped>
.settings-workspace{display:flex;flex-direction:column;gap:14px}.compact-summary{margin:0}.summary-card small{color:#98a1b0;font-size:9px}.summary-card .table-name{font-size:17px;line-height:1.45;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.summary-card .strategy-state{font-size:20px;color:#227d63}.settings-tabs{padding-top:10px}.settings-tabs :deep(.el-tabs__header){margin:0 0 18px}.settings-tabs :deep(.el-tabs__item){font-size:12px}.section-toolbar{display:flex;justify-content:space-between;align-items:center;gap:14px;margin-bottom:13px}.section-toolbar h2{margin:0 0 4px;font-size:16px}.section-toolbar p{max-width:720px;margin:0;color:#8994a6;font-size:10px;line-height:1.55}.compact-count{padding:5px 8px;border-radius:8px;background:#f1f4f8;color:#667287;font-size:10px;font-weight:700}.role-callout{display:grid;grid-template-columns:auto minmax(160px,.65fr) minmax(240px,1fr);align-items:center;gap:12px;margin-bottom:12px;padding:11px 13px;border:1px solid #d7eee6;border-radius:10px;background:#f3fbf7}.role-callout.recruiter{border-color:#e1e8f5;background:#f7f9fc}.role-callout strong{font-size:11px}.role-callout small{color:#778497;font-size:10px}.role-badge,.mini-status{display:inline-flex;width:max-content;padding:4px 7px;border-radius:999px;font-size:9px;font-weight:750}.role-badge.admin,.mini-status.success{background:#e6f7ef;color:#137756}.role-badge.recruiter,.mini-status.neutral{background:#edf1f7;color:#59677c}.mini-status.warning{background:#fff3dc;color:#9d691c}.member-heading{margin-top:28px}.active-table-card{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:13px;padding:13px 15px;border:1px solid #d7eee6;border-radius:11px;background:#f3fbf7}.active-table-card small,.active-table-card strong,.active-table-card div>span{display:block}.active-table-card small{color:#7f9a8f;font-size:9px}.active-table-card strong{margin:3px 0;font-size:13px}.active-table-card div>span{color:#829087;font-size:9px}.live-dot{display:flex;align-items:center;gap:6px;color:#18795a;font-size:10px;font-weight:700}.live-dot:before{content:"";width:7px;height:7px;border-radius:50%;background:#2aae7f;box-shadow:0 0 0 4px #2aae7f1c}.table-input{display:flex;gap:8px}.safe-tip{margin:10px 0 14px}.validation-box{justify-content:flex-start;margin:0 0 14px}.validation-box>div{margin-right:auto}.validation-box small,.validation-box strong{display:block}.validation-box small{color:#8893a4;font-size:9px}.validation-box strong{margin-top:3px;font-size:12px}.validation-box>span{color:#677386;font-size:10px}.strategy-list{display:flex;flex-direction:column}.strategy-list>div{display:grid;grid-template-columns:38px minmax(0,1fr) auto;align-items:center;gap:13px;padding:17px 3px;border-top:1px solid #eef1f5}.strategy-list>div:first-child{border-top:0}.strategy-list strong,.strategy-list small{display:block}.strategy-list strong{font-size:12px}.strategy-list small{margin-top:4px;color:#8792a4;font-size:10px}.setting-icon{width:36px;height:36px;display:grid;place-items:center;border-radius:10px;background:#eff4ff;color:#4668c3;font-size:10px;font-weight:800}.danger-zone{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:18px;border:1px solid #f4cdcf;border-radius:12px;background:#fff8f8}.danger-heading{display:flex;align-items:flex-start;gap:12px}.danger-heading>span{width:28px;height:28px;display:grid;place-items:center;flex:none;border-radius:9px;background:#e1515d;color:#fff;font-weight:900}.danger-heading h2{margin:0 0 5px;font-size:15px;color:#a4313e}.danger-heading p{max-width:700px;margin:0;color:#8a6670;font-size:10px;line-height:1.55}.reset-preview-panel{display:flex;flex-direction:column;gap:13px;margin-top:14px;padding:16px;border:1px solid #f2d3d5;border-radius:12px}.preview-heading{display:flex;justify-content:space-between;align-items:center}.preview-heading strong,.preview-heading small{display:block}.preview-heading strong{font-size:12px}.preview-heading small{margin-top:3px;color:#8b95a5;font-size:9px}.preview-heading>span{padding:4px 7px;border-radius:999px;background:#fff0f1;color:#bd3d4b;font-size:9px;font-weight:800}.reset-preview-panel>.el-button{align-self:flex-end}
@media(max-width:720px){.section-toolbar,.danger-zone{align-items:stretch;flex-direction:column}.section-toolbar>.el-button,.danger-zone>.el-button{width:100%}.role-callout{grid-template-columns:1fr}.table-input{flex-direction:column}.table-input .el-button{width:100%}.active-table-card{align-items:flex-start;flex-direction:column}.reset-preview-panel :deep(.el-descriptions__body){overflow:auto}}
</style>
