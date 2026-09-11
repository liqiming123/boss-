import { collectDailyReport, isReportUrl } from "../adapters/boss/company-daily";
import { sendRuntimeMessage } from "../shared/runtime-message";
import { delay } from "../shared/delay";

export async function runCompanyDailyPage() {
  if (window !== window.top || !isReportUrl(location.href)) return;
  let job: { account: string; dates: string[]; useCurrentDayDefault?: boolean } | undefined;
  for (let i = 0; i < 20; i++) {
    const response = await sendRuntimeMessage<any>({ type: "DAILY_JOB" }).catch(() => null);
    if (response?.ok && response.data) { job = response.data; break; }
    await delay(500);
  }
  if (!job) return; // user-opened report: do not change its filters
  const authorized = async () => {
    const response = await sendRuntimeMessage<any>({ type: "DAILY_JOB" }).catch(() => null);
    return !!response?.ok && !!response.data;
  };
  try {
    for (const [index, date] of job.dates.entries()) {
      const payload = await collectDailyReport(job.account, date, authorized, index === 0 && job.useCurrentDayDefault === true);
      const response = await sendRuntimeMessage<any>({ type: "DAILY_BATCH", payload });
      if (!response?.ok) throw new Error("DAILY_UPLOAD_FAILED");
    }
    await sendRuntimeMessage({ type: "DAILY_FINISH" });
  } catch (error) {
    const errorCode = error instanceof Error && /^DAILY_[A-Z_]+$/.test(error.message) ? error.message : "DAILY_READ_FAILED";
    await sendRuntimeMessage({ type: "DAILY_FAILED", payload: { errorCode } }).catch(() => undefined);
  }
}
