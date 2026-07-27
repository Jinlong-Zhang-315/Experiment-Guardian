# Experiment Guardian

Experiment Guardian 是提高实验一致性、可追溯性和风险可见性的治理系统。它通过版本化
项目策略、训练前确定性检查、不可变 Run Manifest、可恢复 Submission 分析、人工确认和
结构化过滤优先的实验查询，减少团队实验条件漂移。

它不保证实验一定正确，不完整验证真实训练行为，也不把本地 Agent 声明描述成云端事实。

## R14 MVP 能力

完整纵向链路：

```text
Owner 通过 Cognito 或仅限本机的 local_owner 登录并发布正式 Context / Intent / Constraint 版本
-> Researcher 通过 OAuth MCP 读取相同上下文
-> YAML/JSON Plan Check 返回 PASS / NEEDS_APPROVAL / BLOCKED
-> Owner 对审批型变化作最终决定
-> 生成冻结历史版本和证据的 Run Manifest
-> 用户自行运行实验
-> Agent 上传配置、结果、日志和说明到 Versioned S3
-> 云端校验、查重、风险、摘要、embedding 和确定性审核回执
-> Researcher/Owner 按风险权限人工确认
-> CockroachDB 单事务创建正式 Experiment、Metric、Memory 和 Artifact 关联
-> 团队按结构化条件和向量候选查询完整追溯链
```

当前实现包括：

* Owner/Researcher 实时角色与项目权限；
* Context、Intent、Constraint 全版本、确认人、生效时间、修改原因和 supersedes 关系；
* 正式策略同时保存结构化事实与版本绑定的人类可读说明，来源哈希不一致时隐藏过期文本；
* YAML/JSON 重复键拒绝、JSON 标量语义、严格类型比较、无碰撞参数路径；
* LOCKED、APPROVAL_REQUIRED、EXPERIMENT_VARIABLE 确定性判定；
* 核心本地证据不可 `NOT_APPLICABLE`，所有证据保留来源、时间和采集工具；
* Plan Check 保存当时 Context/baseline、Intent、约束、配置和证据快照；
* 不可修改 ApprovalRecord、不可变 Manifest 和所有写操作幂等协议；
* S3 防覆盖上传、SHA-256、固定 VersionId、失败审计和可恢复重传；
* 数据库游标驱动的解析、Manifest 校验、结构化查重和风险工作流；
* CockroachDB Outbox、SQS 或数据库队列、租约 Worker、Bedrock/百炼摘要和 1024 维 embedding；
* High/Critical 强制展开的短审核回执，Critical 不能批准；
* 正式确认单事务和 project/protocol/status 等结构化过滤先行的向量候选查询；
* React/Vite 五页 Web 工作台，治理 Agent 页内含 Policy Bundle 草稿及 Policy/Plan/Submission 提案工作台；
* Cognito Managed Login、服务端 Web Session、CSRF、撤销和近期认证；
* MCP 2025-11-25 OAuth Resource Server、RFC 9728、PKCE、RFC 8707 和预注册客户端；
* AWS CloudFront/WAF/ALB/ECS/S3/SQS/Cognito/Bedrock Terraform 部署定义；
* CockroachDB、MinIO、数据库队列、百炼和 local_owner 的单机 Compose 部署模式；
* R15e-b 内部治理 Agent：候选研究报告的每条 finding 会形成独立 Research Memory，并在
  team/project/status/来源有效性等结构化过滤后进行语义召回；结果始终是
  `CANDIDATE_EVIDENCE`，不能发布治理事实或触发正式操作。
* R15e-c Agent provider parity：治理 Agent 可在云端选择百炼或 Bedrock ConverseStream；
  两者共用严格响应契约、工具目录、权限和状态机，不做静默 provider 回退。Owner 可查看
  项目级调用量、token、延迟、失败、重试及按调用时配置费率冻结的费用估算。

MCP 只暴露七个工具：

```text
project_get_context
experiment_check_plan
run_manifest_create
submission_prepare
submission_finalize
submission_get_status
experiments_query
```

`project_get_context` 同时返回 `human_readable` 和完整结构化 Context/Intent/Constraints。
自然语言说明只用于阅读和 Agent 理解，任何执行、检查和治理决定仍以结构化字段为准。

本地 Agent 不能通过 MCP 修改正式策略、批准计划、确认正式实验、运行训练或修改代码。

## 人类登录

Web 不实现应用密码。浏览器跳转 Amazon Cognito User Pool Managed Login，FastAPI 完成 OIDC
Authorization Code + PKCE 交换并创建服务端 `web_sessions`。浏览器只持有 Secure、HttpOnly、
SameSite=Lax Cookie，不接触 Cognito Token。

```text
idle timeout       8 hours
absolute timeout   7 days
recent auth        10 minutes via Cognito prompt=login
```

策略发布、Plan Check 最终决定和 Owner High-risk Submission 批准需要近期认证。首次 Cognito 登录
只绑定管理员已创建、邮箱已验证且恰好属于一个团队的现有 User；不会自助创建成员。

云端模式使用上述 Cognito 流程。本地模式使用 `local_owner`：后端按配置邮箱查询现有 User，
确认其在唯一 Team 中确实拥有 Owner Membership，再创建同一种 Web Session、CSRF Cookie、
实时 RBAC 和审计记录；没有“权限恒为 true”的旁路。

`local_owner` 只允许 `development`/`test`，默认只监听 `127.0.0.1`。任何能访问该本地服务的
人都将获得配置用户的 Owner 权限，禁止把它暴露到局域网或公网。云端安全边界详见
[`docs/R14_SECURITY.md`](docs/R14_SECURITY.md)。

## 远程 MCP OAuth

AWS 演示使用 Streamable HTTP OAuth，不使用静态 MCP Token：

* `/.well-known/oauth-protected-resource/mcp` 提供 RFC 9728 元数据；
* Cognito 提供 OIDC discovery、Authorization Code + PKCE；
* authorization/token 请求必须携带 `resource=<MCP URL>`；
* Cognito Public App Client 与 callback URI 必须预注册；
* 当前 FastMCP 全局门槛要求预注册客户端申请完整七个 scope；
* Access Token resource audience、client、scope、User sub、Membership、本地 Client/Grant 均校验；
* 本地 Client/Grant 撤销立即生效；
* R14 不支持 Dynamic Client Registration 或 Client ID Metadata Documents。

本地开发仍可使用 stdio + 数据库哈希 `MCP_ACCESS_TOKEN`。

## 本地一键部署

本地模式不需要 Cognito、SQS、AWS Access Key、AWS Region 或 Bedrock Model ID，唯一允许的
外部云依赖是阿里云百炼。先配置百炼 Key 和两个模型：

```bash
cp .env.local.example .env.local
# 编辑 .env.local 中 BAILIAN_API_KEY、BAILIAN_SUMMARY_MODEL、
# BAILIAN_EMBEDDING_MODEL；embedding 模型必须输出 1024 维。
docker compose --env-file .env.local up -d --build
docker compose --env-file .env.local ps -a
```

默认 Web：`http://127.0.0.1:5173`，API：`http://127.0.0.1:8000`，MinIO Console：
`http://127.0.0.1:9001`。`migration` 只执行一次 Alembic，`minio-init` 创建 Bucket 并开启
Versioning，`local-init` 幂等创建 Owner、Team、Project 和首版策略。

单独重复初始化：

```bash
docker compose --env-file .env.local run --rm local-init
docker compose --env-file .env.local logs local-init
```

完整操作、验收和故障排查见 [`docs/LOCAL_DEPLOYMENT.md`](docs/LOCAL_DEPLOYMENT.md)。
双表示的数据关系、降级和 CockroachDB 能力取舍见
[`docs/POLICY_DUAL_REPRESENTATION.md`](docs/POLICY_DUAL_REPRESENTATION.md)。
内部实验治理 Agent 的能力边界、工具目录、上下文压缩、确认协议和分期计划见
[`docs/INTERNAL_GOVERNANCE_AGENT_PLAN.md`](docs/INTERNAL_GOVERNANCE_AGENT_PLAN.md)；
当前完成 R15e-c Agent provider parity 与模型运行观测。报告仍由用户明确选择 Experiment；每条
finding 使用确定性模板形成独立候选记忆，embedding 由可恢复 Worker 异步生成。报告和正式
Experiment Memory 均不受索引失败影响；所有提案仍需独立 Web 确认。Bedrock Agent 强制使用
Structured Outputs JSON Schema，不接受仅靠提示词约束 JSON 的降级路径。

启用本地治理 Agent 时，在 `.env.local` 设置：

```text
AGENT_ENABLED=true
AGENT_PROVIDER=bailian
BAILIAN_AGENT_MODEL=<支持 Function Calling 的百炼模型>
# 可选：输入/输出每百万 token 的配置费率，仅用于估算，不是云平台账单。
AGENT_COST_CURRENCY=CNY
AGENT_INPUT_COST_PER_MILLION_TOKENS=
AGENT_OUTPUT_COST_PER_MILLION_TOKENS=
```

然后带 `agent` profile 启动。Agent Worker 与 Submission Worker 独立部署：

```bash
docker compose --env-file .env.local --profile agent up -d --build
```

## 本地源码开发

项目要求 Python 3.12：

```bash
conda activate experiment-guardian
pip install -e .
cp .env.example .env
docker compose --env-file .env.cockroach up -d cockroachdb
alembic upgrade head
experiment-guardian-api
```

默认 API：`http://127.0.0.1:8000`，OpenAPI：`/docs`。

当前 Alembic head 为 `20260727_23`：

```text
01 foundation/context
02-03 plan checks and complete snapshots
04 approvals/manifests
05 submissions/artifacts
06 upload verification
07 deterministic analysis
08 async summary/outbox
09 embedding/review receipt
10 formal experiments/memories
11 Cognito binding/web sessions/OIDC transactions
12 pre-registered MCP OAuth clients/grants
13 local backends/outbox terminal status/model provider metadata
14 version-bound human-readable policy narratives
15 durable read-only governance Agent conversations/runs/tool evidence/events
16 deterministic Agent analysis context summaries/model-call purpose
17 append-only full Policy Bundle drafts/revisions
18 immutable Agent Policy publish action proposals
19 Agent Plan Check decision proposals
20 Agent Submission decision proposals
21 immutable Agent research reports
22 candidate Research Memory and recoverable embeddings
23 Agent provider/model, latency and configured-rate cost observability
```

初始化现有团队和首个项目仍可使用可信本地 CLI/API：

```bash
experiment-guardian-admin bootstrap-owner \
  --email owner@example.com --name Owner --team-name "Vision Lab"

curl -X POST http://127.0.0.1:8000/api/v1/projects/initialize \
  -H "Authorization: Bearer $API_ACCESS_TOKEN" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @examples/project-initialize.json
```

stdio MCP：

```bash
experiment-guardian-admin issue-mcp-token \
  --owner-email owner@example.com --project-id "$PROJECT_ID"
MCP_ACCESS_TOKEN="$MCP_ACCESS_TOKEN" experiment-guardian-mcp
```

Worker 按配置集中装配 S3/SQS/Bedrock 或 MinIO/数据库队列/百炼：

```bash
experiment-guardian-worker
```

启用治理 Agent 后，另行运行专用 Worker：

```bash
experiment-guardian-agent-worker
```

## Web 源码开发

Cloud 模式的 Web 登录需要 Cognito；local 模式由后端 `local_owner` 创建正常 Session。前端
本身不保存任何 Cognito 或百炼凭据。

```bash
cd web
npm ci
npm run dev
```

Vite 默认地址：`http://127.0.0.1:5173`，并把 `/api` 代理到 `127.0.0.1:8000`。端口冲突时
可用 `VITE_API_PROXY_TARGET=http://127.0.0.1:8788 npm run dev -- --port 5174` 指向备用 API；
npm 脚本显式加载 `vite.config.ts`，不会被本地残留的编译产物覆盖。

Cloud 模式必须配置：

```text
WEB_PUBLIC_BASE_URL=http://127.0.0.1:8000
WEB_FRONTEND_URL=http://127.0.0.1:5173
COGNITO_ISSUER_URL=...
COGNITO_DOMAIN=...
COGNITO_WEB_CLIENT_ID=...
COGNITO_WEB_CLIENT_SECRET=...
WEB_OIDC_STATE_KEY=...
WEB_CSRF_SECRET=...
```

## AWS 部署

部署定义位于 `infra/terraform/`：

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan
```

Cockroach Cloud 是外部托管依赖；Terraform 不创建数据库。完整步骤见
[`docs/R14_DEPLOYMENT.md`](docs/R14_DEPLOYMENT.md)。最终双角色验收见
[`docs/R14_DEMO.md`](docs/R14_DEMO.md)。

Terraform 创建 MCP Cognito client 后，还必须在数据库绑定单一项目：

```bash
experiment-guardian-admin register-mcp-oauth-client \
  --owner-email owner@example.com \
  --project-id "$PROJECT_ID" \
  --client-id "$COGNITO_MCP_CLIENT_ID" \
  --name "Codex demo client"
```

## 验证

后端：

```bash
pytest
ruff check src tests migrations
mypy src/experiment_guardian
```

前端：

```bash
cd web
npm run lint
npm run test
npm run build
npx playwright install --with-deps chromium
npm run test:e2e
```

Terraform：

```bash
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform validate
```

公开部署：

```bash
python scripts/verify_r14_deployment.py --base-url https://guardian.example.com
```

默认 pytest 不访问真实 CockroachDB、MinIO/S3、SQS、Bedrock 或百炼；相关测试通过
`RUN_*_INTEGRATION` 环境开关显式执行。

## 目录

```text
src/experiment_guardian/
├── domain/          纯领域契约与确定性规则
├── application/     用例、事务、Web auth/management、分析和查询
├── infrastructure/  CockroachDB、Cognito/MCP OAuth、S3/MinIO、SQS/DB Queue、Bedrock/百炼
├── api/             FastAPI 协议边界
├── mcp_server/      七个 MCP 工具与 OAuth Resource Server
├── workflows/       LangGraph 编排，数据库游标负责恢复
└── worker.py        Outbox 调度、SQS/数据库队列消费和模型处理 Worker

web/                 React/Vite 四页工作台
infra/terraform/     AWS/Cognito 部署定义
demo/r14/            最终演示固定输入
scripts/             部署验收脚本
docs/                架构、迭代、日志、安全、部署和演示文档
```

当前框架图：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
每轮实现：[`docs/ITERATION_STATUS.md`](docs/ITERATION_STATUS.md)
完整开发日志：[`docs/DEVELOPMENT_LOG.md`](docs/DEVELOPMENT_LOG.md)
