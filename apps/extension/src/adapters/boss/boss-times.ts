import { normalizeBossText } from "./boss-normalizers";

type ConversationTimes = {
  conversationStartedAt?: string;
  conversationUpdatedAt?: string;
};
const DAY = 86_400_000,
  HOUR = 3_600_000;
const shanghaiParts = (now: Date) => {
  const shifted = new Date(now.getTime() + 8 * HOUR);
  return {
    year: shifted.getUTCFullYear(),
    month: shifted.getUTCMonth() + 1,
    day: shifted.getUTCDate(),
  };
};
const shanghaiDate = (
  year: number,
  month: number,
  day: number,
  hour: number,
  minute: number,
) => new Date(Date.UTC(year, month - 1, day, hour - 8, minute));

function exactTimes(value: string, now: Date) {
  const results: Date[] = [];
  const base = shanghaiParts(now);
  const push = (
    year: number,
    month: number,
    day: number,
    hour: number,
    minute: number,
  ) => {
    const date = shanghaiDate(year, month, day, hour, minute);
    if (date.getTime() <= now.getTime() + 5 * 60_000) results.push(date);
  };
  for (const match of value.matchAll(
    /(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})日?\s+(\d{1,2}):(\d{2})/g,
  ))
    push(+match[1], +match[2], +match[3], +match[4], +match[5]);
  for (const match of value.matchAll(
    /(?<!\d{4}[年./-])(\d{1,2})月(\d{1,2})日?\s+(\d{1,2}):(\d{2})/g,
  )) {
    let year = base.year;
    const candidate = shanghaiDate(
      year,
      +match[1],
      +match[2],
      +match[3],
      +match[4],
    );
    if (candidate.getTime() > now.getTime() + DAY) year--;
    push(year, +match[1], +match[2], +match[3], +match[4]);
  }
  for (const match of value.matchAll(
    /(?<![\d./-])(\d{1,2})[-./](\d{1,2})\s+(\d{1,2}):(\d{2})/g,
  )) {
    let year = base.year;
    const candidate = shanghaiDate(
      year,
      +match[1],
      +match[2],
      +match[3],
      +match[4],
    );
    if (candidate.getTime() > now.getTime() + DAY) year--;
    push(year, +match[1], +match[2], +match[3], +match[4]);
  }
  for (const match of value.matchAll(/(今天|昨天)\s*(\d{1,2}):(\d{2})/g)) {
    const calendar = new Date(
      Date.UTC(base.year, base.month - 1, base.day) -
        (match[1] === "昨天" ? DAY : 0),
    );
    push(
      calendar.getUTCFullYear(),
      calendar.getUTCMonth() + 1,
      calendar.getUTCDate(),
      +match[2],
      +match[3],
    );
  }
  for (const match of value.matchAll(/^\s*(\d{1,2}):(\d{2})(?:\s|$)/gm))
    push(base.year, base.month, base.day, +match[1], +match[2]);
  return results;
}

function detailText(text: string, candidateName: string) {
  const normalized = text.replace(/\u00a0/g, " ");
  const escaped = candidateName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const profile = new RegExp(
    `${escaped}\\s+(?:刚刚活跃|在线|离线|今日活跃|昨日活跃)?\\s*\\d{1,2}\\s*岁`,
  ).exec(normalized);
  const index = profile?.index ?? normalized.lastIndexOf(candidateName);
  return index >= 0 ? normalized.slice(index) : "";
}

export function parseBossConversationTimes(
  text: string,
  candidateName: string,
  now = new Date(),
): ConversationTimes {
  const detail = detailText(text, candidateName);
  if (!detail) return {};
  const lines = detail.split(/\n+/).map(normalizeBossText).filter(Boolean);
  const started: Date[] = [];
  for (let index = 0; index < lines.length; index++)
    if (/沟通(?:的)?职位/.test(lines[index]))
      started.push(
        ...exactTimes(
          lines.slice(Math.max(0, index - 2), index + 1).join(" "),
          now,
        ),
      );
  const updated = exactTimes(detail, now);
  const output: ConversationTimes = {};
  if (started.length)
    output.conversationStartedAt = new Date(
      Math.min(...started.map((item) => item.getTime())),
    ).toISOString();
  if (updated.length)
    output.conversationUpdatedAt = new Date(
      Math.max(...updated.map((item) => item.getTime())),
    ).toISOString();
  return output;
}
