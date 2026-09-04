import type { ContextResponse } from "@recruitment/api-client";
export class PanelController {
  private host: HTMLElement;
  private root: ShadowRoot;
  constructor() {
    document.querySelector("#recruitment-collab-host")?.remove();
    this.host = document.createElement("div");
    this.host.id = "recruitment-collab-host";
    this.root = this.host.attachShadow({ mode: "open" });
    document.documentElement.append(this.host);
  }
  show(data: ContextResponse) {
    this.render(
      `<button class="close">×</button><strong>${this.escape(data.ui.title)}</strong><p>${this.escape(data.ui.message)}</p>${data.matches.map((m) => `<small>${this.escape(m.recruiter_name)}｜${this.escape(m.job_name || "岗位未知")}｜${this.escape(m.stage === "FOLLOWING" ? "沟通中" : m.stage)}${m.updated_at ? `｜${this.escape(this.time(m.updated_at))}` : ""}${m.evidence_source === "BOSS_NATIVE" ? "｜BOSS已确认" : ""}</small>`).join("")}`,
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
  hide() {
    this.host.style.display = "none";
    this.root.innerHTML = "";
  }
  private render(content: string) {
    this.host.style.display = "block";
    this.root.innerHTML = `<style>:host{all:initial}section{position:fixed;z-index:2147483647;right:18px;bottom:18px;width:320px;padding:16px;border-radius:10px;background:#fff;color:#1f2937;box-shadow:0 8px 34px #0003;font:14px/1.5 system-ui}strong{font-size:16px}p{margin:8px 0}small{display:block;color:#64748b;margin:5px 0}button{border:0;border-radius:6px;padding:7px 10px;margin:8px 6px 0 0;background:#2156a5;color:#fff;cursor:pointer}.close{float:right;background:transparent;color:#64748b;font-size:18px;margin:0}</style><section>${content}</section>`;
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
