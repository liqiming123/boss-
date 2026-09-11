import { describe, expect, it } from "vitest";
import { readBossInterviewDialog } from "../src/adapters/boss/boss-interview-dialog";

function dialog(html: string) {
  document.body.innerHTML = `<div id="scheduler">${html}<button id="confirm" aria-label="发送">发送</button></div>`;
  return document.querySelector<HTMLElement>("#confirm")!;
}

describe("BOSS interview scheduler reader", () => {
  it("reads the format, the schedule and the address of an offline interview", () => {
    const confirm = dialog(`
      <p>线下面试邀请</p>
      <label><input type="radio" name="type" checked /> 线下面试</label>
      <label><input type="radio" name="type" /> 线上面试</label>
      <div><span>面试地址</span><input value="无锡梁溪区世金中心39层" /></div>
      <div><span>面试时间</span><input value="2026-09-12" /><input value="14:00" /></div>
      <div><span>联系人</span><input value="成珈莉 18961734157" /></div>
    `);
    expect(readBossInterviewDialog(confirm)).toEqual({
      interview_type: "OFFLINE",
      scheduled_at: "2026-09-12T14:00:00+08:00",
      location: "无锡梁溪区世金中心39层",
    });
  });
  it("never collects the contact person's phone number", () => {
    const confirm = dialog(`
      <div><span>面试时间</span><input value="2026-09-12" /><input value="14:00" /></div>
      <div><span>联系人</span><input value="成珈莉 18961734157" /></div>
    `);
    expect(JSON.stringify(readBossInterviewDialog(confirm))).not.toContain(
      "18961734157",
    );
  });
  it("reports only the format when the schedule is still unselected", () => {
    const confirm = dialog(`
      <label><input type="radio" name="type" checked /> 线上面试</label>
      <div><span>面试时间</span><input placeholder="选择日期" value="" /><input placeholder="选择开始时间" value="" /></div>
    `);
    const details = readBossInterviewDialog(confirm);
    expect(details.interview_type).toBe("ONLINE");
    // Without a date and time no calendar entry may be invented.
    expect(details.scheduled_at).toBeUndefined();
  });
  it("ignores a placeholder address and an unfilled scheduler", () => {
    const confirm = dialog(`
      <div><span>面试地址</span><input placeholder="选择地址" value="" /></div>
      <div><span>面试时间</span><input value="" /><input value="" /></div>
    `);
    expect(readBossInterviewDialog(confirm)).toEqual({});
  });
  it("returns nothing when the control is not inside a scheduler", () => {
    document.body.innerHTML = '<button id="confirm" aria-label="发送">发送</button>';
    expect(
      readBossInterviewDialog(
        document.querySelector<HTMLElement>("#confirm")!,
      ),
    ).toEqual({});
  });
  it("never throws on an unreadable scheduler DOM", () => {
    // The reader is called from the send gesture that also arms the invitation.
    // It must be total: any layout it cannot handle degrades to "no details"
    // instead of throwing, which would drop the 已约面 status sync entirely.
    const hostile = new Proxy({} as HTMLElement, {
      get() {
        throw new Error("hostile DOM");
      },
    });
    expect(readBossInterviewDialog(hostile)).toEqual({});
  });
});
