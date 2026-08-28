import { z } from 'zod';

export const tokenResponseSchema = z.object({ access_token: z.string(), refresh_token: z.string().optional(), token_type: z.string() });
export const matchSchema = z.object({ match_level: z.string(), candidate_source_id: z.string(), recruiter_id: z.string(), recruiter_name: z.string(), job_id: z.string().nullable(), stage: z.string(), match_reason: z.string() });
export const contextResponseSchema = z.object({ candidate_source_id: z.string().nullable(), result_type: z.string(), ui: z.object({ severity: z.string(), title: z.string(), message: z.string() }), matches: z.array(matchSchema), available_actions: z.array(z.string()), account_mapping: z.record(z.string(), z.unknown()), job_mapping: z.record(z.string(), z.unknown()) });
export type ContextResponse = z.infer<typeof contextResponseSchema>;

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) { super(message); }
}

export class ApiClient {
  constructor(private baseUrl: string, private getToken: () => string | null) {}
  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = this.getToken();
    const response = await fetch(`${this.baseUrl}${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(data.error?.code ?? 'HTTP_ERROR', data.error?.message ?? '请求失败', response.status);
    return data as T;
  }
  login(email: string, password: string) { return this.request<{access_token:string;refresh_token:string;user:{display_name:string;role:string}}>('/dev/login', { method: 'POST', body: JSON.stringify({ email, password }) }); }
  resolveContext(payload: unknown) { return this.request<ContextResponse>('/plugin/context/resolve', { method: 'POST', body: JSON.stringify(payload) }).then((data) => contextResponseSchema.parse(data)); }
  event(payload: unknown) { return this.request<{event_id:string;conflict_ids:string[]}>('/plugin/events', { method: 'POST', body: JSON.stringify(payload) }); }
  list<T>(resource: string) { return this.request<T[]>(`/admin/${resource}`); }
}

