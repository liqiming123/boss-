import { observeUserActivity } from "../../shared/user-activity";
import { delay } from "../../shared/delay";

export function parseBossListActivity(text: string, now = new Date()): Date | null {
  const normalized = text.replace(/\s+/g, " "), result = new Date(now);
  if (/昨天/.test(normalized)) {
    result.setDate(result.getDate() - 1);
    result.setHours(0, 0, 0, 0);
    return result;
  }
  const full = normalized.match(/(\d{4})[./年-](\d{1,2})[./月-](\d{1,2})/);
  if (full) return new Date(Number(full[1]), Number(full[2]) - 1, Number(full[3]));
  const short = normalized.match(/(\d{1,2})月(\d{1,2})日/);
  if (short) {
    result.setMonth(Number(short[1]) - 1, Number(short[2]));
    result.setHours(0, 0, 0, 0);
    if (result.getTime() > now.getTime() + 86_400_000) result.setFullYear(result.getFullYear() - 1);
    return result;
  }
  if (/今天|刚刚|\d{1,2}:\d{2}/.test(normalized)) return now;
  return null;
}

type ConversationGroup = { parent: HTMLElement; items: HTMLElement[] };

function conversationGroup(): ConversationGroup | null {
  const groups = [...document.querySelectorAll<HTMLElement>("div,ul,section")]
    .map((parent) => ({
      parent,
      items: [...parent.children]
        .filter((node): node is HTMLElement => node instanceof HTMLElement)
        .filter((child) => {
          const text = (child.innerText || "").replace(/\s+/g, " ").trim();
          const rect = child.getBoundingClientRect();
          return text.length >= 6 && text.length <= 420 && !!parseBossListActivity(text) &&
            rect.height >= 38 && rect.height <= 180 && rect.width < window.innerWidth * 0.42 && rect.left < window.innerWidth * 0.48;
        }),
    }))
    .filter((group) => group.items.length >= 2);
  groups.sort((a, b) => b.items.length - a.items.length);
  return groups[0] ?? null;
}

function scrollContainer(group: ConversationGroup): HTMLElement | null {
  let node: HTMLElement | null = group.parent;
  while (node && node !== document.body && node !== document.documentElement) {
    if (node.scrollHeight > node.clientHeight + 16) return node;
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

function loadMoreAction(): HTMLElement | undefined {
  return [...document.querySelectorAll<HTMLElement>("body *")].find((node) =>
    (node.innerText || "").trim() === "滚动加载更多" && node.getBoundingClientRect().width > 0,
  );
}

export type CatchupResult = { scanned: number; available: boolean; complete: boolean };

export async function runBossCatchup(
  watermark: string,
  originalCandidate: string,
  onProgress: (activity: string) => Promise<boolean>,
): Promise<CatchupResult> {
  let group = conversationGroup();
  if (!group) return { scanned: 0, available: false, complete: false };

  let container = scrollContainer(group);
  const originalScrollTop = container?.scrollTop ?? 0;
  if (container) {
    container.scrollTop = 0;
    container.dispatchEvent(new Event("scroll"));
    await delay(500);
  }

  const threshold = Date.parse(watermark);
  const seen = new Set<string>();
  let pausedUntil = 0;
  let scanned = 0;
  let loadMoreAttempts = 0;
  let complete = false;
  const stopObserving = observeUserActivity(() => { pausedUntil = Date.now() + 30_000; });

  try {
    // BOSS virtualizes the list. Process each mounted viewport before moving
    // down; candidate text stays only in this in-page deduplication set.
    for (let step = 0; step < 100; step++) {
      group = conversationGroup();
      if (!group) break;
      if (!container?.isConnected) container = scrollContainer(group);
      const batch = group.items
        .map((item) => ({ item, key: itemKey(item), date: parseBossListActivity(item.innerText) }))
        .filter(({ key }) => !seen.has(key));

      for (const entry of batch) {
        seen.add(entry.key);
        if (!entry.date || entry.date.getTime() + 86_400_000 < threshold || !entry.item.isConnected) continue;
        while (Date.now() < pausedUntil) await delay(Math.min(1000, pausedUntil - Date.now()));
        entry.item.click();
        await delay(900);
        if (!(await onProgress(entry.date.toISOString()))) return { scanned, available: true, complete: false };
        scanned++;
      }

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
    if (container?.isConnected) {
      container.scrollTop = originalScrollTop;
      container.dispatchEvent(new Event("scroll"));
      await delay(300);
    }
    const original = conversationGroup()?.items.find((item) => (item.innerText || "").includes(originalCandidate));
    if (original) {
      original.click();
      await delay(500);
    }
    stopObserving();
  }
  return { scanned, available: true, complete };
}
