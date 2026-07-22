# Experiment Guardian 迭代实现与计划

更新时间：2026-07-22
当前完成轮次：R12b
下一轮：R13 正式实验确认与结构化/向量查询

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
| R10 | 上传确认与 S3 复核 | 完成 | UPLOAD_VERIFIED 与原子云端证据 |
| R11 | 可恢复的确定性分析前半程 | 完成 | 解析、校验、查重和风险 |
| R12a | 可靠异步编排与摘要 | 完成 | Outbox + SQS Worker + Bedrock 摘要 |
| R12b | embedding 与审核回执 | 完成 | VECTOR(1024) + 确定性回执 + NEEDS_REVIEW |
| R13 | 正式实验确认、查询和向量候选 | 下一轮 | 不属于当前轮次 |
| R14 | Web 页面与 AWS 演示部署 | 排队 | 不属于下一轮 |

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

### R10：上传完成确认与 S3 对象复核

交付：

* revision `20260722_06` 增加 Submission 上传复核人、时间、回执快照以及 Artifact
  复核时间、S3 version ID 和结构化证据；状态列扩至 `VARCHAR(32)`。
* MCP `submission_finalize` 只接受 `submission_id` 和 `idempotency_key`，调用者身份来自
  项目绑定 Token，并要求 `submission:finalize` scope；原提交者或项目 Owner 可执行。
* S3 适配器使用 `HEAD` 与 `ChecksumMode=ENABLED` 获取对象存在性、Content-Length、
  Content-Type、ChecksumSHA256、ETag、VersionId 和观测时间。
* finalize 强制要求不可变 VersionId；错误或无版本对象使用新的随机 object key 重传，
  保持 `If-None-Match: *` 防覆盖语义。
* 对象缺失或声明不匹配返回完整的可重试 FAILED 回执，Submission 保持 `RECEIVED`，
  不写入任何部分 Artifact 验证结果。
* 同一失败 key 可在修复对象后重新检查；相同 key 对不同 Submission 的异体请求冲突。
* 全部对象通过后，一个 CockroachDB 事务原子写入 Artifact `CLOUD_VERIFIED` 证据、
  Submission `UPLOAD_VERIFIED`、AuditLog 和 IdempotencyRecord。
* 成功幂等重放直接返回原回执，不再次访问 S3；prepare 重放也不再签发上传 URL。
* S3 服务暂时不可用时不绑定失败幂等结果，不修改 Submission 或 Artifact，可用相同 key
  安全重试。
* 每次确定性验证失败都写入独立 AuditLog；成功和失败记录均包含具体 Token ID、原始
  `source_agent` 和 Owner 恢复模式，后续成功不会覆盖历史失败事实。
* 迁移 downgrade 会先把新版 `UPLOAD_VERIFIED` 映射为 R9 可理解的 `RECEIVED`，再恢复
  旧状态列长度，同时清除 R9 无法解释的 Artifact 云端验证标记，保证存在实际数据时
  也能回滚。
* 106 项测试被收集，104 项默认执行全部通过；CockroachDB 隔离库验收已显式执行通过，
  真实 AWS S3 PUT/HEAD 测试因未配置专用 Bucket 和凭据而默认跳过。

R10 没有下载或解析 CONFIG/RESULT，不启动 LangGraph，不生成风险、摘要或 embedding，
也不实现正式实验确认、查询和 Web。

### R11：可恢复的确定性提交分析前半程

交付：

* revision `20260722_07` 为 Submission 增加 `workflow_status`、`processing_step`、
  `processing_error` 和有界 `analysis_snapshot`，并正式迁移 `submission_risks`。
* `submission_finalize` 上传验证成功后同步启动分析；相同 key 重放动态合成当前分析状态，
  上传幂等快照不会固化旧工作流状态。
* LangGraph 生产图只编排前五个连续节点；数据库业务表游标是唯一恢复依据，不引入
  PostgreSQL checkpointer。
* CONFIG/RESULT 只按上传验证保存的 S3 VersionId 下载，限制为 1 MiB，下载后再次核对
  SHA-256；真实 S3 opt-in 测试覆盖指定版本 GET。
* CONFIG 复用重复键拒绝、JSON 标量语义和稳定规范化哈希；`result.json` 固定为
  `schema_version/status/metrics/timestamps/failure_reason`，拒绝额外字段、布尔指标、NaN、
  无时区时间和逆序时间。
* Manifest 校验覆盖追溯链、Manifest hash、配置原始/规范化 hash、配置快照、结果状态、
  指标声明和主指标。可解析的不一致形成阻断型 CRITICAL 风险，不由 LLM 降级。
* 查重先按 project 和可用状态过滤；相同 Manifest + CONFIG/RESULT hash 为非阻断 MEDIUM，
  同运行条件为非阻断 LOW，结果只作为候选证据。
* 每个节点独立事务提交。S3 暂时不可用进入 `RETRYABLE_FAILURE` 并可用同 finalize key
  恢复；不可变版本缺失、哈希变化或内容无效进入 `FAILED/TERMINAL_FAILURE`。
* 完成 R11 后保持 `PROCESSING/AWAITING_ENRICHMENT` 和 `processing_step=RISK_ANALYSIS`，
  没有提前伪装为 `NEEDS_REVIEW`。
* 125 项测试被收集，123 项默认执行全部通过；CockroachDB 隔离库验收显式通过，真实
  AWS S3 测试因未配置专用 Bucket/凭据默认跳过。

R11 没有调用 Bedrock、生成 embedding/审核回执、确认正式实验、查询向量或开发 Web。

## R12a：可靠异步编排与摘要

交付：

* revision `20260722_08` 只增加 `generated_summary`、`workflow_jobs` 和
  `outbox_events`，没有提前迁移 embedding 或审核回执字段。
* R11 风险节点在同一数据库事务内创建唯一摘要 Job 和 Outbox；升级前停在 R11 终点的
  Submission 由 Worker 启动时对账补齐。
* SQS Standard 消息只包含 schema、Job、Submission 和 generation；API 不直接发消息，
  Outbox 发布允许重复但不会丢失业务意图。
* 单并发 Worker 使用 120 秒数据库租约和 SQS 可见性超时；摘要 Job 最多尝试五次，按
  30/120/480/... 秒退避并封顶 3600 秒，最终进入可人工重置的 `DEAD_LETTER`。
* Bedrock Converse 输入只使用 Intent 目标、Manifest 运行条件、结果和已存在风险；不发
  LOG/NOTE，不接收工具调用，不允许模型新增风险、修改等级、批准或断言实验正确。
* 摘要最多 3000 字，保存 model、prompt version、source hash、生成时间、usage 和明确
  disclaimer；它是模型解释，不被标为验证证据。
* 新增 `submission_get_status`，因此 MCP 边界从六个工具显式扩为七个；仅原提交者或
  Owner 可读取 Submission/Job/风险/摘要动态状态。
* `submission_finalize` 同 key 继续动态重放；新 key 可由原提交者或 Owner 恢复 R11 前缀，
  或对 retryable/dead 摘要 Job 增加 generation 重新入队，不重复访问 S3。
* 摘要成功后刻意停在 `PROCESSING/AWAITING_ENRICHMENT/SUMMARY_GENERATION`，没有提前
  生成 embedding、审核回执或进入 `NEEDS_REVIEW`。
* 默认严格 Fake 覆盖 Outbox 发布失败、发送后崩溃、重复/旧 generation、模型提交前崩溃、
  重试/死信/恢复、权限和降级；真实 SQS/Bedrock 验收通过环境开关显式执行。

R12a 不创建 SQS/DLQ/IAM/KMS 基础设施，不实现 embedding、审核回执、正式实验、查询、
Web、自动训练或自动改代码。

## R12b：embedding 与审核回执

交付：

* revision `20260722_09` 增加独立 `submission_embeddings`、Submission 审核回执字段，
  并扩展 Job/Outbox 类型；正式 Experiment/Memory Schema 保持未迁移。
* 摘要成功事务原子创建 `SUBMISSION_REVIEW_PREPARATION` Job 与 Outbox；Worker 启动会为
  revision 08 已有摘要补建缺失任务，两类 Job 继续共用同一 SQS Standard Queue。
* 固定使用 Titan Text Embeddings V2、1024 维和归一化输出；严格拒绝错误维度、布尔值、
  NaN/Infinity 与非归一化向量。
* `submission-search-v1` 输入只来自历史 Intent、Manifest、Plan Check 变化、结果和风险，
  不包含生成摘要、LOG、NOTE 或原始配置全文；输入文本、SHA-256、模型和 token 数可追溯。
* embedding 独立提交后再生成确定性短回执。该恢复边界保证回执提交前崩溃不会再次调用
  embedding 模型；外部调用后、向量提交前仍只承诺至少一次。
* 回执展示目标、版本追溯、关键运行条件、允许/审批变化、关键结果和证据边界；所有
  HIGH/CRITICAL 强制展开，LOW/MEDIUM 只保存折叠计数。
* 未解决 blocking 或 CRITICAL 风险得到 `BLOCKED`；HIGH 得到 `OWNER_ONLY`；其余得到
  `RESEARCHER_OR_OWNER`。风险权限完全由确定性数据计算，不读取摘要结论。
* 成功终态为 `NEEDS_REVIEW/COMPLETED/NEEDS_REVIEW`。该状态是数据库交接，不是
  LangGraph 原生人工中断。
* `submission_get_status` 向后兼容保留 `job`，新增有序 `jobs`、embedding 元数据和审核
  回执；不返回原始向量或冻结输入全文。
* `submission_finalize` 新 key 会按当前阶段恢复 Summary 或 Review Job；同 key 重放继续
  动态返回最新分析状态，不再次访问 S3。

R12b 不实现正式实验确认、`experiments_query`、Web、AWS 资源创建、自动训练或自动改代码。

## 下一轮 R13：正式实验确认与查询

### 单一目标

在不调用外部模型的数据库事务中确认一个 `NEEDS_REVIEW` Submission，并让团队成员通过
结构化条件和向量候选查询正式 Experiment。

### 本轮包含

1. 按 R12b `review_eligibility` 和调用者角色执行 Researcher/Owner/阻断权限门禁。
2. 一个 CockroachDB 事务创建 Experiment、Metrics、artifact 关联、摘要和向量记忆，
   更新 Submission 并写入审计与确认幂等结果；事务内禁止调用 Bedrock/S3。
3. 正式记录完整追溯 Submission、Manifest、Plan Check、Intent 和 Context 版本。
4. `experiments_query` 先强制 project、确认状态、实验状态和 protocol 过滤，再使用向量
   相似度生成候选；DEPRECATED/SUPERSEDED 明确标记且默认不返回。
5. 只实现服务/MCP/数据库链路，不同时开发四个 Web 页面和 AWS 部署。

## 后续队列

后续轮次只表示顺序，不在 R12b 同时开发：

```text
R13 Transactional experiment confirmation + structured/vector query
R14 Four Web pages + AWS deployment + final demonstration
```

## 每轮更新要求

完成一轮后必须同步更新：

1. 本文件：当前轮状态、实际交付、下一轮唯一目标。
2. `docs/DEVELOPMENT_LOG.md`：逐项更新、修复和验证结果。
3. `docs/ARCHITECTURE.md`：模块状态、数据表和调用链。
4. README：只保留面向使用者的当前能力和启动方法。
