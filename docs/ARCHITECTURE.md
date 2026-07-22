# Experiment Guardian 当前框架图

更新时间：2026-07-22
对应数据库 revision：`20260722_10`

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
| [DONE]               |                                  | 7 tools exposed      |
| - bootstrap Owner    |                                  | [DONE protocol]      |
| - add Researcher     |                                  |                      |
| - issue API/MCP token |                                 +----------+-----------+
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
           |                 v                              | [DONE: 7/7]            |
           |      +------------------------+                | project_get_context    |
           |      | FastAPI                |                | experiment_check_plan  |
           |      | /health [DONE]         |                |   [DONE]               |
           |      | /capabilities [DONE]   |                | run_manifest_create    |
           |      | /plan-check decision  |                |   [DONE]               |
           |      | [DONE]                 |                | submission_prepare     |
           |      | /projects/initialize   |                | submission_finalize    |
           |      | [DONE]                 |                |   [DONE]               |
           |      | /submission decision  |                | submission_get_status  |
           |      | [DONE]                 |                |   [DONE]               |
           |      |                        |                | experiments_query      |
           |      |                        |                |   [DONE]               |
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
                  | - R12a summary scheduling     |
                  | - R12b embedding/review       |
                  | - R13 formal decision/query   |
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
                  | - two-stage Job/Outbox lease  |
                  | - formal experiment query     |
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
                  | submission_risks, submission_embeddings       |
                  | workflow_jobs, outbox_events                   |
                  | experiments, experiment_metrics, memories     |
                  +-----------------------------------------------+
```

## 领域与基础设施分层

```text
+--------------------------------------------------------------------------+
| Interface Layer                                                          |
|                                                                          |
|  FastAPI routes                 MCP tools                 Admin CLI       |
|  [DONE P0 API]                  [DONE 7/7 use cases]      [DONE]          |
+------------------------------------+-------------------------------------+
                                     |
+------------------------------------v-------------------------------------+
| Application Layer                                                        |
|                                                                          |
|  container.py       identity.py       ports.py       services.py         |
|  dependency wiring  trusted identity  use-case API   transactions/auth   |
|  submission_analysis.py [DONE R11 five-node persisted analysis]          |
|  async_summary.py [DONE R12a scheduler/outbox/lease/retry/summary]        |
|  async_review.py  [DONE R12b embedding/review receipt/recovery]           |
|  experiments.py   [DONE R13 decision transaction/vector query]           |
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
|  S3 PUT/HEAD/GET [DONE]  SQS [DONE adapter]  Bedrock summary/Titan [DONE] |
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
  |                                  |          +---- generated_summary (LLM interpretation)
  |                                  |          +---- review_receipt (deterministic)
  |                                  |          +----0..1 SubmissionEmbedding
  |                                  |          |        +---- VECTOR(1024), model/input hash
  |                                  |          |        +---- frozen submission-search-v1 input
  |                                  |          +----< SubmissionRisk
  |                                  |          |        +---- unique risk fingerprint
  |                                  |          |        +---- severity/evidence/blocking
  |                                  |          +----< Artifact
  |                                  |                   +---- declared hash/size/S3 key
  |                                  |                   +---- cloud metadata/evidence/time
  |                                  |                   +---- immutable S3 VersionId
  |                                  |          +----< WorkflowJob
  |                                  |                   +---- SUMMARY or REVIEW_PREPARATION
  |                                  |                   +---- generation/status/attempts
  |                                  |                   +---- lease/error/SQS message ID
  |                                  |                   +----< OutboxEvent per generation
  |                                  |
  |                                  +----< AuditLog
  |                                  |
  |                                  +----< Experiment
  |                                             +---- submission/manifest/intent/context trace
  |                                             +---- approval + summary/review snapshots
  |                                             +----< ExperimentMetric
  |                                             +----< Memory VECTOR(1024)
  |                                             +----< Artifact association
  |
  +----< AccessToken
  |       API: team scope
  |       MCP: required project binding
  |
  +----< IdempotencyRecord
          unique(actor_id, operation, idempotency_key)
```

`Experiment -> ExperimentMetric -> Memory` 已由 revision `20260722_10` 正式迁移；草稿
`SubmissionEmbedding` 保留不变，确认事务复制其冻结内容、向量和模型来源，便于审计。

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
   -> completed risk prefix: PROCESSING/QUEUED at RISK_ANALYSIS
   -> same risk transaction creates unique WorkflowJob + OutboxEvent
   -> successful replay skips completed HEAD, GET and analysis nodes

10. experiment-guardian-worker
   -> startup reconciles revision 07 rows missing Summary Job and R12a rows missing Review Job
   -> leases pending Outbox row, publishes minimal generation envelope to SQS outside transaction
   -> crash after send may duplicate a Standard Queue message; generation/cursor makes it harmless
   -> routes by persisted Job type; the same queue never carries business payloads
   -> Summary Job runs the six-node prefix; first five nodes are cursor no-ops
   -> builds a bounded structured source from Intent, Manifest, result metrics and existing risks
   -> Bedrock Converse returns plain text only; no LOG/NOTE payload is sent
   -> summary success atomically persists generated_summary and creates Review Job/Outbox
   -> Review Job runs the full graph; first six nodes are cursor no-ops
   -> builds frozen submission-search-v1 input without generated_summary, LOG or NOTE
   -> Titan V2 returns normalized VECTOR(1024); input text/hash/model/token metadata are persisted
   -> next node deterministically creates the short review receipt without another LLM call
   -> HIGH/CRITICAL are expanded; unresolved blocking/CRITICAL makes eligibility BLOCKED
   -> retryable dependency failure uses 30s/120s/480s/... capped at 3600s
   -> fifth failure marks DEAD_LETTER; original submitter or Owner can use a new finalize key
   -> success: NEEDS_REVIEW/COMPLETED at NEEDS_REVIEW

11. Local Agent calls submission_get_status(submission_id)
   -> submission:read + project binding + membership
   -> original submitter or Owner only
   -> returns dynamic Submission/Job history/risk/summary/embedding metadata/review receipt
   -> never returns raw vector or frozen embedding input; never triggers processing

12. User calls POST /projects/{project}/submissions/{submission}/decision
   -> API Token + submission:review + membership
   -> Researcher only own draft; Owner any project draft
   -> recomputes eligibility/source/document hashes from persisted facts
   -> APPROVED creates Experiment/Metric/Memory and Artifact links in one DB transaction
   -> REJECTED creates no formal experiment; both paths persist approval/audit/idempotency
   -> no S3 or Bedrock call inside the transaction

13. Local Agent calls experiments_query
   -> experiment:query + project binding + membership
   -> exact detail by experiment_id without Bedrock, or query+protocol candidate mode
   -> project/protocol/status/CONFIRMED/current filters before Titan query embedding
   -> exact cosine scan over at most 200 compatible memories; no vector index in P0
   -> vector results remain CANDIDATE_EVIDENCE
```

## 工作流实现边界

```text
experiments_query           [DONE R13: structured filter + exact vector candidates]

Submission LangGraph full topology:
[DONE R11]
UPLOAD_VERIFICATION -> CONFIG_PARSE -> MANIFEST_VALIDATION -> DUPLICATE_CHECK
-> RISK_ANALYSIS

[DONE R12a]
-> SUMMARY_GENERATION

[DONE R12b]
-> EMBEDDING_GENERATION -> NEEDS_REVIEW -> END

数据库业务表和 WorkflowJob 是恢复真相源，LangGraph 不保存 checkpoint。SQS 只携带
`schema_version/job_id/submission_id/generation`。NEEDS_REVIEW 是持久化交接状态，
不是 LangGraph interrupt；正式确认使用 R13 独立幂等事务。
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
       CockroachDB               Amazon S3              SQS Standard
       state/outbox              artifacts              summary/review jobs
       [PARTIAL]                 [PARTIAL: PUT/HEAD/GET] [DONE adapter]
             |                                                   |
             |                                                   v
             +-------------------------------------------> R12b Worker
                                                               |
                                                               v
                                                         Bedrock Converse + Titan V2
                                                         [DONE adapters]
             |
             v
       CloudWatch [PLANNED]
```

Queue、DLQ/redrive、IAM/KMS 和 Bedrock model access 的 AWS 资源定义仍为 `[PLANNED R14]`；
R13 只实现应用适配器和运行协议，不创建上述 AWS 资源。

## 文档更新要求

发生以下变化时必须同步更新本文件：

* 新增或删除外部入口；
* 某个 MCP 工具从 `[SCAFFOLD]` 变为 `[DONE]`；
* 新的数据库 revision 将 ORM-only 表转为正式表；
* 引入 S3、Bedrock、Web、队列或新的工作流运行方式；
* 层间依赖方向发生变化。
