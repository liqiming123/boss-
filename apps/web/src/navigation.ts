export const resourceNavigation = [
  { name: "accounts", title: "BOSS 账号", icon: "B" },
  { name: "candidates", title: "候选人", icon: "候" },
  { name: "jobs-workspace", title: "岗位与面试", icon: "岗" },
  { name: "team-settings", title: "团队设置", icon: "设" },
] as const;

type NavigationItem = { name: string; title: string; icon: string };

export const navigationGroups = [
  { title: "概览", items: [{ name: "overview", title: "招聘总览", icon: "总" }] },
  { title: "招聘", items: resourceNavigation.filter((x) => ["accounts", "candidates", "jobs-workspace"].includes(x.name)) },
  { title: "管理", items: [{ name: "team-settings", title: "团队设置", icon: "设" }] as NavigationItem[] },
] as const;
