import type { NativeCommunicationRecord } from "../types";
import { normalizeBossText } from "./boss-normalizers";

const RECORD =
  /Ta\s*向\s*([^\n[［]{1,40}?)\s*发起沟通\s*[[［]([^\]］\n]{1,120})[\]］]\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2})/giu;

function shanghaiIso(value: string) {
  const match = value.match(
    /(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s+(\d{1,2}):(\d{2})/,
  );
  if (!match) return "";
  const pad = (part: string) => part.padStart(2, "0");
  return `${match[1]}-${pad(match[2])}-${pad(match[3])}T${pad(match[4])}:${pad(match[5])}:00+08:00`;
}

export function parseBossNativeCommunicationHistory(
  text: string,
): NativeCommunicationRecord[] {
  const normalized = text
    .normalize("NFKC")
    .replace(/[\u200B-\u200D\uFEFF]/g, " ")
    .replace(/[ \t]+/g, " ")
    .replace(/\s*\n\s*/g, "\n");
  if (
    !/(?:合作客户专享|了解同事沟通进度)/.test(normalized) ||
    !/同事沟通/.test(normalized) ||
    !/我的沟通/.test(normalized)
  )
    return [];
  const result: NativeCommunicationRecord[] = [];
  const seen = new Set<string>();
  for (const match of normalized.matchAll(RECORD)) {
    const recruiterName = normalizeBossText(match[1]),
      jobName = normalizeBossText(match[2]),
      contactedAt = shanghaiIso(match[3]);
    if (!recruiterName || !jobName || !contactedAt) continue;
    const key = `${recruiterName}\u0000${jobName}\u0000${contactedAt}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push({ recruiterName, jobName, contactedAt, source: "BOSS_NATIVE" });
  }
  return result.sort(
    (a, b) => Date.parse(b.contactedAt) - Date.parse(a.contactedAt),
  );
}

function visible(element: HTMLElement) {
  const style = getComputedStyle(element);
  if (style.display === "none" || style.visibility === "hidden") return false;
  const rect = element.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function historySurface(root: ParentNode = document) {
  return Array.from(root.querySelectorAll<HTMLElement>("body *"))
    .filter((element) => {
      const text = element.innerText || "";
      return (
        text.length < 4000 &&
        /(?:合作客户专享|了解同事沟通进度)/.test(text) &&
        /同事沟通/.test(text) &&
        /我的沟通/.test(text) &&
        visible(element)
      );
    })
    .sort((a, b) => (a.innerText || "").length - (b.innerText || "").length)[0];
}

export function collectBossNativeCommunicationHistory(
  root: ParentNode = document,
): NativeCommunicationRecord[] {
  const surface = historySurface(root);
  return surface ? parseBossNativeCommunicationHistory(surface.innerText) : [];
}
