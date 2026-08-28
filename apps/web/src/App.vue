<script setup lang="ts">import {useRoute,useRouter} from 'vue-router';import {useAuthStore} from './stores/auth';
const route=useRoute(),router=useRouter(),auth=useAuthStore();
const menus=[['overview','概览'],['conflicts','重复候选人'],['candidates','候选人来源'],['interviews','面试'],['jobs','岗位与映射'],['accounts','招聘账号'],['recruiters','招聘人员'],['notifications','通知'],['plugin-diagnostics','插件诊断'],['audit-logs','审计日志'],['settings','系统设置'],['mock-feishu','Mock 飞书']];
</script>
<template><router-view v-if="route.path==='/login'"/><el-container v-else class="shell"><el-aside width="230px"><div class="brand">招聘协同平台</div><el-menu router :default-active="route.path"> <el-menu-item v-for="m in menus" :key="m[0]" :index="`/recruitment/${m[0]}`">{{m[1]}}</el-menu-item></el-menu></el-aside><el-container><el-header><span>{{route.meta.title}}</span><span class="user">{{auth.user?.display_name}} · {{auth.user?.role}} <el-button link @click="auth.logout();router.push('/login')">退出</el-button></span></el-header><el-main><router-view/></el-main></el-container></el-container></template>

