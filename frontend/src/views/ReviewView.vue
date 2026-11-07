<script setup>
/** 独立 Skill 审核台：登录、筛选、编辑、通过和驳回候选知识。 */
import { computed, nextTick, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, clearToken, saveToken, token } from "../api";
import { displayStatus } from "../api/status";
import TaxonomyTreeNode from "../components/TaxonomyTreeNode.vue";

// 审核台页面状态：登录信息、候选列表、筛选条件和请求反馈相互独立。
const username = ref("");
const password = ref("");
const currentUser = ref(null);
const items = ref([]);
const status = ref("pending");
const statusLabel = displayStatus;
// 轻量 Markdown 渲染：审核正文只读展示，限制为常用文本语法，避免引入重量级编辑器。
function renderMarkdown(value) {
  return String(value || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/^### (.*)$/gm, "<h3>$1</h3>").replace(/^## (.*)$/gm, "<h2>$1</h2>").replace(/^# (.*)$/gm, "<h1>$1</h1>").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\n/g, "<br>");
}
const reviewKeyword = ref("");
const reviewTaxonomy = ref({ knowledge_domain: "", product_category: "", product_subcategory: "", product_model: "", topic: "" });
const reviewPage = ref({ page: 1, pages: 0, total: 0, page_size: 30 });
const selectedReview = ref(null);
const detailCache = new Map();
const error = ref("");
const loading = ref(false);
const viewMode = ref("review");
const taxonomyTree = ref([]);
const catalogTree = ref([]);
const wikiEditor = ref(null);
const wikiPages = ref([]);
const wikiPage = ref({ page: 1, pages: 0, total: 0, page_size: 30 });
const wikiKeyword = ref("");
const selectedTaxonomy = ref(null);
const expandedTaxonomyKeys = ref(new Set());
const reviewDetail = ref(null);
const policyRules = ref([]);
const policyStatus = ref("pending");
const policyKeyword = ref("");
const policyPage = ref({ page: 1, pages: 0, total: 0, page_size: 30 });
const selectedPolicy = ref(null);
const policyDetail = ref(null);
const policyNotice = ref("");
const router = useRouter();

// 五级分类字段与后端 taxonomy_path 的 level 保持一致，页面不维护第二套分类模型。
const taxonomyFields = [
  ["knowledge_domain", "知识域"],
  ["product_category", "产品大类"],
  ["product_subcategory", "产品小类"],
  ["product_model", "具体型号"],
  ["topic", "叶子主题"],
];
const knowledgeDomains = ref([]);

/** @param {Array<object>} path 后端返回的分类路径。 @returns {object} 审核表单使用的五级名称。 */
function taxonomyForm(path = []) {
  return Object.fromEntries(taxonomyFields.map(([level]) => [level, path.find((part) => part.level === level)?.name || ""]));
}

/**
 * 把审核表单转换为后端有序路径；通用知识允许省略三个产品层级。
 *
 * @param {object} item 当前 Skill 候选。
 * @returns {Array<object>} 待校验的完整五级或两级短路径。
 */
function taxonomyPath(item) {
  const form = item.taxonomy_form || {};
  const domain = knowledgeDomains.value.find((item) => item.name === form.knowledge_domain);
  const levels = form.product_category || form.product_subcategory || form.product_model
    ? taxonomyFields
    : [taxonomyFields[0], taxonomyFields[4]];
  return levels
    .filter(([level]) => (form[level] || "").trim())
    .map(([level]) => {
      const name = (form[level] || "").trim();
      const original = (item.taxonomy_path || []).find((part) => part.level === level && part.name === name);
      return { level, ...(level === "knowledge_domain" && domain ? { code: domain.code } : original?.code ? { code: original.code } : {}), name };
    });
}

/** @returns {Array<object>} 把目录树压平为带缩进层级的只读行。 */
const flatTaxonomyTree = computed(() => {
  const rows = [];
  const visit = (nodes, depth = 0) => (nodes || []).forEach((node) => {
    rows.push({ ...node, depth });
    visit(node.children, depth + 1);
  });
  visit(taxonomyTree.value);
  return rows;
});

/** @returns {object} 各分类层级已经发布的名称集合，供审核输入联想复用。 */
const taxonomyOptions = computed(() => {
  const result = Object.fromEntries(taxonomyFields.map(([level]) => [level, new Set()]));
  flatTaxonomyTree.value.forEach((node) => result[node.level]?.add(node.name));
  // 专业语雀目录作为审核输入基线；已发布 Skill 的实际叶子仍动态追加。
  catalogTree.value.forEach((category) => {
    result.product_category.add(category.name);
    (category.children || []).forEach((child) => result.product_subcategory.add(child.name));
  });
  return Object.fromEntries(Object.entries(result).map(([key, values]) => [key, [...values].sort()]));
});

/** @returns {boolean} 当前登录账号是否可以进入管理员专属的聊天记录页面。 */
const isAdmin = computed(() => currentUser.value?.role === "admin");

/** @returns {boolean} 终态驳回记录只读，其他状态可按各自状态机保存。 */
const reviewEditable = computed(() => selectedReview.value?.status !== "rejected");

/** @returns {boolean} 草案可由审核员编辑，已发布规则只能由管理员修改。 */
const policyEditable = computed(() => Boolean(
  selectedPolicy.value
  && (selectedPolicy.value.status === "pending" || (selectedPolicy.value.status === "published" && isAdmin.value))
));

/**
 * 使用后台账号密码登录审核台，并在成功后加载默认待审核列表。
 *
 * @returns {Promise<void>} 登录完成后保存令牌并刷新候选列表。
 */
async function login() {
  loading.value = true;
  error.value = "";
  try {
    const result = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username: username.value, password: password.value }) });
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
  } finally { loading.value = false; }
}

/**
 * 校验已有令牌和审核角色，再加载 Skill 候选。
 *
 * @returns {Promise<void>} reviewer/admin 加载审核数据，其他角色返回权限提示。
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
    if (!["reviewer", "admin"].includes(currentUser.value?.role)) {
      throw new Error("仅审核人员或管理员可以访问 Skill 审核管理台");
    }
    knowledgeDomains.value = (await api("/api/admin/skill-taxonomy/domains")).items || [];
    catalogTree.value = (await api("/api/admin/skill-taxonomy/catalog")).items || [];
    await load();
    await loadTaxonomyTree();
  } catch (reason) {
    clearToken();
    currentUser.value = null;
    error.value = reason.message;
  }
}

/**
 * 按当前状态筛选条件加载 Skill 候选。
 *
 * @returns {Promise<void>} 成功后替换页面候选列表；401 时清除失效令牌。
 */
async function load() {
  if (!token()) return;
  loading.value = true;
  try {
    const params = new URLSearchParams({ status: status.value, page: "1", page_size: String(reviewPage.value.page_size) });
    if (reviewKeyword.value.trim()) params.set("keyword", reviewKeyword.value.trim());
    Object.entries(reviewTaxonomy.value).forEach(([level, value]) => {
      if (value.trim()) params.set(level, value.trim());
    });
    const result = await api(`/api/admin/skills?${params}`);
    items.value = result.items || [];
    reviewPage.value = result;
    selectedReview.value = null;
    if (items.value.length) {
      await openReviewDetail(items.value[0]);
      if (items.value[1]) api(`/api/admin/skills/${items.value[1].id}`).then((next) => detailCache.set(items.value[1].id, next)).catch(() => {});
    }
  } catch (reason) {
    error.value = reason.message;
    if (reason.message.includes("401")) clearToken();
  } finally { loading.value = false; }
}

/** 加载审核队列指定页；筛选参数始终由服务端执行。 */
async function loadReviewPage(page) {
  loading.value = true;
  error.value = "";
  try {
    const params = new URLSearchParams({ status: status.value, page: String(page), page_size: String(reviewPage.value.page_size) });
    if (reviewKeyword.value.trim()) params.set("keyword", reviewKeyword.value.trim());
    Object.entries(reviewTaxonomy.value).forEach(([level, value]) => value.trim() && params.set(level, value.trim()));
    const result = await api(`/api/admin/skills?${params}`);
    items.value = result.items || [];
    reviewPage.value = result;
    selectedReview.value = null;
    if (items.value.length) {
      await openReviewDetail(items.value[0]);
      if (items.value[1]) api(`/api/admin/skills/${items.value[1].id}`).then((next) => detailCache.set(items.value[1].id, next)).catch(() => {});
    }
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/** 按需读取一条候选的正文、分类、审核意见和来源状态。 */
async function openReviewDetail(item) {
  loading.value = true;
  error.value = "";
  try {
    const detail = detailCache.get(item.id) || await api(`/api/admin/skills/${item.id}`);
    detailCache.set(item.id, detail);
    selectedReview.value = {
      ...detail,
      taxonomy_form: taxonomyForm(detail.taxonomy_path),
      review_opinion: detail.review_opinion || "",
      reason_code: "",
      expires_at_input: detail.expires_at ? detail.expires_at.slice(0, 16) : "",
      source_open: false,
      source_loading: false,
      source: null,
      review_events_open: false,
      review_events_loading: false,
      review_events: null,
    };
    // 详情区使用独立滚动容器。切换 Skill 时必须回到标题和正文顶部，
    // 否则会保留上一条记录的滚动位置，让用户误以为正文无法查看或编辑。
    await nextTick();
    reviewDetail.value?.scrollTo({ top: 0, behavior: "auto" });
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/** 清空审核筛选并回到待审核第一页。 */
async function resetReviewFilters() {
  status.value = "pending";
  reviewKeyword.value = "";
  reviewTaxonomy.value = taxonomyForm();
  await loadReviewPage(1);
}

/** @returns {Promise<void>} 加载由已发布 Skill 实时聚合的知识目录。 */
async function loadTaxonomyTree() {
  if (!token()) return;
  try {
    const result = await api("/api/admin/skill-wiki/tree");
    taxonomyTree.value = result.tree || [];
    if (!expandedTaxonomyKeys.value.size) {
      expandedTaxonomyKeys.value = new Set(
        taxonomyTree.value.map((node) => `${node.level}:${node.code}`)
      );
    }
  } catch (reason) {
    error.value = reason.message;
  }
}

/** 切换一个目录节点；Set 必须替换实例，Vue 才会稳定刷新递归子树。 */
function toggleTaxonomyNode(key) {
  const next = new Set(expandedTaxonomyKeys.value);
  next.has(key) ? next.delete(key) : next.add(key);
  expandedTaxonomyKeys.value = next;
}

/** 将整棵目录统一收起或展开，避免逐级重复点击。 */
function setAllTaxonomyExpanded(expanded) {
  const keys = [];
  const visit = (nodes, path = []) => (nodes || []).forEach((node) => {
    const nextPath = [...path, node];
    if (node.children?.length) keys.push(nextPath.map((item) => `${item.level}:${item.code}`).join("/"));
    visit(node.children, nextPath);
  });
  visit(taxonomyTree.value);
  expandedTaxonomyKeys.value = new Set(expanded ? keys : []);
}

/** 选择任意分类层级并分页加载该分支下的已发布 Skill。 */
async function selectTaxonomy(payload) {
  selectedTaxonomy.value = payload;
  wikiEditor.value = null;
  await loadWikiPages(1);
}

/** 按当前分类和关键词从服务端分页读取轻量列表。 */
async function loadWikiPages(page = 1) {
  if (!selectedTaxonomy.value && !wikiKeyword.value.trim()) {
    wikiPages.value = [];
    wikiPage.value = { page: 1, pages: 0, total: 0, page_size: 30 };
    return;
  }
  loading.value = true;
  error.value = "";
  try {
    const params = new URLSearchParams({ page: String(page), page_size: String(wikiPage.value.page_size) });
    if (wikiKeyword.value.trim()) params.set("keyword", wikiKeyword.value.trim());
    (selectedTaxonomy.value?.path || []).forEach((item) => params.set(item.level, item.code));
    const result = await api(`/api/admin/skill-wiki/pages?${params}`);
    wikiPages.value = result.items || [];
    wikiPage.value = result;
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/** 打开知识体系叶子编辑器，复用审核页现有分类表单。 */
async function editWikiSkill(skill) {
  loading.value = true;
  error.value = "";
  try {
    const detail = await api(`/api/admin/skill-wiki/pages/${skill.id}`);
    wikiEditor.value = {
    ...detail,
    taxonomy_form: taxonomyForm(skill.taxonomy_path),
    review_opinion: "",
    reason_code: "",
    expires_at_input: detail.expires_at ? detail.expires_at.slice(0, 16) : "",
  };
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/** 保存已发布 Skill，或将不再适用的知识归档。 */
async function saveWikiSkill(decision = "published") {
  if (!wikiEditor.value) return;
  if (decision === "archived" && !window.confirm("归档后该知识将退出知识树和 AI 召回，确定继续吗？")) return;
  loading.value = true;
  error.value = "";
  try {
    const item = wikiEditor.value;
    await api(`/api/admin/skills/${item.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        decision,
        note: item.review_opinion || (decision === "archived" ? "从知识体系归档" : "编辑已发布知识"),
        question_text: item.title,
        answer_text: item.content,
        taxonomy_path: taxonomyPath(item),
        expires_at: item.expires_at_input ? new Date(item.expires_at_input).toISOString() : null,
        expected_version: item.version,
        reason_codes: decision === "archived" ? [item.reason_code || "outdated"] : [],
      }),
    });
    wikiEditor.value = null;
    await loadTaxonomyTree();
    await loadWikiPages(wikiPage.value.page);
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/**
 * 展开或收起候选的来源会话；原始消息仅在首次展开时按需加载。
 *
 * @param {object} item 当前 Skill 候选。
 * @returns {Promise<void>} 加载成功后在当前卡片内展示精确证据消息。
 */
async function toggleSource(item) {
  item.source_open = !item.source_open;
  if (!item.source_open || item.source) return;
  item.source_loading = true;
  error.value = "";
  try {
    item.source = await api(`/api/admin/skills/${item.id}/source`);
  } catch (reason) {
    item.source_open = false;
    error.value = reason.message;
  } finally {
    item.source_loading = false;
  }
}

/** 按需读取不可变审核时间线，避免列表查询携带全部快照。 */
async function toggleReviewEvents(item) {
  item.review_events_open = !item.review_events_open;
  if (!item.review_events_open || item.review_events) return;
  item.review_events_loading = true;
  error.value = "";
  try {
    const result = await api(`/api/admin/skills/${item.id}/review-events`);
    item.review_events = result.items || [];
  } catch (reason) {
    item.review_events_open = false;
    error.value = reason.message;
  } finally {
    item.review_events_loading = false;
  }
}

/**
 * 保存人工编辑结果，并执行驳回或发布操作。
 *
 * @param {object} item 当前候选及审核人员修改后的标题、正文和意见。
 * @param {"published"|"rejected"} decision 人工审核决定。
 * @returns {Promise<void>} 后端处理完成后重新加载列表，避免展示旧状态。
 */
async function review(item, decision) {
  if (["rejected", "archived"].includes(decision) && !item.reason_code) {
    error.value = item.status === "archived" ? "删除归档结果前请选择原因" : "删除候选前请选择驳回原因";
    if (decision === "archived") error.value = "归档前请选择操作原因";
    return;
  }
  if (decision === "archived" && item.status === "published" && !window.confirm("归档后该知识将退出知识树和 AI 召回，确定继续吗？")) return;
  loading.value = true;
  try {
    const publishing = decision === "published";
    await api(`/api/admin/skills/${item.id}${publishing ? "/publish" : ""}`, {
      method: publishing ? "POST" : "PATCH",
      body: JSON.stringify({
        decision,
        note: item.review_opinion || "",
        question_text: item.title,
        answer_text: item.content,
        taxonomy_path: taxonomyPath(item),
        expires_at: item.expires_at_input ? new Date(item.expires_at_input).toISOString() : null,
        expected_version: item.version,
        reason_codes: ["rejected", "archived"].includes(decision) ? [item.reason_code] : [],
      }),
    });
    await loadReviewPage(reviewPage.value.page);
    await loadTaxonomyTree();
  } catch (reason) { error.value = reason.message; } finally { loading.value = false; }
}

/** 快速审核：拒绝默认使用“其他”作为系统原因，避免打断审核节奏。 */
async function quickApprove() { if (selectedReview.value) await review(selectedReview.value, "published"); }
async function quickReject() { if (selectedReview.value) { selectedReview.value.reason_code = "other"; selectedReview.value.review_opinion = "快速审核拒绝"; await review(selectedReview.value, "rejected"); } }

/** 保存审核工作区中的已发布或已归档 Skill，不错误套用候选审核按钮。 */
async function saveReviewEdit(item) {
  if (!item || !["published", "archived"].includes(item.status)) return;
  if (!item.review_opinion.trim()) {
    error.value = "保存修改前请填写修改说明";
    return;
  }
  // 归档原地编辑沿用 archived 状态并记录原因，确保不会因编辑意外回到召回链路。
  if (item.status === "archived" && !item.reason_code) item.reason_code = "other";
  await review(item, item.status);
}

/** 管理员把归档知识恢复为待审核，禁止绕过审核直接发布。 */
async function reopenArchived(item) {
  if (!item || item.status !== "archived") return;
  if (!window.confirm("恢复后将回到待审核队列，仍需重新审核发布。确定继续吗？")) return;
  await review(item, "pending");
}

/** 进入审核规则视图时先幂等补齐历史事件，再加载待确认规则草案。 */
async function openPolicyMode() {
  viewMode.value = "policy";
  await compilePolicyRules(false);
  await loadPolicyRules(1);
}

/** 将尚未关联的历史通过/驳回事件补编译为待确认规则 Skill。 */
async function compilePolicyRules(showNotice = true) {
  loading.value = true;
  error.value = "";
  policyNotice.value = "";
  try {
    const result = await api("/api/admin/review-policy/compile", { method: "POST" });
    if (showNotice) policyNotice.value = `已扫描 ${result.scanned || 0} 条审核结果，新建 ${result.created || 0} 条规则草案。`;
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/** 按状态和关键词分页读取审核规则 Skill，不与业务 Skill 混排。 */
async function loadPolicyRules(page = 1) {
  loading.value = true;
  error.value = "";
  try {
    const params = new URLSearchParams({ status: policyStatus.value, keyword: policyKeyword.value.trim(), page: String(page), page_size: String(policyPage.value.page_size) });
    const result = await api(`/api/admin/review-policy/rules?${params}`);
    policyRules.value = result.items || [];
    policyPage.value = result;
    selectedPolicy.value = null;
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/** 打开规则正文和产生它的原始审核事件，并重置独立详情滚动区。 */
async function openPolicyDetail(item) {
  loading.value = true;
  error.value = "";
  try {
    const detail = await api(`/api/admin/review-policy/rules/${item.id}`);
    selectedPolicy.value = { ...detail, note: "", reason_code: "" };
    await nextTick();
    policyDetail.value?.scrollTo({ top: 0, behavior: "auto" });
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/** 保存、发布或终止审核规则；发布规则必须由管理员明确确认。 */
async function savePolicyRule(decision) {
  const item = selectedPolicy.value;
  if (!item) return;
  if (!item.title.trim() || !item.content.trim()) {
    error.value = "审核规则标题和正文不能为空";
    return;
  }
  if (["rejected", "archived"].includes(decision) && !item.reason_code) {
    error.value = "终止或归档规则前请选择原因";
    return;
  }
  const confirms = {
    published: "发布后该规则将参与后续知识候选提炼，确定已经人工核对并完成脱敏吗？",
    rejected: "终止后该规则草案只保留审核历史，不再参与确认，确定继续吗？",
    archived: "归档后该规则立即停止影响候选提炼，确定继续吗？",
    pending: "",
  };
  if (confirms[decision] && !window.confirm(confirms[decision])) return;
  loading.value = true;
  error.value = "";
  try {
    await api(`/api/admin/review-policy/rules/${item.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        decision,
        note: item.note || "",
        question_text: item.title,
        answer_text: item.content,
        expected_version: item.version,
        reason_codes: ["rejected", "archived"].includes(decision) ? [item.reason_code] : [],
      }),
    });
    await loadPolicyRules(policyPage.value.page);
  } catch (reason) {
    error.value = reason.message;
  } finally {
    loading.value = false;
  }
}

/** 管理员恢复归档规则时只回到待确认草案，不直接重新发布。 */
async function reopenPolicyRule() {
  if (!selectedPolicy.value || !window.confirm("恢复后规则回到待确认状态，不会立即影响候选提炼。确定继续吗？")) return;
  await savePolicyRule("pending");
}

/** 清除共享登录状态并返回登录表单。 */
function logout() {
  clearToken();
  currentUser.value = null;
    items.value = []; router.replace("/login");
    selectedReview.value = null;
    taxonomyTree.value = [];
    wikiPages.value = [];
    wikiEditor.value = null;
    policyRules.value = [];
    selectedPolicy.value = null;
}

// 已有令牌时先向后端确认身份，不能仅凭浏览器中存在 Token 展示管理界面。
function handleQuickReviewKeydown(event) {
  if (!currentUser.value || !selectedReview.value || loading.value) return;
  if (event.key === "ArrowRight") { event.preventDefault(); quickApprove(); }
  if (event.key === "ArrowLeft") { event.preventDefault(); quickReject(); }
}
onMounted(() => { window.addEventListener("keydown", handleQuickReviewKeydown); initialize(); });
</script>

<template>
  <main class="quick-review-shell">
    <header class="quick-review-header">
      <div><h1>Skill 快速审核</h1></div>
      <div class="quick-review-count">待审核 <strong>{{ reviewPage.total }}</strong> 条</div>
      <nav v-if="currentUser" class="admin-nav quick-admin-nav"><RouterLink to="/records/">聊天记录</RouterLink><RouterLink to="/users/">用户管理</RouterLink><button class="quick-logout" @click="logout" title="退出登录">退出登录</button></nav>
    </header>
    <section v-if="!currentUser" class="login-panel panel"><h2>后台账号登录</h2><label>账号<input v-model="username" autocomplete="username" /></label><label>密码<input v-model="password" type="password" autocomplete="current-password" @keyup.enter="login" /></label><button class="primary" :disabled="loading" @click="login">登录</button></section>
    <section v-else class="quick-review-card" tabindex="0" @keydown.right.prevent="quickApprove" @keydown.left.prevent="quickReject">
      <template v-if="selectedReview">
        <div class="quick-score">预评分 <strong>{{ selectedReview.quality_score ?? '-' }}</strong></div>
        <label>标题<input v-model="selectedReview.title" readonly /></label>
        <section class="quick-content"><h2>知识内容</h2><div class="quick-markdown" v-html="renderMarkdown(selectedReview.content)"></div></section>
        <div class="quick-review-actions"><button class="quick-reject" :disabled="loading" @click="quickReject">拒绝</button><button class="quick-approve" :disabled="loading" @click="quickApprove">审核通过</button></div>
        <p class="quick-hint">快捷键：← 拒绝　→ 通过</p>
      </template>
      <div v-else class="quick-empty">{{ loading ? "正在加载待审核内容…" : "没有待审核内容" }}</div>
    </section>
    <p v-if="error" class="error-banner quick-error">{{ error }}</p>
  </main>
</template>


