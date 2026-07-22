# Experiment Guardian 当前框架图

更新时间：2026-07-22
当前实现：R14
数据库 head：`20260722_12`

本文只描述当前仓库已经实现的结构。`[DONE]` 表示已有代码与自动化验证，`[EXTERNAL]`
表示由部署环境提供，`[MANUAL]` 表示最终演示仍需真实 AWS/Cognito 人工验收。

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
+---------+--------+          | - management APIs   |
          |                   +----------+-----------+
          |                              |
          |                              v
          |                   +----------------------+
          |                   | Application services|
          |                   | [DONE]               |
          |                   | deterministic rules  |
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

ECR -> immutable application image
CloudWatch Logs/Metrics -> API/MCP/Worker, ALB, WAF, SQS
```

Terraform 位于 `infra/terraform/`，已经通过 Terraform 1.9.8 和 AWS Provider 6.55 schema
验证。实际 `apply`、Bedrock model access、域名证书和双角色演示属于部署环境验收。

## 代码分层

```text
src/experiment_guardian/
|
+-- api/
|   +-- auth.py                 Cognito redirect/callback/me/logout/reauth
|   +-- web.py                  四页读取、策略发布、下载 URL、查询
|   +-- plan_checks.py          Owner 计划决定
|   +-- submissions.py          草稿最终决定
|
+-- mcp_server/server.py        七个 MCP 工具 + HTTP health
|
+-- application/
|   +-- web_auth.py             OIDC transaction、Session、CSRF、撤销
|   +-- web_management.py       页面读取、版本发布、Artifact 下载
|   +-- services.py             Context/Plan/Manifest/Submission 主线
|   +-- experiments.py          正式确认与结构化/向量查询
|   +-- submission_analysis.py  可恢复确定性分析
|   +-- async_summary.py        Outbox/SQS/摘要
|   +-- async_review.py         embedding/确定性回执
|
+-- domain/                     Pydantic 契约、状态、纯确定性规则
|
+-- infrastructure/
|   +-- cognito.py              人类 OIDC Provider adapter
|   +-- mcp_oauth.py            Cognito Access Token + 本地 Grant verifier
|   +-- storage.py              固定 S3 VersionId 上传/读取/下载
|   +-- database.py/models/     SQLAlchemy/CockroachDB
|   +-- queue.py/bedrock.py     SQS 与 Bedrock adapters
|
+-- workflows/                  LangGraph 固定拓扑，DB 游标负责恢复
+-- worker.py                   单并发可租约 Worker
+-- admin_cli.py                成员/本地 token/预注册 MCP 客户端管理

web/                            React 19 + Vite + TanStack Query + Lucide
infra/terraform/                AWS/Cognito/ECS/CloudFront/S3/SQS IaC
demo/r14/                       最终演示输入文件
scripts/verify_r14_deployment.py 公开部署认证/发现验收
```

依赖方向保持：接口层 -> 应用层 -> 领域层；基础设施实现应用端口。前端不重新计算风险、
审批资格或角色权限。

## 数据关系

```text
User(cognito_sub)
  +--< TeamMember >-- Team --< Project
  |                            |
  +--< WebSession              +--< ProjectContext(version, supersedes)
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

AccessToken                     local API/stdio MCP compatibility
IdempotencyRecord               unique actor + operation + key
AuditLog                        actor, Session/Token/Client/Grant evidence
```

## 完整业务链路

```text
Owner Cognito login
-> publish confirmed Context/Intent/Constraint version (recent auth)
-> Researcher OAuth MCP project_get_context
-> experiment_check_plan
   -> BLOCKED: stop
   -> NEEDS_APPROVAL: Owner Web approval (recent auth)
   -> PASS: continue
-> run_manifest_create from frozen Plan Check snapshots
-> user runs experiment outside Guardian
-> submission_prepare -> S3 presigned PUT with checksum and no overwrite
-> submission_finalize -> fixed VersionId/cloud hash verification
-> deterministic parse/manifest/duplicate/risk workflow
-> SQS Worker -> constrained summary -> embedding -> deterministic receipt
-> NEEDS_REVIEW
-> Researcher or Owner Web confirmation according to risk policy
-> one CockroachDB transaction creates formal Experiment/Metric/Memory/artifact links
-> team Web/MCP query with structured filters before vector candidate ordering
```

## 不在框架内

* 自建密码认证、密码恢复或应用密码策略；
* 动态 MCP 客户端注册和任意第三方零配置接入；
* 自动训练、自动改代码或自动批准；
* CRITICAL 风险人工绕过；
* baseline 自动晋升、复杂邀请、多级审批、Dashboard 或周报；
* 对实验正确性、训练行为或 LLM 可靠性的绝对保证。
