# Experiment Guardian 开发日志

本文档按时间追加，记录每次可识别的开发更新、问题修复、验证结果和遗留项。它描述
仓库中实际发生的变化，不把需求文档中的规划写成已完成功能。

## 维护规则

1. 每次功能实现或缺陷修复完成后，在文件顶部“最新更新”之后追加一节。
2. 每条记录至少包含：更新内容、修复问题、验证方式、明确未完成项。
3. 已提交更新记录 Git commit；尚未提交的工作记录为 `working tree`。
4. 不删除旧记录。需求变化使用新记录说明替代关系。

## 更新索引

| 日期 | 轮次 | 版本标识 | 主题 |
| --- | --- | --- | --- |
| 2026-07-21 | R0 | 需求收敛 | P0 纵向主线和治理边界 |
| 2026-07-21 | R1 | `b981106` | 环境与仓库目录初始化 |
| 2026-07-21 | R2 | `e519cd5` | 第一阶段代码框架 |
| 2026-07-21 | R3 | `43eacd8` | 治理语义、证据和追溯修正 |
| 2026-07-21 | R4 | `ccc5d7b` | 配置检查与工作流运行级修复 |
| 2026-07-21 | R5 | `7bfe457` | NOT_APPLICABLE 和 YAML 隐式类型修复 |
| 2026-07-21 | R6 | `7bfe457` | 基础迁移、认证、项目初始化和上下文读取 |
| 2026-07-21 | DOC-1 | `7bfe457` | 建立持续维护的开发文档体系 |
| 2026-07-21 | R7 | `4f0a7b3` | 已认证、幂等、可追溯的训练前检查 |
| 2026-07-21 | R7.1 | `2937a0b` | Plan Check 历史依据与事务稳定性加固 |
| 2026-07-21 | R8 | `2937a0b` | Owner 计划审批和不可变 Run Manifest |
| 2026-07-21 | R9 | `5ace7c3` | S3 实验草稿上传准备 |
| 2026-07-22 | R10 | `working tree` | 上传完成确认与 S3 对象复核 |
| 2026-07-22 | R10.1 | `working tree` | 上传恢复、不可变版本和审计加固 |

## R0：需求分析与 MVP 收敛

### 更新内容

* 将产品定位收敛为“提高实验一致性、可追溯性和风险可见性的治理系统”。
* 确定单一 P0 纵向链路：正式上下文和意图、训练前检查、Manifest、实验草稿、分析、
  人工确认、正式入库和查询。
* 首版角色收敛为 Owner 和 Researcher；Viewer、复杂邀请、多级审批等降级。
* MCP 接口收敛为六个工具：`project_get_context`、`experiment_check_plan`、
  `run_manifest_create`、`submission_prepare`、`submission_finalize`、`experiments_query`。
* 固定检查结果 `PASS / NEEDS_APPROVAL / BLOCKED` 及独立审批状态。
* 固定 `CLOUD_VERIFIED / LOCAL_ATTESTED / USER_PROVIDED` 证据边界。
* 明确 LLM 只能生成候选意图、摘要和风险，不能覆盖确定性规则或自动确认正式事实。

### 解决的问题

* 避免把 MVP 扩展成完整科研管理平台。
* 消除 `WARNING` 与“是否允许继续”之间的模糊语义。
* 明确配置一致性检查不等于真实训练行为正确。

### 产物

* `Requirement Analysis.md`

## R1：环境与仓库目录初始化

版本：`b981106`，提交说明：`环境配置完成`。

### 更新内容

* 增加 Python 依赖清单、开发依赖和服务端锁定依赖。
* 增加 CockroachDB 单节点 Docker Compose 配置和环境变量模板。
* 建立 `api`、`application`、`core`、`domain`、`infrastructure`、`mcp_server`、
  `workflows` 等包目录。
* 增加 `.gitignore`，避免提交本地环境文件和运行产物。

### 验证结果

* 仓库具备可安装 Python 包的基本目录结构。
* CockroachDB 开发容器配置已固定。

### 当时未完成

* 没有业务契约、数据库模型、API、MCP 实现和测试。

## R2：第一阶段代码框架

版本：`e519cd5`，提交说明：`第一阶段代码框架`。

### 更新内容

* 建立 FastAPI 入口、健康检查和能力发现接口。
* 建立 Pydantic 领域契约、核心枚举和三态检查结果。
* 实现不依赖 LLM 的训练前确定性配置检查器。
* 建立从项目、Context、Intent 到 Submission、Experiment、Memory 的 SQLAlchemy 模型。
* 暴露六个 MCP 工具的协议入口。
* 建立提交分析 LangGraph 的节点顺序骨架。
* 增加 Alembic、Ruff、mypy、pytest 和项目脚本配置。
* 增加 API、MCP、模型和配置检查的第一批测试。

### 解决的问题

* 把需求文档中的状态和数据对象转换为可执行、可测试的代码边界。
* 让未接入的业务适配器明确失败，避免返回伪造数据。

### 当时未完成

* ORM 模型尚无正式迁移。
* 数据库仓储、Token 认证、S3、Bedrock 和 Web 管理端均未接入。

## R3：治理语义、证据和追溯修正

版本：`43eacd8`，提交说明：`第一阶段代码框架_fix1.0`。

### 更新内容

* 为意图和约束增加 `EXPLICIT / INFERRED` 来源，以及 `PENDING / CONFIRMED /
  REJECTED / SUPERSEDED` 确认状态。
* 只有 `CONFIRMED` 约束可以产生强制 `BLOCKED`；候选约束只能提醒或要求审批。
* Context、Intent、Plan Check、Manifest、Submission 和 Experiment 增加版本绑定和追溯字段。
* 增加正式实验与探索实验模式，限制探索结果替代正式 baseline。
* 风险字段增加证据类型、来源、采集时间、采集工具和推断依据。
* 向量记忆增加项目、协议、模型、状态和有效性等结构化过滤字段。
* 收敛产品措辞，删除“保证实验正确”等绝对描述。
* 增加治理契约、模型约束和规则行为测试。

### 修复的问题

* 自然语言推断可能被直接固化为 Locked 正式约束。
* 正式 Context 缺少确认、版本、生效时间和修改原因。
* 本地 Agent 声明可能被风险报告误写为云端事实。
* 探索实验可能静默改变正式主线。
* 向量检索可能混合协议、seed 和模型版本不同的实验。

## R4：配置检查与工作流运行级修复

版本：`ccc5d7b`，提交说明：`第一阶段代码框架_fix2.0`。

### 更新内容和修复

* 使用 `pairwise(WORKFLOW_ORDER)` 构建 LangGraph 边，修复长度为 8 和 7 的序列在
  `zip(..., strict=True)` 下抛出 `ValueError` 的问题。
* YAML 和 JSON 解析均拒绝重复键，避免后值静默覆盖前值。
* 参数路径对点和反斜杠进行可逆转义，并拒绝非字符串键，修复点分路径碰撞。
* 在比较候选配置前校验正式 baseline 是否符合 `expected_value`，防止已漂移 baseline
  因“候选未变化”得到 PASS。
* MCP 用户身份改由服务端 Identity Provider 提供，不再接受客户端 actor UUID。
* 本地证据增加 `APPLICABLE / NOT_APPLICABLE`，区分缺失和合法不适用。
* 同一路径的多个 Pending 约束全部保留并产生冲突风险，不再由字典覆盖。
* 明确 `NEEDS_REVIEW` 是分析图终态交接，不宣称已实现 LangGraph 原生人工中断。

### 验证结果

* 增加重复键、路径碰撞、baseline 漂移、Pending 冲突、身份来源和工作流构建测试。
* `build_submission_workflow()` 可以完成到 `graph.compile()`。

## R5：NOT_APPLICABLE 和 YAML 隐式类型修复

版本：`7bfe457`。

### 更新内容和修复

* `NOT_APPLICABLE` 必须给出原因，且不能同时携带实际值。
* Git 工作区、branch、commit、运行命令、输出目录、配置 SHA-256 和 Python 版本被定义为
  核心证据，缺失或标记不适用会直接拒绝契约。
* 只有 checkpoint、CUDA、PyTorch 等确实可能不存在的字段允许标记不适用。
* 自定义 YAML 标量解析规则只自动解析与 JSON 一致的 `true/false/null`、整数和浮点数。
* `yes`、`on` 和日期形式保持字符串，避免 YAML 1.1 隐式类型改变配置含义。
* 规范化哈希仅处理 JSON 可表示值，避免日期对象造成哈希失败或不稳定。

### 验证结果

* 增加全字段绕过、缺少原因、值与不适用并存，以及 YAML 布尔/日期解析测试。

## R6：基础迁移、认证、项目初始化和上下文读取

版本：`7bfe457`。

### 更新内容

* 增加首个 Alembic revision `20260721_01`，只迁移本轮需要的 10 张基础表。
* 增加 Alembic 表白名单，避免提前冻结尚未实现的 Submission、Experiment 等表。
* 实现 256-bit 随机 API/MCP Token；数据库只保存 SHA-256、显示前缀、scope、audience、
  过期时间和撤销时间。
* 增加 `bootstrap-owner`、`issue-mcp-token`、`revoke-token` 管理 CLI。
* 增加 Bearer Token 认证、团队角色和项目绑定检查。
* 实现 Owner 原子项目初始化 API，同时创建 Project、Context v1、Intent v1、确认约束、
  审计记录和幂等结果。
* 相同用户、操作和 Idempotency-Key 重放原结果；请求体不同则返回冲突。
* 实现数据库驱动的 `project_get_context`，只返回当前 Context 绑定的 Active、Confirmed
  Intent 和有效确认约束。
* 已确认的推断约束继续保留 `INFERRED` 来源、依据和置信度，不被改写为 `EXPLICIT`。
* 其余五个 MCP 工具继续明确返回未实现，没有扩大本轮范围。
* 增加初始化 JSON 示例并更新 README 操作步骤。

### 修复的问题

* CockroachDB/PostgreSQL 约束名最长 63 字符：缩短
  `protected_parameters` 自引用外键名，修复真实迁移失败。
* FastAPI 同步依赖在当前 AnyIO 运行环境中无法唤回事件循环：HTTP 认证依赖和初始化
  端点改为异步边界，领域事务服务仍保持同步。
* 仓储读取曾把已确认推断约束硬编码成 `EXPLICIT`：改为映射数据库真实来源。
* 迁移时间默认值改为 `func.now()`，同时兼容 SQLite 测试和 CockroachDB。

### 验证结果

* Ruff、格式检查、mypy 和 `git diff --check` 通过。
* 45 项 pytest 测试全部通过。
* SQLite 完成 migration upgrade/downgrade、Token 和 CLI 冒烟测试。
* CockroachDB v26.2.0 成功升级到 revision `20260721_01`，10 张基础表创建完成。
* FastAPI 健康接口和 `/docs` 返回 HTTP 200。

### 已知遗留项

* CockroachDB 方言会把 JSON 反射为 JSONB、Text 反射为 VARCHAR，并在索引表达式中返回
  `NULLS FIRST`。因此 `alembic check` 仍产生等价类型/索引的假差异；不影响 migration
  upgrade 和当前运行，但后续使用自动迁移生成前需要增加方言比较过滤。
* 当前只完成 `project_get_context` 的数据库链路；Plan Check 持久化、审批、Manifest、S3、
  Bedrock、正式实验确认和 Web 页面仍未实现。

## DOC-1：建立持续维护的开发文档体系

版本：`7bfe457`。

### 更新内容

* 新增本文档，按历史轮次补录需求、环境、框架、缺陷修复和基础认证切片。
* 新增 `docs/ARCHITECTURE.md`，用文本图区分已实现、部分实现、骨架和规划模块。
* 新增 `docs/ITERATION_STATUS.md`，维护每轮交付、下一轮唯一目标、范围外事项和验收条件。
* README 增加三份维护文档的入口。

### 验证结果

* 文档中的当前能力与仓库模块、测试数量和数据库 revision 交叉核对。
* 文档明确将 R7 限制为 Plan Check 持久化，不提前开发审批、Manifest 或 S3。

### 功能影响

* 本次只增加项目维护文档，没有改变运行时代码或数据库 Schema。

## R7：已认证、幂等、可追溯的训练前检查

版本：`4f0a7b3`。

### 更新内容

* 新增 Alembic revision `20260721_02`，只将 `plan_checks` 加入正式数据库。
* 实现 Plan Check 仓储，支持按 `(requester_id, idempotency_key)` 查找和稳定重放。
* `experiment_check_plan` 现在从数据库读取当前 Context、Active Intent、已确认约束
  和当前版本的 Pending 候选约束，再调用纯确定性规则引擎。
* 持久化配置解析快照和规范化哈希、Context/Intent ID 与版本、约束快照、
  参数变化、Git commit、运行命令、本地证据、风险和完整回执。
* MCP 命令不再包含 `requester_id`；请求者仅来自服务端验证的 Token 身份。
* MCP Token 默认签发 `project:read` 和 `experiment:check` 两个 scope。
* 数据库检查约束固定了 `check_result` 与 `approval_status` 的合法组合，并要求
  `APPROVED` 状态必须有审批人和审批时间。

### 修复的问题

* 防止客户端伪造 Plan Check 请求者，并同时校验 Token 项目、Token 团队和项目成员资格。
* 防止请求使用过期的 Intent，或将新 Context 静默套用到旧检查。
* 相同幂等 key 且相同请求直接返回原 `plan_check_id`；请求体不同时返回冲突。
* 幂等重放在 Context 后续变更后仍返回原回执，不重新解释历史请求。
* 确保 Pending 推断约束只能导致 `NEEDS_APPROVAL`，不能产生强制 `BLOCKED`。

### 验证结果

* 53 项 pytest 测试全部通过，新增 PASS、NEEDS_APPROVAL、BLOCKED、baseline 漂移、
  Pending 约束、版本绑定、scope、Token 签发、项目/团队边界、幂等重放和服务重建测试。
* MCP 边界测试确认应用用例收到的是服务端身份，而非客户端参数。
* Ruff、Ruff format、mypy 和 `git diff --check` 通过。
* CockroachDB 实际连接成功升级到 `20260721_02 (head)`。

### 已知遗留项

* `approval_records` 和 `run_manifests` 仍只有 ORM 骨架，未进入迁移。
* `NEEDS_APPROVAL` 尚无 Owner 决策入口，因此当前不能转为 `APPROVED`。
* `run_manifest_create`、S3、Submission、Bedrock、正式实验确认和 Web 页面仍未实现。

## R7.1：Plan Check 历史依据与事务稳定性加固

版本：`2937a0b`。

### 更新内容

* 新增 revision `20260721_03`，为 Plan Check 保存原始配置文档/哈希、完整 Context
  参考和 payload、baseline `active_config`、完整 Intent 参考和策略 payload。
* 幂等 report 只保存不可变的检查结论；`approval_status` 和 `can_create_manifest`
  改为重放时从 Plan Check 当前数据库状态合成。
* 配置比较改为递归校验类型和值，`true`、`1` 和 `1.0` 不再被视为相同。
* 正式策略加载时拒绝同一参数同时出现在 Intent `allowed_variables` 和已确认
  `LOCKED` 约束中。
* 配置文档限制为 1 MiB UTF-8，运行命令限制为 8192 字符，Git commit 和
  SHA-256 必须为规定长度的十六进制值，并校验布尔/字符串证据类型。
* 云端对收到的配置内容重算原始字节 SHA-256，与 `LOCAL_ATTESTED` 值冲突时
  生成 `CONFIG_DOCUMENT_SHA256_MISMATCH` Critical 阻断风险。
* CockroachDB `40001` 序列化冲突改为服务端最多重跑完整事务三次，耗尽后才返回
  可使用原 Idempotency-Key 重试的错误。

### 修复的问题

* Context 或 Intent 后续变化不再影响历史 Plan Check 的决策依据；迁移前已有但无法
  补建快照的记录会被明确拒绝重放，要求使用新 key 重新检查。
* 未来审批状态变化不会再被旧 report 中的 `PENDING` 覆盖。
* Python 宽松相等语义不会隐藏配置类型漂移。
* 配置哈希和内容不一致时不再可能返回 PASS。
* README 和迭代文档在检查前已标记 R7，不存在所报的“仍为 R6”问题；本轮继续
  同步 R7.1 和 revision `20260721_03`。

### 验证结果

* 常规套件收集 61 项测试，其中 60 项默认执行，1 项真实 CockroachDB 测试显式启用。
* 真实 CockroachDB 测试使用随机临时数据库，完成 migration upgrade、Plan Check
  持久化、幂等重放、异体冲突、失败回滚、downgrade 和临时库清理。
* 开发 CockroachDB 已成功升级到 `20260721_03 (head)`。

### 已知遗留项

* R7.1 不实现 Owner 审批业务、Manifest、S3 或 Web；这些仍按 R8/R9 顺序开发。

## R8：Owner 计划审批和不可变 Run Manifest

版本：`2937a0b`。

### 更新内容

* 新增 revision `20260721_04`，只迁移 `approval_records` 和 `run_manifests`；迁移包含
  最终审批状态、单目标唯一审批、单 Plan 唯一 Manifest、项目幂等键、Manifest 哈希和
  `schema_version=1` 检查约束。
* 新增 `ApprovalDecision`、审批请求/结果和 `RunManifestResult` 契约。公开审批请求只允许
  `APPROVED/REJECTED`，可选理由会去除首尾空白，纯空白归一为 null。
* 新增 `PlanApprovalService` 和最小 Owner API：
  `POST /api/v1/projects/{project_id}/plan-checks/{plan_check_id}/decision`。
* 审批事务同时写入最终 ApprovalRecord、PlanCheck 状态、AuditLog 和 IdempotencyRecord；
  已决定记录不能反转，拒绝不会伪造 `approved_by/approved_at`。
* 实现 MCP `run_manifest_create`。调用者身份来自服务端 MCP Token，不接受 actor/requester
  参数；新 MCP Token 增加 `manifest:create`，新 Owner API Token 增加 `plan:approve`。
* 新增纯确定性 Manifest 构建器。dataset、protocol、seed、checkpoint、Git、命令、环境和
  证据只从 Plan Check 历史快照提取，当前 Context 或 Intent 漂移不会改变历史运行凭据。
* 配置同时保存原始文档/解析值、规范化配置哈希和原始文档哈希；Manifest 哈希使用排序
  JSON、紧凑分隔符、`allow_nan=False` 和 SHA-256，可在服务重建后重复计算。
* seed 优先使用配置顶层严格整数，否则只接受 Context 中唯一的严格整数 default seed；
  布尔、字符串、多默认值均拒绝。环境中的 N/A/unavailable 归一为 null。
* Plan Check、审批和 Manifest 共用有界 CockroachDB 40001 重试辅助函数；所有外部调用均
  位于数据库事务之外，本轮没有引入 S3、Bedrock 或工作流执行。

### 修复的问题

* `NEEDS_APPROVAL` 不再停留在无法推进的 PENDING 状态，Owner 可以作出一次性批准或拒绝。
* `PASS`、`BLOCKED`、已批准和已拒绝记录不能错误进入审批。
* Manifest 不会读取当前 Context 为旧 Plan Check 补值，消除多版本对象的时间不一致。
* PENDING、REJECTED、BLOCKED、缺失完整 R7.1 快照或缺少匹配审批记录均不能生成 Manifest。
* 同一 Plan Check 使用不同 Idempotency-Key 不再静默返回旧 Manifest，而是返回 409 冲突。
* 相同 key 的异体审批或 Manifest 请求不会覆盖原结果；事务并发由幂等记录和数据库唯一
  约束共同兜底。

### 验证结果

* 常规套件共收集 75 项测试，74 项默认执行并全部通过，1 项真实 CockroachDB 测试
  继续显式 opt-in。
* 新增审批权限/状态/审计/幂等、批准与拒绝、PASS 直建、历史快照抗漂移、Manifest
  唯一性、服务重建重放、seed 边界、API 和 MCP 身份边界测试。
* SQLite migration 完成 `20260721_03 -> 20260721_04 -> 20260721_03` 往返，并验证唯一与
  schema version 约束。
* 真实 CockroachDB 隔离库完成 revision 04 降级/升级、NEEDS_APPROVAL、Owner APPROVED、
  Manifest 创建、幂等事务和最终 downgrade/base 清理。
* Ruff、Ruff format、mypy 和 `git diff --check` 通过。

### 已知遗留项

* `submission_prepare`、S3 上传槽位和 artifact 草稿尚未实现，收敛到 R9。
* `submission_finalize`、S3 对象复核、分析工作流、Bedrock、正式实验确认和 Web 页面继续
  保持未实现，不在 R8 扩大范围。

## R9：S3 实验草稿上传准备

版本：`5ace7c3`。

### 更新内容

* 新增 revision `20260721_05`，只将 `experiment_submissions` 和 `artifacts`
  加入正式数据库；未提前迁移风险、正式实验或向量表。
* 新增 `SubmissionArtifactInput`、`SubmissionPrepareCommand`、预签名上传目标和
  `SubmissionPrepareResult` 契约。上传类型限于 YAML/JSON 配置、JSON 结果、
  TXT 日志、Markdown 说明和可选 JSON Manifest。
* 运行状态限于 `COMPLETED/FAILED`；指标名、数量和值域使用严格校验，
  不允许布尔值、NaN 或 Infinity 被宽松转成合法指标。
* 实现 `submission_prepare` 应用用例和 MCP 边界。调用者只来自服务端校验的
  项目 Token，必须具备 `submission:create` scope 并通过项目、团队和成员检查。
* 一个 CockroachDB 事务中写入 `RECEIVED` Submission、全部 Artifact 声明、
  AuditLog 和 IdempotencyRecord。相同 key 的同体请求复用原 ID，异体请求返回冲突。
* 同一 Run Manifest 可以产生多个 Submission，以支持人工多次运行；每个
  Submission 仍完整保存 Manifest ID 和哈希。
* 新增 `ArtifactStorage` 端口、boto3 S3 适配器和未配置 Bucket 时的稳定错误适配器。
  URL 在数据库事务提交后生成，数据库只保存 object key 和文件声明。
* 预签名 PUT 同时绑定 Content-Type、Content-Length、SHA-256 checksum 和
  `If-None-Match: *`；默认 900 秒过期，可在 60 到 3600 秒范围内配置。
* 幂等重放不持久化旧预签名 URL，而是为原 Artifact ID 签发新 URL；
  S3 签名失败时已提交的 `RECEIVED` 草稿可使用原 key 安全重试。
* 运行结果、指标和文件元数据标记为 `LOCAL_ATTESTED`；数据库中的 Manifest
  关联标记为 `CLOUD_VERIFIED`；`cloud_hash_verified` 在 prepare 阶段始终为 false。

### 修复的问题

* 修复 Submission 和 Artifact 没有 ORM relationship 时单次 flush 可能先写子表的外键失败；
  现在显式先 flush Submission，并仍由同一事务保证原子性。
* MCP `metrics_summary` 保留原始 JSON 值到领域契约，避免协议层先把 `true`
  宽松转成 `1.0` 而绕过严格指标校验。
* 文件名采用大小写无关重复检查，并拒绝路径、控制字符、错误扩展名、
  错误 MIME、非法 SHA-256、缺失核心文件和超限文件。
* object key 仅使用 Project/Submission/Artifact UUID，不将客户端文件名拼入 S3 路径。

### 验证结果

* 常规套件共收集 94 项测试，92 项默认执行并全部通过；2 项外部集成测试
  需要显式 opt-in。
* 新增契约、S3 适配器、MCP 身份边界、草稿持久化、权限、Manifest 归属、
  幂等同体/异体、新 URL 重放、签名失败恢复和 migration 升降级测试。
* 开发 CockroachDB 已从 `20260721_04` 升级至 `20260721_05 (head)`。
* 真实 CockroachDB 隔离库完成 revision 05 降级/升级、审批、Manifest、Submission、
  Artifact 和幂等重放事务链路，并已实际执行通过。
* 新增真实 AWS S3 预签名 PUT/HEAD/checksum 兼容性测试；当前未配置专用
  Bucket/凭据，因此本轮没有对 AWS 实际执行该 opt-in 测试。
* Ruff check、Ruff format、mypy、`git diff --check` 和全量 pytest 通过。

### 已知遗留项

* `submission_finalize` 仍明确返回尚未实现，S3 中的对象存在性、大小、MIME 和
  SHA-256 尚未由云端复核。
* LangGraph 分析、Bedrock、风险回执、正式实验确认、查询和 Web 继续延后，未在 R9
  扩大实现范围。

## R10：上传完成确认与 S3 对象复核

版本：`working tree`，当前实现轮次。

### 更新内容

* 新增 revision `20260722_06`，为 Submission 增加上传复核人、时间和完整回执快照，
  为 Artifact 增加复核时间、结构化云端证据和 S3 VersionId。
* 新增 `UPLOAD_VERIFIED` 状态，以及上传验证 PASS/FAILED、逐文件验证回执和结构化
  缺失/大小/MIME/checksum 问题契约。
* `submission_finalize` MCP 输入收敛为 `submission_id`、`idempotency_key`；身份只来自
  服务端 MCP Token，新签 Token 增加 `submission:finalize` scope。
* finalize 校验 Token 项目绑定、团队成员和原提交者身份，只处理 `RECEIVED` 草稿。
* S3 适配器新增 `inspect_object`，使用 `HEAD` 与 `ChecksumMode=ENABLED` 读取对象
  长度、Content-Type、SHA-256 checksum、ETag、VersionId 和 LastModified。
* S3 调用全部位于数据库事务之外。只有所有对象匹配时才在单一 CockroachDB 事务中
  写入全部 Artifact 云端证据、Submission 状态、AuditLog 和幂等结果。
* 失败回执包含本次发现的全部确定性问题，保持 Submission 为 `RECEIVED` 且不写部分
  Artifact 验证；修复对象后可用相同 key 重新检查。
* 成功幂等重放从数据库回执直接返回，不再次调用 S3；成功后 prepare 重放不再签发
  上传 URL。
* 真实 S3 可选测试改为通过生产 `inspect_object` 验收 PUT 后的对象元数据，而不是测试
  自己直接调用 boto3 HEAD。

### 修复的问题

* CockroachDB 实库测试发现 R9 创建的 Submission 状态列为 `VARCHAR(12)`，无法保存
  15 字符的 `UPLOAD_VERIFIED`；revision 06 现将该列扩至 `VARCHAR(32)`，ORM 也固定
  使用同一长度。
* 带 `UPLOAD_VERIFIED` 实际数据直接 downgrade 会因列缩短失败；回滚现先把新版状态
  映射为旧版可识别的 `RECEIVED`，再恢复 `VARCHAR(12)`。
* 对象部分成功、部分缺失时不会留下混合验证状态；数据库提交只接受完整且未漂移的
  Artifact 声明快照。
* 同一失败幂等键不会永久缓存暂时性对象问题；相同 key 可在对象修复后从 FAILED
  更新为 COMPLETED，但用于不同 Submission 时仍返回冲突。
* S3 服务不可用不会被错误记为业务验证失败，也不会写入幂等或部分证据。
* finalize 成功不再错误进入 `PROCESSING`；R10 使用独立 `UPLOAD_VERIFIED` 状态，明确
  表示尚未启动内容分析。

### 验证结果

* 全量收集 103 项测试，101 项默认执行全部通过，真实 CockroachDB 和 AWS S3 两项
  外部测试默认跳过。
* 新增 finalize 成功/失败/修复重试/异体冲突、原提交者授权、服务端身份、S3 故障、
  无部分写入、成功重放不访问 S3、prepare 后置重放和契约状态一致性测试。
* SQLite migration 完成 revision 06 升级、降级到 05、再升级及后续全链路降级。
* 真实 CockroachDB 隔离库显式执行通过：完成 `head -> 05 -> head -> 03 -> head`、
  完整 Plan/审批/Manifest/prepare/finalize 事务，以及含 `UPLOAD_VERIFIED` 数据时的
  `head -> base` 回滚。
* 开发 CockroachDB 已从 `20260721_05` 升级至 `20260722_06 (head)`。
* Ruff format、Ruff check、mypy、`git diff --check` 和全量 pytest 均通过。
* 真实 AWS S3 测试代码已更新；当前未配置专用 Bucket/凭据，本轮未实际访问 AWS。

### 已知遗留项

* R10 仅校验 S3 对象元数据与声明一致，不下载/解析配置或结果，不保证真实训练行为正确。
* LangGraph 持久化分析、重复检查、风险/摘要、Bedrock、embedding、人工审核确认、正式
  实验查询和 Web 均未在本轮实现。

## R10.1：上传恢复、不可变版本和审计加固

版本：`working tree`，当前修复轮次。

### 更新内容

* 核对官方 CLI 后确认权限并未缺失，而是按用途拆分：Owner API Token 包含
  `project:initialize/plan:approve`，项目绑定 MCP Token 包含读取、检查、Manifest、
  prepare 和 finalize scopes。CLI 输出会显式返回排序后的 scopes，并新增两类 Token
  的签发/认证回归测试。
* 保留 `If-None-Match: *` 禁止覆盖。已存在但大小、MIME、checksum 或 VersionId 不合格
  的对象验证失败时，只为受影响 Artifact 原子生成新的随机 object key；缺失对象继续
  使用尚未占用的原 key。
* FAILED 回执增加 `reupload_artifact_ids`。调用者使用原 prepare 请求获取新 URL，只上传
  指定 Artifact；旧错误对象不再被数据库引用，也不会被静默覆盖。
* finalize 要求 S3 返回非空且非 `null` 的 VersionId。未开启 Versioning 的对象不能进入
  `UPLOAD_VERIFIED`，未来下载和分析必须使用已保存的具体版本。
* 每次确定性验证失败都新增不可变 `submission.finalize.failed` AuditLog。同一幂等键连续
  失败会有多条审计，但仍只有一个当前 IdempotencyRecord；后续成功不会抹掉历史失败。
* prepare、finalize 成功和 finalize 失败审计均记录 Token ID 与 Submission 的
  `source_agent`；finalize 额外记录 `ORIGINAL_SUBMITTER/OWNER_RECOVERY`。
* 项目 Owner 可以代替不可用的原提交者完成 finalize，其他 Researcher 仍不能操作他人
  Submission。
* 真实 AWS S3 opt-in 测试要求 Bucket Versioning=Enabled，并验证 checksum、Content-Type、
  VersionId、预签名必需 Header 和第二次 PUT 返回 412；测试清理所有生成版本。

### 修复的问题

* 错误对象不再因固定 key 与禁止覆盖组合而永久卡住 Submission。
* 数据库中的验证证据现在绑定具体 S3 VersionId，不能仅凭可变 object key 表示不可变性。
* revision 06 downgrade 先清除全部 R10 `cloud_hash_verified=true`，再把 Submission 退回
  `RECEIVED`，避免重新升级后出现无法继续的半验证状态。
* 原 Token 被撤销或原用户无法继续操作时，Owner 有明确恢复入口和可区分审计。
* 失败回执被同一幂等记录更新时，历史失败事实仍由独立 AuditLog 完整保存。

### 验证结果

* 全量收集 106 项测试，104 项默认执行全部通过；CockroachDB 和真实 AWS S3 两项外部
  测试默认跳过。
* 真实 CockroachDB 隔离库显式执行通过，验证 finalize 数据降到 revision 05 后状态为
  `RECEIVED` 且所有 Artifact `cloud_hash_verified=false`，随后可重新升级并降到 base。
* 新增 CLI scope 展示、错误 key 轮换、同 key 多次失败审计、无 VersionId 阻断、Owner
  恢复 finalize 及 Token/Agent 审计测试。
* Ruff format、Ruff check、mypy、`git diff --check` 和全量 pytest 均通过。

### 已知遗留项

* 真实 AWS 测试仍需专用 Versioning Bucket 和凭据才能执行，本地环境未访问 AWS。
* KMS key policy、Bucket lifecycle、跨账号权限和生产部署检查属于 R14 AWS 部署阶段。
* R11 下载 Artifact 时必须带已保存的 VersionId；当前 R10 不下载或解析文件。

## R11：可恢复的确定性提交分析前半程

版本：`working tree`，当前实现轮次。

### 更新内容

* 新增 Alembic revision `20260722_07`。Submission 正式增加工作流状态、最后成功步骤、
  结构化错误和有界分析快照；`submission_risks` 从 ORM 骨架进入正式迁移，并使用
  `(submission_id, risk_fingerprint)` 防止恢复或并发产生重复风险。
* 增加 `NOT_STARTED/RUNNING/RETRYABLE_FAILURE/TERMINAL_FAILURE/AWAITING_ENRICHMENT`
  工作流状态。`processing_step` 只表示最后成功节点，不把失败节点误记为已完成。
* LangGraph 构建器支持 `WORKFLOW_ORDER` 的非空连续前缀。生产只编译 R11 前五节点，完整
  八节点拓扑仍保留给 R12；恢复真相源是 CockroachDB 业务表，不增加 checkpoint 表。
* `submission_finalize` 保持原上传回执和幂等快照不变，同时动态附加 `analysis` 回执。
  首次成功和同 key 重放都会启动或恢复分析，完成后为
  `PROCESSING/AWAITING_ENRICHMENT/RISK_ANALYSIS`。
* S3 端口与适配器增加精确 VersionId GET。读取前检查 ContentLength，流式读取最多
  `max_bytes + 1`，核对返回 VersionId/长度并关闭 Body；NoSuchVersion 与服务故障分别
  映射为终止问题和可重试问题。
* CONFIG/RESULT 在 prepare 阶段即限制为各 1 MiB。分析下载后重新计算 SHA-256，不能只
  信任 HEAD；CONFIG 复用严格 YAML/JSON 解析器，`result.json` 使用固定 schema。
* 固定结果契约拒绝重复键、额外字段、非有限/布尔指标、超过 50 个指标、无时区时间、
  完成时间早于开始时间，以及 COMPLETED 空指标或 FAILED 缺少失败原因。
* Manifest 校验覆盖数据库追溯链、Manifest hash、配置文档 hash、规范化配置 hash、配置
  快照、运行状态、指标声明和主指标。数据库追溯损坏直接终止；可解析内容不一致保存为
  CRITICAL 阻断风险并继续完成风险节点，便于后续人工审核。
* 查重先按 project、上传验证时间和状态过滤最近草稿。Manifest 与 CONFIG/RESULT hash
  全相同记为非阻断 MEDIUM；运行条件相同记为非阻断 LOW；不调用向量相似度替代结构化
  判断。
* 风险明确保存证据边界：上传配置和指标内容仍是 `LOCAL_ATTESTED`，云端只把固定版本、
  字节 hash、数据库状态和查重比较描述为可验证事实。

### 修复的问题

* 上传成功后不再依赖进程内 LangGraph 状态；后端重启或同 key 重放可根据最后成功步骤
  继续，已经完成的 HEAD、VersionId GET、解析和风险节点不会重复。
* S3 短暂失败不会把不可变 Artifact 错判为永久损坏；状态保存为可重试并保留此前游标。
* 固定 VersionId 缺失、下载 hash 漂移、配置/结果无法解析时明确进入终止失败，避免在同
  一 Submission 上覆盖已验证版本；调用者必须创建新 Submission。
* 上传幂等 `response_snapshot` 不保存易变化的分析状态，避免未来审批/分析推进后同 key
  返回过时回执；分析状态每次从 Submission 动态合成。
* revision 07 downgrade 会把带上传证据的 R11 `PROCESSING/FAILED` 映射回
  `UPLOAD_VERIFIED`，删除风险和分析列后仍可由 revision 06/05 继续安全降级。
* `submission_prepare` 的历史幂等回执在分析开始后仍返回上传阶段的
  `UPLOAD_VERIFIED`，不会错误重签 URL，也不会把 PROCESSING 塞进旧上传契约。

### 验证结果

* 全量收集 125 项测试，123 项默认执行全部通过；真实 CockroachDB 与 AWS S3 两项外部
  验收默认跳过。
* 新增固定 result.json、严格 CONFIG、精确 VersionId GET/限长/关闭流、五节点图前缀、
  同 key 故障恢复、不可变内容终止失败、Manifest CRITICAL 风险、MEDIUM/LOW 查重和
  revision 07 升降级测试。
* 真实 CockroachDB 隔离库显式执行通过：完成 head 升级、完整 Plan/审批/Manifest/
  prepare/finalize/R11 分析事务、含 PROCESSING 数据降至 revision 05 后再升 head，最终
  降至 base。
* 开发 CockroachDB 已从 `20260722_06` 升级至 `20260722_07 (head)`，更新后的 FastAPI
  已在 `127.0.0.1:8790` 重启，health/capabilities 均返回 200。
* 真实 AWS opt-in 测试已增加指定 VersionId GET 验收；当前未配置专用 Bucket/凭据，
  本轮未实际访问 AWS。
* Ruff check、mypy 和全量 pytest 通过。

### 已知遗留项

* R11 不生成摘要、embedding 或最终审核回执，成功状态刻意停在
  `AWAITING_ENRICHMENT`；这些只在 R12 实现。
* R11 查重只覆盖同项目的已验证 Submission，不查询正式 Experiment 或向量记忆；正式
  结构化/向量查询属于 R13。
* CockroachDB 的 `alembic check` 仍会把反射出的 JSONB/Text 类型和 `NULLS FIRST` 索引
  表达误报为全库 metadata 差异；revision 07 的真实升降级由 SQLite 与隔离 CockroachDB
  测试覆盖，本轮不扩展为跨历史 revision 的反射规范化重构。
* 未增加 Web、自动训练、自动改代码、KMS/lifecycle 或生产 AWS 部署配置。

## R12a：可靠异步编排与 Bedrock 摘要

版本：`working tree`，当前实现轮次。

### 更新内容

* 新增 Alembic revision `20260722_08`。`experiment_submissions` 只增加
  `generated_summary`；新增 `workflow_jobs` 与 `outbox_events`，以唯一
  `(submission_id, job_type)` 和 `(workflow_job_id, generation)` 约束防止重复业务记录。
* Job 保存 generation、尝试次数、最大次数、available time、租约、错误、SQS message ID
  和起止时间；Outbox 保存发布状态、租约、退避和回执。数据库约束限制 R12a 只能使用
  `SUBMISSION_SUMMARY` 和 `SUBMISSION_SUMMARY_REQUESTED`。
* R11 风险节点完成时，在写入风险摘要的同一事务内创建 Job/Outbox，并把 Submission
  切换为 `PROCESSING/QUEUED/RISK_ANALYSIS`。Worker 启动时会为 revision 07 遗留的
  `AWAITING_ENRICHMENT/RISK_ANALYSIS` 记录补建缺失任务。
* 增加 SQS Standard 端口与 boto3 适配器。消息体严格限制为
  `schema_version/job_id/submission_id/generation`，配置、指标、Artifact 和摘要始终从
  CockroachDB 重新读取。
* 增加事务 Outbox Dispatcher：事务内租约领取，事务外发布，第二个事务写发布回执。
  发送成功但回执落库前崩溃时允许重复发送，由 generation 和持久化游标消除副作用。
* 新增 `experiment-guardian-worker` 单并发入口。Worker 使用 20 秒长轮询、120 秒 SQS
  可见性和数据库租约；SQS 只在摘要已持久化后删除。成功消息重复投递不会再次调用
  Bedrock。
* 摘要 Job 默认最多五次，按 `min(30 * 4^(attempt-1), 3600)` 退避。依赖失败进入
  `RETRYABLE_FAILURE`；达到上限进入 `DEAD_LETTER` 并保留消息供外部 DLQ/redrive；上游
  快照损坏进入不可重试 `FAILED/TERMINAL_FAILURE`。
* 增加 Bedrock Runtime Converse 适配器，连接/读取超时和 SDK 重试可配置。模型只返回
  纯文本；空输出、超过 3000 字、工具调用、未知内容块、权限/限流/网络错误都按可重试
  依赖故障处理。
* 摘要输入使用 Intent objective、Manifest 运行条件、result 指标/状态和已持久化风险。
  HIGH/CRITICAL 风险优先且不得遗漏，MEDIUM/LOW 按稳定顺序在 32 KiB 上限内选择；不读取
  LOG/NOTE。提示词把所有事实标为不可信数据，要求跟随 Intent 语言并保持指标、路径、
  hash 和模型标识原样。
* `generated_summary` 保存 schema、纯文本、model ID、prompt version、source hash、语言
  策略、生成时间、token usage 和 disclaimer。模型摘要是解释性产物，不标记为
  `CLOUD_VERIFIED`，也不写入或修改 `submission_risks`。
* 新增第七个 MCP 工具 `submission_get_status` 和 `submission:read` scope。状态工具只允许
  原提交者或项目 Owner，动态返回 Submission、Job、风险统计和摘要，不触发处理。
* `submission_finalize` 新 key 支持恢复：R11 尚未完成时只恢复确定性前缀，不提前创建
  摘要 Job；摘要 retryable/dead 时增加 generation、重置次数并创建新 Outbox；活跃或成功
  Job 不重复。恢复不重新 HEAD/GET S3，并审计 Token ID、source Agent、actor/recovery mode。
* `.env.example` 增加 Queue、可见性、租约、最大尝试、Bedrock model 和 timeout 配置；
  本轮不创建 Queue、DLQ、IAM、KMS 或 Bedrock access policy。

### 修复的问题

* 外部 Bedrock 调用不再发生在 finalize 请求或数据库事务内，避免请求超时和长事务。
* 风险分析完成与队列任务创建不再存在双写丢失窗口；Outbox 是 CockroachDB 事务的一部分。
* SQS Standard 的重复、延迟、旧 generation 和 Worker 崩溃不会产生多个数据库摘要。
* 模型输出不能改变风险等级、阻断状态、审批权限或正式记录；失败只会暂停摘要步骤。
* 同一 finalize key 的上传快照保持不可变，但分析状态继续动态读取，不返回过时状态。
* 新 finalize key 不会把 R11 中途的 retryable Submission 误当成摘要任务；只有风险前缀已
  完成或已有 Job 时才进入 R12a 调度。
* revision 08 downgrade 会把所有带 Job/摘要步骤的 Submission 退回 R11 可理解的
  `PROCESSING/AWAITING_ENRICHMENT/RISK_ANALYSIS`，清除瞬时错误，再删除 Job/Outbox/摘要。

### 验证结果

* 默认严格 Fake 覆盖 Job/Outbox 唯一性、升级遗留对账、SQS 发布失败、发送后崩溃重复、
  重复/旧 generation、可见性租约、Bedrock 响应提交前崩溃、空/超长/工具响应、重试、
  死信、新 key 恢复、状态权限、source hash/提示词边界和 revision 08 有数据降级。
* 可选 `RUN_SQS_INTEGRATION=1` 与 `RUN_BEDROCK_INTEGRATION=1` 真实 AWS 验收默认跳过，
  本轮没有访问真实 AWS。
* 全量收集 144 项测试，默认执行 140 项全部通过；真实 CockroachDB、S3、SQS 和
  Bedrock 四项外部验收默认跳过。
* 真实 CockroachDB 隔离库验收显式执行通过：revision 08 完整升级、Plan/审批/Manifest/
  prepare/finalize/R11 风险与 R12a Job/Outbox 事务、含数据降级回升及最终降至 base 均通过。
* Ruff format/check、mypy（51 个源文件）和 `git diff --check` 全部通过。
* 开发 CockroachDB 已从 `20260722_07` 升级至 `20260722_08 (head)`。
* 更新后的 FastAPI 已在 `127.0.0.1:8790` 重启；health 返回 200，capabilities 返回七个
  MCP 工具并包含 `submission_get_status`。未配置 Queue URL 时 Worker 按设计明确拒绝启动。

### 已知遗留项

* R12a 成功只到 `PROCESSING/AWAITING_ENRICHMENT/SUMMARY_GENERATION`。embedding、短审核
  回执和 `NEEDS_REVIEW` 明确留给 R12b。
* SQS Queue、DLQ/redrive policy、IAM Role、KMS、CloudWatch 和生产部署属于 R14；Worker
  在缺少 Queue URL 或 summary model ID 时会明确拒绝启动。
* 摘要是非确定性的解释性文本。Worker 在模型返回后、数据库提交前崩溃时可能再次调用
  模型，但只有一个持久化结果；这不构成 exactly-once 外部调用承诺。
* 正式 Experiment/Metric/Memory、`experiments_query`、人工确认和 Web 仍未实现。

## 新日志模板

```text
## YYYY-MM-DD / Rn：主题

版本：commit 或 working tree。

### 更新内容
### 修复的问题
### 验证结果
### 已知遗留项
```
