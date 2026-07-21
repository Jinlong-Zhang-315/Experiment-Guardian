# Experiment Guardian

Experiment Guardian 是提高实验一致性、可追溯性和风险可见性的治理系统。本仓库当前
已完成 P0 基础骨架和第一个已认证读取切片，重点固定领域状态、追溯数据模型、训练前
确定性规则、正式上下文初始化和 MCP 读取边界。它不保证实验一定正确，也不声称能够
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
* 首个阶段性 Alembic 迁移和可信本地管理 CLI；
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
* 正式与探索实验使用不同模式，探索结果不得成为正式 baseline；
* 向量相似度只生成候选证据，执行前必须按项目、确认状态、实验状态和协议过滤。

MCP 工具不接受客户端提交的用户 UUID，调用者来自服务端验证的项目绑定 Token。当前仅
`project_get_context` 已接入 CockroachDB；其他五个工具会明确返回尚未实现，不会伪造数据。
S3、Bedrock、CockroachDB checkpoint 和四个 Web 页面尚未实现。

提交分析图可从最后成功的分析步骤恢复；`NEEDS_REVIEW` 是分析图的终态交接，并非
LangGraph 原生 `interrupt()`。用户确认由独立、幂等的数据库事务完成。

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

应用首个基础迁移：

```bash
alembic upgrade head
```

该迁移只创建身份、项目、正式上下文、Intent、约束、幂等和审计表。后续表按开发阶段
通过新的 revision 添加，不在当前阶段提前冻结。

## 初始化读取链路

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

stdio MCP Server 从 `MCP_ACCESS_TOKEN` 环境变量读取凭据。原始 Token 只在签发时显示一次，
数据库只保存 SHA-256，日志和审计记录不得包含原始值。

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
└── workflows/       # 可恢复的提交分析 LangGraph
```

项目维护文档：

* [`docs/DEVELOPMENT_LOG.md`](docs/DEVELOPMENT_LOG.md)：按轮次记录每次更新、修复和验证；
* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)：当前已实现、骨架和规划模块的文本框架图；
* [`docs/ITERATION_STATUS.md`](docs/ITERATION_STATUS.md)：每轮交付内容和下一轮收敛计划。

## 验证

```bash
pytest
ruff check src tests
```

## 下一开发步

1. 将 `experiment_check_plan` 接入当前正式上下文仓储并持久化幂等结果；
2. 实现计划审批，再创建不可变 Run Manifest；
3. 后续再接 S3 提交和 LangGraph 分析节点，保持每个切片可独立验收。
