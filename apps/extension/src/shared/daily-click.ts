import { sendRuntimeMessage } from "./runtime-message";

export function isDailyControlVisible(element: Element): boolean {
  if (!element.isConnected) return false;
  const view = element.ownerDocument.defaultView;
  for (let node: Element | null = element; node; node = node.parentElement) {
    const style = view?.getComputedStyle(node);
    if (node.hasAttribute("hidden") || style?.display === "none" || style?.visibility === "hidden" || style?.opacity === "0") return false;
  }
  const rect = element.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

// CDP expects CSS pixels relative to the top-level viewport, not screen pixels.
export async function clickDailyControl(element: HTMLElement): Promise<void> {
  if (!isDailyControlVisible(element)) throw new Error("DAILY_CONTROL_NOT_VISIBLE");
  element.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
  const rect = element.getBoundingClientRect();
  let x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
  let doc = element.ownerDocument;
  const hit = doc.elementFromPoint(x, y);
  if (!hit || !(element === hit || element.contains(hit))) throw new Error("DAILY_CONTROL_OBSCURED");
  while (doc.defaultView && doc.defaultView !== doc.defaultView.top) {
    const frame = doc.defaultView.frameElement as HTMLElement | null;
    if (!frame) throw new Error("DAILY_FRAME_UNSUPPORTED");
    const frameRect = frame.getBoundingClientRect();
    // Do not guess coordinates for CSS-transformed frames.
    if (Math.abs(frameRect.width - frame.offsetWidth) > 1 || Math.abs(frameRect.height - frame.offsetHeight) > 1) throw new Error("DAILY_FRAME_UNSUPPORTED");
    x += frameRect.left + frame.clientLeft;
    y += frameRect.top + frame.clientTop;
    doc = frame.ownerDocument;
    if (doc.elementFromPoint(x, y) !== frame) throw new Error("DAILY_CONTROL_OBSCURED");
  }
  // A lost response must not replay a click and close an already-open menu.
  const response = await sendRuntimeMessage<{ ok: boolean; error?: string }>({ type: "DAILY_REAL_CLICK", payload: { x, y } }, 1);
  if (!response?.ok) throw new Error(response?.error || "DAILY_CLICK_FAILED");
}
