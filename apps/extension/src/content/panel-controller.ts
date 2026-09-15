import type { ContextResponse } from "@recruitment/api-client";
export class PanelController {
  private catchupVisible = false;
  private disposed = false;
  private host: HTMLElement;
  private root: ShadowRoot;
  constructor() {
    document.querySelector("#recruitment-collab-host")?.remove();
    this.host = document.createElement("div");
    this.host.id = "recruitment-collab-host";
    this.root = this.host.attachShadow({ mode: "open" });
    document.documentElement.append(this.host);
  }
  /**
   * Render a duplicate card.
   *
   * `note` explains why this card appeared when it did. A card produced by the
   * list watcher names a conversation the recruiter is not reading, so without
   * the note it would look like a warning about the candidate on screen.
   */
  show(data: ContextResponse, note = "") {
    const exact = data.matches.filter((m) => m.match_level === "EXACT_IDENTITY");
    const sameIdentityAcrossJobs = exact.length > 1 && new Set(exact.map((m) => m.job_name || "岗位未知")).size > 1;
    const groupingNote = sameIdentityAcrossJobs
      ? `<p class="identity-note">疑似同一候选人，已按不同岗位分别保留沟通记录</p>`
      : "";
    this.render(
      `<button class="close">×</button><strong>${this.escape(data.ui.title)}</strong><p>${this.escape(data.ui.message)}</p>${note ? `<small class="watch-note">${this.escape(note)}</small>` : ""}${groupingNote}${data.matches.map((m) => `<small>${this.escape(m.recruiter_name)}｜${this.escape(m.job_name || "岗位未知")}｜${this.escape(m.stage === "FOLLOWING" ? "沟通中" : m.stage)}${m.first_contact_at ? `｜首次沟通 ${this.escape(this.time(m.first_contact_at))}` : ""}${m.updated_at ? `｜最近活动 ${this.escape(this.time(m.updated_at))}` : ""}${m.evidence_source === "BOSS_NATIVE" ? "｜BOSS已确认" : ""}</small>`).join("")}`,
    );
    this.root
      .querySelector(".close")
      ?.addEventListener("click", () => (this.host.style.display = "none"));
  }
  showLookupUnavailable(detail = "无法读取飞书记录，请稍后重试") {
    this.render(
      `<strong>查重暂不可用</strong><p>${this.escape(detail)}；本次不会按“无历史”放行。</p>`,
    );
  }
  showDevelopmentStatus(message: string) {
    this.render(
      `<strong>招聘协同助手</strong><p>${message}</p><small>开发模式状态提示</small>`,
    );
  }
  /** The banner itself is rendered from `catchupVisible`; this only adds progress. */
  showCatchupStatus(progress?: string) {
    this.catchupVisible = true;
    this.render(progress ? `<small>${this.escape(progress)}</small>` : "");
  }
  showSnapshotStatus(message: string, error = false) {
    this.render(`<strong>${error ? "截图未完成" : "聊天截图"}</strong><p>${this.escape(message)}</p>`);
  }
  hideCatchupStatus() {
    this.catchupVisible = false;
    if (this.root.querySelector(".catchup-status")) this.hide();
  }
  hide(force = false) {
    if (!force && this.catchupVisible && !this.disposed) { this.render(""); return; }
    this.host.style.display = "none";
    this.root.innerHTML = "";
  }
  dispose() {
    this.disposed = true;
    this.catchupVisible = false;
    this.hide();
  }
  private render(content: string) {
    if (this.disposed) return;
    this.host.style.display = "block";
    const marker = this.catchupVisible ? " class=\"catchup-status\"" : "";
    const catchup = this.catchupVisible
      ? `<div class="catchup-banner"><strong>后台补扫进行中</strong><p>正在检查最近沟通记录，用户操作时会自动暂停。</p><small>本轮扫描结束后自动隐藏</small></div>`
      : "";
    this.root.innerHTML = `<style>:host{all:initial}section{position:fixed;z-index:2147483647;right:18px;bottom:18px;width:320px;padding:16px;border-radius:10px;background:#fff;color:#1f2937;box-shadow:0 8px 34px #0003;font:14px/1.5 system-ui}strong{font-size:16px}p{margin:8px 0}small{display:block;color:#64748b;margin:5px 0}button{border:0;border-radius:6px;padding:7px 10px;margin:8px 6px 0 0;background:#2156a5;color:#fff;cursor:pointer}.close{float:right;background:transparent;color:#64748b;font-size:18px;margin:0}.catchup-banner{padding-bottom:10px;margin-bottom:10px;border-bottom:1px solid #e5e7eb}.catchup-banner strong{color:#1d4ed8}.watch-note{color:#1d4ed8}</style><section${marker}>${catchup}${content}</section>`;
  }
  private escape(value: string) {
    const span = document.createElement("span");
    span.textContent = value;
    return span.innerHTML;
  }
  private time(value: string | number) {
    const date = new Date(typeof value === "number" ? value : value);
    return Number.isNaN(date.valueOf())
      ? ""
      : new Intl.DateTimeFormat("zh-CN", {
          month: "numeric",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        }).format(date);
  }
}
