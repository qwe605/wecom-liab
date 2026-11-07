/** 统一处理 API 地址、鉴权头、错误解析和访问令牌。 */
// 版本化本地 Token 键：无法操作企业微信内置 WebView 缓存时，
// 发布新前端也能自动忽略旧的过期 Token，并重新进入企业微信授权流程。
const TOKEN_KEY = "kefubao_token_v2";

/** @returns {string} 当前浏览器保存的客服宝访问令牌；未登录时返回空字符串。 */
export const token = () => localStorage.getItem(TOKEN_KEY) || "";
/** @param {string} value 后端登录接口返回的令牌。 */
export const saveToken = (value) => localStorage.setItem(TOKEN_KEY, value);
/** 清除失效令牌，使页面重新进入登录流程。 */
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

/**
 * 清除失效会话并把非企业微信页面送回统一登录页。
 * @returns {void}
 */
function redirectAfterAuthLoss() {
  clearToken();
  const path = window.location.pathname;
  // 侧边栏使用企业微信 OAuth，不能被管理后台登录页打断。
  if (path.startsWith("/sidebar") || path === "/sidebar-demo/" || path === "/login" || path === "/login/") return;
  const next = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  window.location.replace(`/login?next=${encodeURIComponent(next)}`);
}

/**
 * 统一发送客服宝后端请求，并处理鉴权与错误结构。
 *
 * @param {string} path 以 `/api/` 开头的后端接口路径。
 * @param {RequestInit} options fetch 配置，例如 method、body 或附加请求头。
 * @returns {Promise<object>} 后端返回并解析后的 JSON 数据。
 * @throws {Error} HTTP 非 2xx 时抛出后端 detail/error/message 中最明确的错误。
 */
export async function api(path, options = {}) {
  // 所有业务接口使用 JSON；调用方自定义请求头可以覆盖默认值。
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  // 登录后的请求自动携带 Bearer Token，页面无需重复拼装鉴权头。
  if (token()) headers.Authorization = `Bearer ${token()}`;
  const response = await fetch(path, { ...options, headers });
  // 兼容反向代理返回空响应或非 JSON 错误页，避免解析异常覆盖真实 HTTP 状态。
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) redirectAfterAuthLoss();
    const detail = payload.detail || payload.error || payload.message || `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload;
}

/**
 * 使用现有 Bearer Token 下载后端生成的文件。
 *
 * @param {string} path 带筛选参数的导出接口路径。
 * @param {string} fallbackName 响应未提供文件名时使用的本地文件名。
 * @returns {Promise<void>} 浏览器完成文件保存提示后结束。
 * @throws {Error} 鉴权失败或导出接口返回非 2xx 状态时抛出明确错误。
 */
export async function downloadFile(path, fallbackName = "chat-records.csv") {
  // 下载接口返回二进制 CSV，不能复用强制 JSON 解析的 api()。
  const headers = {};
  if (token()) headers.Authorization = `Bearer ${token()}`;
  const response = await fetch(path, { headers });
  if (!response.ok) {
    if (response.status === 401) redirectAfterAuthLoss();
    const payload = await response.json().catch(() => ({}));
    const detail = payload.detail || payload.error || payload.message || `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }

  // 优先采用服务端 Content-Disposition 文件名，确保导出时间与服务端一致。
  const disposition = response.headers.get("Content-Disposition") || "";
  const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
  const filename = filenameMatch?.[1] || fallbackName;
  const objectUrl = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}
