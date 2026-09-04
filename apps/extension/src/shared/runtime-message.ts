import { delay } from "./delay";

/**
 * MV3 service workers can restart between receiving a message and sending its
 * async response. Retry only the transport, leaving business errors intact.
 */
export async function sendRuntimeMessage<T = unknown>(
  message: unknown,
  attempts = 2,
): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      return (await chrome.runtime.sendMessage(message)) as T;
    } catch (error) {
      lastError = error;
      if (attempt + 1 < attempts) await delay(250);
    }
  }
  throw lastError instanceof Error ? lastError : new Error("扩展后台通道已断开，请刷新 BOSS 页面");
}
