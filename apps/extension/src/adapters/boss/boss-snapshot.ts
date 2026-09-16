import { sha256Hex } from "../../shared/sha256";
import { observeUserActivity } from "../../shared/user-activity";
import { delay } from "../../shared/delay";
import { sendRuntimeMessage } from "../../shared/runtime-message";
import { findBossConversationRegion } from "./boss-chat";

type SnapshotResult = { parts: string[]; hash: string };
type CaptureTarget = { getBoundingClientRect: () => DOMRect };

/**
 * A canvas that will be read back must ask for the CPU-backed context when it
 * is created: Chrome otherwise keeps it GPU-backed, copies the whole surface on
 * every `getImageData` call and logs the "willReadFrequently" warning. The
 * captured conversation tile is read row by row for its signature, so it is
 * created that way; canvases that are only drawn into and exported stay
 * GPU-backed, where `drawImage`/`toDataURL` are faster.
 */
function context2d(canvas: HTMLCanvasElement, readFrequently = false) {
  return canvas.getContext(
    "2d",
    readFrequently ? { willReadFrequently: true } : undefined,
  );
}

/** Vertical luminance signature of a captured tile, used only to prove that a
 * new viewport really shows different content. */
export function columnSignature(canvas: HTMLCanvasElement): number[] | undefined {
  // The tile canvas comes from `captureCrop`, which already requested the
  // readback-friendly context; `getContext` returns that same context here.
  const context = context2d(canvas, true);
  if (!context || !canvas.height || !canvas.width) return undefined;
  const stride = Math.max(1, Math.floor(canvas.height / 64));
  const rows: number[] = [];
  try {
    for (let y = 0; y < canvas.height; y += stride) {
      const data = context.getImageData(0, y, canvas.width, 1).data;
      let total = 0;
      for (let index = 0; index < data.length; index += 4)
        total += data[index] * 0.299 + data[index + 1] * 0.587 + data[index + 2] * 0.114;
      rows.push(total / (data.length / 4));
    }
  } catch {
    // Tainted or detached canvas: fall back to no verification rather than
    // failing the snapshot.
    return undefined;
  }
  return rows.length >= 2 ? rows : undefined;
}

/** How confidently the previously captured viewport reappears exactly
 * `shift` pixels lower in the new tile. 1 means every sampled row matches. */
export function shiftConfidence(
  previous: number[] | undefined,
  current: number[] | undefined,
  shift: number,
  stride: number,
): number | undefined {
  if (!previous || !current) return undefined;
  const offset = Math.round(shift / stride);
  if (offset <= 0 || offset >= previous.length) return undefined;
  let compared = 0;
  let matched = 0;
  for (let index = 0; index + offset < current.length; index++) {
    compared++;
    if (Math.abs(previous[index] - current[index + offset]) <= 3) matched++;
  }
  if (!compared) return undefined;
  return matched / compared;
}

export class SnapshotCaptureError extends Error {
  constructor(public code: string) {
    super(code);
  }
}

function chatScroller(): HTMLElement | null {
  const region = findBossConversationRegion();
  const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
  const candidates = [
    ...(region
      ? [region, ...region.querySelectorAll<HTMLElement>("div,section,main")]
      : document.querySelectorAll<HTMLElement>("div,section,main")),
  ].filter((element) => {
    const rect = element.getBoundingClientRect(),
      text = element.innerText || "";
    // BOSS frequently uses overflow:hidden plus wheel handlers on a nested
    // viewport. Requiring a particular CSS overflow value made the same
    // conversation fall back to a single latest-message crop on some
    // accounts/layouts. Scroll geometry is the reliable signal here.
    const scrollable = element.scrollHeight > element.clientHeight + 24;
    return (
      scrollable &&
      rect.width > Math.min(420, viewportWidth * 0.38) &&
      rect.height > 220 &&
      rect.left > viewportWidth * 0.25 &&
      !/全部职位/.test(text) &&
      (text.match(/\d{1,2}\s*岁/g) || []).length < 2
    );
  });
  // The outer right-hand shell is usually the largest scrollable node. It
  // contains the candidate header, job panel and sometimes the left list, so
  // choosing it produces a visually valid but semantically wrong screenshot.
  // Prefer the smallest qualifying scroll container: in BOSS layouts this is
  // the actual virtualized message viewport. The geometry/evidence filters
  // above keep tiny nested bubbles out of the candidate set.
  const ranked = candidates.sort(
    (a, b) =>
      a.getBoundingClientRect().width * a.getBoundingClientRect().height -
      b.getBoundingClientRect().width * b.getBoundingClientRect().height,
  );
  if (ranked[0]) return ranked[0];
  // Virtualized BOSS layouts may not expose overflow CSS. Fall back to the
  // smallest visible right-hand panel containing conversation evidence.
  const visiblePanels = region
    ? [region, ...region.querySelectorAll<HTMLElement>("div,section,main")]
    : [...document.querySelectorAll<HTMLElement>("body *")]
    .filter((element) => element.children.length > 0)
    .filter((element) => {
      const rect = element.getBoundingClientRect();
      const text = element.innerText || "";
      return (
        rect.left > viewportWidth * 0.25 &&
        rect.width > Math.min(360, viewportWidth * 0.35) &&
        rect.height > 160 &&
        rect.top >= 0 &&
        /送达|沟通职位|沟通记录/.test(text)
      );
    })
    .sort((a, b) => {
      const area = (item: HTMLElement) => {
        const rect = item.getBoundingClientRect();
        return rect.width * rect.height;
      };
      return area(a) - area(b);
    });
  return visiblePanels[0] || null;
}

async function imageFromDataUrl(dataUrl: string) {
  const image = new Image();
  image.src = dataUrl;
  await new Promise<void>((resolve, reject) => {
    image.onload = () => resolve();
    image.onerror = () => reject(new Error("SCREENSHOT_IMAGE_DECODE_FAILED"));
  });
  return image;
}
async function captureCrop(element: CaptureTarget, fallback = false): Promise<HTMLCanvasElement> {
  const response = await sendRuntimeMessage<{ ok: boolean; data?: { dataUrl: string }; error?: string }>({
    type: "CAPTURE_VISIBLE_TAB",
    payload: { fallback },
  });
  if (!response.ok || !response.data?.dataUrl)
    throw new SnapshotCaptureError(response.error || "SCREENSHOT_CAPTURE_FAILED");
  const image = await imageFromDataUrl(response.data.dataUrl),
    rect = element.getBoundingClientRect(),
    scale = image.width / window.innerWidth;
  // A hidden Chrome tab can report a 1px compositor viewport. Never upload
  // that blank strip; retry once using the compositor-surface capture path.
  if (!fallback && (image.width < 300 || image.height < 200))
    return captureCrop(element, true);
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.floor(rect.width * scale));
  canvas.height = Math.max(1, Math.floor(rect.height * scale));
  // This tile is read back row by row for its signature, so declare the
  // readback-friendly (CPU-backed) context before the first draw.
  context2d(canvas, true)
    ?.drawImage(
      image,
      Math.max(0, rect.left * scale),
      Math.max(0, rect.top * scale),
      canvas.width,
      canvas.height,
      0,
      0,
      canvas.width,
      canvas.height,
    );
  return canvas;
}

export async function captureBossPreviewScreenshot(
  element: HTMLElement,
): Promise<string> {
  return toJpeg(await captureCrop(element));
}

function toJpeg(canvas: HTMLCanvasElement) {
  return canvas.toDataURL("image/jpeg", 0.82);
}
async function digest(parts: string[]) {
  const arrays = await Promise.all(
    parts.map(
      async (value) => new Uint8Array(await (await fetch(value)).arrayBuffer()),
    ),
  );
  const size = arrays.reduce((sum, value) => sum + value.length, 0),
    joined = new Uint8Array(size);
  let offset = 0;
  for (const value of arrays) {
    joined.set(value, offset);
    offset += value.length;
  }
  return sha256Hex(joined);
}

export async function captureBossConversationSnapshot(): Promise<SnapshotResult> {
  // The floating panel is fixed over the conversation area and the catch-up
  // banner is visible exactly while snapshots are taken, so captureVisibleTab
  // would bake it into every stitched tile of the long image. Hide the panel
  // host for the duration of the capture and restore it afterwards.
  const panelHost = document.getElementById("recruitment-collab-host");
  if (panelHost) panelHost.style.display = "none";
  try {
    return await captureConversationTiles();
  } finally {
    if (panelHost) panelHost.style.display = "";
  }
}

async function captureConversationTiles(): Promise<SnapshotResult> {
  const scroller = chatScroller();
  if (!scroller) {
    // BOSS virtualizes the chat list in some versions and exposes no usable
    // scroll container. Capture only the right-hand conversation viewport as
    // a bounded fallback rather than dropping the snapshot entirely.
    const width = document.documentElement.clientWidth;
    const height = document.documentElement.clientHeight;
    if (width < 500 || height < 300)
      throw new SnapshotCaptureError("SNAPSHOT_CHAT_REGION_NOT_FOUND");
    const target: CaptureTarget = {
      getBoundingClientRect: () =>
        new DOMRect(width * 0.38, height * 0.22, width * 0.6, height * 0.58),
    };
    const crop = await captureCrop(target);
    const part = toJpeg(crop);
    return { parts: [part], hash: await digest([part]) };
  }
  let interrupted = false;
  // Candidate selection itself is a pointer action and commonly occurs just
  // before an automatic historical snapshot starts. Do not treat that click
  // as an interruption; still stop for a deliberate scroll or keypress while
  // the user is reading the conversation.
  const stopObserving = observeUserActivity(
    () => {
      interrupted = true;
    },
    { pointer: false },
  );
  const original = scroller.scrollTop;
  try {
    let stable = 0,
      lastHeight = 0;
    while (stable < 3 && !interrupted) {
      scroller.scrollTop = 0;
      await delay(450);
      stable = scroller.scrollHeight === lastHeight ? stable + 1 : 0;
      lastHeight = scroller.scrollHeight;
    }
    if (interrupted)
      throw new SnapshotCaptureError("SNAPSHOT_INTERRUPTED_BY_USER");
    const max = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
    // No overlap: neighbouring tiles must be adjacent slices, otherwise the
    // same messages are stitched twice.
    const step = Math.max(1, scroller.clientHeight);
    const crops: HTMLCanvasElement[] = [];
    let previousSignature: number[] | undefined;
    let previousAppliedTop = 0;
    for (
      let position = 0;
      position <= max && !interrupted;
      position = Math.min(max, position + step)
    ) {
      let appended = false;
      for (let attempt = 0; attempt < 3 && !interrupted && !appended; attempt++) {
        // A stalled scroll (wrong container, or the shell swallowing the
        // wheel event) leaves the viewport where it was. Retry a little
        // further down, and never append a tile that repeats the previous one.
        const target = Math.min(max, position + attempt * Math.floor(step / 2));
        scroller.scrollTop = target;
        // captureVisibleTab is quota-limited by Chrome (normally two calls per
        // second). Keep a safe gap between historical chat tiles so long
        // conversations do not fail midway through with a quota error.
        await delay(650);
        const crop = await captureCrop(scroller);
        const signature = columnSignature(crop);
        const appliedTop = scroller.scrollTop;
        if (crops.length) {
          const rows = signature?.length ?? 0;
          const stride = rows > 1
            ? Math.max(1, Math.floor(crop.height / rows))
            : Math.max(1, crop.height);
          const confidence = shiftConfidence(
            previousSignature,
            signature,
            appliedTop - previousAppliedTop,
            stride,
          );
          // Undefined means we cannot compare pixels; trust the scroll instead
          // of losing the capture.
          if (confidence !== undefined && confidence < 0.6) continue;
        }
        crops.push(crop);
        previousSignature = signature;
        previousAppliedTop = appliedTop;
        appended = true;
        if (appliedTop >= max - 1) position = max;
      }
      if (!appended) break;
      if (position >= max) break;
    }
    if (interrupted)
      throw new SnapshotCaptureError("SNAPSHOT_INTERRUPTED_BY_USER");
    if (!crops.length) throw new SnapshotCaptureError("SNAPSHOT_EMPTY");
    const width = crops[0].width,
      maxTileHeight = 24000,
      parts: string[] = [];
    let index = 0;
    while (index < crops.length) {
      let height = 0,
        end = index;
      while (
        end < crops.length &&
        height + crops[end].height <= maxTileHeight
      ) {
        height += crops[end].height;
        end++;
      }
      if (end === index) end++;
      const tile = document.createElement("canvas");
      tile.width = width;
      tile.height = crops
        .slice(index, end)
        .reduce((sum, item) => sum + item.height, 0);
      let y = 0;
      for (const crop of crops.slice(index, end)) {
        tile.getContext("2d")?.drawImage(crop, 0, y);
        y += crop.height;
      }
      parts.push(toJpeg(tile));
      index = end;
    }
    return { parts, hash: await digest(parts) };
  } finally {
    scroller.scrollTop = original;
    stopObserving();
  }
}
