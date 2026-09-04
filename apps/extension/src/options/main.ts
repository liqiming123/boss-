const root = document.querySelector("#app")!;
root.innerHTML =
  '<h2>招聘协同助手高级设置</h2><label>中央服务器 API 地址 <input id="api" style="width:420px" placeholder="https://recruit.example.com/api/v1"></label><p style="max-width:560px;color:#64748b">API 地址仅用于本地开发或故障恢复。生产环境的自动补扫由招聘管理后台统一控制，此页面不会覆盖公司设置。</p><button id="save">保存连接地址</button><p id="result"></p>';
const input = root.querySelector<HTMLInputElement>("#api")!,
  result = root.querySelector<HTMLElement>("#result")!;
chrome.runtime.sendMessage({ type: "GET_AUTH" }).then((response) => {
  input.value = response.data.apiBaseUrl;
});
root.querySelector("#save")?.addEventListener("click", async () => {
  try {
    const url = new URL(input.value.replace(/\/$/, ""));
    const local = /^(localhost|127\.0\.0\.1)$/.test(url.hostname);
    if (!local && url.protocol !== "https:")
      throw new Error("生产服务器必须使用 HTTPS");
    if (!local) {
      const granted = await chrome.permissions.request({
        origins: [`${url.origin}/*`],
      });
      if (!granted) throw new Error("未授权扩展访问该服务器地址");
    }
    await chrome.runtime.sendMessage({
      type: "SET_AUTH",
      payload: {
        apiBaseUrl: url.toString().replace(/\/$/, ""),
      },
    });
    result.textContent = "已保存。补扫设置会在刷新 BOSS 沟通页后生效。";
  } catch (error) {
    result.textContent = error instanceof Error ? error.message : "保存失败";
  }
});
export {};
