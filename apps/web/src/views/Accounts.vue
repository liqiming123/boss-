<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useAuthStore } from "../stores/auth";

type BossAccount = { id:string; boss_account:string; platform:string; status:string; assignment_version:number; feishu_open_id?:string; feishu_display_name?:string; source_count:number; active_device_count:number; assigned_at?:string };
type FeishuUser = { open_id:string; user_id?:string; display_name:string };
const auth = useAuthStore();
const loading = ref(true), saving = ref(false), directoryLoading = ref(false);
const accounts = ref<BossAccount[]>([]), directory = ref<FeishuUser[]>([]);
const search = ref(""), directorySearch = ref("");
const directoryError = ref("");
const dialog = ref(false), selectedOpenId = ref(""), current = ref<BossAccount|null>(null);
const canManage = computed(() => auth.user?.workspace?.status === "ADMIN" && !!auth.user?.capabilities?.can_manage_team);
const filtered = computed(() => accounts.value.filter(row => `${row.boss_account} ${row.feishu_display_name||""}`.toLowerCase().includes(search.value.toLowerCase())));
const assignedCount = computed(() => accounts.value.filter(x => x.status === "ASSIGNED").length);
const selectedUser = computed(() => directory.value.find(x => x.open_id === selectedOpenId.value));

async function load(){ loading.value=true; try { accounts.value=await auth.client.request<BossAccount[]>("/admin/boss-accounts"); } catch(e){ ElMessage.error(e instanceof Error?e.message:"加载 BOSS 账号失败"); } finally { loading.value=false; } }
async function loadDirectory(){
  directoryLoading.value=true; directoryError.value="";
  try { directory.value=await auth.client.request<FeishuUser[]>(`/admin/feishu/directory?query=${encodeURIComponent(directorySearch.value.trim())}`); }
  catch(e){ directory.value=[]; directoryError.value=e instanceof Error?e.message:"读取飞书通讯录失败"; ElMessage.error(directoryError.value); }
  finally { directoryLoading.value=false; }
}
async function openReplace(row:BossAccount){ current.value=row; selectedOpenId.value=row.feishu_open_id||""; directorySearch.value=""; directory.value=[]; directoryError.value=""; dialog.value=true; await loadDirectory(); }
async function save(){
  if(!current.value || !selectedUser.value) return ElMessage.warning("请选择飞书负责人");
  const person=selectedUser.value, row=current.value;
  try { await ElMessageBox.confirm(`确认把 BOSS 账号“${row.boss_account}”分配给“${person.display_name}”？原负责人的扩展会立即退出，历史候选人继续保留在这个 BOSS 账号下。`,"确认替换负责人",{confirmButtonText:"确认替换",cancelButtonText:"取消",type:"warning"}); } catch { return; }
  saving.value=true;
  try {
    await auth.client.request(`/admin/boss-accounts/${row.id}/assignment`,{method:"PUT",body:JSON.stringify({feishu_open_id:person.open_id,feishu_user_id:person.user_id,feishu_display_name:person.display_name,expected_version:row.assignment_version||undefined})});
    ElMessage.success("负责人已替换，历史数据已继承"); dialog.value=false; await load();
  } catch(e){ ElMessage.error(e instanceof Error?e.message:"替换失败"); } finally { saving.value=false; }
}
function formatTime(value?:string){ return value ? new Date(value).toLocaleString("zh-CN",{hour12:false}) : "—"; }
onMounted(load);
</script>

<template>
  <div class="workspace-page account-page" v-loading="loading">
    <header class="page-heading compact-heading account-hero">
      <div><p class="eyebrow">BOSS ACCOUNTS</p><h1>BOSS 账号</h1><p>查看每个招聘账号的飞书负责人、扩展连接和候选人数据；替换负责人不会改变历史数据归属。</p></div>
      <el-button @click="load">刷新</el-button>
    </header>
    <section class="summary-grid compact-summary account-metrics">
      <article class="summary-card"><span>BOSS 账号</span><strong>{{ accounts.length }}</strong><small>插件已识别的招聘账号</small></article>
      <article class="summary-card"><span>已分配</span><strong>{{ assignedCount }}</strong><small>已有飞书负责人</small></article>
      <article class="summary-card" :class="accounts.length-assignedCount ? 'warning-card' : ''"><span>待分配</span><strong>{{ accounts.length-assignedCount }}</strong><small>可由首次扩展登录认领</small></article>
      <article class="summary-card"><span>同步候选人</span><strong>{{ accounts.reduce((n,x)=>n+x.source_count,0) }}</strong><small>数据始终跟随 BOSS 账号</small></article>
    </section>
    <div class="assignment-rule"><span>规则</span><p>未分配的 BOSS 账号可由成员首次登录扩展时自动绑定；已分配账号只能由管理员在这里替换。一个飞书成员同时只负责一个 BOSS 账号。</p></div>
    <section class="account-card">
      <div class="account-toolbar"><div><h2>账号运行与负责人</h2><p>当前插件的 BOSS 同步数据会实时汇总在对应账号下。</p></div><el-input v-model="search" clearable placeholder="搜索 BOSS 或飞书姓名" class="search-input" /></div>
      <div class="account-list">
        <article v-for="row in filtered" :key="row.id" class="account-row">
          <div class="boss-identity"><span class="boss-avatar">B</span><div><strong>{{ row.boss_account }}</strong><small>{{ row.platform.toUpperCase() }} 招聘账号</small></div></div>
          <div class="owner"><small>当前飞书负责人</small><div><span class="owner-dot" :class="{empty:!row.feishu_display_name}"></span><strong>{{ row.feishu_display_name || '尚未分配' }}</strong></div><small v-if="row.assigned_at">{{ formatTime(row.assigned_at) }} 分配</small></div>
          <div class="account-stats"><span><b>{{ row.source_count }}</b> 候选人</span><span><b>{{ row.active_device_count }}</b> 个扩展在线</span></div>
          <el-button v-if="canManage" type="primary" plain @click="openReplace(row)">{{ row.feishu_display_name?'替换负责人':'分配负责人' }}</el-button>
        </article><el-empty v-if="!filtered.length" description="暂无匹配的 BOSS 账号" />
      </div>
    </section>
    <el-dialog v-model="dialog" width="min(560px, 92vw)" title="选择飞书负责人" destroy-on-close>
      <div class="dialog-account"><span>BOSS 账号</span><strong>{{ current?.boss_account }}</strong></div>
      <div class="directory-search"><el-input v-model="directorySearch" clearable placeholder="输入姓名搜索飞书通讯录" @keyup.enter="loadDirectory"/><el-button @click="loadDirectory">搜索</el-button></div>
      <div v-if="!directoryError" class="directory-summary"><span>实时读取飞书应用可见通讯录</span><strong>{{ directoryLoading ? '读取中…' : `${directory.length} 位成员` }}</strong></div>
      <el-alert v-else class="directory-error" type="error" :closable="false" title="通讯录读取失败" :description="directoryError" show-icon />
      <div class="directory-list" v-loading="directoryLoading">
        <label v-for="person in directory" :key="person.open_id" class="person" :class="{selected:selectedOpenId===person.open_id}"><el-radio v-model="selectedOpenId" :value="person.open_id"><span class="person-avatar">{{ person.display_name.slice(0,1) }}</span><span>{{ person.display_name }}</span></el-radio></label>
        <el-empty v-if="!directoryLoading&&!directoryError&&!directory.length" description="应用可见范围内没有找到成员" :image-size="72"/>
      </div>
      <template #footer><el-button @click="dialog=false">取消</el-button><el-button type="primary" :disabled="!selectedUser" :loading="saving" @click="save">确认分配</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.account-page{display:flex;flex-direction:column;gap:14px}.account-hero p:not(.eyebrow){max-width:760px;line-height:1.6}.compact-summary{margin:0}.summary-card small{color:#98a1b0;font-size:9px}.warning-card strong{color:#a66d18}.assignment-rule{display:flex;align-items:center;gap:10px;padding:10px 13px;border:1px solid #dbe5f4;border-radius:10px;background:#f7f9fd}.assignment-rule>span{padding:4px 7px;border-radius:7px;background:#e9effb;color:#4162ad;font-size:9px;font-weight:800}.assignment-rule p{margin:0;color:#6d798c;font-size:10px;line-height:1.55}.account-card{background:#fff;border:1px solid #e6ebf2;border-radius:14px;padding:18px 20px;box-shadow:0 4px 18px #17203308}.account-toolbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.account-toolbar h2{margin:0 0 4px;font-size:16px}.account-toolbar p{margin:0;color:#8a94a6;font-size:10px}.search-input{width:280px}.account-list{display:flex;flex-direction:column}.account-row{display:grid;grid-template-columns:minmax(180px,1.1fr) minmax(190px,1fr) minmax(190px,.8fr) auto;align-items:center;gap:20px;padding:15px 5px;border-top:1px solid #edf0f5}.boss-identity{display:flex;align-items:center;gap:11px}.boss-avatar,.person-avatar{display:inline-grid;place-items:center;border-radius:10px;background:#142033;color:#42d5a5;font-weight:800}.boss-avatar{width:38px;height:38px}.boss-identity strong{font-size:12px}.boss-identity small,.owner>small{display:block;color:#8a94a6;margin-top:3px;font-size:9px}.owner>div{display:flex;align-items:center;gap:8px}.owner>div strong{font-size:11px}.owner-dot{width:7px;height:7px;border-radius:50%;background:#25b783;box-shadow:0 0 0 4px #25b78318}.owner-dot.empty{background:#b6bfcc;box-shadow:none}.account-stats{display:flex;gap:18px;color:#7a8699;font-size:10px}.account-stats b{color:#263248;font-size:14px}.dialog-account{display:flex;justify-content:space-between;background:#f5f7fa;border-radius:10px;padding:13px 15px;margin-bottom:14px}.directory-search{display:flex;gap:8px}.directory-summary{display:flex;justify-content:space-between;align-items:center;margin-top:10px;padding:0 2px;color:#8490a3;font-size:11px}.directory-summary strong{color:#2b7766;font-weight:650}.directory-error{margin-top:12px}.directory-list{min-height:90px;max-height:330px;overflow:auto;margin-top:10px;border:1px solid #e6ebf2;border-radius:10px}.person{display:block;padding:12px 14px;border-bottom:1px solid #eef1f5;cursor:pointer}.person.selected{background:#f0fbf7}.person-avatar{width:30px;height:30px;margin:0 9px}.person :deep(.el-radio){height:auto;width:100%}.person :deep(.el-radio__label){display:flex;align-items:center;color:#263248}.person:last-child{border:0}@media(max-width:900px){.account-row{grid-template-columns:1fr 1fr}.account-row>.el-button{justify-self:start}.account-stats{justify-self:end}}@media(max-width:620px){.account-hero,.account-toolbar{align-items:stretch;flex-direction:column;gap:12px}.search-input{width:100%}.account-row{grid-template-columns:1fr;gap:13px}.account-stats{justify-self:start}.account-card{padding:14px}.assignment-rule{align-items:flex-start}.directory-search{align-items:stretch;flex-direction:column}.directory-search .el-button{width:100%}}
</style>
