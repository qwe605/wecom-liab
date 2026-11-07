<script setup>
/** 统一登录入口：隐藏密码、不回显凭据，成功后仅进入角色允许的本地页面。 */
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, clearToken, saveToken } from "../api";
import { loginDestination } from "../api/navigation";

const username = ref("");
const password = ref("");
const loading = ref(false);
const error = ref("");
const router = useRouter();
const route = useRoute();

/** @returns {Promise<void>} 登录后优先完成首次改密，再选择安全的返回页面。 */
async function login() {
  loading.value = true;
  error.value = "";
  try {
    const result = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: username.value, password: password.value }),
    });
    saveToken(result.token);
    await router.replace(result.must_change_password ? "/change-password/" : loginDestination(result.role, route.query.next));
  } catch (reason) {
    clearToken();
    error.value = reason.message;
  } finally {
    password.value = "";
    loading.value = false;
  }
}
</script>
<template>
  <main class="auth-shell">
    <section class="auth-layout">
      <div class="auth-intro">
        <div class="brand-mark">客</div>
        <p class="eyebrow">KEFUBAO WORKSPACE</p>
        <h1>让每一次服务<br /><strong>更专业、更高效</strong></h1>
        <p class="auth-description">统一管理客户会话、知识审核与服务记录，帮助团队把经验沉淀为可复用的服务能力。</p>
        <div class="auth-highlights"><span><b>01</b> 会话统一管理</span><span><b>02</b> 知识协作审核</span><span><b>03</b> 服务过程可追溯</span></div>
      </div>
      <section class="login-panel panel auth-card">
        <div class="auth-card-heading"><p class="eyebrow">WELCOME BACK</p><h2>登录客服宝</h2><p>使用管理员或审核账号继续工作</p></div>
        <form @submit.prevent="login">
          <label>账号<input v-model="username" autocomplete="username" placeholder="请输入登录账号" /></label>
          <label>密码<input v-model="password" type="password" autocomplete="current-password" placeholder="请输入密码" /></label>
          <p v-if="error" class="error-banner">{{ error }}</p>
          <button class="primary auth-submit" type="submit" :disabled="loading">{{ loading ? '正在登录…' : '登录' }}<span>→</span></button>
        </form>
        <p class="auth-footer">客服宝 · 企业微信服务工作台</p>
      </section>
    </section>
  </main>
</template>
