# Experiment Guardian 当前框架图

更新时间：2026-07-28
当前实现：R17b 版本化自然语言实验计划、有限自动修订与人工决定

下一计划：R17c 计划、运行前和结果提交三阶段关键不变量核对。总体边界见
`EXTERNAL_CODING_AGENT_PLAN.md`。
数据库 head：`20260728_25`

除明确标记“计划”的章节外，本文描述当前仓库已经实现的结构。`[DONE]` 表示已有代码与
自动化验证，`[EXTERNAL]` 表示由部署环境提供，`[MANUAL]` 表示真实云服务仍需部署环境验收。

## 总体运行图

```text
                                     Human users
                               Owner / Researcher
                                        |
                     Cognito Managed Login (OIDC code + PKCE)
                                        |
                                        v
+------------------+          +----------------------+          +------------------+
| React/Vite Web   | Cookie   | FastAPI API         |          | Cognito User Pool|
| [DONE]           +--------->| [DONE]              +--------->| [IaC DONE]       |
| - Project setup  | HttpOnly | - OIDC callback     | code     | - Managed Login  |
| - Plan approval  | + CSRF   | - web_sessions      | exchange | - human client   |
| - Submission     |          | - live RBAC         |          | - MCP clients    |
| - Experiments    |          | - recent auth       |          +------------------+
| - Governance AI |          | - Agent/report API  |
+---------+--------+          | - management APIs   |
          |                   +----------+-----------+
          |                              |
          |                              v
          |                   +----------------------+
          |                   | Application services|
          |                   | [DONE]               |
          |                   | deterministic rules  |
          |                   | policy narrative     |
          |                   | transactions/retries |
          |                   +----------+-----------+
          |                              |
          |                              v
          |                   +----------------------+
          |                   | CockroachDB          |
          |                   | [EXTERNAL + schema]  |
          |                   | versions/evidence    |
          |                   | sessions/grants/audit|
          |                   +----------------------+
          |
          |             Local or remote Coding Agent
          |                        |
          |       +----------------+----------------+
          |       |                                 |
          |       v                                 v
          | MCP stdio + hashed token      OAuth HTTP MCP 2025-11-25
          | [DONE local development]      [DONE protocol/resource server]
          |                                         |
          |                          RFC9728 discovery -> Cognito OIDC
          |                          pre-registered client + PKCE + resource
          |                                         |
          |                                         v
          |                              +----------------------+
          +----------------------------->| FastMCP Server       |
                                         | [DONE 13 tools]      |
                                         | JWT + client/grant   |
                                         | + membership checks  |
                                         +----------+-----------+
                                                    |
                                                    v
                              +-------------+----------------+
                              |                              |
                              v                              v
                   GuardianApplication       AgentConversation/Plan services
                   7 formal tools            6 collaboration tools
```

## AWS 部署图

```text
Internet
   |
Route53
   |
CloudFront + WAF
   |-- default ------------------------> private S3 Web bucket
   |-- /api/* -------------------------> HTTPS ALB --+--> private ECS API
   |-- /mcp* -------------------------->             +--> private ECS MCP
   |-- /.well-known/oauth-protected-resource/mcp --> +
                                                      (CloudFront secret origin header)

private ECS API/MCP/Worker
   |-- Secrets Manager + KMS
   |-- Cockroach Cloud [external]
   |-- KMS S3 Artifact bucket [Versioning required]
   |-- SQS Standard -> DLQ
   +-- Bedrock Converse / Titan Embeddings V2

optional private Agent Worker
   +-- AgentChatModel selected once at startup
       |-- Bailian streaming Function Calling
       +-- Bedrock ConverseStream + required Structured Outputs
   +-- no automatic provider fallback
   +-- CockroachDB Agent run/event/tool/citation tables

ECR -> immutable application image
CloudWatch Logs/Metrics -> API/MCP/Worker, ALB, WAF, SQS
```

Terraform 位于 `infra/terraform/`，已经通过 Terraform 1.9.8 和 AWS Provider 6.55 schema
验证。实际 `apply`、Bedrock model access、域名证书和双角色演示属于部署环境验收。

## 本地部署图

```text
Browser (127.0.0.1 only)
   |  Host: 127.0.0.1/localhost only
   v
Web/Nginx Host allowlist
   |
   v
FastAPI TrustedHost -> local_owner login --------+--> CockroachDB
                 |    normal HttpOnly Session     |    business truth + sessions
                 |    + CSRF                      |    workflow jobs + outbox
                 +--> S3CompatibleObjectStorage -+--> MinIO versioned bucket
                 |                                    fixed VersionId evidence
                 +--> Outbox transaction
                          |
                          v
                  DatabaseOutboxQueue poll/claim
                          |
                          v
                    Worker lease/generation
                          |
                          +--> BailianSummaryGenerator
                          +--> BailianEmbeddingGenerator (VECTOR(1024))
                               malformed upstream output
                               -> ServiceUnavailableError
                               -> persisted retry/dead letter
                 |
                 +--> Agent API -> agent_runs/events
                                  |
                                  v
                            Agent Worker lease
                                  |
                                  +--> BailianAgentChatModel
                                  |    auto Function Calling
                                  |    -> separate strict JSON final turn
                                  +--> configured-rate cost snapshot
                                  +--> eight authorized read/analysis tools
                                  +--> four candidate-only policy draft tools
                                  +--> one prepare-only Policy publish proposal tool

minio-init: create bucket + enable/verify Versioning, then exit
database-init -> migration -> local-init: ordered one-shot services

verify_r16_local.py [DONE]
   +--> config / migration / MinIO / Web / API / local_owner / session revoke
   +--> optional live Bailian read-only Run / citation / observability / state invariant
```

本地与云端共用应用服务、领域规则、数据库状态机和审计。`DEPLOYMENT_MODE` 只在 Settings 校验、
依赖容器和 Worker 组合根中决定适配器，业务服务不判断 MinIO、SQS、Cognito 或百炼。

## 代码分层

```text
src/experiment_guardian/
|
+-- api/
|   +-- auth.py                 Cognito/local_owner login、Session、logout/reauth
|   +-- web.py                  四页读取、策略发布、下载 URL、查询
|   +-- plan_checks.py          Owner 计划决定
|   +-- submissions.py          草稿最终决定
|   +-- agent.py                Thread/Message/Run/SSE/retry API
|                               + Policy Draft and Action Proposal API
|
+-- mcp_server/server.py        七个正式治理 + 三个外部协作工具 + HTTP health
|
+-- application/
|   +-- web_auth.py             Cognito/local_owner、Session、CSRF、撤销
|   +-- web_management.py       页面读取、版本发布、Artifact 下载
|   +-- services.py             Context/Plan/Manifest/Submission 主线
|   +-- experiments.py          正式确认与结构化/向量查询
|   +-- submission_analysis.py  可恢复确定性分析
|   +-- async_summary.py        Outbox/SQS/摘要
|   +-- async_review.py         embedding/确定性回执
|   +-- agent.py                对话、幂等入队、归档、恢复与身份解析
|   +-- agent_runtime.py        有界 LangGraph loop、lease、审计与最终提交
|   +-- agent_observability.py  Owner-only token/latency/failure/cost aggregation
|   +-- agent_tools.py          八个只读/分析 + 四个候选草稿工具
|   +-- policy_drafts.py        完整 Bundle revision、权限、diff 与影响模拟
|   +-- action_proposals.py     24h 冻结提案、实时失效、Owner 原子确认
|
+-- domain/                     Pydantic 契约、状态、纯确定性规则
|   +-- policy_narrative.py     结构化策略来源哈希与确定性 Markdown
|   +-- policy_draft.py         草稿 schema、严格 diff、校验与候选说明
|   +-- action_proposal.py      提案 digest、确认/取消契约和展示状态
|
+-- infrastructure/
|   +-- cognito.py              人类 OIDC Provider adapter
|   +-- mcp_oauth.py            Cognito Access Token + 本地 Grant verifier
|   +-- storage.py              AWS/MinIO 固定 VersionId 上传/读取/下载
|   +-- database.py/models/     SQLAlchemy/CockroachDB
|   +-- queue.py                SQS/DatabaseOutboxQueue adapters
|   +-- bedrock.py/bailian.py   摘要、embedding 与 AgentChatModel adapters
|   +-- repositories/agent.py   Agent event/claim/lease/generation persistence
|
+-- workflows/                  LangGraph 固定拓扑，DB 游标负责恢复
+-- worker.py                   批量轮询、可租约/可恢复 Worker
+-- agent_worker.py             独立 Agent Worker
+-- admin_cli.py                本地幂等初始化/成员/token/MCP client 管理

web/                            React 19 + Vite + TanStack Query + Lucide
docker-compose.yml              CRDB/MinIO/API/Worker/Web + 可选 Agent profile
infra/terraform/                AWS/Cognito/ECS/CloudFront/S3/SQS IaC
demo/r14/                       最终演示输入文件
scripts/verify_r14_deployment.py 公开部署认证/发现验收
scripts/verify_r16_local.py      本地 RC 与可选真实百炼只读验收
```

依赖方向保持：接口层 -> 应用层 -> 领域层；基础设施实现应用端口。前端不重新计算风险、
审批资格或角色权限。

## R17b Agent 当前框架图

```text
Web 治理 Agent 页 <------------------ MCP 创建的同用户任务可见并可续聊
   |
   | existing WebSession + project binding
   v
Agent API
   |
   +--> AgentConversationService
   |       +--> Thread / Message / Run / Citation / answer sections [DONE]
   |       +--> rolling summary v6 / draft + proposal + report references [DONE]
   |       +--> Policy Draft list / revision / abandon [DONE]
   |       +--> Action Proposal list / confirm / cancel [DONE]
   |       +--> shared Research Report list / detail [DONE]
   |       +--> idempotent enqueue / archive / retry [DONE]
   |       +--> durable SSE replay / heartbeat [DONE]
   |
   +--> CockroachDB agent_runs
           |
           v
       dedicated Agent Worker
           |
   +--> GovernanceAgentRuntime [DONE]
   |       +--> bounded single-agent LangGraph, max calls/tools/wall time
   |       +--> frozen prior catalogs + r15e-b prompt/catalog compatibility
   |       +--> recent messages + non-authoritative rolling summary
   |       +--> AgentChatModel
   |       |       +--> Bailian streaming Function Calling [DONE]
   |       |       |    +--> auto 工具选择与 strict JSON 最终回合分离
   |       |       |    +--> finish_reason 或 [DONE] 证明流完成
   |       |       +--> Bedrock ConverseStream + strict JSON Schema [DONE]
   |       |       +--> startup-selected provider; no fallback [DONE]
   |       +--> AgentToolRegistry
   |               +--> project_status_get_v1 [DONE]
   |               +--> experiments_list_v1 [DONE]
   |               +--> experiment_get_v1 [DONE]
   |               +--> pending_work_list_v1 [DONE]
   |               +--> experiments_compare_v1 [DONE]
   |               +--> experiment_group_stats_v1 [DONE]
   |               +--> plan_check_explain_v1 [DONE]
   |               +--> submission_diagnose_v1 [DONE]
   |               +--> policy_draft_create_v1 [DONE, candidate only]
   |               +--> policy_draft_update_v1 [DONE, candidate only]
   |               +--> policy_draft_validate_v1 [DONE]
   |               +--> policy_draft_impact_get_v1 [DONE]
   |               +--> action_proposal_prepare_v1 [DONE, no execute]
   |               +--> action_proposal_prepare_plan_decision_v1 [DONE, no execute]
   |               +--> action_proposal_prepare_submission_decision_v1 [DONE, no execute]
   |               +--> research_report_prepare_v1 [DONE, explicit 2-8 experiments]
   |               +--> research_reports_list_v1 [DONE, shared read]
   |               +--> research_report_get_v1 [DONE, shared read]
   |               +--> research_memories_search_v1 [DONE, candidate evidence]
   |               +--> FORMAL EXECUTE [NOT REGISTERED]
   +--> validated AgentAnswer + evidence/citations + AuditLog [DONE]
   +--> immutable AgentResearchReport + frozen source/hash [DONE]

External Coding Agent
   |
   | stdio MCP Token / remote OAuth
   v
external_agent_task_start / external_agent_ask / external_agent_task_get [DONE]
external_agent_plan_submit / external_agent_plan_revise / external_agent_plan_get [DONE]
   |
   +--> AgentThread origin=EXTERNAL_MCP
   |       +--> initial formal ProjectContextBundle snapshot + source hash
   |       +--> idempotent task start; one active external Run per user/project
   |
   +--> AgentRun credential binding
   |       +--> MCP_TOKEN -> access_tokens, live revoke/expiry/project checks
   |       +--> MCP_OAUTH -> grant + client, live revoke/expiry/project checks
   |       +--> scope snapshot intersected with current permission
   |
   +--> r17a-external-v1 read-only catalog
           +--> project / formal experiment / comparison / statistics
           +--> candidate report and research-memory reads
           +--> no draft, proposal, approval, manifest or formal write tool
   |
   +--> ExperimentPlanService
           +--> immutable ExperimentPlanRevision
           |      +--> full plan/evidence + formal policy snapshot/hash
           |      +--> deterministic YAML/JSON + LOCKED hard check
           +--> AgentRun kind=EXPERIMENT_PLAN_REVIEW
           |      +--> r17b prompt + existing read-only catalog
           |      +--> at most 2 text-only automatic revisions
           |      +--> policy drift checked before model call
           +--> ExperimentPlanReview
           |      +--> citations/findings/candidate invariants
           |      +--> review hash + approval digest
           +--> ExperimentPlanDecision
                  +--> Web Session + recent auth + live RBAC
                  +--> exact revision/candidate choices/approved snapshot
                  +--> does not replace formal Plan Check or create Manifest

Owner Web Session
   |
   +--> AgentObservabilityService [DONE, metadata columns only]
           +--> 7/30/90-day project aggregate
           +--> provider/model/purpose, token, latency, failure and retry
           +--> per-currency configured-rate estimate (not cloud billing)

Project member -> Run detail [DONE, max 50 model calls]
                   provider/model/purpose/status/token/latency/cost/error code only
                   no prompt/answer/tool payload exposure

Experiment Memory VECTOR(1024)       formal confirmed experiments only
Agent Research Report                immutable ANALYSIS, shared in project
Agent Research Memory                finding-level CANDIDATE, separate VECTOR(1024) jobs
```

R16-L 没有新增模型工具。既有模型写工具仍只能追加候选草稿、准备不可变
Policy/Plan/Submission 提案，或基于
用户显式实验集生成候选研究报告。
正式执行没有注册为模型工具；独立 Web 确认请求分别复用 Policy 发布、Plan 审批和 Submission
审核事务核心。Submission Proposal 确认时，Proposal、ApprovalRecord、Experiment、Metric、Memory、
Artifact 关联、双幂等结果和审计在同一事务中落库。正式事实、候选草稿、操作提案和影响分析
继续使用独立 evidence，摘要和研究报告不参与正式判断。报告生成时冻结来源 ToolCall 输出、
Experiment 顺序、provider/model/prompt/schema 与双哈希；后续来源状态变化只显示警告，不追溯改写。

## 数据关系

```text
User(cognito_sub)
  +--< TeamMember >-- Team --< Project
  |                            |
  +--< WebSession              +--< ProjectContext(version, supersedes)
  |                            |      +--0..1 PolicyNarrative(source hash/template version)
  |      idle/absolute/recent   +--< ExperimentIntent(version, context version)
  |                            +--< ProtectedParameter(version, confirmation)
  +--< OidcTransaction         |
  |      state hash/encrypted  +--< PlanCheck(full policy/evidence snapshots)
  |                            |      +--0..1 ApprovalRecord
  +--< McpOAuthGrant           |      +--0..1 RunManifest(immutable)
         |                     |
         +-- McpOAuthClient ---+--< ExperimentSubmission
              one project      |      +--< Artifact(S3 VersionId/evidence)
                               |      +--< SubmissionRisk
                               |      +--0..1 SubmissionEmbedding VECTOR(1024)
                               |      +--< WorkflowJob --< OutboxEvent
                               |
                               +--< Experiment
                                      +-- Submission/Manifest/Plan/Intent/Context trace
                                      +--< ExperimentMetric
                                      +--< Memory VECTOR(1024)
                                      +--< Artifact association
                               |
                               +--< AgentThread
                                      +--< AgentMessage
                                      |      +--0..1 AgentResearchReport(final response)
                                      +--0..1 current READY AgentContextSummary
                                      +--< AgentPolicyDraft(originating thread)
                                      |      +--< AgentPolicyDraftRevision(append-only)
                                      |             +--< AgentActionProposal(24h/digest/status)
                                      |                    targets Policy/Plan/Submission
                                      +--< AgentRun(lease/generation/retry)
                                             +--< AgentModelCall
                                             |      purpose=AGENT_TURN/CONTEXT_SUMMARY
                                             |      provider/model + schema hash
                                             |      usage/latency/provider request ID
                                             |      frozen configured rates/estimated cost
                                             +--< AgentToolCall
                                             |      +--0..1 AgentResearchReport(source snapshot)
                                             +--< AgentRunEvent
                                             +--< AgentCitation
                                             +--0..1 AgentContextSummary attempt
                                      +--0..1 ExperimentPlan
                                             +--< ExperimentPlanRevision(append-only)
                                                    +--0..1 ExperimentPlanReview
                                                    +--0..1 ExperimentPlanDecision
                                      +--< AgentResearchReport
                                             +--< AgentResearchMemory(CANDIDATE)
                                                    +--< AgentResearchMemoryEmbedding
                                                         lease/generation/provider version

AccessToken                     local API/stdio MCP compatibility
IdempotencyRecord               unique actor + operation + key
AuditLog                        actor, Session/Token/Client/Grant evidence
```

## 完整业务链路

```text
Owner Cognito login
-> publish confirmed Context/Intent/Constraint version (recent auth)
   -> deterministic human-readable representation
      -> READY: version/hash-bound Markdown
      -> FAILED: formal policy remains active; Owner may regenerate
-> Researcher OAuth MCP project_get_context
   -> human_readable for understanding + complete structured authority
-> experiment_check_plan
   -> BLOCKED: stop
   -> NEEDS_APPROVAL: Owner Web approval (recent auth)
   -> PASS: continue
-> run_manifest_create from frozen Plan Check snapshots
-> user runs experiment outside Guardian
-> submission_prepare -> S3 presigned PUT with checksum and no overwrite
-> submission_finalize -> fixed VersionId/cloud hash verification
-> deterministic parse/manifest/duplicate/risk workflow
-> SQS 或 DB Queue Worker -> constrained summary -> embedding -> deterministic receipt
-> NEEDS_REVIEW
-> Researcher or Owner Web confirmation according to risk policy
-> one CockroachDB transaction creates formal Experiment/Metric/Memory/artifact links
-> team Web/MCP query with structured filters before vector candidate ordering

Owner/Researcher Web Agent message
-> idempotently persist user message + queued AgentRun
-> Agent Worker atomically claims lease/generation
-> bounded Bailian Function Calling
-> authorized tools return structured facts/analysis, append a candidate-only draft revision,
   prepare an immutable Policy/Plan/Submission proposal, or freeze an explicit experiment set
-> validate answer/citations
-> atomically persist assistant message, citations, optional immutable Research Report,
   completion and AuditLog
-> browser SSE replays durable events; disconnect does not cancel the run
-> Web draft workbench edits full Bundle with optimistic revision and displays deterministic diff/impact
-> Web report workbench reads project-shared candidate reports and source-change warnings
-> no execute tool exists; proposal confirmation is a separate recent-authenticated Web transaction

External Coding Agent plan
-> MCP submits full natural-language plan + optional evidence in an existing external task
-> service freezes current Context/Intent/Constraints and runs deterministic hard checks
-> Agent Worker checks policy freshness, then produces a cited semantic review
-> only fully auto-fixable text issues may append another revision, at most two rounds
-> Web user reviews exact revision, candidate invariants, hashes and impact receipt
-> independent recent-authenticated decision freezes the plan-level authorization
-> formal experiment_check_plan remains mandatory before Run Manifest creation
```

## 不在框架内

* 自建密码认证、密码恢复或应用密码策略；
* 把 local_owner 暴露到局域网、公网或 production；
* 动态 MCP 客户端注册和任意第三方零配置接入；
* 自动训练、自动改代码或自动批准；
* CRITICAL 风险人工绕过；
* baseline 自动晋升、复杂邀请、多级审批、Dashboard 或周报；
* 对实验正确性、训练行为或 LLM 可靠性的绝对保证。
