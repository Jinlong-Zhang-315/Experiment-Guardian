# Experiment Guardian 当前框架图

更新时间：2026-07-23
当前实现：R15a 只读内部实验治理 Agent

下一计划：R15b 确定性比较、统计、诊断和上下文压缩。R15 总体边界见
`INTERNAL_GOVERNANCE_AGENT_PLAN.md`。
数据库 head：`20260723_15`

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
| - Governance AI |          | - Agent SSE API     |
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
                                         | [DONE 7 tools]       |
                                         | JWT + client/grant   |
                                         | + membership checks  |
                                         +----------+-----------+
                                                    |
                                                    v
                                         GuardianApplication
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
   +-- Bailian streaming Function Calling
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
                                  +--> four authorized read-only tools

minio-init: create bucket + enable/verify Versioning, then exit
database-init -> migration -> local-init: ordered one-shot services
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
|
+-- mcp_server/server.py        七个 MCP 工具 + HTTP health
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
|   +-- agent_tools.py          四个实时鉴权的只读工具
|
+-- domain/                     Pydantic 契约、状态、纯确定性规则
|   +-- policy_narrative.py     结构化策略来源哈希与确定性 Markdown
|
+-- infrastructure/
|   +-- cognito.py              人类 OIDC Provider adapter
|   +-- mcp_oauth.py            Cognito Access Token + 本地 Grant verifier
|   +-- storage.py              AWS/MinIO 固定 VersionId 上传/读取/下载
|   +-- database.py/models/     SQLAlchemy/CockroachDB
|   +-- queue.py                SQS/DatabaseOutboxQueue adapters
|   +-- bedrock.py/bailian.py   摘要与 embedding adapters
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
```

依赖方向保持：接口层 -> 应用层 -> 领域层；基础设施实现应用端口。前端不重新计算风险、
审批资格或角色权限。

## R15a Agent 当前框架图

```text
Web 治理 Agent 页
   |
   | existing WebSession + project binding
   v
Agent API
   |
   +--> AgentConversationService
   |       +--> Thread / Message / Run / Citation [DONE]
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
   |       +--> AgentChatModel
   |       |       +--> Bailian streaming Function Calling [DONE]
   |       |       +--> other provider adapters [PLANNED]
   |       +--> AgentToolRegistry
   |               +--> project_status_get_v1 [DONE]
   |               +--> experiments_list_v1 [DONE]
   |               +--> experiment_get_v1 [DONE]
   |               +--> pending_work_list_v1 [DONE]
   |               +--> ANALYSIS/DRAFT/EXECUTE [NOT REGISTERED]
   +--> validated AgentAnswer + evidence/citations + AuditLog [DONE]

Experiment Memory VECTOR(1024)       formal confirmed experiments only
Agent Research Memory                not present; planned R15e separate store
```

R15a 没有草稿或正式写工具。模型参数不能传入 `user_id`、`team_id` 或 `project_id`；服务端从
Web Session 绑定身份和项目，每次工具执行重新校验 Membership。模型只看到受限结构化结果，
最终回答必须引用本轮实际取得的 evidence ID。

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
                                      +--< AgentRun(lease/generation/retry)
                                             +--< AgentModelCall
                                             +--< AgentToolCall
                                             +--< AgentRunEvent
                                             +--< AgentCitation

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
-> authorized read-only tools return structured facts + evidence
-> validate answer/citations
-> atomically persist assistant message, citations, completion and AuditLog
-> browser SSE replays durable events; disconnect does not cancel the run
```

## 不在框架内

* 自建密码认证、密码恢复或应用密码策略；
* 把 local_owner 暴露到局域网、公网或 production；
* 动态 MCP 客户端注册和任意第三方零配置接入；
* 自动训练、自动改代码或自动批准；
* CRITICAL 风险人工绕过；
* baseline 自动晋升、复杂邀请、多级审批、Dashboard 或周报；
* 对实验正确性、训练行为或 LLM 可靠性的绝对保证。
