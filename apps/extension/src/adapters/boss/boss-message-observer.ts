import type {
  InterviewDetails,
  RecruiterMessageSent,
  ResumePreviewOpened,
} from "../types";
import { normalizeBossText } from "./boss-normalizers";
import { findBossConversationRegion } from "./boss-chat";
import { readBossInterviewDialog } from "./boss-interview-dialog";
import {
  BOSS_DIALOG_CONFIRM,
  BOSS_INTERVIEW_DIALOG,
  BOSS_INTERVIEW_ENTRY,
  BOSS_INVITE_SENT_MARKER,
  bossInterviewInviteEvidence,
  classifyBossOutgoingMessage,
} from "./boss-status";

const QUICK_ACTIONS = new Set([
  "你好啊，可以聊一聊~",
  "不好意思，不太合适哦",
  "求简历",
  "换电话",
  "换微信",
  "约面试",
  "不合适",
]);
const SUCCESS_MARKER = new RegExp(
  `(?:送达|简历请求已发送|电话(?:交换)?请求已发送|微信(?:交换)?请求已发送|${BOSS_INVITE_SENT_MARKER.source})`,
  "g",
);
const successCount = () =>
  Array.from((document.body?.innerText || "").matchAll(SUCCESS_MARKER)).length;
const editorText = (target: EventTarget | null) => {
  const element = target instanceof HTMLElement ? target : null;
  const editor = element?.matches('textarea,input,[contenteditable="true"]')
    ? element
    : document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  if (!editor?.matches('textarea,input,[contenteditable="true"]')) return "";
  return normalizeBossText(
    editor instanceof HTMLInputElement || editor instanceof HTMLTextAreaElement
      ? editor.value
      : editor.innerText || editor.textContent || "",
  );
};

export function observeBossRecruiterMessageSent(
  callback: (event: RecruiterMessageSent) => void,
) {
  let lastEditor: HTMLElement | null = null;
  const rememberEditor = (event: FocusEvent) => {
    if (event.target instanceof HTMLElement &&
        event.target.matches('textarea,input,[contenteditable="true"]'))
      lastEditor = event.target;
  };
  let armed: null | { draft: string; markers: number; expiresAt: number; armedAt: number; interviewInvite: boolean } =
      null,
    timer = 0,
    // Clicking 约面 opens a scheduling dialog whose confirmation carries no chat
    // draft and may be rendered outside the conversation region. Remember the
    // entry click so that confirmation is still recognized as an invitation.
    interviewIntentUntil = 0,
    inviteConfirmedAt = 0,
    // Read from the scheduler at confirmation time, because BOSS closes the
    // dialog as soon as the invitation is sent.
    pendingInterview: InterviewDetails | null = null;
  const bodyText = () => normalizeBossText(document.body?.innerText || "");
  /** BOSS renders the scheduler in a page-level dialog, so its evidence is read
   * from the whole document rather than from the chat pane. */
  const inviteDialogOpen = (text = bodyText()) => BOSS_INTERVIEW_DIALOG.test(text);
  const clear = () => {
    armed = null;
    pendingInterview = null;
    window.clearTimeout(timer);
  };
  const arm = (draft = "", interviewInvite = false) => {
    armed = { draft, markers: successCount(), expiresAt: Date.now() + 120_000, armedAt: Date.now(), interviewInvite };
    window.clearTimeout(timer);
    timer = window.setTimeout(clear, 120_000);
  };
  const interviewActive = () => Date.now() <= interviewIntentUntil;
  const emitInvite = () => {
    interviewIntentUntil = 0;
    inviteConfirmedAt = 0;
    const interview = pendingInterview;
    clear();
    callback({
      sentAt: new Date().toISOString(),
      evidence: "DELIVERY_MARKER",
      statusEvidence: bossInterviewInviteEvidence(),
      ...(interview ? { interview } : {}),
    });
  };
  /** Some BOSS builds confirm the invitation by closing the scheduler without
   * rendering a text marker. Only accepted once the dialog is really gone, so a
   * validation error that keeps it open is never recorded as a send. */
  const inviteDialogClosedAfterConfirm = (text = bodyText()) =>
    !!inviteConfirmedAt &&
    Date.now() - inviteConfirmedAt >= 1_000 &&
    !inviteDialogOpen(text);
  const isControlLabel = (text: string) =>
    text === "发送" ||
    text === "取消" ||
    QUICK_ACTIONS.has(text) ||
    BOSS_DIALOG_CONFIRM.test(text) ||
    BOSS_INTERVIEW_ENTRY.test(text);
  /** BOSS renders several chat controls as plain divs/spans without any button
   * role, so `closest("button")` alone silently misses 约面试. Resolve those by
   * their exact visible label. The walk is bounded and label-exact, so a
   * surrounding toolbar can never be mistaken for a single control. */
  const controlElement = (target: EventTarget | null): HTMLElement | null => {
    if (!(target instanceof HTMLElement)) return null;
    const semantic = target.closest<HTMLElement>(
      'button,[role="button"],a[href]',
    );
    if (semantic) return semantic;
    let node: HTMLElement | null = target;
    for (let depth = 0; node && depth < 4; depth++, node = node.parentElement) {
      const text = normalizeBossText(
        node.innerText || node.textContent || "",
      );
      if (text.length <= 12 && isControlLabel(text)) return node;
    }
    return null;
  };
  const click = (event: MouseEvent) => {
    const element = controlElement(event.target);
    if (!element) return;
    const region = findBossConversationRegion();
    const inRegion = !region || region.contains(element);
    const label = normalizeBossText(
      element.innerText ||
        element.textContent ||
        element.getAttribute("aria-label") ||
        "",
    );
    const dialogOpen = inviteDialogOpen();
    // Step 1 — the 约面 entry always comes first and is deliberately *not*
    // gated by the detected chat region: BOSS renders the quick-action bar
    // inside or outside that pane depending on the build, and dropping this
    // click makes the later confirmation unrecognizable.
    if (BOSS_INTERVIEW_ENTRY.test(label)) {
      interviewIntentUntil = Date.now() + 10 * 60_000;
      arm(label, true);
      return;
    }
    // Step 2 — the scheduler confirmation. It carries no chat draft and is
    // usually rendered outside the chat pane, so it is accepted while the 约面
    // intent is still fresh and the scheduler is genuinely open. The scheduler
    // gate is what keeps an ordinary chat 发送 from being read as an invite.
    if (interviewActive() && dialogOpen && BOSS_DIALOG_CONFIRM.test(label)) {
      // Arm first: recognizing and syncing the invitation must never depend on
      // the optional scheduler fields being readable.
      arm("", true);
      inviteConfirmedAt = Date.now();
      // The dialog disappears once BOSS accepts the invitation, so the chosen
      // date, time and address are read immediately — and only as a bonus.
      pendingInterview = readBossInterviewDialog(element);
      return;
    }
    if (!inRegion) {
      // Any other outside click means the recruiter moved on. Never let a stale
      // intent capture a later candidate's existing invitation marker.
      if (!dialogOpen) interviewIntentUntil = 0;
      return;
    }
    if (label === "发送") {
      const draft = editorText(document.activeElement) ||
        (lastEditor?.isConnected && (!region || region.contains(lastEditor))
          ? editorText(lastEditor) : "");
      if (draft) arm(draft);
      return;
    }
    if (QUICK_ACTIONS.has(label)) arm(label);
  };
  const keydown = (event: KeyboardEvent) => {
    if (event.key === "Enter" && !event.shiftKey) {
      const draft = editorText(event.target);
      if (draft) arm(draft);
    }
  };
  const observer = new MutationObserver(() => {
    if (!armed || Date.now() > armed.expiresAt) {
      if (armed) clear();
      else if (inviteDialogClosedAfterConfirm()) emitInvite();
      return;
    }
    const text = normalizeBossText(document.body?.innerText || "");
    if (successCount() > armed.markers) {
      // BOSS's own invitation marker is authoritative. The scheduler flow has
      // no chat draft, so the label/time text must never be classified as one.
      if (armed.interviewInvite) {
        emitInvite();
        return;
      }
      const messageText = armed.draft;
      clear();
      callback({
        sentAt: new Date().toISOString(),
        evidence: "DELIVERY_MARKER",
        ...(messageText
          ? {
              messageText,
              statusEvidence: classifyBossOutgoingMessage(messageText),
            }
          : {}),
      });
      return;
    }
    if (armed.interviewInvite) {
      if (inviteDialogClosedAfterConfirm(text)) emitInvite();
      return;
    }
    if (
      armed.draft &&
      text.includes(armed.draft) &&
      !editorText(document.activeElement)
    ) {
      const messageText = armed.draft;
      clear();
      callback({
        sentAt: new Date().toISOString(),
        evidence: "OUTGOING_TEXT",
        messageText,
        statusEvidence: classifyBossOutgoingMessage(messageText),
      });
      return;
    }
    // Windows BOSS builds sometimes clear the editor and render the sent
    // bubble inside a virtualized layer that is not exposed to body.innerText.
    // A cleared editor after a real send click is still useful evidence; wait
    // briefly so a validation failure that immediately restores the draft is
    // not recorded as a message.
    if (
      armed.draft &&
      Date.now() - armed.armedAt >= 1_000 &&
      !editorText(document.activeElement)
    ) {
      const messageText = armed.draft;
      clear();
      callback({
        sentAt: new Date().toISOString(),
        evidence: "OUTGOING_TEXT",
        messageText,
        statusEvidence: classifyBossOutgoingMessage(messageText),
      });
    }
  });
  document.addEventListener("click", click, true);
  document.addEventListener("focusin", rememberEditor, true);
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
    document.removeEventListener("focusin", rememberEditor, true);
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
    // The profile card has attachment buttons too. Never treat a missing
    // conversation region as permission to capture an arbitrary page button.
    if (!region || !region.contains(element)) return;
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
