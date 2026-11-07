# Public repository development rules

本仓库是授权代码的脱敏展示版本。不得恢复企业原始文档、员工姓名、真实成员标识、聊天数据、知识库快照、部署域名或任何凭据。

## 工作方式

- 修改前检查 Git 状态与相关测试，不覆盖开发者未提交的修改。
- 若根目录存在 `.codegraph/`，先使用 CodeGraph 定位代码；未索引时无需建立索引。
- 保持 `Controller -> Service -> Repository / Integration` 分层。
- 会话权限只能来自后台可信同步，客户端上下文不是授权证据。
- 禁用账号不得被 OAuth 或会话连接重新启用；首次改密状态由后端强制执行。
- 测试和演示只使用虚构身份、型号和消息，不连接生产系统。
- 第三方来源与许可证必须保留；不提交 SDK 二进制、模型权重或复制的完整外部手册。
- 新增逻辑应提供中文说明、函数 docstring 和对应回归测试。

## 提交前

```text
python scripts/check_public_content.py
python -m unittest discover -s backend/tests -v
python -m compileall -q backend
cd frontend
npm ci
npm test
npm run build
```

同时审阅 Git diff；密钥与依赖检查由 CI 执行。扫描不能替代业务隐私人工审查。
