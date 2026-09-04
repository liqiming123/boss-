import { apiRequest } from "./api-client";
import {
  enqueueBounded,
  readStoredQueue,
  replaceStoredQueue,
} from "./storage-queue";

const STORAGE_KEY = "pendingMessageSentEvents";
const MAX_PENDING = 1000;
type MessagePayload = Record<string, unknown> & { client_event_id: string };

function isPayload(value: unknown): value is MessagePayload {
  return (
    !!value &&
    typeof value === "object" &&
    typeof (value as Record<string, unknown>).client_event_id === "string"
  );
}

export function shouldRetryMessageSent(error: unknown): boolean {
  if (!(error instanceof Error)) return true;
  const status =
    "status" in error
      ? Number((error as Error & { status?: number }).status)
      : 0;
  return status === 0 || status === 401 || status >= 500;
}

export async function queueMessageSent(payload: unknown): Promise<void> {
  if (!isPayload(payload)) throw new Error("INVALID_MESSAGE_SENT_PAYLOAD");
  // Identity/job/time metadata only; chat content is never queued.
  await enqueueBounded(
    STORAGE_KEY,
    payload,
    isPayload,
    (item) => item.client_event_id,
    MAX_PENDING,
  );
}

export async function flushMessageQueue(): Promise<void> {
  const queue = await readStoredQueue(STORAGE_KEY, isPayload);
  if (!queue.length) return;
  const remaining: MessagePayload[] = [];
  for (let index = 0; index < queue.length; index++) {
    const payload = queue[index];
    try {
      await apiRequest("/plugin/engagements/message-sent", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    } catch (error) {
      if (shouldRetryMessageSent(error)) {
        remaining.push(...queue.slice(index));
        break;
      }
      // A permanent validation error is discarded so it cannot block later sends.
    }
  }
  await replaceStoredQueue(STORAGE_KEY, remaining.slice(-MAX_PENDING));
  if (!remaining.length) await chrome.alarms.clear("recruitment-message-retry");
}
