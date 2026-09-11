import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiClient } from "@recruitment/api-client";

type AdminUser = { id: string; display_name: string; role: string; workspace?: { status: "ADMIN" | "READY" | "UNASSIGNED" | "EXTENSION_REQUIRED"; boss_account_name: string | null }; has_boss_assignment?: boolean; capabilities?: { data_scope: "OWN" | "COMPANY"; can_manage_team: boolean; can_manage_feishu: boolean; can_manage_jobs: boolean; can_reset_system: boolean } };

export const useAuthStore = defineStore("auth", () => {
  const user = ref<AdminUser | null>(null);
  const initialized = ref(false);
  // Production is always served behind the same HTTPS origin. Keeping the
  // production fallback relative prevents a local development URL from ever
  // being baked into a release when the build environment is missing.
  const baseUrl =
    import.meta.env.VITE_API_BASE_URL ??
    (import.meta.env.DEV ? "http://localhost:8000/api/v1" : "/api/v1");
  const client = new ApiClient(baseUrl, () => null);
  let restoreInFlight: Promise<boolean> | null = null;
  async function restore() {
    if (initialized.value) return !!user.value;
    restoreInFlight ??= client
      .request<AdminUser>("/auth/me")
      .then((data) => {
        user.value = {
          id: data.id,
          display_name: data.display_name,
          role: data.role,
          has_boss_assignment: data.has_boss_assignment,
          workspace: data.workspace,
          capabilities: data.capabilities,
        };
        return true;
      })
      .catch(() => {
        user.value = null;
        return false;
      })
      .finally(() => {
        initialized.value = true;
        restoreInFlight = null;
      });
    return restoreInFlight;
  }
  async function login() {
    const data = await client.request<{ authorization_url: string }>(
      "/auth/feishu/web/start",
      { method: "POST" },
    );
    window.location.assign(data.authorization_url);
  }
  async function refreshIdentity() {
    user.value = await client.request<AdminUser>("/auth/me");
  }
  async function logout() {
    try {
      await client.request("/auth/logout", { method: "POST" });
    } finally {
      user.value = null;
      initialized.value = true;
    }
  }
  return { user, initialized, client, restore, refreshIdentity, login, logout };
});
