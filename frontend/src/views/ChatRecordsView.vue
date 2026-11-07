<script setup>
/** 管理员聊天记录台：查询数据库中的单聊/群聊，分页查看并按筛选条件导出。 */
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { renderMarkdown } from "../api/markdown";
import { api, clearToken, downloadFile, saveToken, token } from "../api";

// 登录状态仅复用现有账号体系，不在前端伪造管理员身份。
const username = ref("");
const password = ref("");
const currentUser = ref(null);
const loading = ref(false);
const exporting = ref(false);
const error = ref("");

// 会话列表、消息详情和筛选条件分别维护，避免切页时互相覆盖。
const conversations = ref([]);
const conversationPage = ref({ page: 1, pages: 0, total: 0, page_size: 20 });
const selectedConversation = ref(null);
const messages = ref([]);
const messagePage = ref({ page: 1, pages: 0, total: 0, page_size: 5 });
const filters = ref({ conversation_type: "", keyword: "", start_at: "", end_at: "" });
const summaryOpen = ref(false);
const summaryLoading = ref(false);
const summary = ref(null);
const selectedEvidence = ref([]);
const candidate = ref({ title: "", content: "", domain: null, product_category: "", product_subcategory: "", product_model: "", topic: "" });
const router = useRouter();

const analysisFields = [
  ["project_progress", "项目进度"], ["key_communications", "沟通重点"],
  ["blockers", "当前卡点"], ["resolution_process", "解决过程"],
  ["next_actions", "待办事项"], ["good_practices", "优秀做法"],
  ["improvements", "改进建议"],
];
const knowledgeDomains = [
  ["product_knowledge", "产品知识"], ["troubleshooting", "排障步骤"],
  ["service_script", "客服话术"], ["service_process", "服务流程"],
  ["communication_method", "沟通方法"],
];

/** @returns {boolean} 当前令牌是否已确认属于管理员。 */
const isAdmin = computed(() => currentUser.value?.role === "admin");

/**
 * 将 datetime-local 的本地时间转换为后端接受的 ISO 时间。
 *
 * @param {string} value 浏览器 datetime-local 控件值。
 * @returns {string} UTC ISO 时间；空输入保持为空。
 */
function toIso(value) {
  return value ? new Date(value).toISOString() : "";
}

/**
 * 构造列表和导出共用的筛选查询串。
 *
 * @returns {URLSearchParams} 已剔除空条件的 URL 参数。
 */
function filterParams() {
  const params = new URLSearchParams();
  if (filters.value.conversation_type) params.set("conversation_type", filters.value.conversation_type);
  if (filters.value.keyword.trim()) params.set("keyword", filters.value.keyword.trim());
  if (filters.value.start_at) params.set("start_at", toIso(filters.value.start_at));
  if (filters.value.end_at) params.set("end_at", toIso(filters.value.end_at));
  return params;
}

/**
 * 使用管理账号登录并确认角色。
 *
 * @returns {Promise<void>} 登录成功后加载第一页会话。
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
 * 校验已有令牌并加载管理页面。
 *
 * @returns {Promise<void>} 管理员加载数据，非管理员显示权限提示。
 */
async function initialize() {
  if (!token()) return;
  error.value = "";
  try {
    currentUser.value = await api("/api/auth/me");
    if (currentUser.value?.must_change_password) {
      await router.replace("/change-password/");
      return;
    }
    if (!isAdmin.value) throw new Error("仅管理员可以访问聊天记录管理");
    await loadConversations(1);
  } catch (reason) {
    error.value = reason.message;
    clearToken();
    currentUser.value = null;
  }
}

/**
 * 按当前筛选条件加载一页会话。
 *
 * @param {number} page 目标页码。
 * @returns {Promise<void>} 会话列表加载完成后更新分页状态。
 */
async function loadConversations(page = 1) {
  loading.value = true;
  error.value = "";
  try {
    const params = filterParams();
    params.set("page", String(page));
    params.set("page_size", String(conversationPage.value.page_size));
    const result = await api(`/api/admin/chat-records/conversations?${params}`);
    conversations.value = result.items || [];
    conversationPage.value = result;
    if (selectedConversation.value && !conversations.value.some((item) => item.id === selectedConversation.value.id)) {
      selectedConversation.value = null;
      messages.value = [];
    }
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/**
 * 打开一个会话并加载指定消息页。
 *
 * @param {object} conversation 当前会话列表项。
 * @param {number} page 消息目标页码。
 * @returns {Promise<void>} 消息详情加载完成后更新右侧工作区。
 */
async function openConversation(conversation, page = 1) {
  loading.value = true;
  error.value = "";
  selectedConversation.value = conversation;
  try {
    const params = new URLSearchParams({ page: String(page), page_size: String(messagePage.value.page_size) });
    if (filters.value.start_at) params.set("start_at", toIso(filters.value.start_at));
    if (filters.value.end_at) params.set("end_at", toIso(filters.value.end_at));
    const result = await api(`/api/admin/chat-records/conversations/${conversation.id}/messages?${params}`);
    selectedConversation.value = result.conversation;
    messages.value = result.items || [];
    messagePage.value = result;
    selectedEvidence.value = [];
    summaryOpen.value = false;
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/** 加载当前会话纪要并打开右侧抽屉。 */
async function openSummary() {
  if (!selectedConversation.value) return;
  summaryLoading.value = true;
  error.value = "";
  try {
    const result = await api(`/api/admin/chat-records/conversations/${selectedConversation.value.id}/summary`);
    summary.value = {
      ...result,
      analysis: Object.fromEntries(analysisFields.map(([key]) => [key, (result.analysis?.[key] || []).join("\n")])),
    };
    summaryOpen.value = true;
  } catch (reason) {
    error.value = reason.message;
  } finally {
    summaryLoading.value = false;
  }
}

/** 调用现有增量摘要链路追到最新消息，成功后重新加载抽屉。 */
async function refreshSummary() {
  summaryLoading.value = true;
  error.value = "";
  try {
    await api(`/api/admin/chat-records/conversations/${selectedConversation.value.id}/summary/refresh`, { method: "POST" });
    await openSummary();
  } catch (reason) {
    error.value = reason.message;
  } finally {
    summaryLoading.value = false;
  }
}

/** 保存派生纪要，不修改原始消息，也不推进摘要边界。 */
async function saveSummary() {
  if (!summary.value?.last_message_id) return;
  summaryLoading.value = true;
  error.value = "";
  try {
    const analysis = Object.fromEntries(analysisFields.map(([key]) => [key, (summary.value.analysis[key] || "").split("\n").map((item) => item.trim()).filter(Boolean)]));
    await api(`/api/admin/chat-records/conversations/${selectedConversation.value.id}/summary`, {
      method: "PATCH",
      body: JSON.stringify({ summary_text: summary.value.summary_text, analysis, last_message_id: summary.value.last_message_id }),
    });
    await openSummary();
  } catch (reason) {
    error.value = reason.message;
  } finally {
    summaryLoading.value = false;
  }
}

/** 用一条优秀做法预填候选，最终仍需人工选择原始消息证据。 */
function prepareCandidate(text) {
  candidate.value = { title: text.slice(0, 30), content: text, domain: null, product_category: "", product_subcategory: "", product_model: "", topic: "" };
}

/** 提交固定为 pending 的 Skill 候选，发布仍在统一审核台完成。 */
async function submitCandidate() {
  const [domainCode, domainName] = candidate.value.domain || [];
  const topic = (candidate.value.topic || "").trim();
  const productFields = [
    ["product_category", candidate.value.product_category],
    ["product_subcategory", candidate.value.product_subcategory],
    ["product_model", candidate.value.product_model],
  ];
  const taxonomyPath = domainCode && topic
    ? [{ level: "knowledge_domain", code: domainCode, name: domainName }, ...productFields.filter(([, value]) => value.trim()).map(([level, name]) => ({ level, name: name.trim() })), { level: "topic", name: topic }]
    : [];
  loading.value = true;
  error.value = "";
  try {
    await api(`/api/admin/chat-records/conversations/${selectedConversation.value.id}/skill-candidates`, {
      method: "POST",
      body: JSON.stringify({ title: candidate.value.title, content: candidate.value.content, taxonomy_path: taxonomyPath, source_message_ids: selectedEvidence.value }),
    });
    candidate.value = { title: "", content: "", domain: null, product_category: "", product_subcategory: "", product_model: "", topic: "" };
    selectedEvidence.value = [];
    window.alert("已提交到 Skill 审核队列");
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/**
 * 导出当前筛选结果或当前选中的单个会话。
 *
 * @param {boolean} selectedOnly 是否仅导出当前会话。
 * @returns {Promise<void>} 浏览器启动 CSV 下载后结束。
 */
async function exportRecords(selectedOnly = false) {
  exporting.value = true;
  error.value = "";
  try {
    const params = filterParams();
    if (selectedOnly && selectedConversation.value) params.set("conversation_id", String(selectedConversation.value.id));
    await downloadFile(`/api/admin/chat-records/export?${params}`);
  } catch (reason) {
    error.value = reason.message;
  } finally {
    exporting.value = false;
  }
}

/**
 * 将参与人数组转换为紧凑、可检索的显示文本。
 *
 * @param {Array<{id: string, name: string}>} participants 员工或客户列表。
 * @returns {string} 姓名优先、ID 作为补充的文本。
 */
function participantText(participants) {
  if (!participants?.length) return "未同步";
  return participants.map((item) => item.name === item.id ? item.id : `${item.name}（${item.id}）`).join("、");
}

/** @param {string} value ISO 时间。 @returns {string} 本地可读时间。 */
function formatTime(value) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "-";
}

/** 清除令牌并返回登录状态。 */
function logout() {
  clearToken();
  currentUser.value = null;
  conversations.value = [];
  selectedConversation.value = null;
  messages.value = [];
  router.replace("/login");
}

// 页面启动时优先复用有效令牌，避免管理员重复登录。
onMounted(initialize);
</script>

<template>
  <main class="records-shell">
    <header class="records-header">
      <div><h1>聊天记录管理</h1><p>查看数据库中已归档的客户单聊与外部群聊，并按条件导出。</p></div>
      <nav v-if="isAdmin" class="admin-nav quick-admin-nav"><RouterLink to="/review/">Skill 审核</RouterLink><RouterLink to="/users/">用户管理</RouterLink><button class="quick-logout" @click="logout">退出登录</button></nav>
    </header>

    <p v-if="error" class="error-banner">{{ error }}</p>
    <section v-if="!isAdmin" class="login-panel panel">
      <h2>后台账号登录</h2>
      <label>账号<input v-model="username" autocomplete="username" /></label>
      <label>密码<input v-model="password" type="password" autocomplete="current-password" @keyup.enter="login" /></label>
      <button class="primary" :disabled="loading" @click="login">登录</button>
    </section>

    <template v-else>
      <section class="records-filters">
        <label>会话类型<select v-model="filters.conversation_type"><option value="">全部</option><option value="single">客户单聊</option><option value="group">外部群聊</option></select></label>
        <label>参与人或会话 ID<input v-model="filters.keyword" placeholder="姓名、企业微信 ID、群聊 ID" @keyup.enter="loadConversations(1)" /></label>
        <label>开始时间<input v-model="filters.start_at" type="datetime-local" /></label>
        <label>结束时间<input v-model="filters.end_at" type="datetime-local" /></label>
        <div class="filter-actions"><button class="primary" :disabled="loading" @click="loadConversations(1)">查询</button><button :disabled="exporting" @click="exportRecords(false)">导出筛选结果</button></div>
      </section>

      <section class="records-workspace">
        <div class="conversation-pane">
          <div class="records-section-head"><div><h2>会话</h2><p>共 {{ conversationPage.total }} 个</p></div></div>
          <button v-for="item in conversations" :key="item.id" class="conversation-row" :class="{ active: selectedConversation?.id === item.id }" @click="openConversation(item, 1)">
            <span class="conversation-title"><strong>{{ item.conversation_type === 'single' ? '客户单聊' : '外部群聊' }}</strong><time>{{ formatTime(item.newest_message_at) }}</time></span>
            <span>{{ item.name || item.wecom_chat_id }}</span>
            <small>员工：{{ participantText(item.users) }}</small><small>客户：{{ participantText(item.customers) }}</small>
            <small>{{ item.message_count }} 条消息</small>
          </button>
          <p v-if="!conversations.length && !loading" class="empty">当前筛选条件下暂无会话。</p>
          <div class="pager"><button :disabled="conversationPage.page <= 1" @click="loadConversations(conversationPage.page - 1)">上一页</button><span>{{ conversationPage.page }} / {{ conversationPage.pages || 1 }}</span><button :disabled="conversationPage.page >= conversationPage.pages" @click="loadConversations(conversationPage.page + 1)">下一页</button></div>
        </div>

        <div class="message-pane">
          <template v-if="selectedConversation">
            <div class="records-section-head"><div><h2>消息明细</h2><p>{{ selectedConversation.name || selectedConversation.wecom_chat_id }}，共 {{ messagePage.total }} 条</p></div><div class="message-actions"><button :disabled="summaryLoading" @click="openSummary">会话纪要</button><button :disabled="exporting" @click="exportRecords(true)">导出当前会话</button></div></div>
            <div class="admin-message-list">
              <article v-for="message in messages" :key="message.id" class="admin-message" :class="message.sender_type">
                <header><strong>{{ message.sender_name || message.sender_id }}</strong><time>{{ formatTime(message.sent_at) }}</time></header>
                <div class="markdown-body" v-html="renderMarkdown(message.content || `[${message.message_type}]`)"></div>
                <footer><small>{{ message.sender_type === 'staff' ? '员工' : '客户' }} · {{ message.sender_id }} · seq={{ message.archive_seq }}</small><label class="evidence-check"><input v-model="selectedEvidence" type="checkbox" :value="message.id" />候选证据</label></footer>
              </article>
            </div>
            <p v-if="!messages.length && !loading" class="empty">当前时间范围内暂无消息。</p>
            <div class="pager"><button :disabled="messagePage.page <= 1" @click="openConversation(selectedConversation, messagePage.page - 1)">上一页</button><span>{{ messagePage.page }} / {{ messagePage.pages || 1 }}</span><button :disabled="messagePage.page >= messagePage.pages" @click="openConversation(selectedConversation, messagePage.page + 1)">下一页</button></div>
          </template>
          <p v-else class="empty records-placeholder">从左侧选择一个会话查看消息。</p>
        </div>
      </section>
      <aside v-if="summaryOpen" class="summary-drawer">
        <div class="drawer-head"><div><h2>会话纪要</h2><p>未覆盖 {{ summary?.uncovered_message_count || 0 }} 条消息</p></div><button class="compact" @click="summaryOpen = false">关闭</button></div>
        <div class="drawer-actions"><button :disabled="summaryLoading" @click="refreshSummary">刷新纪要</button><button class="primary" :disabled="summaryLoading || !summary?.last_message_id" @click="saveSummary">保存编辑</button></div>
        <template v-if="summary">
          <label>整体纪要<textarea v-model="summary.summary_text" rows="7"></textarea></label>
          <section v-for="field in analysisFields" :key="field[0]" class="summary-section">
            <label>{{ field[1] }}<textarea v-model="summary.analysis[field[0]]" rows="4" placeholder="每行一项"></textarea></label>
            <div v-if="field[0] === 'good_practices'" class="good-practice-list"><button v-for="text in summary.analysis[field[0]].split('\n').filter(Boolean)" :key="text" @click="prepareCandidate(text)">提交此做法</button></div>
          </section>
          <section v-if="candidate.content" class="candidate-editor">
            <h3>提交 Skill 审核</h3>
            <label>标题<input v-model="candidate.title" /></label>
            <label>可复用正文<textarea v-model="candidate.content" rows="5"></textarea></label>
            <label>知识域<select v-model="candidate.domain"><option :value="null">请选择</option><option v-for="domain in knowledgeDomains" :key="domain[0]" :value="domain">{{ domain[1] }}</option></select></label>
            <label>产品大类<input v-model="candidate.product_category" placeholder="产品知识和排障步骤必填" /></label>
            <label>产品小类<input v-model="candidate.product_subcategory" placeholder="产品知识和排障步骤必填" /></label>
            <label>具体型号<input v-model="candidate.product_model" placeholder="产品知识和排障步骤必填" /></label>
            <label>叶子主题<input v-model="candidate.topic" placeholder="例如：需求澄清话术" /></label>
            <p class="source-note">已选择 {{ selectedEvidence.length }} 条当前页原始消息作为证据。</p>
            <button class="primary" :disabled="loading || !selectedEvidence.length" @click="submitCandidate">提交到审核台</button>
          </section>
        </template>
        <p v-else class="empty">当前会话尚无纪要，请点击“刷新纪要”。</p>
      </aside>
    </template>
  </main>
</template>



