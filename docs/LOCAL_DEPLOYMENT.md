# Experiment Guardian 本地部署

更新时间：2026-07-27
适用模式：`DEPLOYMENT_MODE=local`

本地模式复用完整领域规则，只替换人类认证、对象存储、队列和模型适配器。它不提供密码
登录，也不削弱 Plan Check、不可变 Manifest、固定 Artifact 版本、风险权限或审计规则。

## 安全边界

`local_owner` 会把任何成功访问登录入口的人认证为 `LOCAL_OWNER_EMAIL` 对应的数据库 Owner。
因此 API、Web、CockroachDB 和 MinIO 端口默认全部绑定 `127.0.0.1`，不得通过 `0.0.0.0`、
反向代理、端口转发或公网隧道暴露。Settings 在 `production` 环境会拒绝 local 模式启动。
FastAPI 只接受 `WEB_PUBLIC_BASE_URL`/`WEB_FRONTEND_URL` 中明确配置的回环 Host，Compose
Nginx 只接受 `127.0.0.1` 和 `localhost`；其他 Host 会在签发 Owner Session 前被拒绝。

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

`database-init` 创建数据库，`migration` 单独升级到当前 head `20260727_23`，`minio-init` 创建 Bucket、
开启并验证 Versioning，`local-init` 再创建初始业务数据。API/Worker 只有在这些一次性任务成功
后才启动，迁移不会被多个长期服务并发执行。

打开 `http://127.0.0.1:5173`，点击登录。后端查找 `owner@example.com`，验证唯一团队中的
Owner Membership 后创建正常的 HttpOnly Session Cookie 和 CSRF Cookie。

## 启用内部治理 Agent

内部治理 Agent 默认关闭，不影响既有本地业务闭环。需要启用时在 `.env.local` 增加：

```text
AGENT_ENABLED=true
AGENT_PROVIDER=bailian
BAILIAN_AGENT_MODEL=<支持流式 Function Calling 的百炼模型>
AGENT_COST_CURRENCY=CNY
# 以下两个可选费率必须同时配置，只用于模型运行估算，不代表百炼账单。
AGENT_INPUT_COST_PER_MILLION_TOKENS=
AGENT_OUTPUT_COST_PER_MILLION_TOKENS=
```

使用 Compose 的 `agent` profile 启动专用 Worker：

```bash
docker compose --env-file .env.local --profile agent up -d --build
docker compose --env-file .env.local logs -f agent-worker
```

登录后从 Web 的“治理 Agent”页进入。Agent 可以读取项目和实验状态、比较和诊断实验、创建
Context/Intent/Constraint 草稿，以及准备策略发布、Plan 决策和 Submission 决策的不可变提案。
准备提案不会修改正式记录；用户必须在 Web 中查看冻结证据、差异和风险，并通过近期认证后
确认。确认时服务端重新校验版本、权限和业务状态，并复用原业务事务执行正式操作。Agent 不能
执行任意 SQL、绕过 Plan Check、直接修改运行记录、训练或修改代码。每轮运行使用当前 Web
Session 对应的真实用户身份，工具执行时再次校验 Team Membership 和项目权限。浏览器断开
SSE 不会取消后台运行，重连会从 `Last-Event-ID` 继续读取持久化事件。

本地模式只允许 `AGENT_PROVIDER=bailian`；配置 `bedrock` 会在启动校验阶段失败，不会实例化
AWS Client 或要求 AWS 凭据。项目 Owner 可从 Agent 页打开“模型观测”，查看 7/30/90 天的
调用、token、延迟、失败、重试和配置费率估算；项目成员可从消息打开单个 Run 的有界调用
元数据。两个视图都不会返回提示词、回答正文、工具输入或工具输出。

百炼适配器将原生 Function Calling 的 auto 回合和严格 JSON 最终回合分离，避免部分 Qwen
模型在 `json_object` 模式下把工具调用写进正文。HTTPX 使用 SOCKS 可选依赖，主机或容器显式
配置 SOCKS 代理时不会因缺少传输依赖在请求前失败。

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
2. 浏览器访问 Web 并登录；设置页默认确认版本绑定的人类可读说明，并在“结构化 JSON
   （高级视图）”中核对初始 Context、Intent、三条约束、版本和确认人。
3. 从 `local-init` 日志取得 `project_id`，执行 `issue-mcp-token`，用 stdio MCP 调用
   `project_get_context`，确认人类可读说明与完整 Context/Intent/Constraint 同时返回，且版本
   与 Web 一致。执行和治理判断仍只使用结构化字段。
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
# RC 预检：不调用真实模型，不产生模型费用。
python scripts/verify_r16_local.py \
  --base-url http://127.0.0.1:5173 --env-file .env.local

# 不访问真实模型或对象存储的完整应用链：Plan -> Manifest -> Submission ->
# DB Queue -> Mock Bailian -> Review -> Experiment/Memory。
pytest tests/integration/test_local_pipeline.py tests/integration/test_database_queue.py

RUN_MINIO_INTEGRATION=1 pytest tests/integration/test_minio_storage.py

RUN_COCKROACH_INTEGRATION=1 \
TEST_COCKROACH_URL='cockroachdb+psycopg://root@127.0.0.1:26257/defaultdb?sslmode=disable' \
pytest tests/integration/test_agent_local_cockroach.py

RUN_BAILIAN_INTEGRATION=1 pytest tests/integration/test_bailian_optional.py

# 先设置 AGENT_ENABLED=true、BAILIAN_AGENT_MODEL 并启动 agent profile；该命令产生模型费用。
python scripts/verify_r16_local.py \
  --base-url http://127.0.0.1:5173 --env-file .env.local \
  --live-bailian --report /tmp/experiment-guardian-r16-local.json
```

默认测试用 HTTP mock 覆盖百炼协议和失败，不需要真实 Key；只有显式设置
`RUN_BAILIAN_INTEGRATION=1` 或 `RUN_BAILIAN_AGENT_INTEGRATION=1` 才会访问真实百炼。可选
测试会读取 `.env.local`，进程环境变量仍具有更高优先级。MinIO 测试也会读取本地 S3 配置，
`MINIO_TEST_*` 仍可显式覆盖。若修改过默认 CockroachDB 映射端口，应同步修改
`TEST_COCKROACH_URL`；RC 脚本会自动从 env 文件读取 `COCKROACH_SQL_PORT`。

## 停止与数据

```bash
docker compose --env-file .env.local stop
docker compose --env-file .env.local start
docker compose --env-file .env.local --profile agent logs -f api worker agent-worker
```

CockroachDB 和 MinIO 使用命名卷，普通 stop/start 不丢数据。不要随意执行 `down -v`；它会
永久删除本地数据库和 Artifact。单机模式不提供多节点 HA、远程暴露或自动备份。

Compose 默认通过 `COCKROACH_STORE_SIZE=200GiB` 为本地单节点设置明确的 store 容量口径。
该值不会预分配 200GiB，也不是宿主磁盘扩容；它用于避免共享大文件系统接近容量阈值时，
CockroachDB 按整个文件系统总量计算可用比例而误阻止本项目的小型 schema backfill。调整时
必须保证该值高于 CockroachDB 实际数据量且低于底层文件系统真实可用空间。生产或多节点部署
应按实际磁盘规划容量，不能照搬本地默认值。
