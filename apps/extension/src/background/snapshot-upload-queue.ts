import { apiFormRequest } from "./api-client";
import { getAuth } from "./auth-store";
import {
  enqueueBounded,
  readStoredQueue,
  replaceStoredQueue,
} from "./storage-queue";

const STORAGE_KEY = "pendingSnapshotUploads",
  MAX_PENDING = 10;
export type SnapshotPayload = {
  candidateSourceIds: string[];
  parts: string[];
  snapshotHash: string;
};

// A stale source (for example, one created before the API database was reset)
// is a permanent failure. Retrying it forever hides newer snapshots and keeps
// the browser's local queue alive indefinitely.
export function shouldRetrySnapshot(error: unknown): boolean {
  const status =
    error && typeof error === "object" && "status" in error
      ? Number((error as { status?: unknown }).status)
      : 0;
  return status === 0 || status === 401 || status >= 500;
}

function valid(value: unknown): value is SnapshotPayload {
  return (
    !!value &&
    typeof value === "object" &&
    Array.isArray((value as SnapshotPayload).candidateSourceIds) &&
    !!(value as SnapshotPayload).candidateSourceIds[0] &&
    Array.isArray((value as SnapshotPayload).parts) &&
    typeof (value as SnapshotPayload).snapshotHash === "string"
  );
}

export async function uploadSnapshotPayload(payload: SnapshotPayload) {
  const form = new FormData();
  form.append("snapshot_hash", payload.snapshotHash);
  form.append(
    "related_source_ids",
    payload.candidateSourceIds.slice(1).join(","),
  );
  for (let index = 0; index < payload.parts.length; index++) {
    const blob = await (await fetch(payload.parts[index])).blob();
    form.append("files", blob, `boss-chat-${index + 1}.jpg`);
  }
  return apiFormRequest(
    `/plugin/conversations/${encodeURIComponent(payload.candidateSourceIds[0])}/snapshot`,
    form,
  );
}
export async function queueSnapshot(payload: SnapshotPayload) {
  await enqueueBounded(
    STORAGE_KEY,
    payload,
    valid,
    (item) => item.candidateSourceIds[0],
    MAX_PENDING,
  );
  chrome.alarms.create("recruitment-snapshot-retry", { periodInMinutes: 5 });
}
export async function flushSnapshotQueue() {
  const session = await getAuth();
  if (!session.accessToken) return;
  const queue = await readStoredQueue(STORAGE_KEY, valid),
    remaining: SnapshotPayload[] = [];
  for (let index = 0; index < queue.length; index++) {
    const current = await getAuth();
    if (!current.accessToken || current.deviceId !== session.deviceId) return;
    try {
      await uploadSnapshotPayload(queue[index]);
    } catch (error) {
      if (shouldRetrySnapshot(error)) {
        remaining.push(...queue.slice(index));
        break;
      }
    }
  }
  await replaceStoredQueue(STORAGE_KEY, remaining);
  if (!remaining.length)
    await chrome.alarms.clear("recruitment-snapshot-retry");
}
