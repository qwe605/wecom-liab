# 第三方来源与分发范围

## 原始工程

上游来源：[WecomTeam/MiniProgramSample](https://github.com/WecomTeam/MiniProgramSample)。
原始工程使用 MIT License；根目录 LICENSE 保留其版权及许可全文。
上游作者的贡献、提交身份与版权不因本仓库的修改而转归项目维护者。

## 依赖和外部能力

- Python 包通过 `requirements.txt` / `requirements-embedding.txt` 引用；安装时取得其各自许可证。
- Vue、Vite 等前端依赖通过 `frontend/package.json` 与锁文件引用；其许可证由各包提供，不被本项目 MIT 声明替代。
- MySQL 为单独运行的数据库服务，遵循其自身许可与发行条款。
- 企业微信 JS-SDK 与会话内容存档 SDK 遵循平台协议。仓库不分发 SDK 二进制、私钥或复制的完整 SDK 手册。
  接入前应阅读 [企业微信开发文档](https://developer.work.weixin.qq.com/document/)。
- 可选 BAAI 模型由部署者另行下载，许可证和使用条件以相应模型发布页为准；仓库不分发模型权重。
- 语雀、模型 API 和企业微信服务均由部署者使用自己的授权账号，仓库不包含共享账号或可借用凭据。

## 只读语雀实现

当前 `backend/integrations/yuque_readonly_server.py` 是项目内的最小只读适配实现，
基于官方 MCP SDK 和语雀 HTTP API 契约，注册三种读取工具。
旧的缺少可核验授权记录的内嵌完整 MCP 工程已从公开快照中移除。
