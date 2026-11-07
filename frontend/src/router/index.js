/** 按访问路径隔离客服侧边栏和管理员审核台。 */
import { createRouter, createWebHistory } from "vue-router";
import SidebarView from "../views/SidebarView.vue";
import ReviewView from "../views/ReviewView.vue";
import ChatRecordsView from "../views/ChatRecordsView.vue";
import UserManagementView from "../views/UserManagementView.vue";
import ChangePasswordView from "../views/ChangePasswordView.vue";
import LoginView from "../views/LoginView.vue";
import { token } from "../api";

const router = createRouter({
  // history 模式生成正常 URL，便于企业微信后台直接配置固定侧边栏地址。
  history: createWebHistory(),
  routes: [
    // 根网址作为独立管理后台入口；企业微信侧边栏继续使用原有固定路径。
    { path: "/", redirect: "/login/" },
    { path: "/login/", alias: "/login", component: LoginView },
    // sidebar-v2 仅保留既有企业微信地址兼容，实际共用同一 Vue 页面。
    { path: "/sidebar/", alias: "/sidebar-v2/", component: SidebarView },
    // 普通浏览器调试入口，不触发企业微信授权和 JS-SDK。
    { path: "/sidebar-demo/", component: SidebarView },
    // Skill 审核台独立于高频客服侧边栏，避免增加客服操作负担。
    { path: "/review/", component: ReviewView },
    // 聊天记录管理仅面向管理员，与客服高频操作和 Skill 审核职责分离。
    { path: "/records/", component: ChatRecordsView },
    // 用户管理只面向管理员，用于注册员工账号和维护登录权限。
    { path: "/users/", component: UserManagementView },
    // 密码账号在首次登录或主动改密时使用同一个安全表单。
    { path: "/change-password/", component: ChangePasswordView, meta: { requiresAuth: true } },
  ],
});

/**
 * 管理后台路由统一鉴权：令牌缺失时只能进入统一用户登录页。
 * 企业微信侧边栏是独立 OAuth 流程，保留其无令牌进入能力。
 */
router.beforeEach((to) => {
  const isSidebar = to.path.startsWith("/sidebar") || to.path === "/sidebar-demo/";
  if (to.path === "/login" || to.path === "/login/" || isSidebar) return true;
  if (token()) return true;
  return { path: "/login", query: { next: to.fullPath }, replace: true };
});

export default router;



