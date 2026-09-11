<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "./stores/auth";
import { navigationGroups } from "./navigation";
import AdminGrantDialog from "./components/AdminGrantDialog.vue";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const adminDialog = ref(false);
const navOpen = ref(false);
const isAdmin = computed(() => auth.user?.workspace?.status === "ADMIN");
const canManage = computed(() => !!auth.user?.capabilities?.can_manage_team && isAdmin.value);
const hasWorkspaceData = computed(() => ["READY", "ADMIN"].includes(auth.user?.workspace?.status || ""));
const workspaceLabel = computed(() => isAdmin.value ? "全公司管理" : auth.user?.workspace?.status === "READY" ? "个人招聘工作区" : "账号接入");
const roleLabel = computed(() => isAdmin.value ? "管理员" : "招聘人员");
const visibleGroups = computed(() => navigationGroups
  .map((group) => ({
    ...group,
    items: group.items.filter((item) => {
      if (item.name === "overview") return true;
      if (item.name === "team-settings" || item.name === "accounts") return canManage.value;
      return canManage.value || hasWorkspaceData.value;
    }),
  }))
  .filter((group) => group.items.length));

async function logout() {
  await auth.logout();
  await router.push("/login");
}
</script>

<template>
  <router-view v-if="route.path === '/login'" />
  <el-container v-else :class="['shell', { 'nav-open': navOpen }]">
    <button v-if="navOpen" class="nav-backdrop" aria-label="关闭导航" @click="navOpen = false"></button>
    <el-aside width="222px" class="side-navigation">
      <div class="brand">
        <span class="brand-mark">R</span>
        <div><strong>{{ isAdmin ? "招聘管理中心" : "我的招聘" }}</strong><small>{{ workspaceLabel }}</small></div>
      </div>
      <el-menu router :default-active="route.path" @select="navOpen = false">
        <el-menu-item-group v-for="group in visibleGroups" :key="group.title" :title="group.title">
          <el-menu-item v-for="item in group.items" :key="item.name" :index="`/recruitment/${item.name}`">
            <span>{{ item.title }}</span>
          </el-menu-item>
        </el-menu-item-group>
      </el-menu>
      <div class="nav-scope">
        <span :class="['scope-dot', isAdmin ? 'admin' : 'recruiter']"></span>
        <div><strong>{{ workspaceLabel }}</strong><small>{{ isAdmin ? "可查看全部 BOSS 数据" : "只显示本人 BOSS 数据" }}</small></div>
      </div>
    </el-aside>

    <el-container class="content-shell">
      <el-header class="topbar">
        <div class="topbar-title"><button class="mobile-nav-toggle" aria-label="打开导航" @click="navOpen = true">☰</button><div><small>{{ isAdmin ? "ADMIN WORKSPACE" : "RECRUITER WORKSPACE" }}</small><strong>{{ route.meta.title }}</strong></div></div>
        <div class="topbar-actions">
          <el-button v-if="canManage" link type="primary" class="add-admin" @click="adminDialog = true">＋ 添加管理员</el-button>
          <span :class="['role-pill', isAdmin ? 'admin' : 'recruiter']">{{ roleLabel }}</span>
          <span class="user-avatar">{{ auth.user?.display_name?.slice(0, 1) || "用" }}</span>
          <span class="user-name">{{ auth.user?.display_name }}</span>
          <el-button link class="logout" @click="logout">退出</el-button>
        </div>
      </el-header>
      <el-main><router-view /></el-main>
    </el-container>
    <AdminGrantDialog v-model="adminDialog" />
  </el-container>
</template>

<style scoped>
.side-navigation{position:relative;display:flex;flex-direction:column;min-height:100vh;background:#111a2b;border-right:1px solid #23304a;z-index:30}.brand{height:72px;padding:15px 16px;display:flex;align-items:center;gap:11px;color:#fff}.brand-mark{width:36px;height:36px;display:grid;place-items:center;border-radius:11px;background:linear-gradient(145deg,#44d3a2,#2eae84);box-shadow:0 8px 20px #2ab78a2d;color:#0d2430;font-weight:900}.brand strong,.brand small{display:block}.brand strong{font-size:14px}.brand small{margin-top:3px;color:#8290aa;font-size:9px;letter-spacing:.06em}.side-navigation :deep(.el-menu){padding:5px 9px;background:transparent;border:0}.side-navigation :deep(.el-menu-item-group__title){padding:17px 13px 7px!important;color:#64728b;font-size:9px;font-weight:800;letter-spacing:.12em}.side-navigation :deep(.el-menu-item){position:relative;height:42px;margin:3px 0;padding:0 16px!important;border-radius:9px;color:#a6b1c3;font-size:13px}.side-navigation :deep(.el-menu-item:hover){background:#1a2740;color:#fff}.side-navigation :deep(.el-menu-item.is-active){background:linear-gradient(90deg,#263b61,#223451);color:#fff;box-shadow:inset 3px 0 #45d5a4}.side-navigation :deep(.el-menu-item.is-active)::after{content:"";position:absolute;right:13px;width:6px;height:6px;border-radius:50%;background:#50dab0}.nav-scope{margin:auto 12px 14px;padding:12px;display:flex;align-items:center;gap:9px;border:1px solid #26334a;border-radius:11px;background:#172238}.scope-dot{width:8px;height:8px;border-radius:50%}.scope-dot.admin{background:#4cd5a5;box-shadow:0 0 0 4px #4cd5a51a}.scope-dot.recruiter{background:#6b8bea}.nav-scope strong,.nav-scope small{display:block}.nav-scope strong{color:#dbe4f4;font-size:10px}.nav-scope small{margin-top:3px;color:#76849d;font-size:8px}.content-shell{min-width:0}.topbar{height:64px!important;padding:0 22px;display:flex;align-items:center;justify-content:space-between;background:#fff;border-bottom:1px solid #e6ebf2}.topbar-title{display:flex;align-items:center;gap:11px}.topbar-title small,.topbar-title strong{display:block}.topbar-title small{color:#9aa4b3;font-size:8px;font-weight:800;letter-spacing:.13em}.topbar-title strong{margin-top:2px;color:#263248;font-size:14px}.topbar-actions{display:flex;align-items:center;gap:9px;color:#647085;font-size:11px}.add-admin{margin-right:4px;font-weight:700}.role-pill{padding:4px 7px;border-radius:999px;font-size:9px;font-weight:700}.role-pill.admin{background:#e9f8f1;color:#167a5a}.role-pill.recruiter{background:#edf2ff;color:#3d5fc1}.user-avatar{width:29px;height:29px;display:grid;place-items:center;border-radius:9px;background:#19263e;color:#53d6aa;font-weight:800}.user-name{max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.logout{color:#7b8697}.mobile-nav-toggle,.nav-backdrop{display:none}
@media(max-width:1050px){.side-navigation{position:fixed!important;inset:0 auto 0 0;width:min(82vw,280px)!important;transform:translateX(-102%);transition:transform .2s ease;box-shadow:18px 0 50px #0c14283b}.nav-open .side-navigation{transform:translateX(0)}.nav-backdrop{display:block;position:fixed;inset:0;border:0;background:#10182866;z-index:25}.mobile-nav-toggle{display:grid;width:34px;height:34px;place-items:center;border:1px solid #e2e7ef;border-radius:9px;background:#fff;color:#48556b;font-size:16px}.topbar{padding:0 13px}.topbar-title small{display:none}.topbar-actions{gap:6px}.add-admin,.user-name,.role-pill{display:none}.topbar-actions .logout{padding:4px}.content-shell :deep(.el-main){padding:14px}}
</style>
