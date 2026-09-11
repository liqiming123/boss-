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
  // Conversation rows are ordered newest-first. Once a row at or before the
  // persisted watermark is encountered, the rest of the list is outside the
  // incremental scan window and must not be reached by scrolling. Keep
  // scanning the current mounted batch so the anchor row itself can still be
  // observed for unread-state reconciliation.
  const watermarkTime = Date.parse(watermark);
  const watermarkDateKey = Number.isFinite(watermarkTime)
    ? (() => {
        const date = new Date(watermarkTime);
        return date.getFullYear() * 10_000 + (date.getMonth() + 1) * 100 + date.getDate();
      })()
    : null;
  const isNewerThanWatermark = (rowText: string, activity: string) => {
    if (!Number.isFinite(watermarkTime)) return true;
    if (!isBossListActivityDateOnly(rowText)) return Date.parse(activity) > watermarkTime;
    if (watermarkDateKey === null) return true;
    const date = new Date(Date.parse(activity));
    const rowDateKey = date.getFullYear() * 10_000 + (date.getMonth() + 1) * 100 + date.getDate();
    return rowDateKey > watermarkDateKey;
  };
  const stopObserving = observeUserActivity(() => { pausedUntil = Date.now() + 30_000; });

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
        if (loadMoreAttempts++ >= 5) break;
        more.click();
        await delay(700);
        continue;
      }

      const maxScrollTop = Math.max(0, container.scrollHeight - container.clientHeight);
      if (container.scrollTop >= maxScrollTop - 2) {
        const more = loadMoreAction();
        if (more) {
          if (loadMoreAttempts >= 5) break;
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
