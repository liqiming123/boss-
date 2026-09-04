import { apiRequest } from "../background/api-client";
import { getAuth, setAuth, type PendingFeishuLogin } from "../background/auth-store";

type Start = { authorization_url: string; attempt_id: string; poll_token: string; expires_in: number };
type Poll = { status: "PENDING" | "APPROVED"; access_token?: string; refresh_token?: string; device_id?: string };
const root = document.querySelector("#app")!, version = chrome.runtime.getManifest().version;
root.innerHTML = `<style>body{font:14px system-ui;width:320px;padding:14px}button{width:100%;padding:9px;margin:6px 0;background:#2156a5;color:#fff;border:0;border-radius:6px}button:disabled{opacity:.5}.muted{color:#64748b;font-size:12px}.ok{color:#087f5b}.error{color:#b42318}a{display:block;margin-top:8px}</style><h3>招聘协同助手 <span class="muted">v${version}</span></h3><p id="account" class="muted">读取绑定状态中…</p><p id="status">检查中…</p><button id="bind">绑定飞书</button><button id="unbind">解绑飞书</button><a id="admin" href="#">打开招聘管理后台</a>`;
const account = root.querySelector<HTMLElement>("#account")!, status = root.querySelector<HTMLElement>("#status")!, bind = root.querySelector<HTMLButtonElement>("#bind")!, unbind = root.querySelector<HTMLButtonElement>("#unbind")!;
root.querySelector<HTMLAnchorElement>("#admin")!.onclick = async e => { e.preventDefault(); const a = await getAuth(); await chrome.tabs.create({url: `${a.apiBaseUrl.replace(/\/api\/v1\/?$/, "")}/recruitment/overview`}); };
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
async function load() { try { const a = await getAuth(); if(a.pendingFeishuLogin && !(await poll(a.pendingFeishuLogin))) return; const current=await getAuth(); if (!current.accessToken) { account.textContent="当前未绑定飞书账号"; status.textContent="请先绑定飞书"; unbind.disabled=true; return; } const me=await apiRequest<{display_name:string}>("/plugin/me"); account.textContent=`当前同步招聘人：${me.display_name}`; status.className="ok"; status.textContent="飞书已绑定"; bind.textContent="重新绑定飞书"; unbind.disabled=false; } catch (error) { const current=await getAuth(); if(!current.accessToken){account.textContent="当前未绑定飞书账号";status.className="";status.textContent="请先绑定飞书";unbind.disabled=true;return;} status.className="error"; status.textContent=error instanceof Error?error.message:"绑定状态读取失败"; } }
async function currentBossAccount() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs[0];
  if (!tab?.id || !tab.url || !/^https:\/\/(?:www\.)?(?:zhipin\.com|bosszhipin\.com)\//.test(tab.url))
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
bind.onclick=async()=>{bind.disabled=true;try{await start("bind");status.textContent="已打开飞书授权页，请完成授权";window.setTimeout(()=>void load(),1500)}catch(error){status.className="error";status.textContent=error instanceof Error?error.message:"绑定失败"}finally{bind.disabled=false;}};
unbind.onclick=async()=>{if(!confirm("确认解绑当前飞书账号？"))return;unbind.disabled=true;try{await start("unbind")}catch(error){status.className="error";status.textContent=error instanceof Error?error.message:"解绑失败"}finally{unbind.disabled=false;}};
void load();
