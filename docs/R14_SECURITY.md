# R14 认证与安全边界

更新时间：2026-07-22

## 人类用户认证

人类用户只通过 Amazon Cognito User Pool Managed Login 登录。流程固定为 OIDC
Authorization Code Flow + PKCE S256：

```text
Browser -> GET /api/v1/auth/login
        -> Cognito Managed Login
        -> GET /api/v1/auth/callback?code&state
        -> FastAPI 校验 state/nonce/PKCE 并交换授权码
        -> 校验 ID Token 签名、issuer、audience、nonce、auth_time 和 verified email
        -> 绑定现有 User.cognito_sub
        -> 创建 web_sessions
        -> Browser 仅获得 Secure + HttpOnly + SameSite=Lax Session Cookie
```

前端不保存或读取 Cognito ID/Access/Refresh Token。OIDC 一次性事务保存 state 哈希和加密
PKCE/nonce，有效期 5 分钟，成功后不可重放。未知 Cognito 用户不能自助注册，管理员必须
先在 CockroachDB 创建 User 和 TeamMembership；首次登录只允许用 Cognito 已验证邮箱绑定。

应用没有 `password_hash`、`password_changed_at`、set-password CLI、自建密码恢复、自建密码
策略或登录失败限流。密码、MFA、恢复和身份风险策略由 Cognito 管理。

## Web Session

| 属性 | R14 值 | 执行位置 |
| --- | --- | --- |
| Idle timeout | 8 小时 | 每次服务端 Session 校验 |
| Absolute timeout | 7 天 | 数据库 `absolute_expires_at` |
| Recent authentication | 10 分钟 | Cognito `prompt=login` 后更新 |
| Cookie | Secure、HttpOnly、SameSite=Lax | FastAPI callback |
| Session 存储 | 仅 SHA-256 | CockroachDB `web_sessions` |
| CSRF | Session 绑定 HMAC，写请求 Header | FastAPI 依赖 |

团队角色和项目权限每次请求从 CockroachDB 读取。删除 TeamMembership 或撤销 Session 会立即
失效，不依赖 Cookie 自然过期。以下操作在 Web Session 下要求 10 分钟内重新认证：

* 发布新 Context/Intent/Constraint 正式版本；
* 对 `NEEDS_APPROVAL` Plan Check 作批准或拒绝的最终决定；
* Owner 批准仅 Owner 可确认的 High 风险 Submission。

服务返回 `428 RECENT_AUTHENTICATION_REQUIRED`，前端跳转 `/auth/reauth`，后端使用 Cognito
`prompt=login`。普通读取和 Submission 拒绝不要求重复认证。

## 远程 MCP OAuth

R14 远程 MCP 是 OAuth 受保护资源，不是自建 Authorization Server：

```text
MCP Client
  -> GET /.well-known/oauth-protected-resource/mcp       RFC 9728
  -> Cognito OIDC discovery
  -> Authorization Code + PKCE S256
     authorization 和 token 请求均携带 resource=https://.../mcp  RFC 8707
  -> Bearer Cognito Access Token
  -> MCP Server 校验 JWT + 本地授权状态
```

标准边界：

* 使用 MCP 2025-11-25 认证发现模型、RFC 9728、OIDC discovery、PKCE S256 和 RFC 8707；
* Cognito Public App Client 必须预注册，callback URI 必须精确匹配；
* 不实现 Dynamic Client Registration；
* 不实现 Client ID Metadata Documents；
* 不承诺任意第三方 MCP Client 的零配置接入；
* Access Token 15 分钟，Refresh Token 30 天并启用 rotation；
* 一个预注册客户端在 CockroachDB 中只能绑定一个 Project；
* 七个 OAuth scope 映射到七个现有应用 scope，工具内部仍执行项目和业务权限检查。
* 受当前 FastMCP 全局 scope 门槛限制，R14 预注册客户端固定申请完整七 scope；CLI 拒绝创建
  实际无法连接的子集 scope 客户端。按工具最小 scope 不在本轮扩展。

每个远程请求依次验证：JWT 签名、issuer、expiration、`token_use=access`、resource audience、
预注册 `client_id`、允许 scope、`User.cognito_sub`、TeamMembership、客户端撤销状态和本地
Grant 撤销状态。本地 Client 或 Grant 被撤销后，未过期 Cognito Token 也会立即被拒绝。

本地 stdio MCP 在开发环境仍可使用数据库哈希 Token；AWS 演示必须使用 HTTP OAuth。

## 不作出的承诺

认证成功只证明调用者和授权上下文满足当前策略。Experiment Guardian 的定位仍是：

> 提高实验一致性、可追溯性和风险可见性的治理系统。

它不保证实验一定正确，不完整验证训练行为，也不把本地 Agent 声明描述为云端事实。
