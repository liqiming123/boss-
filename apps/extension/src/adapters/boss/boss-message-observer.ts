import type { RecruiterMessageSent, ResumePreviewOpened } from "../types";
import { normalizeBossText } from "./boss-normalizers";
import { findBossConversationRegion } from "./boss-chat";

const QUICK_ACTIONS = new Set([
  "你好啊，可以聊一聊~",
  "不好意思，不太合适哦",
  "求简历",
  "换电话",
  "换微信",
  "约面试",
  "不合适",
]);
const SUCCESS_MARKER =
  /(?:送达|简历请求已发送|电话(?:交换)?请求已发送|微信(?:交换)?请求已发送|面试邀请已发送|邀请已发送)/g;
const successCount = () =>
  Array.from((document.body?.innerText || "").matchAll(SUCCESS_MARKER)).length;
const editorText = (target: EventTarget | null) => {
  const element = target instanceof HTMLElement ? target : null;
  const editor = element?.matches('textarea,input,[contenteditable="true"]')
    ? element
    : document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  if (!editor) return "";
  return normalizeBossText(
    editor instanceof HTMLInputElement || editor instanceof HTMLTextAreaElement
      ? editor.value
      : editor.innerText,
  );
};

export function observeBossRecruiterMessageSent(
  callback: (event: RecruiterMessageSent) => void,
) {
  let armed: null | { draft: string; markers: number; expiresAt: number } =
      null,
    timer = 0;
  const clear = () => {
    armed = null;
    window.clearTimeout(timer);
  };
  const arm = (draft = "") => {
    armed = { draft, markers: successCount(), expiresAt: Date.now() + 120_000 };
    window.clearTimeout(timer);
    timer = window.setTimeout(clear, 120_000);
  };
  const click = (event: MouseEvent) => {
    const element =
      event.target instanceof HTMLElement
        ? event.target.closest<HTMLElement>('button,[role="button"]')
        : null;
    if (!element) return;
    const region = findBossConversationRegion();
    if (region && !region.contains(element)) return;
    const label = normalizeBossText(
      element.innerText || element.getAttribute("aria-label") || "",
    );
    if (label === "发送") {
      const draft = editorText(document.activeElement);
      if (draft) arm(draft);
    } else if (QUICK_ACTIONS.has(label)) arm();
  };
  const keydown = (event: KeyboardEvent) => {
    if (event.key === "Enter" && !event.shiftKey) {
      const draft = editorText(event.target);
      if (draft) arm(draft);
    }
  };
  const observer = new MutationObserver(() => {
    if (!armed || Date.now() > armed.expiresAt) {
      clear();
      return;
    }
    const text = normalizeBossText(document.body?.innerText || "");
    if (successCount() > armed.markers) {
      clear();
      callback({
        sentAt: new Date().toISOString(),
        evidence: "DELIVERY_MARKER",
      });
      return;
    }
    if (
      armed.draft &&
      text.includes(armed.draft) &&
      !editorText(document.activeElement)
    ) {
      clear();
      callback({ sentAt: new Date().toISOString(), evidence: "OUTGOING_TEXT" });
    }
  });
  document.addEventListener("click", click, true);
  document.addEventListener("keydown", keydown, true);
  if (document.body)
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  return () => {
    clear();
    observer.disconnect();
    document.removeEventListener("click", click, true);
    document.removeEventListener("keydown", keydown, true);
  };
}

export function observeBossResumePreviewOpened(
  callback: (event: ResumePreviewOpened) => void,
) {
  const click = (event: MouseEvent) => {
    const element =
      event.target instanceof HTMLElement
        ? event.target.closest<HTMLElement>(
            'a[href],button,[role="button"],[download],[data-url],[data-href]',
          )
        : null;
    if (!element) return;
    const region = findBossConversationRegion();
    if (region && !region.contains(element)) return;
    const label = normalizeBossText(
      element.innerText ||
        element.getAttribute("aria-label") ||
        element.getAttribute("title") ||
        element.getAttribute("data-tooltip-content") ||
        "",
    );
    if (!/(预览|查看附件|附件简历|在线简历|下载简历|下载附件|下载)/.test(label) &&
        !element.hasAttribute("download")) return;
    const rawHref =
      (element instanceof HTMLAnchorElement ? element.href : undefined) ||
      element.getAttribute("data-url") ||
      element.getAttribute("data-href") ||
      element.getAttribute("href") ||
      undefined;
    let href: string | undefined;
    try {
      href = rawHref ? new URL(rawHref, location.href).href : undefined;
    } catch {
      href = undefined;
    }
    let fileName: string | undefined;
    try {
      fileName = href
        ? decodeURIComponent(
            new URL(href, location.href).pathname.split("/").pop() ||
              "candidate-resume.pdf",
          )
        : undefined;
    } catch {
      /* invalid href is handled by the downloader */
    }
    window.setTimeout(
      () => callback({ url: href, fileName, target: element }),
      500,
    );
  };
  document.addEventListener("click", click, true);
  return () => document.removeEventListener("click", click, true);
}
