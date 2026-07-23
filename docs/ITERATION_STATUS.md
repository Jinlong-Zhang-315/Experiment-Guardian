# Experiment Guardian 迭代实现与计划

更新时间：2026-07-23
当前完成轮次：R14 + Local deployment profile
下一步：真实百炼与真实 AWS 双环境验收，不扩展 MVP

本文维护每轮交付和紧邻下一步。详细修改见 `DEVELOPMENT_LOG.md`，当前文本框架图见
`ARCHITECTURE.md`。

## 总体进度

| 轮次 | 目标 | 状态 | 主要结果 |
| --- | --- | --- | --- |
| R0-R5 | 需求收敛、骨架、配置和证据正确性 | 完成 | 确定性规则、版本、来源、严格配置解析 |
| R6 | 基础数据、Token 和正式上下文 | 完成 | CockroachDB、CLI、项目初始化、Context MCP |
| R7 | 训练前检查持久化 | 完成 | 完整历史快照、幂等、严格类型、事务重试 |
| R8 | 计划审批和 Run Manifest | 完成 | Owner 最终决定、不可变 Manifest |
| R9-R10 | S3 草稿上传与验证 | 完成 | 防覆盖、哈希、VersionId、失败审计和恢复 |
| R11 | 可恢复确定性分析 | 完成 | 解析、Manifest 校验、查重和风险 |
| R12a | Outbox/SQS/Bedrock 摘要 | 完成 | 可租约 Worker、至少一次、安全摘要边界 |
| R12b | embedding 与审核回执 | 完成 | VECTOR(1024)、确定性权限回执、NEEDS_REVIEW |
| R13 | 正式实验确认与查询 | 完成 | 单事务确认、结构化过滤优先的向量候选 |
| R14a | Cognito Web 认证与管理 API | 完成 | OIDC+PKCE、服务端 Session、CSRF、近期认证 |
| R14b | 四个收敛 Web 页面 | 完成 | React/Vite 设置、计划、审核、查询页面 |
| R14c | 远程 MCP OAuth | 完成 | RFC9728、Cognito RS、预注册客户端、本地撤销 |
| R14d | AWS 部署定义 | 完成 | Terraform ECS/CloudFront/Cognito/S3/SQS/WAF |
| R14e | 最终演示与运维材料 | 完成 | 验收脚本、双角色 Runbook、文档同步 |
| R14 Local | 单机可替换基础设施 | 完成 | local_owner、MinIO、DB Queue、百炼、Compose |

## R14 Local：可替换基础设施部署

交付：

* `DEPLOYMENT_MODE=cloud/local` 条件配置校验，本地启动不读取或实例化 Cognito、SQS、AWS
  S3 或 Bedrock 客户端。
* `local_owner` 只接受唯一 Team 的真实 Owner Membership，继续使用 WebSession、CSRF、实时
  RBAC、近期认证和审计；production 启动直接拒绝。
* local_owner 只允许配置的回环 URL；Nginx 和 FastAPI 两层 Host 白名单在 Session 签发前
  拒绝 DNS rebinding Host。
* MinIO 复用 S3 协议，要求真实 VersionId、固定版本读取、SHA-256/大小/类型验证和防覆盖。
* DatabaseOutboxQueue 复用 Outbox、WorkflowJob、lease、generation、重试和死信状态。
* 百炼 OpenAI-compatible 摘要/embedding 适配器；固定 1024 维、有限数值和范数校验，保存
  provider/model/dimension/document version；畸形成功响应统一进入可持久化重试和死信路径。
* Compose 明确串行 database-init、migration、local-init，并由 minio-init 开启 Versioning。
* `bootstrap-local` 幂等创建 User、Team、Owner Membership、Project 和首版正式策略。

## R14a：托管身份认证与管理 API

交付：

* 删除最终计划中的自建密码方案；仓库没有密码列、set-password CLI 或应用密码流程。
* Cognito Managed Login 使用 Authorization Code + PKCE S256，后端校验 state、nonce、issuer、
  audience、签名、auth_time 和 verified email 后交换授权码。
* Browser 只保存 HttpOnly Session Cookie；数据库只保存 Session SHA-256。
* Session idle 8 小时、absolute 7 天、recent auth 10 分钟；写请求使用 Session 绑定 CSRF。
* 首次登录只绑定管理员预建的相同 verified email User，不允许自助加入团队。
* 角色和成员关系每个请求实时读取；Session 撤销和成员删除立即生效。
* 策略发布、Plan 最终决定和 Owner High-risk 批准使用 Cognito `prompt=login` 近期认证。
* Web 管理 API 提供项目、设置历史、策略版本发布、Plan/Submission/Experiment 列表详情和
  固定 S3 VersionId 的短期下载地址。
* 策略发布不会覆盖 Context/Intent/约束旧版本，也不会修改既有 Manifest。
* revision `20260722_11` 增加 `User.cognito_sub`、`web_sessions`、`oidc_transactions`。

## R14b：四个 Web 页面

交付：

* React 19、TypeScript、Vite、TanStack Query、React Router 和 Lucide。
* 项目设置页显示生效版本、确认信息、约束和历史，Owner 可发布完整新版本。
* 计划审批页显示参数变化、当前动态审批状态、风险、命令和决定操作。
* 实验审核页显示短回执、强制展开 High/Critical 风险、Artifact 和权限动作。
* 实验查询页提供正式记录浏览及结构化条件先行的向量候选查询。
* 401 显示 Cognito 登录入口，428 自动进入 reauth；前端不持久化 Cognito Token。
* Desktop/Mobile 自适应，按钮和状态尺寸稳定，紧凑工作台布局，无 Dashboard 扩展。
* Playwright 在桌面和移动视口跑通四页导航并检查页面级横向溢出，设置页截图已人工复核。

## R14c：标准 OAuth + 预注册 MCP 客户端

交付：

* Streamable HTTP MCP 作为 Cognito OAuth Resource Server，stdio 保留本地 Token。
* MCP SDK 自动公开 RFC 9728 Protected Resource Metadata 和规范 401 challenge。
* Cognito OIDC discovery、PKCE S256、RFC 8707 resource audience 和七个 OAuth scope。
* JWT 之后再次检查本地预注册 client、单项目绑定、User sub、TeamMembership、scope 和 Grant。
* 本地 Client/Grant 即时撤销优先于 Access Token 自然过期。
* CLI 支持 register/revoke MCP OAuth client 和 revoke user grant，记录具体审计。
* R14 客户端固定使用完整七 scope，避免 FastMCP 全局 scope 门槛下的无效子集配置。
* 明确不实现 DCR 或 Client ID Metadata Documents。
* revision `20260722_12` 增加 `mcp_oauth_clients` 和 `mcp_oauth_grants`。

## R14d：AWS 演示部署

交付：

* 后端和 Web Dockerfile；后端按锁文件安装依赖并以非 root 用户运行。
* Terraform 定义 VPC、公私子网、NAT、CloudFront、WAF、私有 Web S3、HTTPS ALB、私网
  ECS API/MCP/Worker、ECR、CloudWatch、IAM 和 Secrets Manager。
* Artifact S3 开启 KMS、Versioning 和 Public Access Block；SQS Standard 配置 KMS 与 DLQ。
* Cognito User Pool 使用 Managed Login v2、仅管理员建用户、Web confidential client 和显式
  `mcp_clients` public client map。
* CloudFront 转发 API/MCP/OAuth metadata，ALB 规则要求随机 Origin Header，减少直接绕过。
* Cockroach Cloud 作为外部依赖，不在 Terraform 中创建。
* Terraform 1.9.8、AWS Provider 6.55 和 Random Provider 3.9 schema 验证通过。
* 后端/Web 镜像均完成实际构建和启动；后端 health 与 Web 根路径/SPA 深层路由返回 200。
* Server lock 已补齐 boto3 传递依赖；Web 构建上下文和 Nginx 延迟解析已完成运行级修复。

## R14e：验收与文档

交付：

* `scripts/verify_r14_deployment.py` 验证 Web、API、RFC9728、Cognito discovery、scope 与
  DCR 禁用边界，可选验证双角色 Session。
* `demo/r14/` 保存 BLOCKED、NEEDS_APPROVAL、结果、日志和说明演示输入。
* `R14_DEPLOYMENT.md`、`R14_SECURITY.md`、`R14_DEMO.md` 分别维护部署、安全和六场景演示。
* README、开发日志和框架图同步至 R14。
* 本地真实 CockroachDB 完成 revision 12 升级、跨版本降级和再升级验收。

## 下一步唯一目标

不开始 R15 功能开发，只执行真实环境验收：

1. 在目标 AWS 账号执行 `terraform plan/apply`，完成域名、证书和 Bedrock model access。
2. 构建推送不可变镜像，升级 Cockroach Cloud 到 revision `20260722_13`，发布 Web 静态文件。
3. 创建 Owner/Researcher Cognito 用户并完成现有 User sub 绑定。
4. 用一个真实预注册 MCP Client 跑通 OAuth discovery/code/PKCE/resource 和七个工具。
5. 按 `R14_DEMO.md` 完成六场景并保存无敏感信息的验收证据。
6. 建立报警接收人、备份恢复和回滚演练记录。
7. 使用真实百炼摘要和 1024 维 embedding 模型跑一次本地 Submission 全链路。

只有真实验收发现缺陷时修复对应模块；不增加自动训练、自动改代码、复杂成员管理、动态
客户端注册、向量索引或 baseline 自动晋升。

## 每轮更新规则

完成修改后必须同步：

1. 本文件：交付状态和唯一下一步。
2. `DEVELOPMENT_LOG.md`：具体更新、修复、验证和遗留。
3. `ARCHITECTURE.md`：当前模块、数据关系和调用链。
4. README：使用者可见能力和启动/部署入口。
