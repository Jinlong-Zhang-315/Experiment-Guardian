# R14 AWS 部署手册

更新时间：2026-07-22

## 部署组成

Terraform 位于 `infra/terraform/`，创建以下演示资源：

```text
Route53 -> CloudFront + WAF
             |-- private S3 Web bucket
             |-- /api/* -> HTTPS ALB -> private ECS API
             |-- /mcp + RFC9728 metadata -> HTTPS ALB -> private ECS MCP

private ECS Worker -> SQS Standard -> DLQ
API/MCP/Worker -> Cockroach Cloud (external)
API/MCP/Worker -> versioned KMS S3 Artifact bucket
Worker -> Bedrock Converse + Titan Embeddings V2
Cognito Managed Login -> Web OIDC and pre-registered MCP public clients
```

ALB 只接受带 Terraform 随机 `X-Origin-Verify` 值的转发规则，CloudFront 自动注入该 Header。
Artifact Bucket 强制 Block Public Access、KMS 和 Versioning。CockroachDB 不在 AWS Terraform
中创建，连接串通过 Secrets Manager 注入。

## 前置条件

* Terraform 1.8 以上；
* AWS 账号、Route53 公有域名；
* us-east-1 CloudFront ACM 证书和部署 Region 的 ALB ACM 证书；
* Cockroach Cloud 集群和 `sslmode=verify-full` 连接串；
* 已允许的 Bedrock 摘要模型和 Titan Embeddings V2；
* Docker、Node 22 和 AWS CLI；
* Cognito MCP 客户端精确 callback URI 清单。

Terraform State 含敏感输入和生成密钥，必须使用启用加密、版本和锁的远程 Backend；示例不
替团队选择 State Backend。

## 1. 初始化和校验

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan -out r14.tfplan
terraform apply r14.tfplan
```

`mcp_clients` 是唯一允许的远程客户端集合。不要通过控制台临时增加未记录客户端。

## 2. 构建后端镜像

```bash
REPOSITORY_URL=$(terraform output -raw ecr_repository_url)
TAG=r14-demo
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${REPOSITORY_URL%%/*}"
docker build -t "$REPOSITORY_URL:$TAG" ../..
docker push "$REPOSITORY_URL:$TAG"
```

修改 `container_image_tag` 后再次 `terraform apply`。生产发布使用不可变 Git SHA tag，不使用
`latest`。镜像先安装 `requirements/lock-server.txt`，再以 `--no-deps` 安装本包。
受限网络可以通过 `--build-arg PIP_INDEX_URL=https://<approved-mirror>/simple` 指定团队批准的
Python 镜像源；不要把包含凭据的 URL 写入 Dockerfile 或仓库。
若用 `web/Dockerfile` 构建静态镜像，可同样用
`--build-arg NPM_CONFIG_REGISTRY=https://<approved-registry>` 指定团队批准的 npm registry。

## 3. 发布 Web 静态文件

```bash
cd ../../web
npm ci
npm run lint
npm run test
npm run build
WEB_BUCKET=$(cd ../infra/terraform && terraform output -raw web_bucket)
aws s3 sync dist/ "s3://$WEB_BUCKET/" --delete
aws cloudfront create-invalidation \
  --distribution-id "$(cd ../infra/terraform && terraform output -raw cloudfront_distribution_id)" \
  --paths '/*'
```

核心应用在运行时不拥有 Web Bucket 写权限；静态发布凭据应与 ECS Task Role 分离。

## 4. 数据库迁移

在与 Cockroach Cloud 网络可达且使用同一 Secrets Manager 配置的一次性受控任务中执行：

```bash
alembic upgrade head
alembic current
```

当前 head 为 `20260722_12`：revision 11 增加 Cognito subject、Web Session 和 OIDC 事务；
revision 12 增加预注册 MCP Client 与可撤销 Grant。部署应用前先升级，降级前先停止新流量。

## 5. 创建用户和绑定客户端

管理员先在 Cognito 创建 Owner/Researcher，再通过现有 CLI 创建对应 CockroachDB User 和
TeamMembership。应用首次登录会用已验证邮箱绑定 `cognito_sub`，不会自助创建成员。

Terraform 输出 Cognito MCP client ID 后，还必须建立单项目本地绑定：

```bash
experiment-guardian-admin register-mcp-oauth-client \
  --owner-email owner@example.com \
  --project-id "$PROJECT_ID" \
  --client-id "$COGNITO_MCP_CLIENT_ID" \
  --name "Codex demo client"
```

R14 远程客户端固定使用七个已声明 scope；不要通过 `--scopes` 创建子集配置，CLI 会拒绝此类
在当前 FastMCP 全局 scope 门槛下无法连接的客户端。

撤销入口：

```bash
experiment-guardian-admin revoke-mcp-oauth-grant \
  --owner-email owner@example.com --project-id "$PROJECT_ID" \
  --client-id "$COGNITO_MCP_CLIENT_ID" \
  --member-email researcher@example.com --reason "demo completed"

experiment-guardian-admin revoke-mcp-oauth-client \
  --owner-email owner@example.com --project-id "$PROJECT_ID" \
  --client-id "$COGNITO_MCP_CLIENT_ID" --reason "client retired"
```

## 6. 部署验收

```bash
python scripts/verify_r14_deployment.py \
  --base-url "$(terraform -chdir=infra/terraform output -raw application_url)"
```

脚本验证 Web、API health、RFC 9728 Protected Resource Metadata、Cognito discovery、七个
scope 和 DCR 禁用边界。可选传入临时 Owner/Researcher Session Cookie 验证角色，但不要把
Cookie 写入命令历史、日志或仓库。

## 运行告警

至少为以下信号建立 CloudWatch Alarm：ECS desired/running count 不一致、ALB 5xx、Target
unhealthy、SQS oldest message、DLQ visible messages、Worker task exit、WAF blocked request 和
应用日志中的 `SERVICE_UNAVAILABLE`。R14 Terraform创建日志、WAF指标和DLQ，报警接收人由
部署团队配置，不在仓库硬编码。
