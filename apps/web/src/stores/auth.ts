import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiClient } from "@recruitment/api-client";

type AdminUser = { display_name: string; role: string };

export const useAuthStore = defineStore("auth", () => {
  const user = ref<AdminUser | null>(null);
  const initialized = ref(false);
  const baseUrl =
    import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";
  const client = new ApiClient(baseUrl, () => null);
  let restoreInFlight: Promise<boolean> | null = null;
  async function restore() {
    if (initialized.value) return !!user.value;
    restoreInFlight ??= client
      .request<{ display_name: string; role: string }>("/auth/me")
      .then((data) => {
        user.value = { display_name: data.display_name, role: data.role };
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
  async function logout() {
    try {
      await client.request("/auth/logout", { method: "POST" });
    } finally {
      user.value = null;
      initialized.value = true;
    }
  }
  return { user, initialized, client, restore, login, logout };
});
