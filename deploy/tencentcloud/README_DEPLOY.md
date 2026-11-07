# 腾讯云部署说明

服务器目录统一为 `/opt/kefubao/wecom-miniprogram`。

## 1. 准备 MySQL

使用 MySQL 8，并创建 `kefubao` 数据库和独立账号，字符集为 `utf8mb4`。把 SQLAlchemy 地址写入 `.env`：

```text
DATABASE_URL=mysql+pymysql://USER:PASSWORD@127.0.0.1:3306/kefubao?charset=utf8mb4
```

首次部署或本次版本升级执行：

```bash
# 先备份云数据库，再执行结构迁移（包含 Skill 每日配额所需 created_at 字段）
mysqldump --single-transaction -h DB_HOST -u DB_USER -p kefubao > /opt/kefubao/backup-$(date +%F-%H%M).sql
.venv/bin/python -m alembic upgrade head
```

应用只通过 `DATABASE_URL` 连接云数据库；不要把云数据库密码写入仓库、systemd
文件或前端构建产物。将 `DB_HOST` 替换为云 MySQL 私网地址，安全组仅放行应用服务器。

## 2. 安装应用

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
```

将 `deploy/tencentcloud/env.example` 复制为 `.env` 并填写真实凭据，文件权限建议设为 `600`。

## 3. 创建管理员

```bash
.venv/bin/python -m backend.services.admin_service \
  --username admin \
  --wecom-userid ADMIN_WECOM_USERID \
  --name 管理员
```

密码通过终端隐藏交互输入，不写进命令行参数或 shell 历史。

## 4. 安装服务

复制 systemd 和 Nginx 配置后执行：

```bash
# 先复制本目录的 systemd 文件，并将 nginx-assistant.example.conf 的域名改为自有域名
sudo systemctl daemon-reload
sudo systemctl enable --now kefubao-wecom-auth.service
sudo systemctl enable --now kefubao-yuque-mcp.service
sudo systemctl enable --now kefubao-message-sync.service
sudo systemctl enable --now kefubao-profile-sync.timer
sudo systemctl enable --now kefubao-summary-refresh.timer
sudo nginx -t && sudo systemctl reload nginx
```

## 5. 验证

```bash
curl https://assistant.example.com/api/health
systemctl status kefubao-yuque-mcp.service
systemctl status kefubao-wecom-auth.service
journalctl -u kefubao-message-sync.service -n 100 --no-pager
journalctl -u kefubao-profile-sync.service -n 100 --no-pager
journalctl -u kefubao-summary-refresh.service -n 100 --no-pager
journalctl -u kefubao-wecom-auth.service -n 100 --no-pager
```

- 客服侧边栏：`https://assistant.example.com/sidebar/`
- Skill 审核台：`https://assistant.example.com/review/`
- 聊天记录管理：`https://assistant.example.com/records/`
- API 文档：`https://assistant.example.com/api/docs`

企业微信后台的可信域名、OAuth 回调域名和聊天工具栏地址必须与上述 HTTPS 域名一致。

## 6. 上线前检查

```bash
# 确认迁移已到最新版本
.venv/bin/python -m alembic current
# 确认后端实际读取的是云库而非本机默认库
# 通过受控运维渠道检查数据库配置，不向终端或日志输出连接串
# 验证 Skill 新字段和每日配额索引
mysql --defaults-extra-file=/opt/kefubao/secrets/mysql.cnf -e \
  'SHOW COLUMNS FROM kefubao.skills LIKE "created_at"; SHOW INDEX FROM kefubao.skills WHERE Key_name="idx_skills_created_at";'
```

完成以上检查后即可直接切换 Nginx/API 流量；代码、迁移和环境变量契约已经准备好，
只需填入云服务器真实凭据并执行迁移，不需要修改业务代码。

## 公开版本边界

所有域名和知识库标识均为示例；本项目不提供可登录的线上演示。
语雀服务为本项目的最小只读实现。`/opt/kefubao/secrets/yuque-mcp.env` 需设置
`YUQUE_PERSONAL_TOKEN`、`YUQUE_PRODUCT_INDEX_REPO` 和 `YUQUE_PRODUCT_DOCS_REPO`。
参见 [数据边界](../../docs/PRIVACY.md)。
