<script setup lang="ts">
import { ref } from "vue";
import { ElMessage } from "element-plus";
import { useAuthStore } from "../stores/auth";
const loading = ref(false),
  auth = useAuthStore();
async function submit() {
  loading.value = true;
  try {
    await auth.login();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "无法打开飞书登录");
    loading.value = false;
  }
}
</script>
<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-mark">RC</div>
      <h2>招聘协同运维台</h2>
      <p>公司招聘协同管理后台</p>
      <el-button
        type="primary"
        :loading="loading"
        style="width: 100%"
        @click="submit"
        >使用飞书登录</el-button
      ><small>通过飞书验证身份，无需输入邮箱、密码或管理任何令牌。</small>
    </div>
  </div>
</template>
