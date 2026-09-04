<script setup lang="ts">
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "./stores/auth";
import { navigationGroups } from "./navigation";
const route = useRoute(),
  router = useRouter(),
  auth = useAuthStore();
</script>
<template>
  <router-view v-if="route.path === '/login'" /><el-container
    v-else
    class="shell"
    ><el-aside width="244px"
      ><div class="brand">
        <span class="brand-dot"></span>
        <div>招聘协同运维台<small>Developer Operations</small></div>
      </div>
      <el-menu router :default-active="route.path"><el-menu-item-group v-for="group in navigationGroups" :key="group.title" :title="group.title"><el-menu-item v-for="item in group.items" :key="item.name" :index="`/recruitment/${item.name}`">{{ item.title }}</el-menu-item></el-menu-item-group></el-menu
      ></el-aside
    ><el-container
      ><el-header
        ><span>{{ route.meta.title }}</span
        ><span class="user"
          >{{ auth.user?.display_name }} · {{ auth.user?.role }}
          <el-button
            link
            @click="auth.logout().then(() => router.push('/login'))"
            >退出</el-button
          ></span
        ></el-header
      ><el-main><router-view /></el-main></el-container
  ></el-container>
</template>
