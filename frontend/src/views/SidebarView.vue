<script setup>
/** 客服侧边栏：连接当前会话、同步上下文、生成并采纳 AI 回复。 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api, saveToken, token } from "../api";
import { renderMarkdown } from "../api/markdown";
import { configureWeCom, detectConversation, copyReplyDraft } from "../api/wecom";

// 页面运行状态：分别保存提示文字、请求锁、当前会话、消息缓存和 AI 建议。
// 使用 ref 是为了让接口返回后 Vue 能自动刷新界面，不需要手工操作 DOM。
const status = ref("正在初始化...");
const busy = ref(false);
const session = ref(null);
const messages = ref([]);
const coverage = ref({ cached_days: 0 });
const suggestion = ref({ suggested_reply: "", suggestion_id: 0 });
const conversationSummary = ref({ summary_text: "", analysis: {} });
const historyDays = ref(7);
const error = ref("");
const refreshingCache = ref(false);
let cacheRefreshTimer = 0;
let summaryRefreshTimer = 0;
const activeTab = ref("reply");
// 编辑仅修改当前草稿，不触发模型调用或发送接口。
const editingReply = ref(false);
// 后端使用数字型会话主键；尚未连接企业微信会话时统一返回 0。
const conversationId = computed(() => session.value?.conversation?.database_id || 0);
const demoMode = location.pathname.includes("sidebar-demo") || new URLSearchParams(location.search).get("demo") === "1";

/**
 * 确保当前页面已经取得客服宝访问令牌。
 *
 * 已有令牌时直接返回；首次从企业微信打开时，先跳转企业微信 OAuth，取得一次性 code
 * 后调用后端换取应用令牌。企业微信 Secret 始终只由后端使用，不会进入浏览器。
 *
 * @returns {Promise<void>} 登录完成后结束。
 * @throws {Error} 签名接口、OAuth 登录或网络请求失败时抛出，由 connect 统一展示。
 */
async function ensureLogin() {
  if (token()) return;
  const code = new URLSearchParams(location.search).get("code");
  if (!code) {
    const redirectUri = `${location.origin}${location.pathname}`;
    const signed = await api(`/api/wecom/js-signature?url=${encodeURIComponent(redirectUri)}`);
    const authorize = new URL("https://open.weixin.qq.com/connect/oauth2/authorize");
    authorize.searchParams.set("appid", signed.corp_id);
    authorize.searchParams.set("redirect_uri", redirectUri);
    authorize.searchParams.set("response_type", "code");
    authorize.searchParams.set("scope", "snsapi_base");
    authorize.searchParams.set("agentid", signed.agent_id);
    authorize.searchParams.set("state", "kefubao");
    location.replace(`${authorize.toString()}#wechat_redirect`);
    throw new Error("正在跳转企业微信授权...");
  }
  const result = await api("/api/wecom/h5/login", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
  saveToken(result.token);
  history.replaceState({}, "", location.pathname);
}

/**
 * 初始化侧边栏并连接当前企业微信聊天会话。
 *
 * 流程固定为：登录 -> 配置 JS-SDK -> 读取当前客户/群 ID -> 后端建立会话关系
 * -> 读取本地缓存消息 -> 读取最近一次 AI 建议。
 *
 * @returns {Promise<void>} 初始化完成后更新页面状态。
 */
async function connect() {
  error.value = "";
  busy.value = true;
  if (demoMode) {
    // 公开演示只使用下方合成数据；不访问数据库或真实聊天调试接口。
    session.value = { service_user: { name: "演示客服", staff_userid: "demo_staff" }, conversation: { database_id: 1, conversation_type: "external_group_chat" } };
    messages.value = [{ source_message_id: "demo-1", sender_type: "customer", sender_name: "张工", msgtime: "2026-10-03 09:30", content: "请问设备连接不上时，应该先检查哪些配置？" }, { source_message_id: "demo-2", sender_type: "staff", sender_name: "演示客服", msgtime: "2026-10-03 09:32", content: "我先确认一下您的产品型号和上位机版本。" }, { source_message_id: "demo-3", sender_type: "customer", sender_name: "张工", msgtime: "2026-10-03 09:34", content: "型号是 KFB-200，使用 Windows 11。" }];
    coverage.value = { cached_days: 7 };
    conversationSummary.value = { summary_text: "客户咨询 KFB-200 在 Windows 11 下的连接配置，待确认驱动和通信参数。" };
    suggestion.value = { suggestion_id: 0, model_name: "Demo 模式", summary: "客户需要 KFB-200 连接故障排查步骤。", suggested_reply: "您好，建议先确认设备驱动已正确安装，再检查通信端口、波特率和设备管理器状态。如果仍无法连接，请提供错误提示截图和当前配置，我们继续协助排查。", diagnostics: { intent: "配置/故障排查", product_model: "KFB-200", searched_domain_names: ["产品 FAQ", "快速上手"], used_references: [{ reference_id: "demo-faq-001", title: "KFB-200 连接故障 FAQ" }], warnings: [] } };
    status.value = "独立调试模式：未连接企业微信"; busy.value = false; return;
  }
  try {
    await ensureLogin();
    await configureWeCom();
    const detected = await detectConversation();
    session.value = await api("/api/conversations/connect", {
      method: "POST",
      body: JSON.stringify(detected),
    });
    status.value = "已连接当前会话";
    await loadMessages();
    const latest = await api(`/api/conversations/${conversationId.value}/ai-replies/latest`);
    suggestion.value = latest;
    await loadSummary(false);
  } catch (reason) {
    error.value = reason.message;
    status.value = "连接失败";
  } finally {
    busy.value = false;
  }
}

/**
 * 从 MySQL 读取后台已经生成的会话纪要，不在页面轮询中调用模型。
 *
 * @param {boolean} silent 静默刷新时只记录日志，首次连接失败时交由 connect 展示。
 * @returns {Promise<void>} 成功后更新会话纪要和结构化复盘内容。
 */
async function loadSummary(silent = true) {
  if (demoMode) return;
  if (!conversationId.value) return;
  try {
    conversationSummary.value = await api(`/api/conversations/${conversationId.value}/summary`);
  } catch (reason) {
    if (!silent) throw reason;
    console.warn("自动刷新会话纪要失败", reason);
  }
}

/**
 * 同步并读取当前会话的聊天消息。
 *
 * @param {"cache"|"latest"|"older"} mode 同步模式：
 * `cache` 只读取 MySQL 已缓存消息；`latest` 从已保存的上游游标导入最新消息；
 * `older` 从缓存下界继续向前导入指定天数的历史消息。
 * @returns {Promise<void>} 完成后刷新消息列表和缓存覆盖范围。
 */
async function loadMessages(mode = "cache") {
  if (demoMode) { status.value = "演示：只展示合成消息，不执行同步"; return; }
  if (!conversationId.value) return;
  busy.value = true;
  error.value = "";
  try {
    if (mode === "latest") {
      // 请求后端执行“导入最新”业务。await 表示必须等后端同步完成，
      // 才能继续查询消息，否则下一行可能仍读到同步前的旧缓存。
      const synced = await api(`/api/conversations/${conversationId.value}/sync/latest`, { method: "POST" });
      // `scanned` 是全企业归档扫描量，`imported` 才是当前会话新增量；两者必须
      // 分开展示，避免当前群没有新消息时被误解成接口没有工作。
      status.value = synced.is_latest
        ? `当前会话已追平，本批扫描 ${synced.scanned || 0} 条归档，新增 ${synced.imported || 0} 条`
        : `企业归档仍在追平：本批扫描 ${synced.scanned || 0} 条，当前会话新增 ${synced.imported || 0} 条，请再次导入`;
    } else if (mode === "older") {
      // additional_days 只表达用户希望增加的历史范围，具体游标换算由后端 Service 负责。
      await api(`/api/conversations/${conversationId.value}/sync/history?additional_days=${historyDays.value}`, { method: "POST" });
    }
    // 同步接口负责写入 MySQL；消息查询接口负责把数据库中的统一消息结构返回给页面。
    const result = await api(`/api/conversations/${conversationId.value}/messages?limit=200`);
    messages.value = result.messages || [];
    coverage.value = result.coverage || {};
  } catch (reason) {
    error.value = reason.message;
  } finally {
    busy.value = false;
  }
}

/**
 * 静默从 MySQL 刷新消息列表，不触发任何第三方导入操作。
 *
 * 后台独立服务负责高频拉取和入库；页面轮询只调用查询接口。请求尚未完成时会
 * 跳过本轮，避免网络抖动造成多个重复请求堆积，也不会锁住客服的其他按钮。
 *
 * @returns {Promise<void>} 查询成功后更新消息和缓存覆盖范围。
 */
async function refreshCachedMessages() {
  if (demoMode) return;
  if (!conversationId.value || refreshingCache.value) return;
  refreshingCache.value = true;
  try {
    const result = await api(`/api/conversations/${conversationId.value}/messages?limit=200`);
    messages.value = result.messages || [];
    coverage.value = result.coverage || {};
  } catch (reason) {
    // 自动刷新失败不弹窗打断客服操作；手动操作仍会展示明确错误。
    console.warn("自动刷新聊天上下文失败", reason);
  } finally {
    refreshingCache.value = false;
  }
}

/**
 * 完成页面连接后启动数据库消息轮询。
 *
 * @returns {Promise<void>} 连接完成并注册定时器后结束。
 */
async function initialize() {
  await connect();
  // 演示不能启动真实会话轮询，即使同一浏览器中已有管理员令牌也不例外。
  if (demoMode) return;
  if (session.value && !cacheRefreshTimer) {
    // 1 秒只查询本地 MySQL，不直接请求企业微信；配合后台 0.5 秒归档轮询，
    // 在企业微信上游已经生成存档后尽快把新消息呈现到客服侧边栏。
    cacheRefreshTimer = window.setInterval(refreshCachedMessages, 1000);
  }
  if (session.value && !summaryRefreshTimer) {
    // 摘要由独立后台任务低频生成；前端每 30 秒只读取数据库，避免模型调用增加客服等待和成本。
    summaryRefreshTimer = window.setInterval(() => loadSummary(true), 30000);
  }
}

/**
 * 根据当前数据库上下文生成一条 AI 回复建议。
 *
 * @param {boolean} regenerate 是否忽略当前展示结果并重新生成。
 * @returns {Promise<void>} 成功后把建议、摘要和模型信息写入响应式状态。
 */
async function generate(regenerate = false) {
  if (demoMode) { status.value = "演示：使用固定合成回复，未调用模型"; return; }
  busy.value = true;
  error.value = "";
  try {
    suggestion.value = await api(`/api/conversations/${conversationId.value}/ai-replies`, {
      method: "POST",
      body: JSON.stringify({ regenerate }),
    });
  } catch (reason) {
    error.value = reason.message;
  } finally {
    busy.value = false;
  }
}

/**
 * 复制客服确认后的回复草稿，并记录采纳结果；发送由客服在聊天窗口完成。
 *
 * 该函数不会代替客服发送消息。草稿复制成功后，后端保存最终文本和采纳状态，
 * 同时创建待专业客服审核的 Skill 候选；候选未经审核不能进入正式知识库。
 *
 * @returns {Promise<void>} 全部步骤成功后更新页面提示。
 */
async function adopt() {
  const text = suggestion.value.suggested_reply?.trim();
  if (!text) return;
  busy.value = true;
  try {
    if (demoMode) { status.value = "演示：已模拟复制草稿，未发送任何消息"; return; }
    await copyReplyDraft(text);
    await api(`/api/ai-replies/${suggestion.value.suggestion_id}`, {
      method: "PATCH",
      body: JSON.stringify({
        action: "use",
        edited_text: text,
      }),
    });
    await api("/api/admin/skills", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: conversationId.value,
        suggestion_id: suggestion.value.suggestion_id,
        answer_text: text,
      }),
    });
    status.value = "草稿已复制，请手动粘贴并确认发送；已提交待审核 Skill 候选";
  } catch (reason) {
    error.value = reason.message;
  } finally {
    busy.value = false;
  }
}

// 页面挂载后自动连接，客服不需要额外点击“初始化”。
onMounted(initialize);
// 页面被企业微信关闭或切换时清除轮询，避免隐藏页面继续占用请求连接。
onBeforeUnmount(() => {
  if (cacheRefreshTimer) window.clearInterval(cacheRefreshTimer);
  cacheRefreshTimer = 0;
  if (summaryRefreshTimer) window.clearInterval(summaryRefreshTimer);
  summaryRefreshTimer = 0;
});
</script>

<template>
  <main class="sidebar-shell">
    <header class="page-header">
      <div><h1>客服宝助手</h1><p>{{ status }}</p></div>
      <span class="status-dot" :class="{ online: !!session }"></span>
    </header>

    <p v-if="error" class="error-banner">{{ error }}</p>

    <nav class="sidebar-tabs" aria-label="侧边栏内容切换">
      <button :class="{ active: activeTab === 'reply' }" @click="activeTab = 'reply'">回复建议</button>
      <button v-if="conversationSummary.summary_text" :class="{ active: activeTab === 'summary' }" @click="activeTab = 'summary'">会话纪要</button>
    </nav>

    <section v-if="activeTab === 'summary'" class="panel summary-tab-panel">
      <div class="section-head"><h2>会话纪要</h2><span class="summary-live">自动更新</span></div>
      <div class="summary-text markdown-body" v-html="renderMarkdown(conversationSummary.summary_text)"></div>
    </section>

    <section v-else class="panel reply-panel">
      <div class="section-head reply-heading"><h2>回复建议</h2><span v-if="suggestion.model_name" class="model-tag">{{ suggestion.model_name }}</span></div>
      <details v-if="suggestion.summary" class="reply-detail">
        <summary>问题理解</summary>
        <div class="markdown-body" v-html="renderMarkdown(suggestion.summary)"></div>
      </details>
      <textarea v-if="editingReply" v-model="suggestion.suggested_reply" rows="8" aria-label="编辑回复草稿"></textarea>
      <div v-else class="sidebar-reply markdown-body" v-html="renderMarkdown(suggestion.suggested_reply || '')"></div>
      <button :disabled="busy || !suggestion.suggested_reply" @click="editingReply = !editingReply">{{ editingReply ? "预览草稿" : "编辑草稿" }}</button>
      <details v-if="false" class="answer-evidence">
        <summary>{{ suggestion.diagnostics.used_references?.length ? `引用依据 · ${suggestion.diagnostics.used_references.length} 条` : "生成信息 · 未引用知识" }}</summary>
        <div class="diagnostic-content">
          <div class="diagnostic-tags">
            <span>{{ suggestion.diagnostics.intent }}</span>
            <span v-if="suggestion.diagnostics.product_model">{{ suggestion.diagnostics.product_model }}</span>
            <span v-for="domain in suggestion.diagnostics.searched_domain_names" :key="domain">{{ domain }}</span>
          </div>
          <p v-if="suggestion.diagnostics.supplement_reason" class="diagnostic-note">{{ suggestion.diagnostics.supplement_reason }}</p>
        </div>
        <ol v-if="suggestion.diagnostics.used_references?.length" class="evidence-list">
          <li v-for="item in suggestion.diagnostics.used_references" :key="item.reference_id">
            <a v-if="item.source_url" :href="item.source_url" target="_blank" rel="noreferrer">{{ item.title }}</a>
            <strong v-else>{{ item.title }}</strong>
            <small>{{ item.reference_id }}</small>
          </li>
        </ol>
        <p v-else class="no-evidence">本次回复没有采用已发布 Skill 或语雀文档，请人工核对内容。</p>
        <p v-for="warning in suggestion.diagnostics.warnings" :key="warning" class="evidence-warning">{{ warning }}</p>
      </details>
      <div class="button-grid"><button class="primary" :disabled="busy || !conversationId" @click="generate(false)">生成回复</button><button :disabled="busy || !suggestion.suggested_reply" @click="generate(true)">换一个</button></div>
      <button class="primary full" :disabled="busy || !suggestion.suggested_reply" @click="adopt">复制草稿并采纳</button>
    </section>
  </main>
</template>



