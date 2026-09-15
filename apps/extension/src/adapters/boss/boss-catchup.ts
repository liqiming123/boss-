import { observeUserActivity } from "../../shared/user-activity";
import { delay } from "../../shared/delay";

export function parseBossListActivity(text: string, now = new Date()): Date | null {
  const normalized = text.replace(/\s+/g, " "), result = new Date(now);
  const clock = normalized.match(/(?:^|\s)(\d{1,2}):(\d{2})(?:\s|$)/);
  const applyClock = (value: Date) => {
    value.setHours(clock ? Number(clock[1]) : 0, clock ? Number(clock[2]) : 0, 0, 0);
    return value;
  };
  if (/昨天/.test(normalized)) {
    result.setDate(result.getDate() - 1);
    return applyClock(result);
  }
  const full = normalized.match(/(\d{4})[./年-](\d{1,2})[./月-](\d{1,2})/);
  if (full) return applyClock(new Date(Number(full[1]), Number(full[2]) - 1, Number(full[3])));
  const short = normalized.match(/(\d{1,2})月(\d{1,2})日/);
  if (short) {
    result.setMonth(Number(short[1]) - 1, Number(short[2]));
    applyClock(result);
    if (result.getTime() > now.getTime() + 86_400_000) result.setFullYear(result.getFullYear() - 1);
    return result;
  }
  if (/今天|\d{1,2}:\d{2}/.test(normalized)) return applyClock(result);
  if (/刚刚/.test(normalized)) return now;
  return null;
}

/** BOSS puts an unread count immediately before the date in a list row. */
export function hasBossUnreadBadge(text: string): boolean {
  return /^\s*\d{1,3}\s+(?=(?:昨天|今天|刚刚|\d{1,2}:\d{2}|\d{1,2}月\d{1,2}日|\d{4}[./年-]))/.test(
    text.replace(/\s+/g, " "),
  );
}

/**
 * BOSS collapses older list activity to a calendar label such as `09月05日`.
 * That label carries no hour/minute information, so it must never be compared
 * as midnight against the precise timestamp stored by the API.
 */
export function isBossListActivityDateOnly(text: string): boolean {
  const normalized = text.replace(/\s+/g, " ");
  const hasCalendarLabel = /(?:昨天|今天|\d{1,2}月\d{1,2}日|\d{4}[./年-]\d{1,2}[./月-]\d{1,2})/.test(
    normalized,
  );
  const hasClock = /(?:^|\s)\d{1,2}:\d{2}(?:\s|$)/.test(normalized);
  return hasCalendarLabel && !hasClock;
}

/** Only a strictly newer BOSS list value needs reconciliation. */
export function isBossListActivityNewer(
  rowText: string,
  listActivity: string,
  storedActivity: string,
): boolean {
  const listTime = Date.parse(listActivity);
  const storedTime = Date.parse(storedActivity);
  if (!Number.isFinite(listTime)) return false;
  if (!Number.isFinite(storedTime)) return true;
  if (!isBossListActivityDateOnly(rowText))
    return Math.floor(listTime / 60_000) > Math.floor(storedTime / 60_000);
  const calendarKey = (value: number) => {
    const date = new Date(value);
    return date.getFullYear() * 10_000 + (date.getMonth() + 1) * 100 + date.getDate();
  };
  return calendarKey(listTime) > calendarKey(storedTime);
}

/** How many times one pass may click "滚动加载更多" before giving up. */
const LOAD_MORE_MAX_ATTEMPTS = 12;

type ConversationGroup = { parent: HTMLElement; items: HTMLElement[] };

function looksLikeConversationItem(child: HTMLElement, relaxed = false) {
  const text = (child.innerText || "").replace(/\s+/g, " ").trim();
  const rect = child.getBoundingClientRect();
  const dateLike = /昨天|今天|刚刚|\d{1,2}:\d{2}|\d{1,2}月\d{1,2}日|\d{4}[./年-]\d{1,2}/.test(text);
  const chineseCount = (text.match(/[\u4e00-\u9fff]/g) || []).length;
  if (!text || text.length < 6 || text.length > 420 || !dateLike || chineseCount < 2 || !parseBossListActivity(text)) return false;
  if (relaxed && rect.width === 0 && rect.height === 0) return true;
  return rect.height >= 38 && rect.height <= 180 && rect.width >= 160 &&
    rect.width < window.innerWidth * 0.42 && rect.left < window.innerWidth * 0.48;
}

/** Capture the unread state before BOSS consumes the badge on row click. */
export function observeBossUnreadConversationClick(
  callback: (rowText: string) => void,
): () => void {
  const handler = (event: MouseEvent) => {
    let node = event.target instanceof HTMLElement ? event.target : null;
    for (let depth = 0; node && depth < 10; depth++, node = node.parentElement) {
      const text = (node.innerText || node.textContent || "").replace(/\s+/g, " ").trim();
      if (hasBossUnreadBadge(text) && looksLikeConversationItem(node, true)) {
        callback(text);
        return;
      }
    }
  };
  document.addEventListener("click", handler, true);
  return () => document.removeEventListener("click", handler, true);
}

/** The conversation filter labels rendered above the list. */
const BOSS_LIST_FILTERS = [
  "全部",
  "新招呼",
  "沟通中",
  "已约面",
  "已获取简历",
  "已交换电话",
  "已交换微信",
  "收藏",
] as const;

/**
 * The tab a pass returns to when it could not tell where the recruiter was.
 *
 * A pass forces “沟通中”, so a pass that cannot identify the original tab would
 * otherwise leave the recruiter on a list the tool chose. “新招呼” is the list
 * they work from, and it is the one they must be looking at again when the pass
 * ends.
 */
export const DEFAULT_RESTORED_FILTER = "新招呼";

/**
 * A tab may render its badge next to the label (`新招呼3`, `新招呼(3)`), which
 * is why an exact text match is not enough to find — or to recognise — a tab.
 */
function filterLabelMatches(text: string, label: string): boolean {
  const normalized = text.replace(/\s+/g, "").trim();
  if (normalized === label) return true;
  if (!normalized.startsWith(label)) return false;
  return /^[（(·:：]?\d{1,4}[)）]?$/.test(normalized.slice(label.length));
}

function filterTargets(label: string): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>('[role="tab"],button,a,div,span')]
    .filter((node) => filterLabelMatches(node.innerText || node.textContent || "", label))
    .filter((node) => {
      const rect = node.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && rect.top >= 0 &&
        rect.top < window.innerHeight * 0.4 && rect.left < window.innerWidth * 0.6;
    })
    .map((node) => node.closest<HTMLElement>('[role="tab"],button,a') ?? node)
    .filter((node, index, all) => all.indexOf(node) === index)
    .sort((a, b) => {
      const area = (node: HTMLElement) => {
        const rect = node.getBoundingClientRect();
        return rect.width * rect.height;
      };
      return area(a) - area(b);
    });
}

function filterTarget(label: string): HTMLElement | undefined {
  return filterTargets(label)[0];
}

/**
 * BOSS marks the active tab with one of several conventions, and a CSS-module
 * build hashes the class name, so `classList.contains("active")` never matches
 * it. The word boundary matters: `inactive` must not read as selected.
 */
const SELECTED_CLASS_TOKEN = /(?:^|[-_])(?:active|selected|current|checked)(?:$|[-_])/i;

function hasSelectedMarker(node: HTMLElement): boolean {
  if (node.getAttribute("aria-selected") === "true") return true;
  if (node.getAttribute("aria-current") === "true") return true;
  if ((node.getAttribute("data-state") || "").toLowerCase() === "active") return true;
  return [...node.classList].some((token) => SELECTED_CLASS_TOKEN.test(token));
}

function isFilterSelected(target: HTMLElement): boolean {
  if (hasSelectedMarker(target)) return true;
  // Some builds put the marker on the node that renders the label.
  if ([...target.querySelectorAll<HTMLElement>("*")].some(hasSelectedMarker)) return true;
  // A wrapper can carry the ARIA state. Only the explicit attributes count for
  // ancestors: a shared container with a plain `active` class would match the
  // first tab in the list and restore the wrong one.
  let node = target.parentElement;
  for (let depth = 0; node && depth < 3; depth++, node = node.parentElement) {
    if (node.getAttribute("aria-selected") === "true" || node.getAttribute("aria-current") === "true")
      return true;
  }
  return false;
}

/** The filter the recruiter was reading before a pass switches to “沟通中”.
 *
 * Returns undefined when the current filter cannot be identified; the pass then
 * falls back to `DEFAULT_RESTORED_FILTER` instead of leaving the recruiter on
 * whatever list the traversal happened to select. */
export function currentBossListFilter(): string | undefined {
  for (const label of BOSS_LIST_FILTERS) {
    // Every node rendering this label is checked, not just the best-looking
    // one: a stale or duplicated tab that is not the selected node must not
    // hide the tab the recruiter is actually on.
    if (filterTargets(label).some(isFilterSelected)) return label;
  }
  return undefined;
}

/**
 * Put the recruiter back on the list they were reading.
 *
 * The restore is verified: BOSS re-renders the tab strip on a filter change, so
 * a click on a node that is about to be replaced is lost. The target is
 * re-resolved and the click retried once, and the outcome is reported instead of
 * assumed.
 */
export async function restoreBossListFilter(label: string | undefined): Promise<boolean> {
  const wanted = label || DEFAULT_RESTORED_FILTER;
  // The traversal's own list is where it left the page; nothing to restore.
  if (wanted === "沟通中") return false;
  for (let attempt = 0; attempt < 2; attempt++) {
    const target = filterTarget(wanted);
    if (!target) return false;
    if (isFilterSelected(target)) return true;
    target.click();
    await delay(500);
  }
  const settled = filterTarget(wanted);
  return !!settled && isFilterSelected(settled);
}

/** The stored index entry for one list row, as the server returns it. */
export type BossIndexEntry = {
  candidate_display_name: string;
  job_display_name: string;
  conversation_updated_at: string;
  synced?: boolean;
};

export type BossRowDecision = {
  open: boolean;
  reason: "CANDIDATE_OPENED" | "CATCHUP_RECONCILED" | "HISTORY_SNAPSHOT";
  /** Present only when the row was left alone, for logging and tests. */
  skip?: "UNREAD" | "NOT_IN_TABLE" | "UNCHANGED";
};

/**
 * Decide what a polling pass may do with one list row.
 *
 * The historical snapshot pass deliberately opens every read row:
 *
 * 1. An unread row is never opened. Opening a conversation is what BOSS counts
 *    as reading it, so a pass that clicked unread rows silently emptied the
 *    recruiter's inbox — every red dot disappeared without anyone reading the
 *    messages.
 * 2. Every read row is opened even when it is unchanged or has not reached
 *    Feishu yet. The row click gives the adapter the candidate details needed
 *    to synchronize the record and capture that conversation.
 */
export function decideBossRow(
  rowText: string,
  activity: string,
  entry: BossIndexEntry | undefined,
  captureEveryRead = false,
): BossRowDecision {
  if (hasBossUnreadBadge(rowText)) {
    return { open: false, reason: "CANDIDATE_OPENED", skip: "UNREAD" };
  }
  if (captureEveryRead) return { open: true, reason: "HISTORY_SNAPSHOT" };
  if (!entry || entry.synced === false) {
    return { open: false, reason: "CANDIDATE_OPENED", skip: "NOT_IN_TABLE" };
  }
  if (!isBossListActivityNewer(rowText, activity, entry.conversation_updated_at)) {
    return { open: false, reason: "CANDIDATE_OPENED", skip: "UNCHANGED" };
  }
  return { open: true, reason: "CATCHUP_RECONCILED" };
}

/**
 * The activity label BOSS renders in front of a conversation row.
 *
 * It is deliberately stripped as a whole — including its trailing whitespace —
 * so the fields behind it can be read. `昨天 23:51`, `09月05日`, `01:43` and a
 * leading unread count are all one prefix, and the old split left the leading
 * space in place, which made every prefixed row report an empty job name.
 */
const BOSS_ROW_ACTIVITY_PREFIX =
  /^\s*(?:\d{1,3}\s+)?(?:(?:昨天|今天|刚刚)(?:\s+\d{1,2}:\d{2})?|\d{1,2}:\d{2}|\d{1,2}月\d{1,2}日|\d{4}[./年-]\d{1,2}[./月-]\d{1,2})\s*/;

/**
 * Split a conversation row into the candidate and job it displays.
 *
 * These two fields are the minimum identity the duplicate check accepts, so a
 * row can be checked without opening the conversation and reading the profile
 * card. This is the single implementation of that split: `bossRowIdentity`
 * and the catch-up pass's unread-detection key both come from here.
 */
export function parseBossRowIdentity(
  rowText: string,
): { candidateDisplayName: string; jobDisplayName: string } {
  const withoutDate = rowText.replace(/\s+/g, " ").trim().replace(BOSS_ROW_ACTIVITY_PREFIX, "");
  const candidateDisplayName =
    withoutDate.match(/^[\u4e00-\u9fff·]{2,20}/)?.[0] ?? withoutDate.slice(0, 20);
  const jobDisplayName =
    withoutDate.slice(candidateDisplayName.length).trim().split(/\s+/)[0] ?? "";
  return { candidateDisplayName, jobDisplayName };
}

/**
 * A list-row key that survives re-rendering but not a different person.
 *
 * Case and spacing are dropped because BOSS re-paints the same row with
 * different whitespace; the candidate and job text are not.
 */
export function bossRowIdentity(rowText: string): string {
  const compact = (value: string) => value.replace(/\s+/g, "").toLowerCase();
  const { candidateDisplayName, jobDisplayName } = parseBossRowIdentity(rowText);
  return `${compact(candidateDisplayName)}\u0000${compact(jobDisplayName)}`;
}

/** One mounted conversation row, as read without touching the page. */
export type BossMountedRow = {
  rowText: string;
  activity: string;
  hasUnread: boolean;
};

/**
 * Read the conversation rows BOSS has actually mounted.
 *
 * Read-only by construction: it does not click, scroll or switch filters, so
 * it may run while the recruiter is working. The list is virtualized, so only
 * the rows inside the current viewport come back — a row that is not mounted
 * is simply not observed this round, which is the correct trade for a watcher
 * that must never fight the recruiter for the list.
 */
export function readBossMountedRows(now = new Date()): BossMountedRow[] {
  const group = conversationGroup();
  if (!group) return [];
  const rows: BossMountedRow[] = [];
  for (const item of group.items) {
    if (!item.isConnected) continue;
    const rowText = (item.innerText || item.textContent || "").replace(/\s+/g, " ").trim();
    const activity = rowText ? parseBossListActivity(rowText, now) : null;
    if (!activity) continue;
    rows.push({
      rowText,
      activity: activity.toISOString(),
      hasUnread: hasBossUnreadBadge(rowText),
    });
  }
  return rows;
}

/** Select BOSS's top-level “沟通中” conversation filter before catch-up. */
export async function openBossCommunicatingFilter(): Promise<boolean> {
  const target = filterTarget("沟通中");
  if (!target) return false;
  if (!isFilterSelected(target)) {
    target.click();
    await delay(500);
  }
  return true;
}

function nearestScrollable(node: HTMLElement): HTMLElement | null {
  let current: HTMLElement | null = node.parentElement;
  while (current && current !== document.body && current !== document.documentElement) {
    if (current.scrollHeight > current.clientHeight + 16) return current;
    current = current.parentElement;
  }
  return null;
}

function commonParent(items: HTMLElement[]): HTMLElement {
  let parent = items[0]?.parentElement ?? document.body;
  while (parent.parentElement && !items.every((item) => parent.contains(item)))
    parent = parent.parentElement;
  return parent;
}

function conversationGroup(): ConversationGroup | null {
  const groups = [...document.querySelectorAll<HTMLElement>("div,ul,section")]
    .map((parent) => ({
      parent,
      items: [...parent.children]
        .filter((node): node is HTMLElement => node instanceof HTMLElement)
        .filter((child) => looksLikeConversationItem(child)),
    }))
    .filter((group) => group.items.length >= 2);
  groups.sort((a, b) => b.items.length - a.items.length);
  if (groups[0]) return groups[0];

  // BOSS sometimes inserts one or more virtualization wrappers between the
  // scroll viewport and each row.  In that live layout no parent has multiple
  // direct row children, so recover the visible rows by geometry and cluster
  // them under their shared scroll viewport. This uses only sanitized text,
  // dates and rectangles; it does not depend on unstable BOSS class names.
  const matches = [...document.querySelectorAll<HTMLElement>("div,li,a")]
    .filter((node) => looksLikeConversationItem(node, true));
  const leaves = matches.filter((node) =>
    !matches.some((other) => other !== node && node.contains(other)),
  );
  const buckets = new Map<HTMLElement, HTMLElement[]>();
  for (const item of leaves) {
    const viewport = nearestScrollable(item);
    if (!viewport) continue;
    const bucket = buckets.get(viewport) ?? [];
    bucket.push(item);
    buckets.set(viewport, bucket);
  }
  const clustered = [...buckets.entries()]
    .map(([parent, items]) => ({ parent, items }))
    .filter((group) => group.items.length >= 2)
    .sort((a, b) => b.items.length - a.items.length)[0];
  if (clustered) return clustered;
  return leaves.length >= 1
    ? { parent: commonParent(leaves), items: leaves }
    : null;
}

function scrollContainer(group: ConversationGroup): HTMLElement | null {
  let node: HTMLElement | null = group.parent;
  while (node && node !== document.body && node !== document.documentElement) {
    const style = typeof getComputedStyle === "function" ? getComputedStyle(node) : null;
    const scrollableStyle = !!style && /(auto|scroll|overlay)/.test(`${style.overflowY} ${style.overflow}`);
    if (node.scrollHeight > node.clientHeight + 16 || (scrollableStyle && node.clientHeight > 0)) return node;
    node = node.parentElement;
  }
  return null;
}

function itemKey(item: HTMLElement): string {
  const text = (item.innerText || "").replace(/\s+/g, " ").trim();
  const link = item.querySelector<HTMLAnchorElement>("a[href]")?.href ?? "";
  const dataId = Object.entries(item.dataset)
    .filter(([name]) => /id|key/i.test(name))
    .map(([name, value]) => `${name}:${value}`)
    .join("|");
  return `${dataId}\u0000${link}\u0000${text}`;
}

function clickConversationItem(item: HTMLElement) {
  const candidates = [...item.querySelectorAll<HTMLElement>("*")]
    .filter((node) => {
      const text = (node.innerText || node.textContent || "").replace(/\s+/g, " ").trim();
      const rect = node.getBoundingClientRect();
      return text.length >= 6 && !!parseBossListActivity(text) &&
        rect.width > 0 && rect.height > 0;
    })
    .sort((a, b) => {
      const area = (node: HTMLElement) => {
        const rect = node.getBoundingClientRect();
        return rect.width * rect.height;
      };
      return area(a) - area(b);
    });
  // Clicking a leaf/inner row node bubbles through the handler-bearing
  // wrapper in BOSS builds where calling click() on the virtualization shell
  // itself is ignored. Static/simple rows keep the original fallback.
  const target = candidates[0] ?? item;
  target.click();
}

function loadMoreAction(): HTMLElement | undefined {
  return [...document.querySelectorAll<HTMLElement>("body *")].find((node) =>
    (node.innerText || "").trim() === "滚动加载更多" && node.getBoundingClientRect().width > 0,
  );
}

export type CatchupResult = { scanned: number; available: boolean; complete: boolean };
export type CatchupRowObservation = { rowText: string; activity: string; hasUnread: boolean };

export async function runBossCatchup(
  watermark: string,
  originalCandidate: string,
  onProgress: (activity: string, rowText: string) => Promise<boolean>,
  shouldOpen: (rowText: string, activity: string) => boolean | Promise<boolean> = () => true,
  onRowObserved?: (observation: CatchupRowObservation) => Promise<void> | void,
  isCancelled: () => boolean = () => false,
): Promise<CatchupResult> {
  let group = conversationGroup();
  // The BOSS chat shell renders before its virtualized conversation list.
  // A restart reloads the page, so the first DOM pass can legitimately be
  // empty. Wait briefly for the list instead of treating that race as a
  // completed (or unavailable) catch-up run.
  for (let attempt = 0; !group && attempt < 30; attempt++) {
    if (isCancelled()) return { scanned: 0, available: false, complete: false };
    await delay(500);
    group = conversationGroup();
  }
  if (!group || isCancelled()) return { scanned: 0, available: false, complete: false };

  let container = scrollContainer(group);
  const originalScrollTop = container?.scrollTop ?? 0;
  if (container) {
    container.scrollTop = 0;
    container.dispatchEvent(new Event("scroll"));
    await delay(500);
  }

  // Enumerate mounted rows in order until the persisted checkpoint is
  // reached. The checkpoint row is still observed so unread->read transitions
  // remain detectable; rows beyond it are left untouched.
  const seen = new Set<string>();
  let pausedUntil = 0;
  let scanned = 0;
  let loadMoreAttempts = 0;
  let complete = false;
  // Conversation rows are ordered newest-first. When a watermark is supplied,
  // rows at or before it are outside the incremental window and must not be
  // reached by scrolling; the anchor row itself is still observed so unread
  // transitions stay detectable. Per-row reconciliation passes an empty
  // watermark, which means "no stop" — every row is priced individually and
  // the traversal only ends when the list is exhausted.
  const watermarkTime = Date.parse(watermark);
  const hasWatermark = watermark.trim().length > 0;
  const watermarkDateKey = hasWatermark && Number.isFinite(watermarkTime)
    ? (() => {
        const date = new Date(watermarkTime);
        return date.getFullYear() * 10_000 + (date.getMonth() + 1) * 100 + date.getDate();
      })()
    : null;
  const isNewerThanWatermark = (rowText: string, activity: string) => {
    if (!hasWatermark) return true;
    if (!Number.isFinite(watermarkTime)) return true;
    if (!isBossListActivityDateOnly(rowText)) return Date.parse(activity) > watermarkTime;
    if (watermarkDateKey === null) return true;
    const date = new Date(Date.parse(activity));
    const rowDateKey = date.getFullYear() * 10_000 + (date.getMonth() + 1) * 100 + date.getDate();
    return rowDateKey > watermarkDateKey;
  };
  // A pass must never fight the recruiter, but a 30-second back-off per click
  // made an active user watch "后台补扫进行中" for minutes. Ten quiet seconds is
  // enough to know the page is free; typing keeps re-arming it.
  const stopObserving = observeUserActivity(() => { pausedUntil = Date.now() + 10_000; });

  try {
    // BOSS virtualizes the list. Process each mounted viewport before moving
    // down; candidate text stays only in this in-page deduplication set.
    for (let step = 0; step < 100; step++) {
      if (isCancelled()) return { scanned, available: true, complete: false };
      group = conversationGroup();
      if (!group) break;
      if (!container?.isConnected) container = scrollContainer(group);
      const batch = group.items
        .map((item) => ({ item, key: itemKey(item), date: parseBossListActivity(item.innerText) }))
        .filter(({ key }) => !seen.has(key));

      for (const entry of batch) {
        seen.add(entry.key);
        const rowText = (entry.item.innerText || "").replace(/\s+/g, " ").trim();
        if (!entry.date || !entry.item.isConnected) continue;
        const activity = entry.date.toISOString();
        await onRowObserved?.({ rowText, activity, hasUnread: hasBossUnreadBadge(rowText) });
        if (!(await shouldOpen(rowText, activity))) continue;
        while (Date.now() < pausedUntil && !isCancelled()) await delay(Math.min(1000, pausedUntil - Date.now()));
        if (isCancelled()) return { scanned, available: true, complete: false };
        // Different BOSS builds attach the row handler either to the
        // virtualization wrapper or to a nested rendered node. Keep the
        // target geometry-validated and dispatch both native forms so a
        // legitimate row is actually selected without relying on a class
        // selector.
        clickConversationItem(entry.item);
        await delay(900);
        if (isCancelled()) return { scanned, available: true, complete: false };
        if (!(await onProgress(activity, rowText)))
          return { scanned, available: true, complete: false };
        scanned++;
      }

      // Do not scroll beyond the first persisted checkpoint row. This is
      // deliberately checked after the batch: a virtualized viewport can
      // contain both the final new row and the anchor row at once.
      if (container && batch.some(({ item }) => {
        const text = (item.innerText || "").replace(/\s+/g, " ").trim();
        const date = parseBossListActivity(text);
        return !!date && !isNewerThanWatermark(text, date.toISOString());
      })) {
        complete = true;
        break;
      }

      if (isCancelled()) return { scanned, available: true, complete: false };
      if (!container) {
        const more = loadMoreAction();
        if (!more) { complete = true; break; }
        if (loadMoreAttempts++ >= LOAD_MORE_MAX_ATTEMPTS) break;
        more.click();
        await delay(700);
        continue;
      }

      const maxScrollTop = Math.max(0, container.scrollHeight - container.clientHeight);
      if (container.scrollTop >= maxScrollTop - 2) {
        const more = loadMoreAction();
        if (more) {
          if (loadMoreAttempts >= LOAD_MORE_MAX_ATTEMPTS) break;
          loadMoreAttempts++;
          const beforeHeight = container.scrollHeight;
          more.click();
          await delay(700);
          const hasNewItems = conversationGroup()?.items.some((item) => !seen.has(itemKey(item))) ?? false;
          if (container.scrollHeight > beforeHeight || hasNewItems) continue;
          break;
        }
        complete = true;
        break;
      }

      const beforeTop = container.scrollTop;
      container.scrollTop = Math.min(maxScrollTop, beforeTop + Math.max(200, Math.floor(container.clientHeight * 0.75)));
      container.dispatchEvent(new Event("scroll"));
      await delay(500);
      if (container.scrollTop <= beforeTop) break;
    }
  } finally {
    if (!isCancelled() && container?.isConnected) {
      container.scrollTop = originalScrollTop;
      container.dispatchEvent(new Event("scroll"));
      await delay(300);
    }
    const original = originalCandidate
      ? conversationGroup()?.items.find((item) => (item.innerText || "").includes(originalCandidate))
      : undefined;
    if (original && !isCancelled()) {
      original.click();
      await delay(500);
    }
    stopObserving();
  }
  return { scanned, available: true, complete };
}
