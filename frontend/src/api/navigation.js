/** 登录后的本地页面选择；只改善路由边界，实际权限仍由后端校验。 */
const allowedPages = {
  admin: ["/users/", "/review/", "/records/", "/sidebar/"],
  reviewer: ["/review/", "/sidebar/"],
  staff: ["/sidebar/"],
};

/** @returns {string} 当前角色允许的本地路径，不接收外链、数组或未授权页面。 */
export function loginDestination(role, next) {
  const pages = allowedPages[role] || allowedPages.staff;
  return typeof next === "string" && pages.includes(next) ? next : pages[0];
}
