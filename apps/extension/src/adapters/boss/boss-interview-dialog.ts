import { normalizeBossText } from "./boss-normalizers";
import type { InterviewDetails } from "../types";

const DATE = /(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})/;
const TIME = /(?:^|[^\d:])(\d{1,2})\s*[:：]\s*(\d{2})(?!\d)/;

const textOf = (node: Element) =>
  normalizeBossText((node as HTMLElement).innerText || node.textContent || "");
const valueOf = (node: Element) =>
  node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement
    ? normalizeBossText(node.value)
    : "";

function validDate(match: RegExpMatchArray) {
  const [, year, month, day] = match.map(Number);
  return month >= 1 && month <= 12 && day >= 1 && day <= 31
    ? { year, month, day }
    : null;
}
function validTime(match: RegExpMatchArray) {
  const [, hour, minute] = match.map(Number);
  return hour <= 23 && minute <= 59 ? { hour, minute } : null;
}

/**
 * Read what the recruiter picked in BOSS's interview scheduler.
 *
 * The scheduler is a page-level dialog: 「线下面试/线上面试」+「面试时间」+
 * 「面试地址」, confirmed with 「发送」. It is located by walking up from the
 * confirmation control to the nearest container that owns the 面试时间 field,
 * so no BOSS class name is assumed. Only semantics (a checked radio, input
 * values, visible labels) are read.
 *
 * Deliberately NOT collected: the 联系人 row contains a colleague's name and
 * phone number. That is personal data the collaboration flow has no use for.
 *
 * Everything is best effort. A missing or partial schedule returns `{}`, and a
 * layout the reader cannot handle at all still returns `{}` rather than
 * throwing: the invitation itself is the business fact, the calendar entry is
 * only a bonus. Callers may therefore read the scheduler without any risk of
 * losing the 已约面 status sync.
 */
export function readBossInterviewDialog(
  confirm: HTMLElement | null,
): InterviewDetails {
  try {
    return readScheduler(confirm);
  } catch {
    return {};
  }
}

function readScheduler(confirm: HTMLElement | null): InterviewDetails {
  if (!confirm) return {};
  let dialog: HTMLElement | null = confirm.parentElement;
  while (dialog && !/面试时间/.test(textOf(dialog))) dialog = dialog.parentElement;
  if (!dialog) return {};

  const details: InterviewDetails = {};

  // Interview format: the selected radio's own label decides. Two labelled
  // options are always visible, so the dialog text alone is ambiguous.
  const radios = [
    ...dialog.querySelectorAll<HTMLInputElement>('input[type="radio"]'),
  ].filter((input) => input.checked || input.getAttribute("aria-checked") === "true");
  const checkedText = radios
    .map((input) => textOf(input.closest("label") || input.parentElement || input))
    .join(" ");
  if (/线下/.test(checkedText)) details.interview_type = "OFFLINE";
  else if (/线上/.test(checkedText)) details.interview_type = "ONLINE";

  // 面试时间 is a date picker plus a start-time picker. Both must resolve;
  // otherwise the schedule is unknown and must not be stored.
  const fields = [
    ...dialog.querySelectorAll<HTMLElement>(
      'input,textarea,[contenteditable="true"]',
    ),
  ]
    .map(valueOf)
    .filter(Boolean);
  const fromFields = fields.join(" ");
  const dialogText = textOf(dialog);
  const dateMatch = fromFields.match(DATE) || dialogText.match(DATE);
  const timeMatch = fromFields.match(TIME) || dialogText.match(TIME);
  const date = dateMatch && validDate(dateMatch);
  const time = timeMatch && validTime(timeMatch);
  if (date && time) {
    const pad = (value: number) => String(value).padStart(2, "0");
    // BOSS schedules in the recruiter's local time; the product only operates
    // in China Standard Time.
    details.scheduled_at = `${date.year}-${pad(date.month)}-${pad(date.day)}T${pad(time.hour)}:${pad(time.minute)}:00+08:00`;
  }

  // 面试地址 labels can appear more than once (a section heading and the field
  // itself), so take the first one whose row actually resolves to a value.
  const labelNodes = [...dialog.querySelectorAll<HTMLElement>("*")].filter(
    (node) =>
      node.children.length === 0 &&
      textOf(node).replace(/[:：]/g, "") === "面试地址",
  );
  for (const label of labelNodes) {
    const row = label.parentElement;
    const field =
      row?.querySelector<HTMLElement>('input,textarea,[contenteditable="true"]') ||
      (label.nextElementSibling?.matches(
        'input,textarea,[contenteditable="true"]',
      )
        ? (label.nextElementSibling as HTMLElement)
        : null);
    const raw = field
      ? valueOf(field)
      : row
        ? textOf(row).replace(/面试地址/, "").replace(/^[:：]/, "").trim()
        : "";
    const location = normalizeBossText(raw);
    // "选择地址" is a placeholder, not an address.
    if (location && location.length <= 300 && !/^选择/.test(location)) {
      details.location = location;
      break;
    }
  }

  return details;
}
