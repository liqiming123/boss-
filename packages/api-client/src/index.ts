export type ContextMatch = {
  match_level: string;
  candidate_source_id: string;
  recruiter_id: string;
  recruiter_name: string;
  job_id: string | null;
  job_name?: string;
  stage: string;
  updated_at?: string | number | null;
  match_reason: string;
  evidence_source?: string;
  feishu_synced?: boolean;
};
export type ContextResponse = {
  candidate_source_id: string | null;
  result_type: string;
  ui: { severity: string; title: string; message: string };
  matches: ContextMatch[];
  available_actions: string[];
  account_mapping: Record<string, unknown>;
  job_mapping: Record<string, unknown>;
  resume_status?: string;
};

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export class ApiClient {
  private refreshInFlight: Promise<string | null> | null = null;

  constructor(
    private baseUrl: string,
    private getToken: () => string | null,
    private refreshToken?: () => Promise<string | null>,
    private onAuthenticationFailed?: () => void,
  ) {}

  private fetch(path: string, init: RequestInit, token: string | null) {
    const headers = new Headers(init.headers);
    if (!(init.body instanceof FormData) && !headers.has("Content-Type"))
      headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(`${this.baseUrl}${path}`, {
      credentials: "include",
      ...init,
      headers,
    });
  }

  async request<T>(
    path: string,
    init: RequestInit = {},
    retried = false,
  ): Promise<T> {
    const token = this.getToken();
    let response = await this.fetch(path, init, token);
    if (
      response.status === 401 &&
      token &&
      !retried &&
      this.refreshToken &&
      !path.startsWith("/auth/")
    ) {
      this.refreshInFlight ??= this.refreshToken().finally(() => {
        this.refreshInFlight = null;
      });
      const refreshedToken = await this.refreshInFlight;
      if (refreshedToken)
        response = await this.fetch(path, init, refreshedToken);
      else this.onAuthenticationFailed?.();
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) this.onAuthenticationFailed?.();
      throw new ApiError(
        data.error?.code ?? "HTTP_ERROR",
        data.error?.message ?? "请求失败",
        response.status,
      );
    }
    return data as T;
  }
  list<T>(resource: string) {
    return this.request<T[]>(`/admin/${resource}`);
  }
}
