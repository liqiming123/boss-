import { clickDailyControl, isDailyControlVisible } from "../../shared/daily-click";
import { delay } from "../../shared/delay";

// Ground truth: Chrome AX inspection, 2026-09-09. Top menu 公司数据
// opens this URL; report uses table/row/cell, 选择日期 and 按天查看.
// Use the canonical host BOSS currently keeps after navigation. The www host
// redirects here and can make sender/tab URL checks race during page startup.
export const REPORT_URL = "https://zhipin.com/web/frame/enterprise/recruit/data";
export const REPORT_PATH = "/web/frame/enterprise/recruit/data";
export function isReportUrl(url: string | undefined): boolean {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return (parsed.hostname === "zhipin.com" || parsed.hostname === "www.zhipin.com") && parsed.pathname === REPORT_PATH;
  } catch { return false; }
}
export const LABELS = ["BOSS查看牛人", "BOSS发起聊天", "BOSS沟通", "牛人查看BOSS", "牛人发起聊天"];
export const KEYS = ["boss_viewed_talent", "boss_started_chat", "boss_communication", "talent_viewed_boss", "talent_started_chat"] as const;
export type DailyRow = { boss_name: string } & Record<typeof KEYS[number], number>;
const clean = (value: string) => value.replace(/[\s\ue000-\uf8ff\u200b-\u200f\ufeff]/g, "");
const content = (node: Element) => (node as HTMLElement).innerText || node.textContent || "";

export function parseDailyTable(doc: Document): DailyRow[] {
  const table = [...doc.querySelectorAll("table")].find(t => LABELS.every(label => [...t.querySelectorAll("th")].some(th => clean(content(th)) === label)));
  if (!table) throw new Error("DAILY_HEADERS_MISSING");
  const headers = [...table.querySelectorAll("thead th")];
  const columns = (headers.length ? headers : [...table.querySelectorAll("tr:first-child th")]).map(th => clean(content(th)));
  const nameIndex = columns.indexOf("BOSS姓名");
  const indexes = LABELS.map(label => columns.indexOf(label));
  if (nameIndex < 0 || indexes.some(i => i < 0)) throw new Error("DAILY_HEADERS_MISSING");
  const result: DailyRow[] = [];
  for (const tr of table.querySelectorAll("tbody tr")) {
    const cells = [...tr.querySelectorAll("td")];
    if (!cells.length) continue;
    const name = content(cells[nameIndex]).trim();
    if (cells.length < columns.length && !/[\u4e00-\u9fffA-Za-z]/.test(name)) throw new Error("DAILY_ROW_INCOMPLETE");
    if (!name || name.length > 100 || /合计|没有相关数据/.test(name)) throw new Error("DAILY_NAME_INVALID");
    // BOSS omits empty profile/metric cells from data rows in some builds,
    // while keeping their headers. The five tracked counters remain in their
    // stable display order, followed by the three optional counters. Parse
    // the numeric tail instead of requiring a one-to-one header/cell count.
    const completeRow = cells.length === columns.length;
    const numeric = completeRow ? [] : cells.map(cell => content(cell).trim().replace(/,/g, "")).filter(raw => /^\d+$/.test(raw));
    if (!completeRow && numeric.length < LABELS.length) throw new Error("DAILY_ROW_INCOMPLETE");
    const metrics = indexes.map((columnIndex, metricIndex) => {
      const raw = completeRow ? content(cells[columnIndex]).trim().replace(/,/g, "") : numeric[metricIndex];
      if (!/^\d+$/.test(raw) || Number(raw) > 10000000) throw new Error("DAILY_NUMBER_MISSING");
      return Number(raw);
    });
    result.push({ boss_name: name, ...Object.fromEntries(KEYS.map((key, i) => [key, metrics[i]])) } as DailyRow);
  }
  if (!result.length) throw new Error("DAILY_NO_ROWS");
  if (new Set(result.map(r => r.boss_name)).size !== result.length) throw new Error("DAILY_DUPLICATE_NAMES");
  return result;
}

function reportDocument(): Document {
  const docs = [document];
  for (const iframe of document.querySelectorAll("iframe")) {
    try { if (iframe.contentDocument) docs.push(iframe.contentDocument); } catch { /* no cross-origin access */ }
  }
  return docs.find(d => content(d.body).includes("单人汇总")) || document;
}
function reportDocuments(preferred?: Document): Document[] {
  const docs = [preferred, document].filter((d): d is Document => !!d);
  for (let i = 0; i < docs.length; i++) {
    for (const iframe of docs[i].querySelectorAll("iframe")) {
      try { if (iframe.contentDocument && !docs.includes(iframe.contentDocument)) docs.push(iframe.contentDocument); } catch { /* cross-origin */ }
    }
  }
  return [...new Set(docs)];
}
function dateInputIn(docs: Document[]): HTMLElement | undefined {
  for (const d of docs) {
    const labelled = d.querySelector<HTMLInputElement>('input[placeholder*="选择日期"], [role="textbox"][aria-label*="选择日期"], [contenteditable="true"][aria-label*="选择日期"]');
    if (labelled && isDailyControlVisible(labelled)) return labelled;
    // Some BOSS builds omit the placeholder from the native input while
    // keeping its ISO date value and readonly behavior.
    const byValue = [...d.querySelectorAll<HTMLElement>('input,[role="textbox"]')].find(i => isDailyControlVisible(i) && /^(\d{4}[-.]\d{2}[-.]\d{2})$/.test((i as HTMLInputElement).value || i.getAttribute("aria-valuetext") || i.textContent || ""));
    if (byValue) return byValue;
    // Some builds render the single-day picker as a custom div with no
    // input/ARIA role; the visible ISO date is still the activation target.
    const byDateText = [...d.querySelectorAll<HTMLElement>("div,span,p,label,td,button,[class]")].find(i => isDailyControlVisible(i) && /^(\d{4}[-.]\d{2}[.-]\d{2})$/.test((i.innerText || i.textContent || "").trim()));
    if (byDateText) return byDateText;
  }
  return undefined;
}
export function exact(doc: Document, label: string): HTMLElement | undefined {
  return [...doc.querySelectorAll<HTMLElement>("a,button,span,div,li")].reverse().find(e => {
    const value = clean(content(e)).replace(/[：:＞>箭头]/g, "");
    return isDailyControlVisible(e) && value === label;
  });
}
function exactInDocuments(docs: Document[], label: string): HTMLElement | undefined {
  for (const candidate of docs) {
    const found = exact(candidate, label);
    if (found) return found;
  }
  return undefined;
}
export function reportAccountMatches(doc: Document, name: string): boolean {
  // The authenticated account is above 使用说明; table rows are never identity evidence.
  const prefix = content(doc.body).split("使用说明")[0];
  return prefix.length < 2000 && prefix.split(/\s+/).includes(name);
}

export function calendarDayCell(table: Element, day: number, daysInMonth: number): HTMLElement {
  // Some date-picker builds use semantic <td> elements, while others expose
  // the same grid through ARIA roles. Both variants are visible in Chrome AX.
  const cells = [...table.querySelectorAll<HTMLElement>('td,[role="gridcell"],[role="cell"]')];
  const start = cells.findIndex(cell => clean(content(cell)) === "1");
  if (start < 0 || cells.slice(start, start + daysInMonth).some((cell, i) => clean(content(cell)) !== String(i + 1)) || cells.length < start + daysInMonth) throw new Error("DAILY_CALENDAR_INVALID");
  const cell = cells[start + day - 1];
  if (!cell) throw new Error("DAILY_CALENDAR_INVALID");
  // The current BOSS picker binds its selection handler to the TD itself.
  // Clicking the nested text span opens/closes nothing, even though it shows
  // the correct number, so always activate the validated calendar cell.
  return cell;
}

export function visibleCalendarDayCell(roots: (Element | Document)[], day: number, daysInMonth: number): HTMLElement | undefined {
  for (const table of roots.flatMap(root => [...root.querySelectorAll<HTMLElement>('table,[role="grid"],[role="table"]')])) {
    try { return calendarDayCell(table, day, daysInMonth); } catch { /* not the calendar grid */ }
  }
  return undefined;
}

export async function selectReportDate(doc: Document, input: HTMLElement | undefined, date: string, check: () => Promise<void>) {
  const [year, month, day] = date.split("-").map(Number);
  if (input) { await check(); await clickDailyControl(input); }
  for (let attempt = 0; attempt < 40; attempt++) {
    await check(); await delay(300);
    const roots = reportDocuments(reportDocument());
    const buttons = roots.flatMap(root => [...root.querySelectorAll<HTMLElement>('button,[role="button"]')]).filter(isDailyControlVisible);
    const yearButton = buttons.find(b => /^(\d{4})年$/.test(clean(content(b))));
    const monthButton = buttons.find(b => /^(\d{1,2})月$/.test(clean(content(b))));
    if (!yearButton || !monthButton) continue;
    const shownYear = Number(clean(content(yearButton)).replace("年", ""));
    const shownMonth = Number(clean(content(monthButton)).replace("月", ""));
    const delta = (year - shownYear) * 12 + month - shownMonth;
    if (delta === 0) {
      const tables = roots.flatMap(root => [...root.querySelectorAll<HTMLElement>('table,[role="grid"],[role="table"]')]).filter(isDailyControlVisible);
      let cell: HTMLElement | undefined;
      for (const table of tables) {
        try { cell = calendarDayCell(table, day, new Date(year, month, 0).getDate()); break; } catch { /* metrics table */ }
      }
      if (!cell || !isDailyControlVisible(cell)) continue;
      await clickDailyControl(cell);
      return;
    }
    const label = delta < 0 ? "上个月" : "下个月";
    const button = buttons.find(b => [b.getAttribute("aria-label"), b.getAttribute("title"), content(b)].some(value => value?.trim() === label));
    if (!button) throw new Error("DAILY_CALENDAR_NAV_MISSING");
    await clickDailyControl(button);
    let changed = false;
    for (let i = 0; i < 20; i++) {
      await check(); await delay(200);
      const live = reportDocuments(reportDocument()).flatMap(root => [...root.querySelectorAll('button,[role="button"]')]).filter(isDailyControlVisible);
      const y = live.find(b => /^(\d{4})年$/.test(clean(content(b))));
      const m = live.find(b => /^(\d{1,2})月$/.test(clean(content(b))));
      if (y && m && (clean(content(y)) !== `${shownYear}年` || clean(content(m)) !== `${shownMonth}月`)) { changed = true; break; }
    }
    if (!changed) throw new Error("DAILY_CALENDAR_NAV_STALLED");
  }
  // Never guess the month from the number of day cells.
  throw new Error("DAILY_CALENDAR_MONTH_UNVERIFIED");
}

export async function ensureDailyPeriod(check: () => Promise<void>) {
  let period: HTMLElement | undefined;
  for (let i = 0; i < 40 && !period; i++) {
    await check();
    const docs = reportDocuments(reportDocument());
    // The visible range trigger takes priority over a menu option.
    period = exactInDocuments(docs, "近七天") || exactInDocuments(docs, "按天查看");
    if (!period) await delay(500);
  }
  if (!period) throw new Error("DAILY_PERIOD_CONTROL_MISSING");
  if (clean(content(period)) === "近七天") {
    await clickDailyControl(period);
    let dayOption: HTMLElement | undefined;
    for (let i = 0; i < 30 && !dayOption; i++) {
      await check(); await delay(200);
      dayOption = exactInDocuments(reportDocuments(reportDocument()), "按天查看");
    }
    if (!dayOption) throw new Error("DAILY_PERIOD_MENU_NOT_OPEN");
    await clickDailyControl(dayOption);
  }
  for (let i = 0; i < 40; i++) {
    await check(); await delay(250);
    const docs = reportDocuments(reportDocument());
    // A hidden menu label or the old metrics table is not proof of switching.
    const input = dateInputIn(docs);
    if (exactInDocuments(docs, "按天查看") && input) return input;
  }
  throw new Error("DAILY_PERIOD_SWITCH_FAILED");
}

export async function collectDailyReport(account: string, date: string, authorized: () => Promise<boolean>, useCurrentDayDefault = false) {
  const check = async () => { if (!await authorized()) throw new Error("DAILY_SESSION_ENDED"); };
  let doc = reportDocument();
  for (let i = 0; i < 40 && !reportAccountMatches(document, account) && !reportAccountMatches(doc, account); i++) {
    await check(); await delay(500); doc = reportDocument();
  }
  if (!reportAccountMatches(document, account) && !reportAccountMatches(doc, account)) throw new Error("DAILY_WRONG_ACCOUNT");
  await check();
  const input = await ensureDailyPeriod(check);
  // The 23:30 incremental run uses the day BOSS selects automatically after
  // switching to 按天查看. Historical/manual catch-up still opens the calendar
  // and chooses an explicit date.
  if (!useCurrentDayDefault) await selectReportDate(reportDocument(), input, date, check);
  doc = reportDocument();
  let updated = "";
  let last = "";
  let stable = 0;
  let pageRows: DailyRow[] = [];
  for (let i = 0; i < 60; i++) {
    await check(); await delay(500);
    doc = reportDocument();
    const marker = content(doc.body).match(/列表展示数据截止至\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})/);
    if (marker && marker[1] !== date) { stable = 0; last = ""; continue; }
    const selectedDate = (dateInputIn(reportDocuments(doc)) as HTMLInputElement | undefined)?.value;
    if (selectedDate !== date) { stable = 0; last = ""; continue; }
    // Historical reports omit the provider update marker. Use the time of
    // observation for freshness ordering, never manufacture a provider cutoff.

    try { pageRows = parseDailyTable(doc); } catch { stable = 0; continue; }
    const key = JSON.stringify(pageRows);
    stable = key === last ? stable + 1 : 0;
    last = key;
    updated = marker ? `${marker[1]}T${marker[2]}+08:00` : new Date().toISOString();
    if (stable >= 2) break;
  }
  if (!updated || stable < 2) throw new Error("DAILY_DATE_NOT_LOADED");
  const allRows = [...pageRows];
  for (let page = 0; page < 100; page++) {
    // The observed pager uses U+E604 for next and U+E600 for previous.
    const next = [...doc.querySelectorAll<HTMLElement>("a,button")].find(a => content(a).trim() === "\ue604");
    // BOSS omits pagination entirely when all recruiters fit on one page.
    // Absence of a next control is therefore a valid single-page result.
    if (!next) return { account_display_name: account, metric_date: date, source_updated_at: updated, rows: allRows };
    const disabled = [next, next.parentElement].some(e => e && (e.getAttribute("aria-disabled") === "true" || /disabled/i.test(e.className)));
    if (disabled) return { account_display_name: account, metric_date: date, source_updated_at: updated, rows: allRows };
    await check(); await clickDailyControl(next);
    const previous = JSON.stringify(pageRows);
    let changed = false;
    for (let i = 0; i < 30; i++) {
      await check(); await delay(500);
      doc = reportDocument();
      try { pageRows = parseDailyTable(doc); } catch { continue; }
      if (JSON.stringify(pageRows) !== previous) { changed = true; break; }
    }
    if (!changed) throw new Error("DAILY_PAGINATION_STALLED");
    for (const row of pageRows) {
      if (allRows.some(r => r.boss_name === row.boss_name)) throw new Error("DAILY_DUPLICATE_NAMES");
      allRows.push(row);
    }
  }
  throw new Error("DAILY_TOO_MANY_PAGES");
}
