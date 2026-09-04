export const resourceNavigation = [
  { name: "recruiters", title: "招聘者与绑定" },
  { name: "accounts", title: "BOSS 账号" },
  { name: "candidate-sync", title: "飞书同步队列" },
  { name: "lookup-alerts", title: "点击查重提醒" },
  { name: "scan-checkpoints", title: "补扫水位" },
  { name: "plugin-diagnostics", title: "扩展诊断" },
  { name: "binding-attempts", title: "绑定记录" },
  { name: "worker-heartbeats", title: "Worker 心跳" },
  { name: "conflicts", title: "重复候选人" },
  { name: "candidate-sources", title: "候选人来源" },
  { name: "interviews", title: "面试记录" },
  { name: "notifications", title: "通知队列" },
  { name: "audit-logs", title: "审计日志" },
  { name: "jobs", title: "岗位与映射" },
] as const;

type NavigationItem = { name: string; title: string };

export const navigationGroups = [
  { title: "工作台", items: [{ name: "overview", title: "运行总览" }] },
  { title: "招聘业务", items: resourceNavigation.filter((x) => ["recruiters", "accounts", "candidate-sources", "jobs", "interviews"].includes(x.name)) },
  { title: "协同与同步", items: resourceNavigation.filter((x) => ["candidate-sync", "notifications", "lookup-alerts", "conflicts", "scan-checkpoints"].includes(x.name)) },
  { title: "系统管理", items: [...resourceNavigation.filter((x) => ["devices", "plugin-diagnostics", "binding-attempts", "worker-heartbeats", "audit-logs"].includes(x.name)), { name: "settings", title: "系统设置" }] as NavigationItem[] },
] as const;
