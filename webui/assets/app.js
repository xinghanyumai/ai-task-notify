/* W1 验证页的前端逻辑。只做四件事：读配置、保存、改回、重读。
 *
 * 一条硬规则：**必须等 `pywebviewready`**。pywebview 是往页面里异步注入
 * `window.pywebview.api` 的，在 DOMContentLoaded 时它还不存在，
 * 直接取会报 "Cannot read properties of undefined (reading 'api')"。
 */

const $ = (sel) => document.querySelector(sel);

/** 调一个 bridge 方法，统一处理 {ok, data, error}。 */
async function call(method, ...args) {
  const api = window.pywebview && window.pywebview.api;
  if (!api || typeof api[method] !== "function") {
    throw new Error(`bridge 上没有 ${method}（pywebview 未注入或方法未实现）`);
  }
  const res = await api[method](...args);
  if (!res || res.ok !== true) {
    throw new Error((res && res.error) || "未知错误");
  }
  return res.data;
}

function setStatus(text, kind) {
  const el = $("#status");
  el.textContent = text;
  el.className = kind || "";
}

function setResult(text, kind) {
  const el = $("#result");
  el.textContent = text;
  el.className = kind || "";
}

/** 渲染配置：先给一行摘要（验收要对着数字核），再给完整 JSON。 */
function render(data) {
  const cfg = data.cfg || {};
  const channels = Array.isArray(cfg.channels) ? cfg.channels : [];
  const on = channels.filter((c) => c && c.enabled).length;
  const triggers = Object.entries(cfg.triggers || {})
    .filter(([, v]) => v)
    .map(([k]) => k);

  setStatus(
    `已连通，配置路径 ${data.path}　·　` +
      `通道 ${channels.length} 个（启用 ${on}）　·　` +
      `enabled=${cfg.enabled}　·　` +
      `开启的触发 ${triggers.length} 个：${triggers.join(", ") || "无"}`,
    "ok"
  );
  $("#json").textContent = JSON.stringify(data.cfg, null, 2);
}

async function boot() {
  for (const id of ["#save", "#restore", "#reload"]) $(id).disabled = true;
  try {
    const data = await call("config_get");
    render(data);
    for (const id of ["#save", "#restore", "#reload"]) $(id).disabled = false;
  } catch (e) {
    setStatus(`读取失败：${e.message}`, "bad");
  }
}

/** 保存一个补丁，然后重新读磁盘——看到的是「存进去的」而不是「内存里的」。 */
async function save(patch, label) {
  setResult(`${label}…`);
  try {
    const res = await call("config_save", patch);
    const bits = [];
    if (res.turned_on && res.turned_on.length) bits.push(`自动启用 ${res.turned_on.join(", ")}`);
    if (res.turned_off && res.turned_off.length) bits.push(`自动关闭 ${res.turned_off.join(", ")}`);
    setResult(`${label}：已写入${bits.length ? "（" + bits.join("；") + "）" : ""}，重新读取中…`, "ok");
    render(await call("config_get"));
  } catch (e) {
    setResult(`${label}失败：${e.message}`, "bad");
  }
}

function wire() {
  $("#save").addEventListener("click", () => save({ enabled: false }, "改 enabled=false"));
  $("#restore").addEventListener("click", () => save({ enabled: true }, "改 enabled=true"));
  $("#reload").addEventListener("click", boot);
}

window.addEventListener("pywebviewready", () => {
  wire();
  boot();
});
