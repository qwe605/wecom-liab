/** 企业微信 JS-SDK 适配层，页面只接收稳定的会话标识。 */
import { api } from "./index";

/**
 * 把企业微信回调式 JS-SDK 转换成可以使用 await 的 Promise。
 *
 * @param {string} name 企业微信 JS-SDK 方法名，例如 `getContext`。
 * @param {object} payload 传给企业微信的参数；读取类接口通常为空对象。
 * @returns {Promise<object>} 企业微信返回的原始结果，仅在本适配层内使用。
 * @throws {Error} 不在企业微信客户端、接口无权限或 SDK 返回失败时抛出。
 */
function invoke(name, payload = {}) {
  return new Promise((resolve, reject) => {
    // 普通浏览器没有 wx.invoke，提前给出明确提示，避免后续出现 undefined 错误。
    if (!window.wx?.invoke) return reject(new Error("请从企业微信客户端打开"));
    window.wx.invoke(name, payload, (result) => {
      const message = result.err_msg || result.errmsg || "";
      // 企业微信成功标志通常为“方法名:ok”，其他内容一律按失败处理。
      message.includes(":ok") ? resolve(result) : reject(new Error(message || `${name} 调用失败`));
    });
  });
}

/**
 * 使用后端签名配置企业级和应用级 JS-SDK 权限。
 *
 * 签名必须针对当前完整页面 URL 生成；CorpSecret 只保存在后端。`wx.config` 配置
 * 企业身份，`wx.agentConfig` 配置当前自建应用身份，二者成功后才能读取客户会话。
 *
 * @returns {Promise<object>} 后端签名结果，供调用方调试当前 CorpID/AgentID。
 * @throws {Error} 签名请求、企业配置或应用配置任一步失败时抛出。
 */
export async function configureWeCom() {
  // URL 签名不包含 hash；保留 query 是因为企业微信签名要求与实际页面地址一致。
  const cleanUrl = location.href.split("#")[0];
  const signed = await api(`/api/wecom/js-signature?url=${encodeURIComponent(cleanUrl)}`);
  // 第一阶段：配置企业级 JS-SDK。
  await new Promise((resolve, reject) => {
    window.wx.config({
      beta: true,
      debug: false,
      appId: signed.corp_id,
      timestamp: signed.timestamp,
      nonceStr: signed.nonce_str,
      signature: signed.corp_signature,
      jsApiList: ["getContext", "getCurExternalContact", "getCurExternalChat"],
    });
    window.wx.ready(resolve);
    window.wx.error((error) => reject(new Error(JSON.stringify(error))));
  });
  // 第二阶段：配置当前自建应用的 Agent 权限。
  await new Promise((resolve, reject) => {
    window.wx.agentConfig({
      corpid: signed.corp_id,
      agentid: signed.agent_id,
      timestamp: signed.timestamp,
      nonceStr: signed.nonce_str,
      signature: signed.agent_signature,
      jsApiList: ["getContext", "getCurExternalContact", "getCurExternalChat"],
      success: resolve,
      fail: (error) => reject(new Error(JSON.stringify(error))),
    });
  });
  return signed;
}

/**
 * 识别侧边栏当前打开的是外部群聊还是客户单聊。
 *
 * @returns {Promise<object>} 归一化后的会话连接请求，群聊包含 chat_id，单聊包含
 * external_userid；后端不会依赖企业微信字段名差异。
 * @throws {Error} 当前入口不是客户聊天工具栏或应用缺少相关 JS-SDK 权限时抛出。
 */
export async function detectConversation() {
  const context = await invoke("getContext");
  if (context.entry === "group_chat_tools") {
    const chat = await invoke("getCurExternalChat");
    return {
      conversation_type: "external_group_chat",
      chat_id: chat.chatId || chat.chatid,
      entry: context.entry,
      raw_context: context,
      raw_chat: chat,
    };
  }
  const contact = await invoke("getCurExternalContact");
  return {
    conversation_type: "single_external_contact",
    external_userid: contact.userId || contact.external_userid,
    entry: context.entry,
    raw_context: context,
    raw_contact: contact,
  };
}

/**
 * 复制经客服确认的草稿；不调用任何企业微信发送接口。
 *
 * @param {string} content 客服可继续编辑并手动粘贴的回复正文。
 * @returns {Promise<void>} 剪贴板写入完成；这不表示消息已经发送。
 * @throws {Error} 浏览器不支持剪贴板、非安全上下文或权限被拒绝时抛出。
 */
export async function copyReplyDraft(content) {
  if (!navigator.clipboard?.writeText) {
    throw new Error("当前环境无法自动复制，请手动选中草稿并复制；系统不会代发消息");
  }
  await navigator.clipboard.writeText(String(content));
}
