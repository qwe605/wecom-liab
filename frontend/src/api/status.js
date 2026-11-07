/* 项目计划与后台共用的三态展示映射。数据库原始状态仍由接口传输。 */
export const displayStatus = (value) => ({ published: "已完成", completed: "已完成", pending: "进行中", archived: "进行中", rejected: "已完成", "": "未开始" }[value] || value || "未开始");
export const statusClass = (value) => ({ published: "status-completed", completed: "status-completed", pending: "status-in-progress", archived: "status-in-progress", rejected: "status-completed" }[value] || "status-not-started");
