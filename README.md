# Experiment Guardian

Experiment Guardian 是提高实验一致性、可追溯性和风险可见性的治理系统。本仓库当前
完成的是 P0 第一阶段代码骨架，重点固定领域状态、追溯数据模型、训练前确定性规则、
FastAPI/MCP 入口和可恢复工作流拓扑。它不保证实验一定正确，也不声称能够完整验证真实
训练行为。

## 当前已经具备

* `PASS / NEEDS_APPROVAL / BLOCKED` 训练前检查状态；
* `NOT_REQUIRED / PENDING / APPROVED / REJECTED` 审批状态；
* YAML/JSON 安全解析、规范化配置哈希和点分参数 diff；
* LOCKED、APPROVAL_REQUIRED、EXPERIMENT_VARIABLE 确定性判定；
* 从项目上下文到正式实验、artifact、向量记忆和审计的 SQLAlchemy 模型；
* FastAPI `/api/v1/health` 与 `/api/v1/capabilities`；
* 六个 P0 MCP 工具的正式接口名称；
* 提交分析 LangGraph 的固定节点顺序；
* Alembic、pytest、Ruff 和 mypy 基础配置。

## 治理边界

* LLM 只能生成候选意图、候选约束、歧义和风险，不能激活 Intent 或绕过确定性规则；
* 约束同时保存 `EXPLICIT/INFERRED` 来源与确认状态，只有 `CONFIRMED` 约束可以强制阻断；
* 本地 Agent 只读取已确认事实并提交草稿，不能修改正式上下文或确认正式实验；
* `CLOUD_VERIFIED`、`LOCAL_ATTESTED`、`USER_PROVIDED` 始终随字段保存和展示；
* 配置一致性检查不等于实验正确性保证，本地 Git、环境和路径信息仅作为声明处理；
* 正式与探索实验使用不同模式，探索结果不得成为正式 baseline；
* 向量相似度只生成候选证据，执行前必须按项目、确认状态、实验状态和协议过滤。

数据库仓储、鉴权、S3 预签名、Bedrock、CockroachDB checkpoint 和四个 Web 页面尚未实现。
当前 MCP 业务调用会明确返回“业务适配器尚未装配”，不会伪造上下文或实验数据。

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

当前骨架暂未冻结第一版数据库迁移。核心模型评审完成后，先生成迁移，再创建表：

```bash
alembic revision --autogenerate -m "initial p0 schema"
alembic upgrade head
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
└── workflows/       # 可恢复的提交分析 LangGraph
```

## 验证

```bash
pytest
ruff check src tests
```

## 下一开发步

1. 实现用户/项目权限检查与 CockroachDB 仓储；
2. 将 `project_get_context` 和 `experiment_check_plan` 接入真实应用服务；
3. 生成并评审首个 Alembic Schema 迁移；
4. 再实现 Manifest、S3 提交和 LangGraph 节点，保持纵向链路始终可演示。
