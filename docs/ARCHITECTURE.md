# Experiment Guardian 当前框架图

更新时间：2026-07-22
对应数据库 revision：`20260722_07`

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
           |                 v                              | [PARTIAL: 5/6]         |
           |      +------------------------+                | project_get_context    |
           |      | FastAPI                |                | experiment_check_plan  |
           |      | /health [DONE]         |                |   [DONE]               |
           |      | /capabilities [DONE]   |                | run_manifest_create    |
           |      | /plan-check decision  |                |   [DONE]               |
           |      | [DONE]                 |                | submission_prepare     |
           |      | /projects/initialize   |                | submission_finalize    |
           |      | [DONE]                 |                |   [DONE]               |
           |      |                        |                | query [SCAFFOLD]       |
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
                  | - S3 upload verification      |
                  | - safe reupload key rotation  |
                  | - Owner finalize recovery     |
                  | - R11 analysis orchestration  |
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
                  | - finalize row locks/evidence |
                  | - analysis cursor/risk query  |
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
                  | submission_risks                              |
                  +-----------------------------------------------+
```

## 领域与基础设施分层

```text
+--------------------------------------------------------------------------+
| Interface Layer                                                          |
|                                                                          |
|  FastAPI routes                 MCP tools                 Admin CLI       |
|  [DONE partial API]             [DONE 5/6 use cases]      [DONE]          |
+------------------------------------+-------------------------------------+
                                     |
+------------------------------------v-------------------------------------+
| Application Layer                                                        |
|                                                                          |
|  container.py       identity.py       ports.py       services.py         |
|  dependency wiring  trusted identity  use-case API   transactions/auth   |
|  submission_analysis.py [DONE R11 five-node persisted analysis]          |
+------------------------------------+-------------------------------------+
                                     |
+------------------------------------v-------------------------------------+
| Domain Layer                                                             |
|                                                                          |
|  enums.py           contracts.py         administration.py               |
|  state vocabulary   evidence/contracts   initialization request          |
|                                                                          |
|  plan_check.py [DONE]   run_manifest.py [DONE]   submission_analysis.py  |
|  strict config rules   canonical Manifest         strict result parser    |
+------------------------------------+-------------------------------------+
                                     |
+------------------------------------v-------------------------------------+
| Infrastructure Layer                                                     |
|                                                                          |
|  database.py      models/         repositories/        security.py       |
|  SQLAlchemy       ORM schema      project/plan/governance repositories   |
|                                                                          |
|  S3 PUT/HEAD [DONE]  exact VersionId GET [DONE]  Bedrock [PLANNED]         |
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
  |                                  |          +---- UPLOAD_VERIFIED audit snapshot
  |                                  |          +---- workflow status/last completed step
  |                                  |          +---- bounded analysis snapshot
  |                                  |          +----< SubmissionRisk
  |                                  |          |        +---- unique risk fingerprint
  |                                  |          |        +---- severity/evidence/blocking
  |                                  |          +----< Artifact
  |                                  |                   +---- declared hash/size/S3 key
  |                                  |                   +---- cloud metadata/evidence/time
  |                                  |                   +---- immutable S3 VersionId
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
Experiment -> ExperimentMetric -> Memory
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

9. Local Agent calls submission_finalize(submission_id, idempotency_key)
   -> MCP Token project/team/member/submission:finalize validation
   -> original submitter, or Owner recovery for a RECEIVED draft
   -> outside transaction: S3 HEAD with ChecksumMode=ENABLED for every Artifact
   -> require non-null VersionId so later readers can address the verified object version
   -> missing: retryable FAILED receipt and reuse the still-empty object key
   -> mismatch/unversioned: rotate only affected Artifact to a fresh object key
   -> client calls prepare again and uploads only reupload_artifact_ids
   -> every failed attempt writes an immutable audit with Token ID and source Agent
   -> all match: one transaction writes all CLOUD_VERIFIED evidence,
      UPLOAD_VERIFIED Submission, AuditLog and completed IdempotencyRecord
   -> synchronously starts or resumes the R11 analysis prefix
   -> exact VersionId GET outside database transactions; byte SHA-256 is recalculated
   -> strict CONFIG YAML/JSON + fixed result.json parsing, each at most 1 MiB
   -> each node commits analysis_snapshot + processing_step independently
   -> deterministic Manifest findings become risks; exact duplicate MEDIUM,
      same run conditions LOW; duplicate candidates are restricted to the same project
   -> transient S3 failure: PROCESSING/RETRYABLE_FAILURE; same finalize key resumes
   -> invalid immutable content: FAILED/TERMINAL_FAILURE; create a new Submission
   -> completed prefix: PROCESSING/AWAITING_ENRICHMENT at RISK_ANALYSIS
   -> successful replay skips completed HEAD, GET and analysis nodes
```

## 工作流实现边界

```text
experiments_query           [SCAFFOLD: query contract/model only]

Submission LangGraph full topology:
[DONE R11]
UPLOAD_VERIFICATION -> CONFIG_PARSE -> MANIFEST_VALIDATION -> DUPLICATE_CHECK
-> RISK_ANALYSIS

[PLANNED R12]
-> SUMMARY_GENERATION -> EMBEDDING_GENERATION -> NEEDS_REVIEW -> END

数据库业务表是恢复真相源，LangGraph 不保存 checkpoint。NEEDS_REVIEW 是计划中的持久化
交接状态，不是 LangGraph interrupt；正式确认将使用独立幂等事务。
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
       [PARTIAL]                 [PARTIAL: PUT/HEAD/GET]     [PLANNED]
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
