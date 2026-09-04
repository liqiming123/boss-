import { apiFormRequest } from "./api-client";
import { sha256Hex } from "../shared/sha256";
import { isBossHostname } from "../adapters/boss/boss-hosts";

export type ResumeUploadPayload = {
  candidateSourceIds: string[];
  url: string;
  fileName?: string;
};

export async function uploadResumeFromUrl(payload: ResumeUploadPayload) {
  const url = new URL(payload.url);
  if (url.protocol !== "https:" || !isBossHostname(url.hostname))
    throw new Error("RESUME_URL_NOT_ALLOWED");
  const response = await fetch(url.href, { credentials: "include" });
  if (!response.ok) throw new Error("RESUME_DOWNLOAD_FAILED");
  const blob = await response.blob();
  if (!blob.size || blob.size > 25 * 1024 * 1024)
    throw new Error("RESUME_SIZE_INVALID");
  const allowed = new Set([
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/jpeg",
    "image/png",
  ]);
  if (!allowed.has(blob.type)) throw new Error("RESUME_TYPE_INVALID");
  const form = new FormData();
  form.append("resume_hash", await sha256Hex(await blob.arrayBuffer()));
  form.append(
    "related_source_ids",
    payload.candidateSourceIds.slice(1).join(","),
  );
  form.append("file", blob, payload.fileName || "candidate-resume.pdf");
  return apiFormRequest(
    `/plugin/conversations/${encodeURIComponent(payload.candidateSourceIds[0])}/resume`,
    form,
  );
}

export async function uploadResumeScreenshot(payload: {
  candidateSourceIds: string[];
  dataUrl: string;
  fileName?: string;
}) {
  const blob = await (await fetch(payload.dataUrl)).blob();
  if (!blob.size || blob.size > 25 * 1024 * 1024)
    throw new Error("RESUME_SCREENSHOT_SIZE_INVALID");
  const form = new FormData();
  form.append("resume_hash", await sha256Hex(await blob.arrayBuffer()));
  form.append(
    "related_source_ids",
    payload.candidateSourceIds.slice(1).join(","),
  );
  form.append("file", blob, payload.fileName || "candidate-resume-preview.jpg");
  return apiFormRequest(
    `/plugin/conversations/${encodeURIComponent(payload.candidateSourceIds[0])}/resume`,
    form,
  );
}
