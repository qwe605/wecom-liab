<script setup>
/** 登录用户修改本人密码；首次登录待改密账号不能跳过此页面。 */
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, clearToken, token } from "../api";

const router = useRouter();
const currentUser = ref(null);
const currentPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const loading = ref(false);
const error = ref("");

/** @returns {boolean} 当前账号是否处于不可跳过的首次改密状态。 */
const requiredChange = computed(() => Boolean(currentUser.value?.must_change_password));

/**
 * 校验登录状态并读取首次改密标记。
 *
 * @returns {Promise<void>} 未登录时返回后台登录页，已登录时展示改密表单。
 */
async function initialize() {
  if (!token()) {
    await router.replace("/login");
    return;
  }
  try {
    currentUser.value = await api("/api/auth/me");
  } catch (reason) {
    clearToken();
    error.value = reason.message;
    await router.replace("/login");
  }
}

/**
 * 校验确认密码后提交本人密码修改。
 *
 * @returns {Promise<void>} 修改成功后按角色返回对应管理页面。
 */
async function submit() {
  error.value = "";
  if (newPassword.value !== confirmPassword.value) {
    error.value = "两次输入的新密码不一致";
    return;
  }
  loading.value = true;
  try {
    await api("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password: currentPassword.value, new_password: newPassword.value }),
    });
    await router.replace(currentUser.value?.role === "admin" ? "/users/" : "/review/");
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/** 返回当前角色的默认页面；首次改密状态下不提供此操作。 */
async function cancel() {
  await router.replace(currentUser.value?.role === "admin" ? "/users/" : "/review/");
}

onMounted(initialize);
</script>

<template>
  <main class="password-shell">
    <section class="password-panel panel">
      <h1>{{ requiredChange ? '首次登录修改密码' : '修改密码' }}</h1>
      <p>{{ requiredChange ? '管理员设置的是初始密码，修改后才能继续使用管理功能。' : '修改当前登录账号的密码。' }}</p>
      <p v-if="error" class="error-banner">{{ error }}</p>
      <label>当前密码<input v-model="currentPassword" type="password" autocomplete="current-password" /></label>
      <label>新密码<input v-model="newPassword" type="password" autocomplete="new-password" placeholder="至少 8 个字符" /></label>
      <label>确认新密码<input v-model="confirmPassword" type="password" autocomplete="new-password" @keyup.enter="submit" /></label>
      <div class="password-actions"><button v-if="!requiredChange" :disabled="loading" @click="cancel">取消</button><button class="primary" :disabled="loading" @click="submit">确认修改</button></div>
    </section>
  </main>
</template>
