import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "../stores/auth";
import { resourceNavigation } from "../navigation";
const routes = [
  { path: "/login", component: () => import("../views/Login.vue") },
  { path: "/", redirect: "/recruitment/overview" },
  {
    path: "/recruitment/overview",
    component: () => import("../views/Dashboard.vue"),
    meta: { title: "运行总览" },
  },
  { path: "/recruitment/settings", component: () => import("../views/Settings.vue"), meta: { title: "系统设置" } },
  ...resourceNavigation.map(({ name, title }) => ({
    path: `/recruitment/${name}`,
    component: () => import("../views/Resource.vue"),
    props: { resource: name },
    meta: { title },
  })),
];
const router = createRouter({ history: createWebHistory(), routes });
router.beforeEach(async (to) => {
  const auth = useAuthStore(),
    authenticated = await auth.restore();
  if (to.path === "/login" && authenticated) return "/recruitment/overview";
  if (to.path !== "/login" && !authenticated) return "/login";
});
export default router;
