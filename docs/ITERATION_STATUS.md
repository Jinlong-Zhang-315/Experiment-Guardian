# Experiment Guardian 迭代实现与计划

更新时间：2026-07-21  
当前完成轮次：R9
下一轮：R10 上传完成确认与 S3 对象复核

本文档维护“每轮交付了什么”和“下一轮只做什么”。详细缺陷与修复过程见
`docs/DEVELOPMENT_LOG.md`，当前代码结构见 `docs/ARCHITECTURE.md`。

## 总体进度

| 轮次 | 目标 | 状态 | 主要验收结果 |
| --- | --- | --- | --- |
| R0 | 需求和 MVP 收敛 | 完成 | 单一 P0 纵向主线、六个 MCP 工具 |
| R1 | 环境和目录 | 完成 | Python 包与 CockroachDB 开发环境 |
| R2 | 领域/API/MCP/工作流骨架 | 完成 | 可启动、可测试的第一版框架 |
| R3 | 治理来源、证据、版本和追溯 | 完成 | 候选事实不能直接成为正式规则 |
| R4 | 配置检查运行级问题 | 完成 | 工作流可构建，重复键和路径碰撞被拒绝 |
| R5 | 本地证据和 YAML 类型安全 | 完成 | 核心证据不可全字段绕过 |
| R6 | 基础数据、认证和正式上下文读取 | 完成 | CockroachDB migration + API + MCP 读取链路 |
| R7 | 训练前检查持久化 | 完成 | 已认证、幂等、完整策略快照 Plan Check |
| R8 | 计划审批和 Run Manifest | 完成 | Owner 最终决策与不可变 Manifest |
| R9 | S3 草稿提交 | 完成 | RECEIVED 草稿、artifact 声明与短期 PUT URL |
| R10 | 上传确认与 S3 复核 | 下一轮 | 只接通 finalize 和对象元数据验证 |
| R11 | 可恢复提交分析与审核回执 | 排队 | 不属于下一轮 |
| R12 | 正式实验确认、查询和向量候选 | 排队 | 不属于下一轮 |
| R13 | Web 页面与 AWS 演示部署 | 排队 | 不属于下一轮 |

## 已完成轮次

### R0：需求和 MVP 收敛

交付：

* Owner/Researcher 权限矩阵。
* Context、Intent、Constraint、Plan Check、Manifest、Submission 和 Experiment 主线。
* 检查、审批、证据和提交工作流状态定义。
* MVP 输入格式和明确排除项。

### R1：环境和目录

交付：

* Conda/Python 依赖描述。
* CockroachDB Docker Compose。
* 分层包目录和环境变量模板。

### R2：第一阶段代码框架

交付：

* FastAPI 健康和能力接口。
* 六个 MCP 工具协议。
* 领域契约、枚举、ORM 模型和确定性规则引擎。
* LangGraph 提交分析固定拓扑。
* 单元测试与静态检查配置。

### R3：治理语义和证据边界

交付：

* 明确事实与推断事实的来源和确认状态。
* 正式/探索实验隔离。
* Context/Intent/Manifest/Submission 全链路版本字段。
* 本地声明、用户提供和云端验证证据边界。
* 向量查询前的结构化过滤契约。

### R4：配置检查正确性修复

交付：

* LangGraph 图构建修复。
* YAML/JSON 重复键拒绝。
* 无碰撞参数路径。
* baseline 与正式 expected value 校验。
* MCP 服务端身份来源。
* Pending 约束冲突完整展示。

### R5：证据适用性和 YAML 类型安全

交付：

* 核心本地证据不得 `NOT_APPLICABLE`。
* 可选证据必须说明不适用原因且不得同时给值。
* YAML 使用稳定的 JSON 标量语义。
* 配置规范化哈希保持可重复。

### R6：基础数据、认证和正式上下文读取

交付：

* Alembic revision `20260721_01` 和 10 张基础表。
* Token 哈希存储、audience、scope、过期、撤销和项目绑定。
* Owner/团队和 Token 管理 CLI。
* 原子且幂等的项目初始化 API。
* 数据库驱动的 `project_get_context` MCP 用例。
* 正式来源、版本、确认人和生效时间完整返回。
* 45 项自动化测试和 CockroachDB v26.2.0 实库迁移验证。

### R7：训练前检查持久化

交付：

* Alembic revision `20260721_02` 和单一新表 `plan_checks`。
* 数据库驱动的 `experiment_check_plan` MCP 用例。
* Context、Intent、约束、配置和本地证据快照的完整追溯。
* 基于服务端 Token 身份的 scope、项目、团队和成员权限检查。
* `(requester_id, idempotency_key)` 幂等重放和请求哈希冲突检查。
* PASS、NEEDS_APPROVAL、BLOCKED 全状态集成测试。
* revision `20260721_03` 补齐 Context/baseline、Intent 和原始配置历史快照。
* 审批状态在幂等重放时动态合成，不被不可变检查 report 固化。
* 严格配置类型比较、Intent/LOCKED 冲突门禁、输入边界和原始配置 SHA-256 核对。
* CockroachDB `40001` 服务端有界重试。
* 60 项默认自动化测试，另有 1 项已验证通过的真实 CockroachDB 隔离库集成测试。

当前可演示的最短链路：

```text
CLI bootstrap Owner/API Token
-> Owner 调用项目初始化 API
-> CLI 签发项目绑定 MCP Token
-> 本地 Agent 调用 project_get_context
-> 返回 Context v1 + Intent v1 + confirmed constraints
-> 本地 Agent 调用 experiment_check_plan
-> 持久化 Plan Check 并返回检查/审批状态和风险回执
```

### R8：计划审批和不可变 Run Manifest

交付：

* revision `20260721_04`，只新增 `approval_records` 和 `run_manifests`。
* `POST /projects/{project_id}/plan-checks/{plan_check_id}/decision` Owner 管理 API。
* `plan:approve` scope、Owner/团队/项目/状态校验和一次性最终决策。
* 批准与拒绝的不可重复 ApprovalRecord、AuditLog 和幂等回放。
* MCP `run_manifest_create`，身份来自项目绑定 Token 的 `manifest:create` scope。
* PASS/NOT_REQUIRED 和 NEEDS_APPROVAL/APPROVED 两条 Manifest 合法入口。
* 只从 Plan Check 历史快照提取配置、Context/Intent 版本、Git、命令、checkpoint、
  环境与证据，不读取当前策略补值。
* `schema_version=1` 的规范化 Manifest 哈希、单 Plan 唯一约束和异 key 冲突。
* 74 项默认测试通过，1 项真实 CockroachDB 隔离库验收显式执行并通过。

当前可演示链路已扩展为：

```text
experiment_check_plan
-> PASS: 直接 run_manifest_create
-> NEEDS_APPROVAL: Owner decision API -> APPROVED -> run_manifest_create
-> immutable RunManifest with complete version/evidence trace
```

### R9：S3 草稿提交

交付：

* revision `20260721_05`，只迁移 `experiment_submissions` 和 `artifacts`。
* MCP `submission_prepare`，身份来自项目绑定 Token 的 `submission:create` scope。
* 每个草稿必须恰好包含一个 CONFIG 和一个 RESULT；LOG 可多个，NOTE 和
  MANIFEST 各最多一个。
* 扩展名、Content-Type、单文件 20 MiB、总文件 100 MiB、SHA-256、文件名和
  数量边界在写库前统一校验。
* 运行结果只接受 `COMPLETED/FAILED`；完成运行必须提供有限数值的指标摘要。
* 一个事务创建 `RECEIVED` Submission、Artifact、AuditLog 和 IdempotencyRecord。
* 同一 Manifest 允许多次实验提交；同 key 同请求复用 Submission/Artifact ID，异体
  请求冲突。
* S3 PUT 地址在数据库提交后生成，不持久化；每次幂等重放生成新的短期 URL。
* 预签名请求绑定 Content-Type、Content-Length、SHA-256 checksum 和
  `If-None-Match: *`，防止超大上传和已有 artifact 被覆盖。
* 运行结果、指标和文件声明保存为 `LOCAL_ATTESTED`；Manifest 引用保存为
  `CLOUD_VERIFIED`，没有把未上传内容标记为云端已验证。
* 94 项测试被收集，92 项默认执行全部通过；CockroachDB 隔离库验收已显式
  执行通过，真实 AWS S3 兼容性测试默认跳过。

R9 没有实现 `submission_finalize`、S3 对象复核、提交分析、Bedrock 或正式实验确认。

## 下一轮 R10：上传完成确认与 S3 对象复核

### 单一目标

只实现 `submission_finalize`：根据数据库中的 artifact 声明复核 S3 对象，记录“上传已验证”
状态。不在该轮解析配置和结果，不启动 LangGraph。

### 本轮包含

1. 为 finalize 定义专用输入/回执契约，不接受客户端 `actor_id`。
2. 新增 `submission:finalize` scope，校验 Token 项目绑定、团队和成员资格。
3. 只允许 finalize 当前 `RECEIVED` 且拥有完整 artifact 声明的 Submission。
4. 对每个声明 object key 调用 S3 HEAD，比对 Content-Length、Content-Type 和
   `ChecksumSHA256`；不信任客户端回传的文件元数据。
5. 所有对象通过后，在单个 CockroachDB 事务中写入 Artifact 云端验证结果、
   Submission 过渡状态、AuditLog 和幂等结果。
6. 相同 key 重放返回原验证回执；异体请求冲突；部分缺失或不匹配不得留下
   “部分已验证”状态。
7. finalize 成功后，`submission_prepare` 的幂等重放不再为已完成上传的对象签发新 URL。

### 明确不包含

* 下载或解析 CONFIG/RESULT/LOG 内容。
* LangGraph 分析、查重、风险分析、Bedrock、embedding 和人工审核。
* 正式实验确认、查询、Web 页面和 AWS 部署。

### 验收条件

* 对象缺失、超时或任一元数据不匹配时 finalize 失败，Submission 仍可安全重试。
* 通过时所有 Artifact 均带 `CLOUD_VERIFIED` 的复核时间和来源。
* 默认 fake S3 测试、CockroachDB 隔离库事务测试和可选真实 S3 验收分层运行。
* R9 的 prepare、幂等、上传约束和全量旧功能保持回归通过。

## 后续队列

后续轮次只表示顺序，不在 R10 同时开发：

```text
R11 Recoverable submission analysis + review receipt
R12 Transactional experiment confirmation + structured/vector query
R13 Four Web pages + AWS deployment + final demonstration
```

## 每轮更新要求

完成一轮后必须同步更新：

1. 本文件：当前轮状态、实际交付、下一轮唯一目标。
2. `docs/DEVELOPMENT_LOG.md`：逐项更新、修复和验证结果。
3. `docs/ARCHITECTURE.md`：模块状态、数据表和调用链。
4. README：只保留面向使用者的当前能力和启动方法。
