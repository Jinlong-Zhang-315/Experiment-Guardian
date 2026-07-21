# Experiment Guardian

Experiment Guardian 是提高实验一致性、可追溯性和风险可见性的治理系统。本仓库当前
已完成 P0 基础骨架、已认证的正式上下文读取、可持久化的训练前确定性配置检查、
Owner 审批、不可变 Run Manifest、S3 实验草稿上传准备和上传对象复核。
它不保证实验一定正确，也不声称能够
完整验证真实训练行为。

## 当前已经具备

* `PASS / NEEDS_APPROVAL / BLOCKED` 训练前检查状态；
* `NOT_REQUIRED / PENDING / APPROVED / REJECTED` 审批状态；
* YAML/JSON 重复键拒绝、规范化配置哈希和无碰撞点分参数 diff；
* LOCKED、APPROVAL_REQUIRED、EXPERIMENT_VARIABLE 确定性判定；
* 从项目上下文到正式实验、artifact、向量记忆和审计的 SQLAlchemy 模型；
* FastAPI 健康/能力接口和 Owner 原子项目初始化接口；
* 六个 P0 MCP 工具的正式接口名称；
* 基于哈希 Token、scope、角色与项目绑定的 `project_get_context`；
* 数据库驱动的 `experiment_check_plan`，保存版本、快照、证据和幂等回执；
* Plan Check 完整保存当时 Context/baseline、Intent、约束和原始配置依据；
* Owner 对待审批计划的一次性批准/拒绝 API，以及不可修改的审批与审计记录；
* `run_manifest_create` 只从历史 Plan Check 快照生成版本化、可重复计算哈希的 Manifest；
* `submission_prepare` 原子创建 `RECEIVED` 草稿和 artifact 声明，返回绑定
  Content-Type 与 SHA-256 checksum 的短期 S3 PUT 地址；
* `submission_finalize` 通过 S3 HEAD 复核对象存在性、大小、Content-Type 和
  SHA-256 checksum，原子写入 `UPLOAD_VERIFIED` 与 `CLOUD_VERIFIED` 证据；
* 六个收敛的 Alembic 迁移和可信本地管理 CLI；
* 提交分析 LangGraph 的固定节点顺序；
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
`submission_prepare`、`submission_finalize` 已接入 CockroachDB；`experiments_query`
会明确返回尚未实现，不会伪造数据。
提交分析工作流持久化、Bedrock 和四个 Web 页面尚未实现。

`UPLOAD_VERIFIED` 仅表示云端读取到的 S3 对象元数据与数据库声明一致，不表示配置、
训练过程或实验结论已经验证正确。finalize 不下载或解析文件，也不会自动启动分析图。

提交分析图目前只有固定拓扑，尚未接通持久化恢复。后续的 `NEEDS_REVIEW` 将作为分析图
的终态交接，而非 LangGraph 原生 `interrupt()`；用户确认仍由独立幂等事务完成。

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
`project:read`、`experiment:check`、`manifest:create`、`submission:create` 和
`submission:finalize` scope；
旧 Token 需要重新签发。使用 `submission_prepare` 前还需配置 `AWS_REGION`、
`S3_BUCKET` 和 AWS SDK 凭据；`submission_finalize` 使用同一配置读取对象元数据。
预签名 URL 不写入数据库。
新签发的 Owner API Token 包含 `plan:approve`；旧 Owner Token 也需要轮换后才能审批。
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

## 目录职责

```text
src/experiment_guardian/
├── domain/          # 纯领域契约和确定性规则，不访问数据库或云服务
├── application/     # 用例编排与端口定义
├── infrastructure/  # CockroachDB、S3、Bedrock 等适配器
├── api/             # FastAPI 路由与 HTTP 协议转换
├── mcp_server/      # 面向本地 Agent 的六个 MCP 工具
└── workflows/       # 提交分析 LangGraph 固定拓扑，持久化恢复尚未接通
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

配置独立测试 Bucket 和 AWS 凭据后，可显式验证真实预签名 PUT 与对象复核：

```bash
RUN_S3_INTEGRATION=1 pytest -q tests/integration/test_s3_storage.py
```

## 下一开发步

1. 只接通可恢复的确定性分析前半程，不提前生成审核回执；
2. 从 `UPLOAD_VERIFIED` 推进配置解析、Manifest 校验、结构化查重和确定性风险；
3. Bedrock、embedding、审核回执、正式实验确认和 Web 继续按独立轮次推进。
