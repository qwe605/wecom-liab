<script setup>
/** 管理员用户管理页：代注册企业员工账号，并维护角色、状态和密码。 */
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { renderMarkdown } from "../api/markdown";
import { api, clearToken, saveToken, token } from "../api";

// 登录状态与账号分页相互独立，避免表单提交影响当前登录身份。
const username = ref("");
const password = ref("");
const currentUser = ref(null);
const items = ref([]);
const pageState = ref({ page: 1, pages: 0, page_size: 7, total: 0 });
const keyword = ref("");
const loading = ref(false);
const error = ref("");
const notice = ref("");
const registration = ref({ wecom_userid: "", username: "", name: "", password: "", role: "staff" });
const router = useRouter();

/** @returns {boolean} 当前令牌是否已经由后端确认属于管理员。 */
const isAdmin = computed(() => currentUser.value?.role === "admin");

/**
 * 使用统一账号体系登录并初始化用户管理页。
 *
 * @returns {Promise<void>} 登录成功后加载第一页账号。
 */
async function login() {
  loading.value = true;
  error.value = "";
  try {
    const result = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: username.value, password: password.value }),
    });
    saveToken(result.token);
    if (result.must_change_password) {
      await router.push("/change-password/");
      return;
    }
    // 登录接口返回后端确认的角色：审核员进入审核台，不能停留在管理员页面。
    if (result.role === "reviewer") {
      await router.push("/review/");
      return;
    }
    if (result.role !== "admin") throw new Error("当前账号没有管理后台权限");
    await initialize();
  } catch (reason) {
    clearToken();
    currentUser.value = null;
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/**
 * 校验已有令牌和管理员角色。
 *
 * @returns {Promise<void>} 管理员身份有效时加载账号列表。
 */
async function initialize() {
  if (!token()) return;
  try {
    currentUser.value = await api("/api/auth/me");
    if (currentUser.value?.must_change_password) {
      await router.replace("/change-password/");
      return;
    }
    if (currentUser.value?.role === "reviewer") {
      await router.replace("/review/");
      return;
    }
    if (!isAdmin.value) throw new Error("当前账号没有管理后台权限");
    await loadUsers(1);
  } catch (reason) {
    clearToken();
    currentUser.value = null;
    error.value = reason.message;
  }
}

/**
 * 按关键字分页加载已开通账号。
 *
 * @param {number} page 目标页码。
 * @returns {Promise<void>} 更新列表和分页状态。
 */
async function loadUsers(page = 1) {
  loading.value = true;
  error.value = "";
  const params = new URLSearchParams({ page: String(page), page_size: String(pageState.value.page_size) });
  if (keyword.value.trim()) params.set("keyword", keyword.value.trim());
  try {
    const result = await api(`/api/admin/users?${params}`);
    items.value = (result.items || []).map((item) => ({ ...item, new_password: "" }));
    pageState.value = result;
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/**
 * 由管理员为企业员工开通系统账号。
 *
 * @returns {Promise<void>} 注册成功后清空表单并刷新账号列表。
 */
async function createUser() {
  loading.value = true;
  error.value = "";
  notice.value = "";
  try {
    await api("/api/admin/users", { method: "POST", body: JSON.stringify(registration.value) });
    registration.value = { wecom_userid: "", username: "", name: "", password: "", role: "staff" };
    notice.value = "账号注册成功";
    await loadUsers(1);
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/**
 * 保存单个账号的资料、角色、启用状态和可选新密码。
 *
 * @param {object} item 当前账号的可编辑页面副本。
 * @returns {Promise<void>} 保存成功后刷新当前页，避免显示旧状态。
 */
async function saveUser(item) {
  loading.value = true;
  error.value = "";
  notice.value = "";
  const body = { username: item.username, name: item.name, role: item.role, is_enabled: item.is_enabled };
  if (item.new_password) body.password = item.new_password;
  try {
    await api(`/api/admin/users/${encodeURIComponent(item.wecom_userid)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    notice.value = `已保存 ${item.name}`;
    await loadUsers(pageState.value.page);
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

async function deleteUser(item) {
  loading.value = true;
  error.value = "";
  try {
    await api(`/api/admin/users/${encodeURIComponent(item.wecom_userid)}`, { method: "DELETE" });
    notice.value = `已删除 ${item.name}`;
    await loadUsers(pageState.value.page);
    if (!items.value.length && pageState.value.page > 1) await loadUsers(pageState.value.page - 1);
  } catch (reason) { error.value = reason.message; }
  finally { loading.value = false; }
}

/** 清除共享令牌并返回管理员登录表单。 */
function logout() {
  clearToken();
  currentUser.value = null;
  items.value = []; router.replace("/login");
}

// 页面刷新后复用共享令牌，但始终重新向后端确认管理员角色。
onMounted(initialize);
</script>

<template>
  <main class="users-shell">
    <header class="records-header">
      <div><h1>用户管理</h1><p>为企业员工注册客服宝账号，并维护角色、状态和密码。</p></div>
      <nav v-if="isAdmin" class="admin-nav quick-admin-nav"><RouterLink to="/review/">Skill 审核</RouterLink><RouterLink to="/records/">聊天记录</RouterLink><button class="quick-logout" @click="logout">退出登录</button></nav>
    </header>

    <p v-if="error" class="error-banner">{{ error }}</p>
    <p v-if="notice" class="success-banner">{{ notice }}</p>

    <section v-if="!isAdmin" class="login-panel panel">
      <h2>后台账号登录</h2>
      
      <label>账号<input v-model="username" autocomplete="username" /></label>
      <label>密码<input v-model="password" type="password" autocomplete="current-password" @keyup.enter="login" /></label>
      <button class="primary" :disabled="loading" @click="login">登录</button>
    </section>

    <template v-else>
      <section class="registration-panel">
        <div class="records-section-head"><div><h2>注册账号</h2><p>企业微信员工 ID 是账号与企业身份的唯一关联。</p></div></div>
        <div class="registration-grid">
          <label>企业微信员工 ID<input v-model.trim="registration.wecom_userid" /></label>
          <label>登录账号<input v-model.trim="registration.username" autocomplete="off" /></label>
          <label>员工姓名<input v-model.trim="registration.name" /></label>
          <label>初始密码<input v-model="registration.password" type="password" autocomplete="new-password" placeholder="至少 8 个字符" /></label>
          <label>角色<select v-model="registration.role"><option value="staff">客服</option><option value="reviewer">审核员</option><option value="admin">管理员</option></select></label>
          <button class="primary registration-submit" :disabled="loading" @click="createUser">注册账号</button>
        </div>
      </section>

      <section class="users-list-section">
        <div class="user-list-toolbar"><div><h2>系统账号</h2><p>共 {{ pageState.total }} 个</p></div><div class="user-search"><input v-model="keyword" placeholder="姓名、账号或员工 ID" @keyup.enter="loadUsers(1)" /><button :disabled="loading" @click="loadUsers(1)">查询</button></div></div>
        <div class="users-table-wrap">
          <table class="users-table">
            <thead><tr><th>登录账号</th><th>姓名</th><th>角色</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="item in items" :key="item.wecom_userid">
                <td><input v-model.trim="item.username" /></td>
                <td><input v-model.trim="item.name" /></td>
                <td><select v-model="item.role"><option value="staff">客服</option><option value="reviewer">审核员</option><option value="admin">管理员</option></select></td>
                <td class="user-actions"><button class="compact" :disabled="loading" @click="saveUser(item)">保存</button><button class="compact danger" :disabled="loading" @click="deleteUser(item)">删除员工</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="!items.length && !loading" class="empty">暂无已开通账号。</p>
        <div class="pager"><button :disabled="pageState.page <= 1" @click="loadUsers(pageState.page - 1)">上一页</button><span>{{ pageState.page }} / {{ pageState.pages || 1 }}</span><button :disabled="pageState.page >= pageState.pages" @click="loadUsers(pageState.page + 1)">下一页</button></div>
      </section>
    </template>
  </main>
</template>









