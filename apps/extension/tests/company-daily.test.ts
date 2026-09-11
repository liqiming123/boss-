import { describe, expect, it } from "vitest";
import { LABELS, parseDailyTable, reportAccountMatches, calendarDayCell, visibleCalendarDayCell } from "../src/adapters/boss/company-daily";

describe("BOSS company daily report", () => {
  it("selects this month's day, never the next month's repeated number", () => {
    document.body.innerHTML = `<table><tbody><tr>${[31,...Array.from({length:30},(_,i)=>i+1),...Array.from({length:11},(_,i)=>i+1)].map(d=>`<td><span>${d}</span></td>`).join("")}</tr></tbody></table>`;
    const expectedCell = document.querySelectorAll("td")[8];
    expect(calendarDayCell(document.querySelector("table")!,8,30)).toBe(expectedCell);
    expect(calendarDayCell(document.querySelector("table")!,8,30)).not.toBe(expectedCell.querySelector("span"));
  });
  it("accepts calendar weekday headers with split/icon text", () => {
    document.body.innerHTML = `<table><thead><tr>${["一","二","三","四","五","六","日"].map(d=>`<th><span>${d}</span><i></i></th>`).join("")}</tr></thead><tbody><tr>${Array.from({length:35},(_,i)=>`<td>${(i%31)+1}</td>`).join("")}</tr></tbody></table>`;
    const t = [...document.querySelectorAll("table")].find(t => [...t.querySelectorAll("th")].length >= 7 && [...t.querySelectorAll("td")].length >= 28 && [...t.querySelectorAll("th")].some(h=>h.textContent?.includes("一")) && [...t.querySelectorAll("th")].some(h=>h.textContent?.includes("日")));
    expect(t).toBeTruthy();
  });
  it("finds the calendar when its header controls and date grid are siblings", () => {
    document.body.innerHTML = `
      <div class="picker">
        <div class="header"><button>2026 年</button><button>9 月</button></div>
        <table class="metrics"><tbody><tr><td>招聘者</td><td>1</td><td>8</td><td>2</td></tr></tbody></table>
        <table class="calendar"><tbody><tr>${[31,...Array.from({length:30},(_,i)=>i+1),...Array.from({length:11},(_,i)=>i+1)].map(d=>`<td><span>${d}</span></td>`).join("")}</tr></tbody></table>
      </div>`;
    const cell = visibleCalendarDayCell([document], 8, 30);
    expect(cell).toBe(document.querySelectorAll(".calendar td")[8]);
  });
  function fixture(values = [42, 18, 43, 172, 26]) {
    document.body.innerHTML = `<header>直聘企业版\n成珈莉\n使用说明</header><table><thead><tr>${["BOSS姓名", "手机号码", "所属公司", "所在分组", "认证职务", "企业邮箱", ...LABELS, "收获简历"].map(h=>`<th>${h} \ue664</th>`).join("")}</tr></thead><tbody>${["钱筱羽", "未绑定人员"].map(name=>`<tr><td>${name}</td><td>***</td><td>公司</td><td></td><td>职务</td><td></td>${values.map(n=>`<td>${n}</td>`).join("")}<td>2</td></tr>`).join("")}</tbody></table>`;
  }
  it("reads all company rows by header, excludes phone/email and optional columns", () => {
    fixture();
    const rows = parseDailyTable(document);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toEqual({boss_name:"钱筱羽",boss_viewed_talent:42,boss_started_chat:18,boss_communication:43,talent_viewed_boss:172,talent_started_chat:26});
    expect(reportAccountMatches(document, "成珈莉")).toBe(true);
    expect(reportAccountMatches(document, "钱筱羽")).toBe(false);
  });
  it("accepts BOSS rows that omit empty profile and metric cells", () => {
    document.body.innerHTML = `<table><thead><tr>${["BOSS姓名", "手机号码", "所属公司", "所在分组", "认证职务", "企业邮箱", "BOSS查看牛人", "牛人查看BOSS", "BOSS发起聊天", "牛人回应", "牛人发起聊天", "BOSS回应", "BOSS沟通", "收获简历", "交换电话微信", "接受面试"].map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody><tr>${["钱筱羽", "153****3978", "公司", "人事专员", "42", "18", "43", "182", "27", "6", "1", "1"].map(v => `<td>${v}</td>`).join("")}</tr></tbody></table>`;
    expect(parseDailyTable(document)[0]).toEqual({boss_name:"钱筱羽",boss_viewed_talent:42,boss_started_chat:18,boss_communication:43,talent_viewed_boss:182,talent_started_chat:27});
  });
  it("keeps real zeros and rejects missing data instead of manufacturing zeros", () => {
    fixture([0,0,0,0,0]); expect(parseDailyTable(document)[0].boss_viewed_talent).toBe(0);
    document.querySelectorAll("tbody td")[6].textContent = "—";
    expect(()=>parseDailyTable(document)).toThrow("DAILY_NUMBER_MISSING");
  });
  it("rejects incomplete rows and duplicate names", () => {
    fixture(); document.querySelector("tbody tr td")!.remove();
    expect(()=>parseDailyTable(document)).toThrow("DAILY_ROW_INCOMPLETE");
    fixture(); document.querySelectorAll("tbody tr")[1].querySelector("td")!.textContent="钱筱羽";
    expect(()=>parseDailyTable(document)).toThrow("DAILY_DUPLICATE_NAMES");
  });
});
