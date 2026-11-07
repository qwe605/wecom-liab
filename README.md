# WeCom AI 客服助手 · wecom-liab

面向企业微信侧边栏的 AI 辅助回复与人工审核知识闭环。此仓库用于代码与架构展示，
不提供可登录的生产系统，不附带客户聊天、企业知识库、服务器凭据或官方闭源 SDK。

## 功能与技术

- **辅助回复**：按当前会话提取客户意图，检索资料，生成可编辑草稿；仅复制草稿，由客服手动粘贴并确认发送。
- **消息同步**：官方会话内容存档 SDK 在独立子进程拉取、解密；全局游标、消息幂等、整页事务保证一致性。
- **知识治理**：增量会话摘要、精确来源证据、待审核 Skill、乐观锁与不可变审核事件；只有人工发布知识可用于回答。
- **权限隔离**：客服侧边栏、审核台、管理员聊天记录/导出及用户管理使用独立入口与后端角色校验。
- **检索**：MySQL FULLTEXT；可选独立 ONNX INT8 / hnswlib 服务，异常回退全文检索。公开版默认关闭向量服务，未宣称线上压测或效果提升数据。
- **技术栈**：Python 3.12、FastAPI、SQLAlchemy 2、Alembic、MySQL 8、Vue 3、Vite、MCP。

## 快速预览：无需真实数据

```powershell
git clone https://github.com/qwe605/wecom-liab.git
cd wecom-liab/frontend
npm ci
npm run dev
```

打开 [合成数据演示](http://127.0.0.1:5173/sidebar-demo/)。人物、型号与聊天均为合成示例；
演示不访问真实会话、不调用企业微信或模型，也不代表完整端到端验证。

## 本地后端

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# 默认 Docker 开发密码仅用于本机回环地址，不适用于生产。
docker compose -f deploy/local/docker-compose.database.yml up -d
python -m alembic stamp head
python -m backend.services.admin_service --username admin --wecom-userid demo-admin --name 管理员
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8787
```

管理员密码通过终端隐藏交互输入，不放在命令行参数或 shell 历史里。新建 Docker 数据卷自动执行初始化 SQL 后才能
`stamp head`；已有库应执行 `python -m alembic upgrade head`，不要对未初始化的空库直接标记版本。

配置通过**环境变量**传入，主应用不自动读取 `.env`。示例见
`deploy/local/.env.example`；如用 Compose 自定义密码，需同时更新进程的 `DATABASE_URL`。
开发环境未配置 `APP_SECRET` 时使用进程随机密钥，重启后旧令牌失效；多进程部署必须显式配置一致的强密钥。
生产使用 `KEFUBAO_ENV=production` 并设置至少 32 字符的随机 `APP_SECRET`，见
[部署说明](deploy/tencentcloud/README_DEPLOY.md)。

完整企业微信流程还需要自有测试企业、授权会话内容存档、官方 SDK/私钥、可信域名、
AI 模型服务以及授权知识库。没有这些配置时，合成前端演示与离线测试仍可独立运行。

## 会话权限

侧边栏只能连接后台可信归档/资料同步已建立成员关系的会话；浏览器传入的群 ID、客户 ID 或成员字典不构成授权。新会话尚未同步时应等待可信同步，不能通过前端请求自行加入。
已禁用或仅由归档发现的未开通账号，需要管理员明确启用，OAuth 不会覆盖禁用状态。首次改密账号完成改密前不能访问会话消息或 AI 接口。

## 只读知识服务

`backend/integrations/yuque_readonly_server.py` 使用官方 MCP SDK，仅暴露文档读取、
引用解析和文档搜索三个工具。启动前设置 `YUQUE_PERSONAL_TOKEN`、
`YUQUE_PRODUCT_INDEX_REPO`、`YUQUE_PRODUCT_INDEX_DOC`、`YUQUE_PRODUCT_DOCS_REPO`：

```powershell
python -m backend.integrations.yuque_readonly_server
```

默认监听 `127.0.0.1:8003/mcp`，不允许绑定公网；Token 应只具备授权资料的最小读取权限。
公开分类为合成示例，实际部署者须自行提供有权使用的目录和资料。

## 目录

| 路径 | 内容 |
| --- | --- |
| `backend/controllers/` | API、请求边界和鉴权依赖 |
| `backend/services/` | 业务编排、摘要、回复与审核 |
| `backend/database/` | ORM、Repository、初始化 SQL 与 Alembic |
| `backend/integrations/` | 企业微信、模型、只读 MCP 与检索适配 |
| `backend/tests/` | 合成数据回归测试 |
| `frontend/` | Vue 侧边栏、审核及管理页面 |
| `deploy/` | 通用部署模板，不含真实域名与验证文件 |
| `docs/` | 公开架构与数据使用边界 |

## 验证

```powershell
python scripts/check_public_content.py
python -m unittest discover -s backend/tests -v
python -m compileall -q backend
cd frontend
npm ci
npm test
npm run build
```

CI 运行上述离线测试、前端构建与全历史密钥扫描。不依赖真实客户数据。

## 来源、授权与使用边界

本项目基于 [WecomTeam/MiniProgramSample](https://github.com/WecomTeam/MiniProgramSample)，
公开仓库以清理后的独立代码快照初始化，不带旧仓库历史。当前主体为 FastAPI + Vue 应用；保留原始 MIT 版权声明，不将上游示例宣称为原创。
参见 [LICENSE](LICENSE)、[第三方说明](THIRD_PARTY_NOTICES.md)、
[架构说明](docs/ARCHITECTURE.md) 和 [数据与隐私边界](docs/PRIVACY.md)。

公开仓库不授予任何企业数据、企业商标、第三方服务账号或闭源 SDK 的使用权。
