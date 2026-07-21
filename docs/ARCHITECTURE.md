# Experiment Guardian 当前框架图

更新时间：2026-07-21  
对应数据库 revision：`20260721_05`

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
           |                 v                              | [PARTIAL: 4/6]         |
           |      +------------------------+                | project_get_context    |
           |      | FastAPI                |                | experiment_check_plan  |
           |      | /health [DONE]         |                |   [DONE]               |
           |      | /capabilities [DONE]   |                | run_manifest_create    |
           |      | /plan-check decision  |                |   [DONE]               |
           |      | [DONE]                 |                | submission_prepare     |
           |      | /projects/initialize   |                |   [DONE]               |
           |      | [DONE]                 |                | other 2 [SCAFFOLD]     |
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
                  | - plan evaluation/idempotency |
                  | - Owner final decision        |
                  | - immutable Manifest          |
                  | - submission upload draft     |
                  | - stable application errors   |
                  +---------------+---------------+
                                  |
                                  v
                  +-------------------------------+
                  | Repositories [DONE]            |
                  | - membership                  |
                  | - active Context              |
                  | - active confirmed Intent     |
                  | - confirmed/pending rules     |
                  | - policy consistency guard   |
                  | - Plan Check replay           |
                  | - approval/manifest locks     |
                  | - submission/artifact replay  |
                  +---------------+---------------+
                                  |
                                  v
                  +-----------------------------------------------+
                  | CockroachDB [DONE current schema]             |
                  | users, teams, team_members, projects          |
                  | project_contexts, experiment_intents          |
                  | protected_parameters, access_tokens           |
                  | audit_logs, idempotency_records, plan_checks  |
                  | approval_records, run_manifests               |
                  | experiment_submissions, artifacts             |
                  +-----------------------------------------------+
```

## 领域与基础设施分层

```text
+--------------------------------------------------------------------------+
| Interface Layer                                                          |
|                                                                          |
|  FastAPI routes                 MCP tools                 Admin CLI       |
|  [DONE partial API]             [DONE 4/6 use cases]      [DONE]          |
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
|  plan_check.py [DONE pure engine]   run_manifest.py [DONE builder]       |
|  parse/hash/diff/risk result         snapshot extraction/canonical hash   |
+------------------------------------+-------------------------------------+
                                     |
+------------------------------------v-------------------------------------+
| Infrastructure Layer                                                     |
|                                                                          |
|  database.py      models/         repositories/        security.py       |
|  SQLAlchemy       ORM schema      project/plan/governance repositories   |
|                                                                          |
|  S3 presign [DONE]  S3 verify [PLANNED]  Bedrock/CloudWatch [PLANNED]     |
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
  |                                  +----< PlanCheck
  |                                  |          |
  |                                  |          +---- context_id/version
  |                                  |          +---- intent_id/version
  |                                  |          +---- Context/baseline + Intent snapshots
  |                                  |          +---- raw/parsed config + constraint/evidence snapshots
  |                                  |          +----0..1 ApprovalRecord (final decision)
  |                                  |          +----0..1 RunManifest
  |                                  |                   +---- config/document/manifest hashes
  |                                  |                   +---- Git/command/environment evidence
  |                                  |
  |                                  +----< ExperimentSubmission
  |                                  |          |
  |                                  |          +---- run_manifest_id/hash
  |                                  |          +---- declared status/metrics/evidence
  |                                  |          +----< Artifact (declared hash/size/S3 key)
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
SubmissionRisk -> Experiment -> ExperimentMetric -> Memory
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

5. Local Agent calls experiment_check_plan(...)
   -> MCP Token project/team/member/scope validation
   -> current Context + Active Intent + confirmed/pending constraints
   -> policy conflict guard + deterministic parse/hash/diff/risk evaluation
   -> idempotent PlanCheck + complete historical policy/evidence snapshots
   -> current approval status merged into immutable evaluation report
   -> PASS / NEEDS_APPROVAL / BLOCKED receipt

6. Owner calls POST /projects/{project_id}/plan-checks/{plan_check_id}/decision
   -> API Token + plan:approve + Owner role
   -> one final ApprovalRecord + PlanCheck transition + AuditLog + IdempotencyRecord

7. Local Agent calls run_manifest_create(plan_check_id, idempotency_key)
   -> MCP Token project/team/member/manifest:create validation
   -> PASS/NOT_REQUIRED or NEEDS_APPROVAL/APPROVED eligibility gate
   -> build only from historical PlanCheck snapshots
   -> immutable RunManifest + canonical manifest_hash + audit/idempotency

8. Local Agent calls submission_prepare(...)
   -> MCP Token project/team/member/submission:create validation
   -> validate Manifest ownership and CONFIG/RESULT/artifact declarations
   -> one transaction: RECEIVED Submission + Artifacts + AuditLog + IdempotencyRecord
   -> sign short-lived S3 PUT URLs after commit; URLs are never persisted
   -> same key reuses database IDs and issues fresh upload URLs
```

## 已有但尚未接通的框架

```text
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
       [PARTIAL]                 [PARTIAL: presign]       [PLANNED]
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
