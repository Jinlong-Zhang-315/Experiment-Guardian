# Experiment Guardian

Experiment Guardian 是提高实验一致性、可追溯性和风险可见性的治理系统。本仓库当前
已完成 P0 基础骨架、已认证的正式上下文读取、可持久化的训练前确定性配置检查、
Owner 审批、不可变 Run Manifest、S3 实验草稿上传准备和上传对象复核。
上传复核通过后会同步运行可恢复的确定性分析前半程，完成配置/结果解析、Manifest
一致性校验、项目内查重和风险持久化；随后通过 CockroachDB 事务 Outbox、SQS Standard
和独立 Worker 异步生成受约束的 Bedrock 摘要、Titan V2 embedding 与确定性审核回执，
最终交接到 `NEEDS_REVIEW`。R13 已支持通过认证 REST 接口批准或拒绝草稿；批准事务会
创建正式 Experiment、Metric、Artifact 关联和 CONFIRMED 向量记忆，团队成员随后可通过
MCP 执行结构化过滤优先的精确向量候选查询。
它不保证实验一定正确，也不声称能够
完整验证真实训练行为。

## 当前已经具备

* `PASS / NEEDS_APPROVAL / BLOCKED` 训练前检查状态；
* `NOT_REQUIRED / PENDING / APPROVED / REJECTED` 审批状态；
* YAML/JSON 重复键拒绝、规范化配置哈希和无碰撞点分参数 diff；
* LOCKED、APPROVAL_REQUIRED、EXPERIMENT_VARIABLE 确定性判定；
* 从项目上下文到正式实验、artifact、向量记忆和审计的 SQLAlchemy 模型；
* FastAPI 健康/能力接口和 Owner 原子项目初始化接口；
* 七个 P0 MCP 工具的正式接口名称，包括只读 `submission_get_status`；
* 基于哈希 Token、scope、角色与项目绑定的 `project_get_context`；
* 数据库驱动的 `experiment_check_plan`，保存版本、快照、证据和幂等回执；
* Plan Check 完整保存当时 Context/baseline、Intent、约束和原始配置依据；
* Owner 对待审批计划的一次性批准/拒绝 API，以及不可修改的审批与审计记录；
* `run_manifest_create` 只从历史 Plan Check 快照生成版本化、可重复计算哈希的 Manifest；
* `submission_prepare` 原子创建 `RECEIVED` 草稿和 artifact 声明，返回绑定
  Content-Type 与 SHA-256 checksum 的短期 S3 PUT 地址；
* `submission_finalize` 通过 S3 HEAD 复核对象存在性、大小、Content-Type 和
  SHA-256 checksum，并要求不可变 VersionId，原子写入 `UPLOAD_VERIFIED` 与
  `CLOUD_VERIFIED` 证据；
* 错误对象不会被覆盖；失败后系统为冲突 Artifact 换用新 object key，调用者根据
  `reupload_artifact_ids` 重新获取上传地址，所有失败尝试保留独立审计；
* `submission_finalize` 成功后按固定 S3 `VersionId` 下载 CONFIG/RESULT，重新计算原始
  SHA-256，并使用严格 YAML/JSON 规则解析，单文件上限为 1 MiB；
* 业务表游标驱动的五节点分析前缀：上传证据、解析、Manifest 校验、结构化查重、风险；
* 可解析的不一致保存为阻断型 CRITICAL 风险；完全重复为 MEDIUM、相同运行条件为 LOW；
* 风险完成时同事务创建唯一摘要 Job 和 Outbox，API 进程不直接调用 SQS 或 Bedrock；
* 单并发 Worker 使用 generation、数据库租约和 SQS 可见性超时处理至少一次投递；
* Bedrock Converse 只生成最多 3000 字的解释性纯文本，不能新增/降级风险或改变权限；
* Titan Text Embeddings V2 生成固定 1024 维归一化草稿向量，输入、模型和 hash 可追溯；
* 确定性审核回执突出目标、允许变化、关键结果、风险和证据边界，CRITICAL 不可确认；
* 摘要与审核准备使用两个独立 Job，共用 Outbox、SQS、租约和 generation 恢复协议；
* `NEEDS_REVIEW` 审核决定 API，按 Researcher/Owner/风险等级执行确定性权限门禁；
* 正式确认在单个 CockroachDB 事务中写入 Experiment、Metric、Memory、Artifact 关联、
  审计和幂等回执，事务内不调用 S3 或 Bedrock；
* `experiments_query` 先过滤项目、协议、确认/历史状态，再对最多 200 条候选执行精确余弦
  排序；指定 experiment ID 时返回不调用模型的完整结构化详情；
* 十个收敛的 Alembic 迁移和可信本地管理 CLI；
* Alembic、pytest、Ruff 和 mypy 基础配置。

## 治理边界

* LLM 只能生成候选意图、候选约束、歧义和风险，不能激活 Intent 或绕过确定性规则；
* 约束同时保存 `EXPLICIT/INFERRED` 来源与确认状态，只有 `CONFIRMED` 约束可以强制阻断；
* 本地 Agent 只读取已确认事实并提交草稿，不能修改正式上下文或确认正式实验；
* `CLOUD_VERIFIED`、`LOCAL_ATTESTED`、`USER_PROVIDED` 始终随字段保存和展示；
* 配置一致性检查不等于实验正确性保证，本地 Git、环境和路径信息仅作为声明处理；
* 本地证据明确区分“未采集”和带原因的 `NOT_APPLICABLE`；只有 checkpoint、CUDA 等
  可选字段允许不适用，Git、命令、配置哈希等核心字段不得绕过；
* 云端对收到的配置原始字节重算 SHA-256，与本地声明不一致时强制阻断；
* 正式与探索实验使用不同模式，探索结果不得成为正式 baseline；
* 向量相似度只生成候选证据，执行前必须按项目、确认状态、实验状态和协议过滤。

MCP 工具不接受客户端提交的用户 UUID，调用者来自服务端验证的项目绑定 Token。当前
`project_get_context`、`experiment_check_plan`、`run_manifest_create` 和
`submission_prepare`、`submission_finalize`、`submission_get_status` 和
`experiments_query` 已接入 CockroachDB。正式实验确认只通过 Web 可复用的 REST API
开放，不通过 MCP 暴露给本地 Agent。四个 Web 页面尚未实现；SQS 与 Bedrock 资源仍由
部署环境提供，本仓库尚未创建 AWS 资源。

`UPLOAD_VERIFIED` 仅表示云端读取到的 S3 对象元数据与数据库声明一致；R11 分析进一步
确认固定版本的字节哈希、语法和 Manifest 一致性，但仍不表示训练过程或实验结论正确。
原提交者可以 finalize；原提交者不可用时，项目 Owner 可以使用自己的项目绑定 MCP
Token 恢复该操作。成功和失败审计均记录具体 Token ID、原始 Agent 标识和恢复模式。

提交分析使用 `experiment_submissions.processing_step/workflow_status` 作为唯一恢复依据，
LangGraph 只负责编排。R11 风险节点完成后进入 `PROCESSING/QUEUED/RISK_ANALYSIS`；
R12a 摘要成功后由同一事务创建审核准备 Job；R12b 成功终态为
`NEEDS_REVIEW/COMPLETED/NEEDS_REVIEW`。这是持久化交接状态，不是 LangGraph 原生
`interrupt()`；正式确认由 R13 的独立数据库事务完成。

## 本地环境

项目使用已有 Conda 环境：

```bash
conda activate experiment-guardian
pip install -e .
cp .env.example .env
```

启动 CockroachDB 前，确保 Compose 使用的环境变量已经配置：

```bash
docker compose --env-file .env.cockroach up -d cockroachdb
```

应用当前增量迁移：

```bash
alembic upgrade head
```

`20260721_01` 创建 10 张基础表，`20260721_02` 增加 `plan_checks`，`20260721_03`
补齐历史策略和原始配置快照，`20260721_04` 增加 `approval_records` 和
`run_manifests`，`20260721_05` 增加 `experiment_submissions` 和 `artifacts`。
`20260722_06` 增加上传复核证据字段，并扩展 Submission 状态列。
`20260722_07` 增加分析游标、结构化中间结果和 `submission_risks`。
`20260722_08` 增加 `generated_summary`、`workflow_jobs` 和 `outbox_events`。
`20260722_09` 增加 `submission_embeddings`、确定性审核回执和 Review Job 类型。
`20260722_10` 增加正式 `experiments`、`experiment_metrics`、`memories` 和 Artifact 外键。
后续表按开发阶段通过新 revision 添加，不提前冻结。

## 初始化与检查链路

创建或复用首个 Owner/团队并轮换本地管理 Token：

```bash
experiment-guardian-admin bootstrap-owner \
  --email owner@example.com \
  --name Owner \
  --team-name "Vision Lab"
```

管理 API 使用输出的 API Token，并要求 UUID 格式的 `Idempotency-Key`：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/projects/initialize \
  -H "Authorization: Bearer $API_ACCESS_TOKEN" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @examples/project-initialize.json
```

项目创建后签发项目绑定的 MCP Token：

```bash
experiment-guardian-admin issue-mcp-token \
  --owner-email owner@example.com \
  --project-id "$PROJECT_ID"
```

stdio MCP Server 从 `MCP_ACCESS_TOKEN` 环境变量读取凭据。新签发的 MCP Token 同时具备
`project:read`、`experiment:check`、`manifest:create`、`submission:create`、
`submission:finalize`、`submission:read` 和 `experiment:query` scope；
CLI 返回值会显式列出实际 scopes。旧 Token 需要重新签发。使用 `submission_prepare`
前还需配置 `AWS_REGION`、`S3_BUCKET` 和 AWS SDK 凭据；Bucket 必须开启 Versioning，
`submission_finalize` 使用同一配置读取对象元数据并拒绝无 VersionId 的对象。
预签名 URL 不写入数据库。
新签发的 Owner API Token 包含 `plan:approve` 和 `submission:review`；旧 Owner Token 也需
轮换。可信管理员可用 `add-researcher` 添加最小 Researcher 成员，再用
`issue-api-token` 和带 `--member-email` 的 `issue-mcp-token` 分别签发项目绑定凭据。
API Token 与 MCP Token 按用途隔离，不应把两类 scope 合并到单一 Token。
原始 Token 只在签发时显示一次，数据库只保存 SHA-256，日志和审计记录不得包含原始值。

Owner 批准或拒绝待审批计划：

```bash
curl -X POST \
  "http://127.0.0.1:8000/api/v1/projects/$PROJECT_ID/plan-checks/$PLAN_CHECK_ID/decision" \
  -H "Authorization: Bearer $API_ACCESS_TOKEN" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -H "Content-Type: application/json" \
  -d '{"decision":"APPROVED","decision_reason":"Owner reviewed"}'
```

审核一个已经进入 `NEEDS_REVIEW` 的实验草稿：

```bash
curl -X POST \
  "http://127.0.0.1:8000/api/v1/projects/$PROJECT_ID/submissions/$SUBMISSION_ID/decision" \
  -H "Authorization: Bearer $API_ACCESS_TOKEN" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -H "Content-Type: application/json" \
  -d '{"decision":"APPROVED","decision_reason":"reviewed"}'
```

Researcher 只能审核自己的草稿；Owner 可审核项目内任意草稿。HIGH 只能由 Owner 批准，
CRITICAL/blocking 不能批准但可以拒绝。拒绝时 `decision_reason` 必填。

## 启动入口

FastAPI：

```bash
experiment-guardian-api
```

默认地址为 `http://127.0.0.1:8000`，OpenAPI 文档位于 `/docs`。

本地 MCP stdio Server：

```bash
experiment-guardian-mcp
```

R12b Worker 需要额外配置 `SQS_SUBMISSION_QUEUE_URL`、`BEDROCK_SUMMARY_MODEL_ID`、
`BEDROCK_EMBEDDING_MODEL_ID=amazon.titan-embed-text-v2:0` 和
`EMBEDDING_DIMENSION=1024`。未配置摘要模型或队列时 API 仍可可靠写入 Outbox，但 Worker
会以明确错误拒绝启动：

```bash
experiment-guardian-worker
```

队列应为带 DLQ/redrive policy 的 SQS Standard Queue；可见性超时默认 120 秒，Worker
长轮询默认 20 秒。本轮不自动创建 Queue、DLQ、IAM Role 或 Bedrock 模型访问权限。

## 目录职责

```text
src/experiment_guardian/
├── domain/          # 纯领域契约和确定性规则，不访问数据库或云服务
├── application/     # 用例编排、正式确认事务与结构化/向量查询
├── infrastructure/  # CockroachDB、S3、Bedrock 等适配器
├── api/             # FastAPI 路由与 HTTP 协议转换
├── mcp_server/      # 面向本地 Agent 的七个 MCP 工具
├── worker.py        # R12b Outbox 调度及摘要/审核准备 Worker 入口
└── workflows/       # LangGraph 固定八节点拓扑，数据库游标负责恢复
```

项目维护文档：

* [`docs/DEVELOPMENT_LOG.md`](docs/DEVELOPMENT_LOG.md)：按轮次记录每次更新、修复和验证；
* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)：当前已实现、骨架和规划模块的文本框架图；
* [`docs/ITERATION_STATUS.md`](docs/ITERATION_STATUS.md)：每轮交付内容和下一轮收敛计划。

## 验证

```bash
pytest
ruff check src tests migrations
```

默认测试不操作外部数据库或 S3。需要验证真实 CockroachDB 迁移、审批、
Manifest 和 Submission 事务链路时，
使用随机临时数据库执行：

```bash
RUN_COCKROACH_INTEGRATION=1 pytest -q tests/integration/test_plan_check_cockroach.py
```

配置已开启 Versioning 的独立测试 Bucket 和 AWS 凭据后，可显式验证真实预签名 PUT、
防覆盖、对象复核和指定 VersionId GET：

```bash
RUN_S3_INTEGRATION=1 pytest -q tests/integration/test_s3_storage.py
```

默认严格 Fake 已覆盖 SQS/Bedrock 语义。配置专用测试资源和凭据后，可分别显式验收：

```bash
RUN_SQS_INTEGRATION=1 pytest -q tests/integration/test_async_aws.py
RUN_BEDROCK_INTEGRATION=1 pytest -q tests/integration/test_async_aws.py
RUN_BEDROCK_EMBEDDING_INTEGRATION=1 pytest -q tests/integration/test_async_aws.py
```

## 下一开发步

R14 只实现四个收敛 Web 页面、AWS 演示资源和最终双角色演示；不在同一轮增加自动训练、
自动改代码、复杂成员邀请、向量索引或 baseline 自动晋升。
