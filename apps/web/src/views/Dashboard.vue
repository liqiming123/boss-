<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useAuthStore } from "../stores/auth";

type Alert = {
  id: string;
  severity: "critical" | "warning";
  title: string;
  detail: string;
};
type Worker = {
  name: string;
  label: string;
  status: string;
  last_seen_at: string | null;
  last_success_at: string | null;
  last_error_code: string | null;
  total_processed: number;
};
type Device = {
  device_id: string;
  device_name: string;
  status: string;
  last_seen_at: string | null;
  revoked_at: string | null;
};
type Binding = {
  recruiter_id: string;
  boss_accounts: string[];
  role: string;
  bound: boolean;
  feishu_display_name: string | null;
  active_device_count: number;
  source_count: number;
  last_checkpoint_at: string | null;
  devices: Device[];
};
type Failure = {
  kind: string;
  id: string;
  status: string;
  retry_count: number;
  last_error: string | null;
  updated_at: string;
};
type Diagnostic = {
  id: string;
  platform: string;
  adapter_version: string;
  page_type: string;
  account_status: string;
  candidate_status: string;
  job_status: string;
  platform_id_status: string;
  error_codes: string[];
  created_at: string;
};
type Operations = {
  generated_at: string;
  overall_status: "healthy" | "warning" | "critical";
  alerts: Alert[];
  metrics: Record<string, number>;
  workers: Worker[];
  queues: Record<string, Record<string, number>>;
  bindings: Binding[];
  recent_failures: Failure[];
  diagnostics: Diagnostic[];
  configuration: {
    environment: string;
    feishu_mode: string;
    feishu_app_configured: boolean;
    feishu_candidate_table_configured: boolean;
    retention_days: Record<string, number>;
  };
};

const auth = useAuthStore(),
  data = ref<Operations | null>(null),
  loading = ref(true),
  lastError = ref(""),
  timer = ref<number>();
const metricLabels: Record<string, string> = {
  candidate_sources: "系统候选人行",
  candidate_sync_pending: "待同步",
  candidate_sync_failed: "同步失败",
  candidate_sync_sent: "已同步飞书",
  snapshot_ready: "快照完成",
  snapshot_missing: "快照缺失",
  active_devices: "活跃扩展",
  bound_recruiters: "已绑定招聘者",
  checkpoints: "补扫水位",
  lookup_alerts: "点击查重提醒",
};
const statusText: Record<string, string> = {
  healthy: "运行正常",
  warning: "需要关注",
  critical: "存在故障",
  HEALTHY: "正常",
  ERROR: "错误",
  STALE: "心跳超时",
  MISSING: "未启动",
  ACTIVE: "活跃",
  REVOKED: "已撤销",
};
const statusType = (value: string) =>
  value === "healthy" || value === "HEALTHY" || value === "ACTIVE"
    ? "success"
    : value === "warning"
      ? "warning"
      : "danger";
const generated = computed(() =>
  data.value ? formatTime(data.value.generated_at) : "—",
);

function formatTime(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", { hour12: false });
}
async function load(silent = false) {
  if (!silent) loading.value = true;
  try {
    data.value = await auth.client.request<Operations>("/admin/operations");
    lastError.value = "";
  } catch (error) {
    lastError.value =
      error instanceof Error ? error.message : "加载运维状态失败";
    if (!silent) ElMessage.error(lastError.value);
  } finally {
    loading.value = false;
  }
}
async function revoke(device: Device, binding: Binding) {
  try {
    await ElMessageBox.confirm(
      `撤销 ${binding.boss_accounts.join(" / ")} 的扩展设备“${device.device_name}”？该设备会立即退出登录。`,
      "撤销扩展设备",
      {
        type: "warning",
        confirmButtonText: "确认撤销",
        cancelButtonText: "取消",
      },
    );
    await auth.client.request(
      `/admin/devices/${encodeURIComponent(device.device_id)}/revoke`,
      { method: "POST" },
    );
    ElMessage.success("设备已撤销");
    await load(true);
  } catch (error) {
    if (error !== "cancel" && error !== "close")
      ElMessage.error(error instanceof Error ? error.message : "撤销失败");
  }
}
async function retry(failure: Failure) {
  if (failure.kind !== "candidate_sync") return;
  try {
    await auth.client.request(`/admin/candidate-sync/${failure.id}/retry`, {
      method: "POST",
    });
    ElMessage.success("已重新加入同步队列");
    await load(true);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "重试失败");
  }
}
onMounted(() => {
  void load();
  timer.value = window.setInterval(() => void load(true), 15_000);
});
onBeforeUnmount(() => {
  if (timer.value) window.clearInterval(timer.value);
});
</script>

<template>
  <div v-loading="loading" class="operations-page">
    <div class="ops-hero">
      <div>
        <p class="eyebrow">RECRUITMENT COLLAB · LIVE</p>
        <h1>系统运行总览</h1>
        <p>每15秒自动刷新 · 最近检查 {{ generated }}</p>
      </div>
      <div class="hero-actions">
        <el-tag
          size="large"
          effect="dark"
          :type="statusType(data?.overall_status ?? 'critical')"
          >{{ statusText[data?.overall_status ?? "critical"] }}</el-tag
        ><el-button @click="load()">立即刷新</el-button>
      </div>
    </div>
    <el-alert
      v-if="lastError"
      type="error"
      :title="lastError"
      show-icon
      :closable="false"
    />

    <section v-if="data?.alerts.length" class="alert-stack">
      <article
        v-for="alert in data.alerts"
        :key="alert.id"
        :class="['ops-alert', alert.severity]"
      >
        <span class="alert-light"></span>
        <div>
          <strong>{{ alert.title }}</strong>
          <p>{{ alert.detail }}</p>
        </div>
      </article>
    </section>
    <section v-else class="all-clear">
      <span>✓</span>
      <div>
        <strong>没有检测到异常</strong>
        <p>Worker、同步队列、快照和补扫水位均正常。</p>
      </div>
    </section>

    <section class="metric-grid ops-metrics">
      <article v-for="(value, key) in data?.metrics" :key="key" class="metric">
        <span>{{ metricLabels[key] ?? key }}</span
        ><strong>{{ value }}</strong>
      </article>
    </section>

    <div class="ops-columns">
      <section class="page-card">
        <div class="section-title">
          <div>
            <h2>Worker 心跳</h2>
            <p>超过90秒未上报即告警</p>
          </div>
        </div>
        <el-table :data="data?.workers ?? []" size="small"
          ><el-table-column
            prop="label"
            label="服务"
            min-width="150" /><el-table-column label="状态" width="110"
            ><template #default="scope"
              ><el-tag :type="statusType(scope.row.status)">{{
                statusText[scope.row.status] ?? scope.row.status
              }}</el-tag></template
            ></el-table-column
          ><el-table-column label="最近心跳" min-width="170"
            ><template #default="scope">{{
              formatTime(scope.row.last_seen_at)
            }}</template></el-table-column
          ><el-table-column prop="total_processed" label="累计处理" width="90"
        /></el-table>
      </section>
      <section class="page-card">
        <div class="section-title">
          <div>
            <h2>队列状态</h2>
            <p>
              候选人姓名、招聘人、岗位、聊天与更新时间、状态和聊天快照写入飞书
            </p>
          </div>
        </div>
        <div class="queue-list">
          <div v-for="(queue, name) in data?.queues" :key="name">
            <strong>{{
              name === "candidate_sync"
                ? "候选人资料同步"
                : name === "notifications"
                  ? "飞书通知"
                  : "聊天快照"
            }}</strong
            ><span v-for="(count, status) in queue" :key="status"
              ><b>{{ count }}</b
              >{{ status }}</span
            >
          </div>
        </div>
      </section>
    </div>

    <section class="page-card">
      <div class="section-title">
        <div>
          <h2>招聘者与扩展绑定</h2>
          <p>查看当前飞书归属、BOSS账号、设备活跃时间和补扫水位</p>
        </div>
      </div>
      <el-table :data="data?.bindings ?? []" row-key="recruiter_id"
        ><el-table-column type="expand"
          ><template #default="scope"
            ><div class="device-panel">
              <p v-if="!scope.row.devices.length">没有扩展设备记录</p>
              <div
                v-for="device in scope.row.devices"
                :key="device.device_id"
                class="device-row"
              >
                <div>
                  <strong>{{ device.device_name }}</strong
                  ><small
                    >{{ device.device_id }} · 最近活动
                    {{ formatTime(device.last_seen_at) }}</small
                  >
                </div>
                <el-tag :type="statusType(device.status)">{{
                  statusText[device.status] ?? device.status
                }}</el-tag
                ><el-button
                  v-if="device.status === 'ACTIVE'"
                  link
                  type="danger"
                  @click="revoke(device, scope.row)"
                  >撤销设备</el-button
                >
              </div>
            </div></template
          ></el-table-column
        ><el-table-column label="BOSS账号" min-width="160"
          ><template #default="scope"
            ><strong>{{
              scope.row.boss_accounts.join(" / ")
            }}</strong></template
          ></el-table-column
        ><el-table-column label="飞书绑定" min-width="150"
          ><template #default="scope"
            ><el-tag :type="scope.row.bound ? 'success' : 'info'">{{
              scope.row.bound
                ? scope.row.feishu_display_name || "已绑定"
                : "未绑定"
            }}</el-tag></template
          ></el-table-column
        ><el-table-column
          prop="active_device_count"
          label="活跃扩展"
          width="100"
        /><el-table-column
          prop="source_count"
          label="候选人行"
          width="100"
        /><el-table-column label="补扫水位" min-width="170"
          ><template #default="scope">{{
            formatTime(scope.row.last_checkpoint_at)
          }}</template></el-table-column
        ></el-table
      >
    </section>

    <div class="ops-columns bottom-grid">
      <section class="page-card">
        <div class="section-title">
          <div>
            <h2>需要处理的任务</h2>
            <p>只展示失败或仍在等待的队列项</p>
          </div>
        </div>
        <el-empty
          v-if="!data?.recent_failures.length"
          description="没有失败或积压任务"
          :image-size="72"
        />
        <div
          v-for="failure in data?.recent_failures"
          :key="failure.id"
          class="issue-row"
        >
          <div>
            <strong
              >{{
                failure.kind === "candidate_sync" ? "候选人同步" : "通知发送"
              }}
              · {{ failure.status }}</strong
            ><small
              >{{ failure.last_error || "等待 Worker 处理" }} ·
              {{ formatTime(failure.updated_at) }}</small
            >
          </div>
          <el-button
            v-if="failure.kind === 'candidate_sync'"
            link
            type="primary"
            @click="retry(failure)"
            >重试</el-button
          >
        </div>
      </section>
      <section class="page-card">
        <div class="section-title">
          <div>
            <h2>最近扩展诊断</h2>
            <p>只保存脱敏结构，不保存聊天文本</p>
          </div>
        </div>
        <el-empty
          v-if="!data?.diagnostics.length"
          description="暂无诊断上报"
          :image-size="72"
        />
        <div v-for="item in data?.diagnostics" :key="item.id" class="issue-row">
          <div>
            <strong>{{ item.platform }} · {{ item.page_type }}</strong
            ><small
              >{{ item.error_codes.join(", ") || "无错误码" }} ·
              {{ formatTime(item.created_at) }}</small
            >
          </div>
          <code>{{ item.adapter_version }}</code>
        </div>
      </section>
    </div>
    <footer v-if="data" class="ops-footer">
      环境 {{ data.configuration.environment }} · 飞书
      {{ data.configuration.feishu_mode }} · 候选人缓存
      {{ data.configuration.retention_days.candidate }} 天
    </footer>
  </div>
</template>
