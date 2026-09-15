import { apiRequest, logoutExtension } from "../background/api-client";
import { getAuth, setAuth, type PendingFeishuLogin } from "../background/auth-store";

type Start = { authorization_url: string; attempt_id: string; poll_token: string; expires_in: number };
type Poll = { status: "PENDING" | "APPROVED"; access_token?: string; refresh_token?: string; device_id?: string };
const root = document.querySelector("#app")!, version = chrome.runtime.getManifest().version;
root.innerHTML = `<style>
  :root{color-scheme:light}*{box-sizing:border-box}body{margin:0;width:340px;padding:18px;font:14px/1.45 system-ui;color:#172033;background:#fff}h2{margin:0 0 14px;font-size:20px}.version{color:#64748b;font-size:12px;font-weight:500}.account-card{padding:12px;border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc}.account-card p{margin:0}.account-card #status{margin-top:4px;font-size:13px}.muted{color:#64748b}.ok{color:#087f5b}.error{color:#b42318}button{width:100%;min-height:42px;padding:9px 12px;margin-top:12px;border:1px solid transparent;border-radius:8px;font:inherit;font-weight:600;cursor:pointer}.primary{background:#2156a5;color:#fff}.secondary{background:#fff;color:#334155;border-color:#cbd5e1}.danger{background:#fff;color:#b42318;border-color:#fecaca}button:disabled{opacity:.45;cursor:default}[hidden]{display:none!important}details{margin-top:12px;border-top:1px solid #e2e8f0;padding-top:10px}summary{color:#475569;cursor:pointer;user-select:none}.setting-note{margin:5px 2px -5px;color:#64748b;font-size:12px}
</style>
<h2>招聘协同助手 <span class="version">v${version}</span></h2>
<div class="account-card"><p id="account" class="muted">读取绑定状态中…</p><p id="status">检查中…</p></div>
<button id="bind" class="primary">绑定飞书账号</button>
<div id="dailyCard" class="account-card" style="margin-top:16px" hidden><p>公司招聘日报</p><p id="dailyStatus" class="muted">准备同步招聘数据</p><button id="dailyCheck" class="secondary">同步招聘数据</button></div>
<button id="adminButton" class="primary" hidden>打开招聘管理后台</button>
<details id="more" hidden><summary>更多设置</summary>
  <button id="rebind" class="secondary">更换飞书账号</button>
  <button id="restart" class="secondary">立即检查沟通中列表</button>
  <button id="dailyTimerTest" class="secondary" hidden>1 分钟后测试日报定时任务</button>
  <p class="setting-note">停止接收飞书提醒，但保留本机登录。</p>
  <button id="unbind" class="secondary">解除飞书提醒绑定</button>
  <p class="setting-note">仅退出这台浏览器；不会影响其他招聘账号。</p>
  <button id="logout" class="danger">退出此浏览器登录</button>
</details>`;
const account = root.querySelector<HTMLElement>("#account")!, status = root.querySelector<HTMLElement>("#status")!, bind = root.querySelector<HTMLButtonElement>("#bind")!, rebind = root.querySelector<HTMLButtonElement>("#rebind")!, unbind = root.querySelector<HTMLButtonElement>("#unbind")!, restart = root.querySelector<HTMLButtonElement>("#restart")!, logout = root.querySelector<HTMLButtonElement>("#logout")!, adminButton = root.querySelector<HTMLButtonElement>("#adminButton")!, more = root.querySelector<HTMLDetailsElement>("#more")!, dailyCard = root.querySelector<HTMLElement>("#dailyCard")!;
async function openAdmin() { const a = await getAuth(); await chrome.tabs.create({url: `${a.apiBaseUrl.replace(/\/api\/v1\/?$/, "")}/recruitment/overview`}); }
adminButton.onclick = () => void openAdmin();
async function poll(p: PendingFeishuLogin) {
  if (Date.now() > p.expiresAt) {
    await chrome.storage.local.remove("pendingFeishuLogin");
    throw new Error("飞书授权已过期，请重新绑定");
  }
  const result=await apiRequest<Poll>("/auth/feishu/device/poll",{method:"POST",body:JSON.stringify({attempt_id:p.attemptId,poll_token:p.pollToken})});
  if(result.status==="PENDING"){status.textContent="等待飞书授权完成…";window.setTimeout(()=>void load(),2000);return false;}
  await setAuth({accessToken:result.access_token,refreshToken:result.refresh_token,deviceId:result.device_id});
  await chrome.storage.local.remove("pendingFeishuLogin");
  return true;
}
function showBoundUi(bound: boolean) {
  bind.hidden = bound;
  adminButton.hidden = !bound;
  more.hidden = !bound;
  if (!bound) more.open = false;
}
async function load() { try { const a = await getAuth(); if(a.pendingFeishuLogin && !(await poll(a.pendingFeishuLogin))) return; const current=await getAuth(); if (!current.accessToken) { account.textContent="尚未连接招聘账号"; status.className="muted"; status.textContent="绑定飞书后即可同步和接收提醒"; showBoundUi(false); return; } const me=await apiRequest<{display_name:string;role:string}>("/plugin/me"); account.textContent=`当前招聘人：${me.display_name}`; status.className="ok"; status.textContent="● 已连接，自动同步已开启"; showBoundUi(true); const isAdmin=me.role.toUpperCase()==="ADMIN"; dailyCard.hidden=!isAdmin; root.querySelector<HTMLButtonElement>("#dailyTimerTest")!.hidden=!isAdmin; if (isAdmin) await showDailyStatus(); } catch (error) { const current=await getAuth(); if(!current.accessToken){account.textContent="尚未连接招聘账号";status.className="muted";status.textContent="绑定飞书后即可同步和接收提醒";showBoundUi(false);return;} account.textContent="绑定已完成，但暂时无法读取服务器状态"; status.className="error"; status.textContent=error instanceof Error?error.message:"连接状态读取失败"; showBoundUi(true); dailyCard.hidden=true; } }
async function currentBossTab() {
  // The extension popup can be exposed as its own focused window. In that
  // case currentWindow points at the popup rather than the BOSS window.
  // Search all tabs and prefer the active BOSS communication tab instead of
  // treating the popup itself as the current page.
  const tabs = await chrome.tabs.query({});
  const bossTabs = tabs.filter((candidate) =>
    !!candidate.id && !!candidate.url &&
    /^https:\/\/(?:www\.)?(?:zhipin\.com|bosszhipin\.com)\/web\/chat\//.test(candidate.url),
  );
  // The popup itself becomes the active window, so `active` can refer to the
  // popup rather than the BOSS tab. Prefer the active BOSS tab when present;
  // otherwise use the most recently used BOSS conversation tab instead of the
  // first tab returned by Chrome (which is often an old account session).
  return bossTabs.find((candidate) => candidate.active) ??
    [...bossTabs].sort((a, b) => (b.lastAccessed ?? 0) - (a.lastAccessed ?? 0))[0];
}
async function currentBossAccount() {
  const tab = await currentBossTab();
  if (!tab?.id || !tab.url)
    return "";
  try {
    const response = await chrome.tabs.sendMessage(tab.id, { type: "GET_PAGE_ACCOUNT" });
    return response?.ok && typeof response.displayName === "string" ? response.displayName.trim() : "";
  } catch {
    return "";
  }
}
async function start(action:"bind"|"unbind") {
  const a=await getAuth(), deviceId=a.deviceId||crypto.randomUUID();
  const accountDisplayName = await currentBossAccount();
  if (!accountDisplayName) throw new Error("请先在当前 BOSS 沟通页打开招聘人账号，再绑定飞书");
  const r=await apiRequest<Start>("/plugin/feishu-binding/start",{method:"POST",body:JSON.stringify({account_display_name:accountDisplayName,action,device_id:deviceId,device_name:`Chrome ${navigator.platform}`})});
  if(action==="bind") await setAuth({deviceId,pendingFeishuLogin:{attemptId:r.attempt_id,pollToken:r.poll_token,expiresAt:Date.now()+r.expires_in*1000}}); else await chrome.storage.local.remove("pendingFeishuLogin");
  await chrome.tabs.create({url:r.authorization_url});
}
async function showDailyStatus(){
  const {companyDailyStatus:s}=await chrome.storage.local.get("companyDailyStatus");
  root.querySelector("#dailyStatus")!.textContent=s ? [s.status,s.date,s.rows!=null?`${s.rows} 人`:"",s.errorCode].filter(Boolean).join(" · ") : "等待登录并打开成珈莉的 BOSS 沟通页";
}
root.querySelector<HTMLButtonElement>("#dailyCheck")!.onclick=async()=>{
  await chrome.storage.local.remove("companyDailyAttempt");
  await chrome.runtime.sendMessage({type:"DAILY_CHECK",payload:{refresh:true}});
  await showDailyStatus();
};
root.querySelector<HTMLButtonElement>("#dailyTimerTest")!.onclick=async()=>{
  await chrome.runtime.sendMessage({type:"DAILY_TIMER_TEST"});
  await showDailyStatus();
};
chrome.storage.onChanged.addListener(()=>{ if (!dailyCard.hidden) void showDailyStatus(); });
bind.onclick=async()=>{bind.disabled=true;try{await start("bind");status.textContent="已打开飞书授权页，请完成授权";window.setTimeout(()=>void load(),1500)}catch(error){status.className="error";status.textContent=error instanceof Error?error.message:"绑定失败"}finally{bind.disabled=false;}};
rebind.onclick=async()=>{rebind.disabled=true;try{await start("bind");status.textContent="已打开飞书授权页，请完成授权";window.setTimeout(()=>void load(),1500)}catch(error){status.className="error";status.textContent=error instanceof Error?error.message:"绑定失败"}finally{rebind.disabled=false;}};
unbind.onclick=async()=>{if(!confirm("确认解绑当前飞书账号？"))return;unbind.disabled=true;try{await start("unbind")}catch(error){status.className="error";status.textContent=error instanceof Error?error.message:"解绑失败"}finally{unbind.disabled=false;}};
restart.onclick=async()=>{restart.disabled=true;try{const tab=await currentBossTab();if(!tab?.id)throw new Error("请先打开 BOSS 沟通页");const response=await chrome.tabs.sendMessage(tab.id,{type:"RUN_CATCHUP"}) as {ok?:boolean;error?:string};if(!response?.ok)throw new Error(response?.error||"补扫启动失败");status.className="ok";status.textContent="已开始检查沟通中列表，仅同步新增或有更新的候选人";}catch(error){status.className="error";status.textContent=error instanceof Error?error.message:"重新同步失败"}finally{restart.disabled=false;}};
logout.onclick=async()=>{if(!confirm("确认退出这台浏览器的招聘协同登录？其他浏览器不受影响。"))return;logout.disabled=true;try{await logoutExtension();account.textContent="尚未连接招聘账号";status.className="muted";status.textContent="已退出此浏览器";showBoundUi(false)}catch(error){status.className="error";status.textContent=error instanceof Error?error.message:"退出失败"}finally{logout.disabled=false;}};
void load();
