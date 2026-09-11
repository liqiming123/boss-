import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "../stores/auth";
const routes = [
  { path: "/login", component: () => import("../views/Login.vue") },
  { path: "/", redirect: "/recruitment/overview" },
  {
    path: "/recruitment/overview",
    component: () => import("../views/Dashboard.vue"),
    meta: { title: "招聘总览" },
  },
  { path: "/recruitment/accounts", component: () => import("../views/Accounts.vue"), meta: { title: "BOSS 账号", requiresAdmin: true } },
  { path: "/recruitment/candidates", component: () => import("../views/Candidates.vue"), meta: { title: "候选人协同" } },
  { path: "/recruitment/jobs-workspace", component: () => import("../views/JobsWorkspace.vue"), meta: { title: "岗位与面试" } },
  { path: "/recruitment/team-settings", component: () => import("../views/TeamSettings.vue"), meta: { title: "团队设置", requiresAdmin: true } },
  { path: "/recruitment/settings", redirect: "/recruitment/team-settings" },
];
const router = createRouter({ history: createWebHistory(), routes });
router.beforeEach(async (to) => {
  const auth = useAuthStore(),
    authenticated = await auth.restore();
  if (to.path === "/login" && authenticated) return "/recruitment/overview";
  if (to.path !== "/login" && !authenticated) return "/login";
  if (authenticated && to.meta.requiresAdmin && auth.user?.workspace?.status !== "ADMIN") return "/recruitment/overview";
  if (authenticated && to.path !== "/recruitment/overview" && ["UNASSIGNED", "EXTENSION_REQUIRED"].includes(auth.user?.workspace?.status || "")) return "/recruitment/overview";
});
export default router;
