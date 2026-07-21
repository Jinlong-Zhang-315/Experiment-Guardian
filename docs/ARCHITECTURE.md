# Experiment Guardian 当前框架图

更新时间：2026-07-21  
对应数据库 revision：`20260721_01`

本文档维护当前仓库的实际框架。状态标记：

* `[DONE]`：已有可运行实现和测试。
* `[PARTIAL]`：同一模块中只有标明的部分链路已完成。
* `[SCAFFOLD]`：接口、模型或拓扑已存在，但端到端能力尚未接通。
* `[PLANNED]`：需求已确认，当前仓库尚无实现。

## 当前运行框架

```text
                                  Experiment Guardian

  Owner / Admin                                              Local Coding Agent
       |                                                              |
       | CLI                                      MCP stdio            |
       v                                                              v
+----------------------+                                  +----------------------+
| admin_cli.py         |                                  | mcp_server/server.py |
| [DONE]               |                                  | 6 tools exposed      |
| - bootstrap Owner    |                                  | [DONE protocol]      |
| - issue MCP token    |                                  +----------+-----------+
| - revoke token       |                                             |
+----------+-----------+                                             v
           |                                              +-------------------------+
           |                                              | EnvironmentIdentity     |
           |                                              | Provider [DONE]         |
           |                                              | MCP_ACCESS_TOKEN -> DB  |
           |                                              +------------+------------+
           |                                                           |
           |                                                           v
           |        Browser / API Client                    +------------------------+
           |                 |                              | GuardianApplication    |
           |                 v                              | [PARTIAL]              |
           |      +------------------------+                | project_get_context    |
           |      | FastAPI                |                |   [DONE]               |
           |      | /health [DONE]         |                | other 5 tools          |
           |      | /capabilities [DONE]   |                |   [SCAFFOLD/501]       |
           |      | /projects/initialize   |                +-----------+------------+
           |      | [DONE]                 |                            |
           |      +-----------+------------+                            |
           |                  | Bearer auth                             |
           |                  v                                         |
           |      +------------------------+                            |
           +----->| TokenService [DONE]    |<---------------------------+
                  | SHA-256 / audience     |
                  | scope / expiry/revoke  |
                  +-----------+------------+
                              |
                              v
                  +-------------------------------+
                  | Application Services [DONE]   |
                  | - Owner role check            |
                  | - atomic initialization       |
                  | - idempotency                 |
                  | - stable application errors   |
                  +---------------+---------------+
                                  |
                                  v
                  +-------------------------------+
                  | Project Repository [DONE]     |
                  | - membership                  |
                  | - active Context              |
                  | - active confirmed Intent     |
                  | - confirmed constraints       |
                  | - provenance preservation     |
                  +---------------+---------------+
                                  |
                                  v
                  +-----------------------------------------------+
                  | CockroachDB [DONE foundation schema]          |
                  | users, teams, team_members, projects          |
                  | project_contexts, experiment_intents          |
                  | protected_parameters, access_tokens           |
                  | audit_logs, idempotency_records               |
                  +-----------------------------------------------+
```

## 领域与基础设施分层

```text
+--------------------------------------------------------------------------+
| Interface Layer                                                          |
|                                                                          |
|  FastAPI routes                 MCP tools                 Admin CLI       |
|  [DONE partial API]             [DONE 1/6 use cases]      [DONE]          |
+------------------------------------+-------------------------------------+
                                     |
+------------------------------------v-------------------------------------+
| Application Layer                                                        |
|                                                                          |
|  container.py       identity.py       ports.py       services.py         |
|  dependency wiring  trusted identity  use-case API   transactions/auth   |
+------------------------------------+-------------------------------------+
                                     |
+------------------------------------v-------------------------------------+
| Domain Layer                                                             |
|                                                                          |
|  enums.py           contracts.py         administration.py               |
|  state vocabulary   evidence/contracts   initialization request          |
|                                                                          |
|  plan_check.py [DONE pure engine]                                        |
|  parse -> canonical hash -> diff -> deterministic risks -> result         |
+------------------------------------+-------------------------------------+
                                     |
+------------------------------------v-------------------------------------+
| Infrastructure Layer                                                     |
|                                                                          |
|  database.py      models/         repositories/        security.py       |
|  SQLAlchemy       ORM schema      project context      token hashing      |
|                                                                          |
|  S3 [PLANNED]     Bedrock [PLANNED]     CloudWatch [PLANNED]              |
+--------------------------------------------------------------------------+
```

依赖方向固定为：接口层 -> 应用层 -> 领域层；基础设施实现应用端口。领域规则不直接依赖
FastAPI、MCP、CockroachDB、S3 或 Bedrock。

## 当前数据关系

```text
User
  | 1
  +----< TeamMember >----1 Team
  |                         |
  |                         +----< Project
  |                                  |
  |                                  +----< ProjectContext (versioned)
  |                                  |          |
  |                                  |          +---- supersedes_context_id
  |                                  |
  |                                  +----< ExperimentIntent (versioned)
  |                                  |          |
  |                                  |          +---- context_id/version
  |                                  |
  |                                  +----< ProtectedParameter (versioned)
  |                                  |          |
  |                                  |          +---- context_id/version
  |                                  |          +---- optional intent_id/version
  |                                  |
  |                                  +----< AuditLog
  |
  +----< AccessToken
  |       API: team scope
  |       MCP: required project binding
  |
  +----< IdempotencyRecord
          unique(actor_id, operation, idempotency_key)
```

以下对象已有 ORM 模型但尚未进入迁移，状态为 `[SCAFFOLD]`：

```text
PlanCheck -> ApprovalRecord -> RunManifest -> ExperimentSubmission
    -> Artifact / SubmissionRisk -> Experiment -> ExperimentMetric -> Memory
```

这一区分是刻意的：没有业务服务和验收测试的表不提前加入 Alembic revision。

## 当前可用链路

```text
1. experiment-guardian-admin bootstrap-owner
   -> User + Team + TeamMember + hashed API AccessToken

2. POST /api/v1/projects/initialize
   -> Bearer API Token
   -> Owner + scope validation
   -> one transaction:
      Project + Context v1 + Intent v1 + confirmed constraints
      + AuditLog + IdempotencyRecord

3. experiment-guardian-admin issue-mcp-token
   -> project-bound hashed MCP AccessToken

4. Local Agent calls project_get_context(project_id)
   -> MCP_ACCESS_TOKEN authentication
   -> project binding + membership
   -> active versioned Context + Intent + confirmed constraints
```

## 已有但尚未接通的框架

```text
experiment_check_plan       [SCAFFOLD: pure evaluator exists, DB use case absent]
run_manifest_create         [SCAFFOLD: contract/model only]
submission_prepare          [SCAFFOLD: contract/model only, no S3]
submission_finalize         [SCAFFOLD: contract/model/workflow topology only]
experiments_query           [SCAFFOLD: query contract/model only]

Submission LangGraph:
UPLOAD_VERIFICATION -> CONFIG_PARSE -> MANIFEST_VALIDATION -> DUPLICATE_CHECK
-> RISK_ANALYSIS -> SUMMARY_GENERATION -> EMBEDDING_GENERATION -> NEEDS_REVIEW -> END

NEEDS_REVIEW is a persisted hand-off target, not a LangGraph interrupt.
```

## 计划中的完整部署边界

```text
Web Admin [PLANNED] -----------+
                               |
Local Agent -> MCP Server -----+-> FastAPI/Application
                                      |
             +------------------------+------------------------+
             |                        |                        |
             v                        v                        v
       CockroachDB               Amazon S3                Bedrock
       state/vector              artifacts                summary/risk
       [PARTIAL]                 [PLANNED]                [PLANNED]
             |
             v
       CloudWatch [PLANNED]
```

## 文档更新要求

发生以下变化时必须同步更新本文件：

* 新增或删除外部入口；
* 某个 MCP 工具从 `[SCAFFOLD]` 变为 `[DONE]`；
* 新的数据库 revision 将 ORM-only 表转为正式表；
* 引入 S3、Bedrock、Web、队列或新的工作流运行方式；
* 层间依赖方向发生变化。
