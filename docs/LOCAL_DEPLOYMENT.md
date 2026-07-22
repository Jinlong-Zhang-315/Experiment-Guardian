# Experiment Guardian 本地部署

更新时间：2026-07-23  
适用模式：`DEPLOYMENT_MODE=local`

本地模式复用完整领域规则，只替换人类认证、对象存储、队列和模型适配器。它不提供密码
登录，也不削弱 Plan Check、不可变 Manifest、固定 Artifact 版本、风险权限或审计规则。

## 安全边界

`local_owner` 会把任何成功访问登录入口的人认证为 `LOCAL_OWNER_EMAIL` 对应的数据库 Owner。
因此 API、Web、CockroachDB 和 MinIO 端口默认全部绑定 `127.0.0.1`，不得通过 `0.0.0.0`、
反向代理、端口转发或公网隧道暴露。Settings 在 `production` 环境会拒绝 local 模式启动。

百炼 API Key 仅注入 API/Worker 容器环境，不进入浏览器、数据库或仓库。`.env.local` 已被
Git 忽略；不要把真实 Key 写入 `.env.local.example`。

## 首次启动

依赖 Docker Engine 和 Docker Compose v2：

```bash
cp .env.local.example .env.local
```

编辑以下三项，embedding 模型必须实际返回 1024 维：

```text
BAILIAN_API_KEY=...
BAILIAN_SUMMARY_MODEL=...
BAILIAN_EMBEDDING_MODEL=...
```

这些配置为空时 Settings 会在 API/Worker 启动前明确失败，不会退回 Bedrock、伪造摘要或补齐
向量。

启动并查看一次性任务：

```bash
docker compose --env-file .env.local up -d --build
docker compose --env-file .env.local ps -a
docker compose --env-file .env.local logs migration minio-init local-init
curl -fsS http://127.0.0.1:8000/api/v1/health
```

`database-init` 创建数据库，`migration` 单独升级到 `20260722_13`，`minio-init` 创建 Bucket、
开启并验证 Versioning，`local-init` 再创建初始业务数据。API/Worker 只有在这些一次性任务成功
后才启动，迁移不会被多个长期服务并发执行。

打开 `http://127.0.0.1:5173`，点击登录。后端查找 `owner@example.com`，验证唯一团队中的
Owner Membership 后创建正常的 HttpOnly Session Cookie 和 CSRF Cookie。

## 幂等初始化

Compose 首次启动自动执行。需要手工重复时运行：

```bash
docker compose --env-file .env.local run --rm local-init
```

等价的容器内命令是：

```bash
experiment-guardian-admin bootstrap-local \
  --email owner@example.com \
  --name "Local Owner" \
  --team-name "Local Experiment Team" \
  --project-config examples/project-initialize.json
```

命令复用正式项目初始化服务，以邮箱和配置哈希生成稳定幂等键。重复执行返回同一 User、Team
和 Project，不创建全局超级管理员，也不生成密码。

## 完整业务验收

1. `docker compose ... ps -a` 确认 `migration`、`minio-init`、`local-init` 为退出码 0，API、
   Worker、Web、CockroachDB、MinIO healthy/running。
2. 浏览器访问 Web 并登录；设置页确认初始 Context、Intent、三条约束、版本和确认人。
3. 从 `local-init` 日志取得 `project_id`，执行 `issue-mcp-token`，用 stdio MCP 调用
   `project_get_context`，确认 Context/Intent/Constraint 版本与 Web 一致。
4. 提交修改 `dataset.protocol` 的配置，确认 Plan Check 为 BLOCKED；提交只修改 backbone 的
   配置，Owner 在 Web 计划页批准后创建不可变 Manifest。
5. 调用 `submission_prepare`，按返回的全部 required headers 向 MinIO 预签名 PUT URL 上传
   CONFIG、RESULT、LOG、可选 NOTE，再调用 `submission_finalize`。
6. Worker 从 CockroachDB Outbox 原子 claim，完成固定 VersionId 读取、确定性分析、百炼摘要、
   1024 维 embedding 和审核回执；刷新实验审核页直到 `NEEDS_REVIEW`。
7. Owner 确认允许的草稿；在实验查询页核对 Experiment、Metric、Memory、Artifact、Submission、
   Manifest、Plan、Intent、Context 全追溯链，并执行相似实验查询。
8. 再次对同一 idempotency key、同一 finalize 和同一确认动作重试，确认不重复创建正式记录。

MCP Token 示例：

```bash
docker compose --env-file .env.local run --rm api \
  experiment-guardian-admin issue-mcp-token \
  --owner-email owner@example.com --project-id "$PROJECT_ID"
```

## 本地基础设施验收

```bash
# 不访问真实模型或对象存储的完整应用链：Plan -> Manifest -> Submission ->
# DB Queue -> Mock Bailian -> Review -> Experiment/Memory。
pytest tests/integration/test_local_pipeline.py tests/integration/test_database_queue.py

RUN_MINIO_INTEGRATION=1 \
MINIO_TEST_ENDPOINT=http://127.0.0.1:9000 \
MINIO_TEST_BUCKET=experiment-guardian-test \
MINIO_TEST_ACCESS_KEY=experiment-guardian \
MINIO_TEST_SECRET_KEY=change-this-local-secret \
pytest tests/integration/test_minio_storage.py

RUN_BAILIAN_INTEGRATION=1 pytest tests/integration/test_bailian_optional.py
```

默认测试用 HTTP mock 覆盖百炼协议和失败，不需要真实 Key；只有显式设置
`RUN_BAILIAN_INTEGRATION=1` 才会访问真实百炼。该可选测试会读取 `.env.local`，进程环境变量
仍具有更高优先级。

## 停止与数据

```bash
docker compose --env-file .env.local stop
docker compose --env-file .env.local start
docker compose --env-file .env.local logs -f api worker
```

CockroachDB 和 MinIO 使用命名卷，普通 stop/start 不丢数据。不要随意执行 `down -v`；它会
永久删除本地数据库和 Artifact。单机模式不提供多节点 HA、远程暴露或自动备份。
