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
| 2026-07-22 | R11-R13 | `working tree` | 可恢复分析、审核回执、正式实验与查询 |
| 2026-07-22 | R14 | `working tree` | 托管认证、Web、OAuth MCP 与 AWS 部署 |
| 2026-07-23 | R14 Local | `working tree` | 单机可替换基础设施与安全修复 |
| 2026-07-23 | R14f | `working tree` | 正式策略双表示 |
| 2026-07-23 | R15 Plan | `working tree` | 内部实验治理 Agent 分期设计 |
| 2026-07-28 | R17a | `working tree` | 外部 Coding Agent 协作入口与带引用问答 |
| 2026-07-28 | R17b | `working tree` | 版本化自然语言实验计划、有限自动修订与人工决定 |
| 2026-07-23 | R15a | `working tree` | 只读 Agent 对话纵向切片 |
| 2026-07-23 | R15b | `working tree` | 实验分析与对话压缩 |
| 2026-07-24 | R15c | `working tree` | 治理草稿与影响分析 |
| 2026-07-24 | R15d-a | `working tree` | Policy 发布提案与 Owner 独立确认 |
| 2026-07-24 | R15d-b1 | `working tree` | Plan Check 决策提案 |
| 2026-07-27 | R15d-b2 | `working tree` | Submission 决策提案 |
| 2026-07-27 | R15e-a | `working tree` | 显式实验集候选研究报告 |
| 2026-07-27 | R15e-b | `working tree` | 候选 Research Memory 与召回 |
| 2026-07-27 | R15e-c | `working tree` | Agent provider parity 与观测 |
| 2026-07-27 | R16-L | `working tree` | 本地百炼 release candidate hardening |

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

## R12b：Embedding 与确定性审核回执

版本：`working tree`，当前实现轮次。

### 更新内容

* 新增 Alembic revision `20260722_09`：Submission 增加 `review_receipt`，新增独立
  `submission_embeddings` 表；每个 Submission 只有一个草稿向量，保存 `VECTOR(1024)`、
  project、模型、维度、归一化标志、冻结输入、输入 SHA-256、token 数和生成时间。
* 扩展 Job/Outbox 数据库约束，新增 `SUBMISSION_REVIEW_PREPARATION` 和对应 requested
  event；消息 wire shape 仍为 `schema_version/job_id/submission_id/generation`。
* 摘要持久化、Summary Job 成功以及 Review Job/Outbox 创建现在处于同一数据库事务。
  Worker 启动同时对账 R11 缺 Summary Job 和 R12a 有摘要但缺 Review Job 的历史记录。
* 增加通用队列 Job 路由；Summary 和 Review Job 共用 Queue、Outbox、lease、generation、
  退避和死信协议，没有新建第二套异步基础设施。
* 新增 Titan Text Embeddings V2 适配器，固定模型
  `amazon.titan-embed-text-v2:0`、1024 维和 `normalize=true`；响应必须包含恰好 1024 个
  非布尔有限数值且范数满足容差。
* 冻结 `submission-search-v1` 检索文档。来源只使用历史 Intent、Manifest、Plan Check
  变化、解析结果和持久化风险；不使用生成摘要、LOG、NOTE 或配置全文。自由文本/复杂值
  使用有界预览和完整值 hash，必要事实不得被静默丢弃。
* embedding 外部调用发生在事务外。向量单独提交并推进到 `EMBEDDING_GENERATION`；回执
  在下一节点提交，因此向量落库后崩溃可以直接恢复，不重复调用 Bedrock。
* 扩展未启用的 `SubmissionReceipt` 契约，保存完整 Context/Intent/Plan Check/Manifest
  追溯、证据化运行条件、实际允许或 Owner 批准的变化、关键结果和风险摘要。
* 审核权限由确定性规则生成：未解决 blocking/CRITICAL 为 `BLOCKED`，HIGH 为
  `OWNER_ONLY`，LOW/MEDIUM 或无风险为 `RESEARCHER_OR_OWNER`。LLM 摘要只通过
  `summary_available` 标记存在，不参与权限计算。
* 最终事务原子写入回执、完成 Review Job，并切换到
  `NEEDS_REVIEW/COMPLETED/NEEDS_REVIEW`。CRITICAL 仍进入可查看回执，但不能确认。
* `submission_get_status` 保留旧 `job`，新增有序 `jobs`、embedding 元数据和审核回执；
  明确不返回原始向量或冻结输入。Finalize 同 key 的动态重放可以看到最终状态。
* `submission_finalize` 新 key 按游标恢复正确阶段：摘要未完成时恢复 Summary Job，已有
  摘要时创建或重置 Review Job；恢复不重复访问 S3。
* `VectorType` 增加严格 bind/result 处理，拒绝错误维度、布尔值和非有限数值，并在真实
  CockroachDB 中完成 1024 维写入/读回验收。
* 尚未迁移的 Memory ORM scaffold 同步为 1024 维，保证 R13 可以复用草稿向量；本轮没有
  创建正式 Memory 表或向量索引。

### 修复的问题

* 修复摘要完成后 Job 链路可能存在的双写缺口；Review 任务与摘要结果现在原子生成。
* 修复 embedding 已提交但审核回执尚未提交时重启会重复调用模型的问题；数据库向量与
  输入 hash 成为恢复判断依据。
* 修复状态接口只能展示 Summary Job、无法区分当前阶段和完整历史的问题，同时保持旧
  `job` 字段兼容。
* 修复 `CRITICAL` 只能被技术失败表示的语义混淆；它现在是成功分析出的业务阻断回执。
* 修复迟到的 Outbox 发布回执可能把已 `SUCCEEDED/RUNNING` 的 Job 倒退为 `QUEUED` 的
  竞态；只有 `PENDING_DISPATCH` 可以转换为 `QUEUED`。
* revision 09 有数据降级会删除 Review Job/Outbox/草稿向量，并把 Submission 恢复为
  R12a 可理解的 `PROCESSING/AWAITING_ENRICHMENT/SUMMARY_GENERATION`，不污染摘要结果。

### 验证结果

* 默认收集 159 项测试，154 项执行全部通过，5 项真实 AWS/CockroachDB 验收默认跳过。
* R12b Fake 覆盖完整两阶段 Job、稳定输入/hash、错误向量、HIGH/CRITICAL 权限、状态脱敏、
  provider 死信/新 key 恢复、embedding 提交后崩溃恢复和重复消息幂等。
* revision 09 SQLite 测试覆盖完整升降级及带真实 Summary/Review Job、向量和回执的数据。
* CockroachDB 隔离库验收显式执行通过，包括 revision 09 升降级、`VECTOR(1024)` 真实
  写入/读回以及原有 Plan/审批/Manifest/Submission 链路。
* Ruff check、mypy（52 个源文件）、全量 pytest 和开发数据库升级均通过；开发数据库为
  `20260722_09 (head)`。
* FastAPI 已使用 R12b 代码在 `127.0.0.1:8790` 重启，health 与 capabilities 均返回 200；
  Worker 因本地未配置专用 SQS/Bedrock 资源而未启动。
* 新增 `RUN_BEDROCK_EMBEDDING_INTEGRATION=1` 真实 Titan V2 opt-in 验收；当前未配置专用
  AWS 凭据，本轮没有实际调用 SQS、S3 或 Bedrock。

### 已知遗留项

* R12b 只保存草稿 embedding；不迁移正式 Experiment/Metric/Memory，也不创建向量索引。
  R13 确认事务再复制或关联正式记忆，并实现结构化过滤优先的查询。
* `NEEDS_REVIEW` 是分析完成后的数据库交接，不是 LangGraph `interrupt()`；确认、拒绝和
  正式实验事务均属于 R13。
* Worker 仍为单并发，并依赖外部提供的 SQS/DLQ、IAM 和 Bedrock 模型权限；生产 AWS
  资源、监控、Web 和最终演示部署属于 R14。
* Bedrock 外部调用后、数据库向量提交前崩溃可能再次调用模型；系统只承诺数据库副作用
  幂等和至少一次外部调用，不声称端到端 exactly-once。

## R13：正式实验确认与结构化/向量查询

版本：`working tree`，当前实现轮次。

### 更新内容

* 新增 Alembic revision `20260722_10`，正式迁移 `experiments`、
  `experiment_metrics` 和 `memories`，并为 `artifacts.experiment_id` 增加数据库外键。
* Experiment 固化确认 ApprovalRecord、Context/Intent 版本、Manifest/Submission、摘要和
  审核回执快照；Metric 复用 R11 扁平结果格式，固定为 `REPORTED/SINGLE_RUN`。
* Memory 复制 R12b 冻结检索文档和 `VECTOR(1024)`，同时保存模型、维度、归一化标记、
  document version 和内容 SHA-256；不在本轮创建近似向量索引。
* 新增 `POST /projects/{project_id}/submissions/{submission_id}/decision` 和
  `submission:review` scope。Researcher 只能审核自己的草稿，Owner 可审核项目内任意草稿。
* APPROVED 前重新读取并校验完整 Context/Intent/Plan/Manifest 链，重新计算风险权限、回执
  source hash 和 embedding document hash；持久化回执不能自行降低门禁。
* 批准事务原子创建 ApprovalRecord、Experiment、Metrics、Memory、Artifact 关联、审计、
  幂等回执并更新 Submission；事务内没有 S3 或 Bedrock 调用。
* REJECTED 支持 CRITICAL/blocking 草稿，必须提供原因，只写最终决定、审计、幂等结果和
  Submission 状态，不产生部分正式记录。
* `experiments_query` 删除客户端 `actor_id`，改用 MCP Token 身份和 `experiment:query`；
  支持 `query+protocol` 候选模式与 `experiment_id` 完整详情模式。
* 候选查询先过滤 project、protocol、CONFIRMED、current/status、model/seed 和 embedding
  版本，再对最近 200 条兼容记录执行精确余弦排序；空候选不调用 Bedrock。
* 完整详情不调用 Bedrock，返回配置、Git、命令、checkpoint、Metrics、Artifact 元数据和
  完整版本追溯；不暴露 S3 key 或下载地址。
* 管理 CLI 增加 `add-researcher`、`issue-api-token`，并扩展 `issue-mcp-token --member-email`；
  新 Token 显式包含本角色需要的 review/query scopes，旧 Token 不会静默扩权。
* MCP 依赖装配使用延迟 embedding 适配器，非查询工具启动时不会提前初始化 AWS 客户端。

### 修复的问题

* 修复正式确认只信任已存 `review_eligibility` 的风险：确认事务现在按未解决风险重新计算，
  HIGH/CRITICAL/blocking 不能通过篡改回执降级。
* 修复正式 Memory 缺少 embedding 来源信息的问题，查询只比较相同模型、维度、归一化和
  document version 的向量。
* 修复查询契约继续携带客户端 `actor_id` 的身份问题；身份只来自服务端验证 Token。
* 修复真实 CockroachDB `<=>` 距离表达式继承 `VectorType` result processor、将标量距离
  误当向量解析的问题；距离现在显式 `CAST AS FLOAT`，查询向量仍参数化为 VECTOR(1024)。
* 修复项目绑定 Owner API Token 可能获得全局 `project:initialize` 权限的问题；项目 Token
  只得到项目审批/审核 scope，bootstrap Owner Token 才保留初始化能力。
* 修复历史或 `current_valid=false` Memory 可能通过 experiment ID 默认查询返回的问题；
  历史详情必须显式启用 `include_historical`。
* 修复 experiment ID 详情模式只校验 Token 绑定项目、未校验正式记录所属项目的问题；
  详情加载现在强制 Experiment、Memory 和 Manifest 均属于请求项目，跨项目 UUID 返回空集。

### 验证结果

* 默认收集 169 项测试，164 项执行全部通过；5 项真实 AWS/CockroachDB 验收默认跳过。
* R13 纵向测试覆盖批准、拒绝、幂等异体冲突、Researcher 最小权限、CRITICAL 阻断、
  embedding 漂移整体回滚、空候选不调用模型、候选/详情两种查询和 REST 身份注入。
* revision 10 SQLite 测试覆盖正式表、Memory 来源字段、Artifact 外键和完整升降级。
* 真实 CockroachDB 隔离库验收显式通过，包括 revision 10 含数据升降级、正式 Memory
  `VECTOR(1024)` 写入、参数化 `<=>` 精确余弦查询及原有 P0 主链。
* SQLite `alembic check` 未检测到新升级操作；受限 `VectorType` 比较器只处理数据库无法
  原样反射的向量类型，不关闭其他表、列或类型的漂移检测。
* 开发 CockroachDB 已升级到 `20260722_10 (head)`；R13 FastAPI 已在
  `127.0.0.1:8790` 启动，health、capabilities 和包含 Submission decision 的 OpenAPI
  均完成实际请求验收。
* Ruff check、mypy（54 个源文件）、全量 pytest 和 `git diff --check` 全部通过。

### 已知遗留项

* R13 精确扫描结构化过滤后的最近 200 条 Memory；P0 数据量不创建向量索引。向量索引、
  性能压测和检索模型迁移策略应在实际数据规模证明需要后单独实施。
* R13 不支持 baseline 晋升、Experiment 废弃/替代写操作或 Artifact 下载 URL；查询只读取
  已有正式记录并显式标记向量结果为候选证据。
* Web、AWS 资源创建、监控和最终 Owner/Researcher 演示属于 R14；自动训练、自动改代码和
  复杂邀请流仍不属于 MVP。

## 2026-07-22 / R14：托管认证、四页 Web、OAuth MCP 与 AWS 最终部署

版本：working tree，基于 `818c94d`（R13）。

### R14a 更新：Cognito Web 认证

* 增加 `CognitoOidcProvider`，使用 Authorization Code + PKCE S256，校验 ID Token 签名、
  issuer、audience、nonce、auth_time、sub 和 verified email。
* 增加一次性 `oidc_transactions`：数据库只存 state SHA-256，加密保存 PKCE verifier、nonce
  和站内 return path，默认五分钟过期且成功后不可重用。
* 增加 `web_sessions`：Cookie 原文仅返回 Browser，数据库保存 SHA-256；idle 8 小时、
  absolute 7 天、recent auth 10 分钟，支持撤销原因和审计。
* 首次登录只把 Cognito sub 绑定到管理员预建的 verified email User；未知用户和多团队用户
  在 MVP 中被拒绝，不创建隐式成员关系。
* FastAPI 支持 Bearer API Token 或 Web Session；Cookie 写请求统一校验 HMAC CSRF。
* 新增 login/callback/me/logout/reauth 路由。重新认证使用 Cognito `prompt=login`，不会要求
  应用保存密码或 Cognito Token。
* Plan Check 最终决定、正式策略发布和 Owner High-risk Submission 批准增加近期认证门禁，返回
  稳定 `428 RECENT_AUTHENTICATION_REQUIRED`。
* revision 11 只增加 `User.cognito_sub`、Web Session 和 OIDC Transaction，没有任何密码列。

### R14a 更新：管理 API 与正式策略版本

* 增加项目列表、当前设置与 Context 历史、Plan/Submission/Experiment 分页列表和详情。
* Owner 发布策略使用 expected Context version、Owner、scope、近期认证和 Idempotency-Key。
* 一个事务把旧 Context 置为 SUPERSEDED、旧 Intent 置为 CLOSED、旧约束置为
  SUPERSEDED/inactive，再创建全部 CONFIRMED 新版本及审计。
* 约束路径、Intent allowed variables、active config 和 expected value 使用严格类型校验。
* 已创建 Plan Check/Manifest 继续引用旧版本，不因当前策略变化被静默修改。
* 页面读取按实时 TeamMembership 过滤：Researcher 只能看到自己的 Plan/草稿，正式实验对
  团队成员可见。
* Plan report 返回时动态合并当前 approval status 和 Manifest 可创建状态，避免旧报告过时。
* 增加 Artifact 下载 URL，强制 cloud hash verified 和非空 S3 VersionId，并记录下载签发审计。

### R14b 更新：四个 Web 页面

* 新增 `web/` React 19 + TypeScript + Vite 应用，使用 TanStack Query、Router 和 Lucide。
* 项目设置页展示正式 Context/Intent、约束、确认人与历史，并为 Owner 提供版本发布操作。
* 计划审批页展示当前检查/审批状态、参数 diff、风险、命令和最终决定。
* 实验审核页展示短回执、风险、Artifact 和决定；High/Critical 风险默认强制展开。
* 实验查询页浏览正式记录并调用结构化过滤优先的向量候选查询。
* 前端只保存运行时 CSRF，不保存 Cognito Token；401 显示 Managed Login，428 跳转 reauth。
* 增加响应式桌面/移动布局、加载/空/错误/无权限状态、稳定按钮与列表尺寸。
* 增加 Vitest 认证壳测试和 Playwright 四页导航测试；桌面 1440x960 与移动 390x844
  视口截图确认没有页面级横向溢出或控件重叠。
* npm audit 无已知漏洞，build、ESLint、Vitest 和 Playwright 测试通过。

### R14c 更新：远程 MCP OAuth

* Streamable HTTP MCP 使用 MCP SDK TokenVerifier 与 AuthSettings，自动公开 RFC 9728
  Protected Resource Metadata 和带 metadata URL 的 Bearer challenge。
* Cognito Access Token 必须通过 JWT 签名、issuer、resource audience、expiration、token_use、
  client_id 和 scope 校验。
* 增加七个 OAuth scope 到应用 scope 的明确映射；服务内部继续执行现有项目绑定和业务门禁。
* 当前 FastMCP 将 metadata scope 同时作为全局门槛，因此 R14 CLI 只接受完整七 scope 客户端，
  防止生成实际无法连接的子集配置；按工具最小 scope 留待 SDK 支持后评估。
* revision 12 增加 `mcp_oauth_clients` 和 `mcp_oauth_grants`。Client 只允许绑定一个项目；
  User 通过 Cognito sub 识别，成员关系每个请求复核。
* 第一次有效访问创建本地可审计 Grant；Client 或 Grant 被撤销后，仍有效的 Cognito Token
  立即失效。
* CLI 增加 register/revoke client 和 revoke grant。R14 没有 DCR 或 Client ID Metadata。
* 本地 stdio 环境继续支持原哈希 MCP Token；远程 HTTP 缺少 Cognito/Resource 配置会拒绝启动。

### R14d 更新：AWS 和容器

* 增加非 root Python 3.12 后端 Dockerfile及 Web multi-stage Dockerfile。
* 后端镜像最初使用 `pip install .`，已改为先安装 `requirements/lock-server.txt`，再
  `pip install --no-deps .`；实际启动进一步发现锁文件漏掉 boto3 依赖树，已补齐 boto3、
  botocore、s3transfer、jmespath、python-dateutil 和 six 后重新构建通过。
* Dockerfile 支持可选的 `PIP_INDEX_URL`/`NPM_CONFIG_REGISTRY` build arg；Web 构建上下文通过
  独立 `.dockerignore` 从 176 MB 收敛到约 1.5 KB。
* Web Nginx 将 Compose `api` 主机改为请求期解析，静态容器可独立启动；根路径和 SPA 深层
  路由都已实际返回 200。
* Vite 开发代理支持 `VITE_API_PROXY_TARGET`，npm 脚本显式指定 TypeScript 配置；清理了会
  优先于 `vite.config.ts` 加载、导致代理仍指向旧端口的本地 `vite.config.js` 残留产物。
* Terraform 创建 VPC、公私子网、NAT、CloudFront/OAC/WAF、私有 Web S3、HTTPS ALB、
  ECS API/MCP/Worker、ECR、IAM、CloudWatch、Secrets Manager、KMS S3 Artifact、SQS/DLQ。
* Artifact S3 强制 Versioning/KMS/Public Access Block；CloudFront 到 ALB 使用随机源站 Header。
* Cognito 创建 Managed Login v2、管理员建用户、Web confidential client、资源服务器和
  显式 `mcp_clients` public client map；Access Token 15 分钟，MCP Refresh 30 天 rotation。
* Cockroach Cloud 保持外部托管，只通过 Secrets Manager 注入安全连接串。
* 使用 Terraform 1.9.8 下载签名 Provider，AWS 6.55/Random 3.9 schema validate 成功。

### R14e 更新：演示和文档

* 增加公开部署验收脚本，检查 Web、API health、RFC9728、Cognito discovery、七 scope、
  DCR 禁用和可选 Owner/Researcher Session，不输出敏感凭据。
* 增加 BLOCKED、NEEDS_APPROVAL、result/log/note 演示素材和六场景双角色 Runbook。
* 增加 R14 安全边界、AWS 部署手册，同步 README、当前框架图和迭代状态。

### 修复的问题

* 人类身份不再依赖客户端 Bearer API Token 或任何自建密码字段。
* Session 不把 Cognito Token 暴露给前端，CSRF、idle/absolute timeout 和撤销均在服务端执行。
* 敏感操作不会仅凭长期 Cookie 批准，必须有近期 Cognito 认证。
* 远程 MCP 身份不能来自工具参数、未注册 client_id 或仅靠 JWT；本地授权状态可即时撤销。
* 策略更新不会覆盖历史版本或改变旧 Manifest；页面不使用过时 report 审批状态。
* Artifact 下载不会读取可变 latest object，只签发已验证 VersionId。
* AWS 入口不会让 Web、API 和 MCP 以未认证的默认路径裸露；DCR 明确未部署。

### 验证结果

* Ruff 全仓通过；mypy 62 个源文件通过。
* 全量 pytest 通过；新增 Web Auth、生产配置、策略版本和 MCP OAuth 集成测试覆盖登录绑定、
  超时、CSRF、近期认证、历史保留、客户端绑定与即时撤销。
* 本地真实 CockroachDB 完成空库 upgrade head、降级到 R5/R3 和再次升级；R14 Session/OIDC/
  MCP OAuth 表及 `cognito_sub` 的升降级断言通过。
* Web `npm run build`、`npm run lint`、Vitest 和 Playwright 双视口四页验收通过，npm audit 为
  0 vulnerability。
* Terraform `fmt -check` 和 `validate` 通过。
* `experiment-guardian:r14` 和 `experiment-guardian-web:r14` 均实际构建并启动；后端 health、
  Web 根路径与 SPA 深层路由均返回 200。目标 ECR 推送仍属于真实 AWS 验收。
* 本地备用端口 `8788/5174` 实际启动通过，Vite `/api` 代理和 SPA 深层路由均返回 200。

### 已知遗留项

* 本轮没有目标 AWS 账号、域名、ACM、Cockroach Cloud、Bedrock access 和双角色凭据，因此
  未执行 `terraform apply` 或真实六场景；步骤已收敛到 `R14_DEPLOYMENT.md`/`R14_DEMO.md`。
* Cognito MFA、威胁防护和组织 IdP 策略由部署团队在 User Pool 管理，不在应用重新实现。
* Terraform State 含敏感值，目标环境必须配置加密、版本化和锁定的远程 Backend。
* R14 不支持动态 MCP client registration、任意第三方零配置、自动训练、自动改代码、
  baseline 自动晋升或向量索引。

## 2026-07-23 / R14 Local：单机可替换基础设施部署

版本：working tree，延续 R14 领域模型和状态机。

### 更新内容

* 增加 `DEPLOYMENT_MODE=cloud/local` 和 backend/provider 条件配置校验；本地模式不要求或实例化
  Cognito、AWS S3、SQS、Bedrock，production 拒绝 `local_owner`。
* `LocalOwnerWebAuthService` 通过真实 User、唯一 TeamMembership 和 Owner 角色建立现有
  WebSession，复用 HttpOnly Cookie、CSRF、实时 scope、近期认证、撤销和审计。
* 在现有 ArtifactStorage 端口下增加 S3-compatible 适配器，支持 MinIO 内外端点、Bucket
  Versioning、预签名 PUT/GET、真实 VersionId、固定版本读取、SHA-256 metadata fallback。
* 在现有 SubmissionQueue 端口下增加 DatabaseOutboxQueue，复用 Outbox、WorkflowJob、lease、
  generation、重试游标，新增 COMPLETED/DEAD_LETTER 终态用于保留本地投递历史。
* 增加百炼 OpenAI-compatible 摘要和 embedding 适配器。摘要 temperature=0、无工具、长度受限；
  向量固定 1024 维并校验有限数值、零范数和归一化，持久化 provider/model/version 元数据。
* revision 13 增加模型 provider 元数据并升级 Outbox 状态约束，支持安全 downgrade。
* 增加 `bootstrap-local` 幂等初始化，以及 CockroachDB、MinIO、一次性 migration/minio-init/
  local-init、API、Worker、Web Compose 链路。

### 修复的问题

* 单机部署不再被无关 AWS/Cognito 配置阻塞，也不会在本地路径隐式创建 boto3 云客户端。
* MinIO 预签名 URL 区分容器内服务端 endpoint 与宿主机浏览器 endpoint，仍保持同一对象 key
  的防覆盖与固定版本证据链。
* 数据库空队列使用可配置等待，崩溃后由 Outbox/Job 双租约恢复；过期 generation 不能回写。
* CockroachDB 实测发现带 `JOIN` 的 `FOR UPDATE SKIP LOCKED` 可能漏取可用 Outbox；数据库队列
  改为只锁 Outbox，再在同一事务中校验 Job 状态和 generation，并保留 40001 有界重试。
* 历史 Memory/SubmissionEmbedding 明确记录 provider，切换模型不会覆盖旧向量。
* 共享应用层的近期认证与模型错误提示改为 provider-neutral，local_owner/百炼路径不再被错误
  描述为 Cognito/Bedrock 事实。

### 验证结果

* Ruff 全仓通过；mypy 63 个源文件通过；全量 pytest 为 208 passed、8 skipped。跳过项仅为
  需要显式凭据或真实服务的 CockroachDB、MinIO/S3、SQS、Bedrock 和百炼验收。
* 真实 Docker MinIO 的 10 项适配器/单元验收通过：预签名 PUT、`If-None-Match` 防覆盖、
  metadata SHA-256、真实 VersionId、多版本、固定旧版本读取、错误版本与预签名 GET。
* 真实单节点 CockroachDB 完成空库 upgrade head、跨版本 downgrade/re-upgrade、完整业务写入、
  Outbox 发布和双 Worker 并发唯一 claim；revision 13 provider 列及终态约束通过。
* 本地应用集成链覆盖 Plan、Approval、Manifest、Submission、DatabaseOutboxQueue、Mock Bailian
  摘要/embedding、审核回执、正式确认和幂等重放，Experiment/Memory/Artifact 未重复创建。
* Compose 实际完成 database-init、单一 migration、MinIO Versioning、幂等 local-init、API、
  Worker 和 Web 启动；health、local_owner Session/CSRF 和重复初始化均通过。
* Web ESLint、Vitest 2 tests、production build 和 Playwright desktop/mobile 2 场景通过；AWS
  Terraform `fmt -check` 与 `validate` 继续通过。
* 百炼默认 HTTP mock 覆盖成功、超时、非 2xx、空输出、非法 JSON、维度错误和 NaN/Infinity；
  真实调用仅在 `RUN_BAILIAN_INTEGRATION=1` 时运行。

### 已知遗留项

* 本地模式是回环地址上的单机可信开发部署，不提供远程访问、多节点 MinIO、自动备份或 HA。
* 真实百炼可用性、配额和具体模型 1024 维能力取决于部署账号，仓库不保存真实 API Key。
* 本轮未持有真实百炼 Key，因此实际 Compose 闭环使用 HTTP mock 完成模型协议验收；真实百炼
  双模型调用保留为显式 integration gate。

## 2026-07-23 / R14 Local 安全与模型失败边界修复

版本：working tree。

### 修复的问题

* local_owner 模式新增 FastAPI `TrustedHostMiddleware`，只接受配置 URL 中的
  `127.0.0.1`/`localhost`；非回环 Web URL 在 Settings 启动校验阶段直接失败。
* 本地 Compose 的 Nginx 使用运行时模板，允许 Host 由部署环境提供；本地值只允许
  `127.0.0.1 localhost`，默认 server 对其他 Host 返回 421，阻止 DNS rebinding 在登录前
  获得 Owner Session。云端镜像继续使用独立的可配置 Host 模式。
* 百炼摘要严格验证 choice、message、usage 为 JSON 对象；embedding 严格验证 data item、
  vector 和 usage。所有 HTTP 200 畸形结构统一转换为 `ServiceUnavailableError`，继续进入
  现有 Job 错误持久化、最大尝试次数和死信流程。

### 验证结果

* 新增恶意 Host、非回环 URL、`message=null/list`、`data=[null]/[{}]` 和非法 usage 回归。
* 后端全量测试 `219 passed, 8 skipped`；Ruff 和 mypy（63 个源码文件）通过。
* Web ESLint、Vitest、production build 和 Playwright desktop/mobile 2 个场景通过。
* Compose 重建后 API、Worker、Web 正常启动；合法回环 Host 可登录，恶意 Host 由 Nginx
  返回 421，绕过 Nginx 直连 API 时由 FastAPI 返回 400。

### 已知遗留项

* Host 白名单不把 local_owner 变成远程安全认证；本地服务仍不得暴露到局域网或公网。

## 2026-07-23 / R14f 正式策略双表示

版本：working tree。

### 更新内容

* 新增 revision `20260723_14` 和 `policy_narratives`，保存 Context/Intent 版本、结构化来源
  SHA-256、确定性模板版本、Markdown、生成状态、操作者和时间。
* 项目初始化和正式策略发布自动生成 `policy-narrative-v1`；模板逐项覆盖目标、协议、基线、
  Intent、受控变量以及 LOCKED、APPROVAL_REQUIRED、EXPERIMENT_VARIABLE。
* Repository 在每次读取时重算来源哈希；`STALE` 内容不会返回，旧版本无记录时明确为
  `MISSING`，渲染错误持久化为 `FAILED`。
* 增加 Owner 重生成 API，继续使用项目 RBAC、CSRF 和 AuditLog，不提供自然语言直接编辑。
* Web 设置页默认显示人类可读说明，完整 JSON 放入高级视图，Context 历史可展开各自说明。
* MCP `project_get_context` 同时返回双表示，并声明结构化字段是唯一治理依据。

### CockroachDB 评估

* 本轮不增加 Context/Intent embedding 或 Distributed Vector Index：当前没有对应查询入口，
  现有 `experiments_query` 只查询正式 Experiment Memory，混入策略文本没有正确业务语义。
* 当前环境未安装 CockroachDB Agent Skills；使用现有 metadata、迁移升降级和 CockroachDB
  integration gate 完成审查，不增加生产依赖。
* Cloud Managed MCP 只保留为未来 Cloud 只读诊断选项；本轮不涉及 Cloud 集群，排除 ccloud。

### 验证结果

* 确定性模板、来源哈希、初始化、发布、历史版本、STALE 隐藏、FAILED 降级和重生成测试通过。
* 后端全量 `224 passed, 8 skipped`；Web ESLint、Vitest 2 tests、production build 和
  Playwright desktop/mobile 2 个场景通过。
* 在真实 CockroachDB 26.2 本地实例完成 revision 13 -> 14 升级、14 -> 13 降级和再次升级；
  `policy_narratives` 的外键、唯一约束、状态约束和版本索引均成功创建。
* Compose 按当前源码重建并健康启动；真实 `local_owner` Session + CSRF 链路验证旧 Context
  从 `MISSING` 经 Owner 重生成变为 `READY`，返回明确标记 `authoritative=false`。

### 已知遗留项

* revision 14 不猜测性回填旧 Context；升级后显示 `MISSING`，由 Owner 按需重生成。
* Context/Intent 语义搜索没有真实消费路径，待独立查询契约明确后再评估结构化前缀向量索引。

## 2026-07-23 / R15 内部实验治理 Agent 规划

版本：working tree，仅完成设计，未增加 R15 运行时代码或迁移。

### 现有实现审计

* 现有 WebSession、CSRF、实时 RBAC、近期认证、幂等写入和 AuditLog 可以继续作为 Agent 的
  身份与正式执行边界。
* 项目策略、Plan、Submission 和正式 Experiment 已有应用层读取用例；正式策略发布、
  Plan 决定和 Submission 决定已有不可绕过的业务服务。
* 现有 `SummaryTextGenerator` 明确禁止工具调用，不能直接改造成 Agent；需要新增独立的
  provider-neutral `AgentChatModel`。
* Submission WorkflowJob/Outbox 具有成熟 lease/generation 语义，但 schema 与 Submission
  强绑定；R15b 如需异步 Agent Run，应新增专用 Job，而不是复用伪造的 submission_id。
* 当前正式 Experiment Memory 与向量查询已有明确语义，不能混入对话摘要或候选研究结论。

### 规划决策

* 采用有界单 Agent 和类型化工具，不在 R15a 引入多 Agent、任意 SQL、Shell、代码解释器或
  通用写工具。
* R15a 只提供项目状态、正式实验列表、实验详情和当前用户待办四个只读工具，并持久化
  Thread、Message、Run、ToolCall 和 Citation。
* 百炼 OpenAI-compatible Function Calling 作为首个实现；现有摘要、Agent 和 embedding 使用
  三个独立模型槽位，Bedrock 通过相同端口在后续补齐。
* 原始消息由 CockroachDB 保存；上下文压缩使用带 sequence/hash 的 rolling summary 和最近
  消息窗口，不把百炼 Conversations 或 Context Cache 当作正式记忆源。
* R15c 才增加完整 Policy Bundle 草稿；R15d 才增加正式操作提案和人类确认。
* 正式执行不暴露为模型工具。用户确认发起新的 CSRF + recent-auth 请求，服务端锁定提案、
  复核 digest、当前版本和实时权限后调用既有业务服务。
* R15e 才增加独立的 Agent Research Memory；其内容始终是带正式引用的候选证据。

### 调研依据

* 百炼 Function Calling 支持标准的模型选工具、应用执行工具、模型总结结果循环。
* LangGraph 的 checkpointer/store、interrupt 和消息摘要模式适合状态化 Agent，但本项目仍需
  显式领域表保存审计和正式确认。
* 轨迹评测需要覆盖最终回答、单步工具选择和完整工具路径；R15a 先建立 20-30 个仓库内 case。
* Prompt Injection 和 Excessive Agency 通过最小工具集合、严格参数、项目隔离、资源上限和
  独立人类确认控制，而不是只依赖 system prompt。

### 下一步

* 唯一实现目标为 R15a 只读对话纵向切片。R15a 验收完成前不实现统计诊断、治理草稿、正式
  写入或长期向量记忆。
* 完整工具目录、数据设计、上下文策略、提示词版本、确认协议和 R15a-R15e 验收见
  `docs/INTERNAL_GOVERNANCE_AGENT_PLAN.md`。

### 已知风险

* 尚未选定真实百炼 Agent 模型，实施前必须确认该模型支持 Function Calling 并建立真实调用
  integration gate。
* 第一版同步有界 Run 仍可能受 Web 请求时限影响；专用异步 Agent Job 和恢复放在 R15b。
* LLM 输出质量不能只靠普通单元测试，需要从 R15a 起维护版本化 eval 数据集和模型基线。

## 2026-07-23 / R15a：只读内部实验治理 Agent

版本：working tree，revision `20260723_15`。

### 更新内容

* 新增 provider-neutral `AgentChatModel`、严格消息/工具/事件/回答契约，以及百炼
  OpenAI-compatible 流式 Function Calling 适配器；摘要模型继续禁止工具调用。
* 新增 `agent_threads`、`agent_messages`、`agent_runs`、`agent_model_calls`、
  `agent_tool_calls`、`agent_citations`、`agent_run_events` 七张增量表。对话追加写，Run 保存
  provider/model/prompt/tool catalog/context/usage，模型与工具调用保留有界输入输出快照。
* 新增专用 Agent Run 队列和 Worker：CockroachDB 原子 claim、lease、generation、最大尝试、
  指数等待和 dead-letter；不复用与 Submission 强绑定的 WorkflowJob。
* 实现有界单 Agent LangGraph loop，限制模型调用、工具调用、工具输出、模型输出、最近消息和
  wall time。上下文仅来自 CockroachDB，R15a 使用确定性裁剪，不使用 provider 对话缓存。
* 仅注册 `project_status_get_v1`、`experiments_list_v1`、`experiment_get_v1` 和
  `pending_work_list_v1`。身份与 project 不接受模型参数，工具执行时用原 Web Session 恢复
  RequestIdentity 并实时校验 Membership、scope 和项目隔离。
* 最终回答必须通过 `AgentAnswer` schema，并且正式事实只能引用本 Run 工具返回的 evidence；
  重复、缺失、跨 Run 或清单不一致的引用会拒绝，不生成 Assistant 消息。
* 新增 Thread 创建/列表/归档/恢复、幂等消息入队、Run 查询/重试和持久化 SSE API。SSE 支持
  `Last-Event-ID` 重放和 heartbeat，浏览器断开不会取消后台 Worker。
* Web 增加第五页“治理 Agent”，支持会话列表、对话、运行状态、正式引用、归档/恢复、错误
  显示和失败重试；Session capability 控制入口，前端不持有百炼凭据。
* Compose 增加可选 `agent` profile 和独立 `agent-worker`；Terraform 增加可选私有 ECS Agent
  Worker、CloudWatch 日志和百炼模型配置，不改变默认 R14 云端部署。
* 新增 24 个版本化 Agent eval case，覆盖工具选择、权限、引用、澄清、越权和 prompt
  injection；默认 CI 使用 scripted provider，真实百炼 Function Calling 由
  `RUN_BAILIAN_AGENT_INTEGRATION=1` 显式开启。

### 修复的问题

* 所有 Agent Thread、Run 和 SSE 读取/写入现在都只接受 `WEB_SESSION`，Bearer API Token 不能
  读取个人对话或绕过浏览器交互边界。
* Agent Run 重试补齐用户、原 Run、Session 和新 Run 的不可变审计记录。
* 分页 cursor 同时捕获 Base64 padding 和数值错误，统一返回输入校验错误，不再泄漏 500。
* 百炼 SSE 在 `[DONE]` 前结束时统一转换为可重试 `ServiceUnavailableError`，避免把上游截断
  误判为不可重试的模型业务回答错误。
* SSE 在发送响应头前完成 feature、身份、项目和 Run 所有权校验，401/403/404 仍可返回标准
  JSON 错误。

### 验证结果

* Python 全量 `238 passed, 9 skipped`；Ruff、Web ESLint、Vitest 2 tests 和 production
  build 通过。
* Terraform 1.9.8 `validate` 和带 `agent` profile 的 `docker compose config --quiet` 通过。
* 真实 CockroachDB 26.2 完成 revision 14 -> 15 升级、15 -> 14 降级和再次升级；确认七张
  Agent 表全部创建、降级后为零且 head 恢复为 `20260723_15`。
* 额外隔离 CockroachDB 全链测试在数据库 DDL 清理阶段长时间等待，已终止并删除其随机临时
  数据库；默认全量测试和直接真实迁移往返不受影响。
* 真实百炼 Agent 测试默认跳过，因为本次未提供外部模型凭据；可用文档中的显式 gate 验收。

### 已知遗留项

* R15a 不实现实验比较、统计、异常诊断、rolling summary、治理草稿、正式写操作或长期记忆。
* 当前只有百炼 `AgentChatModel`；其他 provider parity 留到后续阶段。
* 真实模型质量、延迟和工具选择稳定性必须在选定具体百炼模型后运行可选 integration gate 和
  版本化 eval，默认 scripted provider 只能证明协议和治理边界。

### 下一步

* R15b 只增加确定性可比性、基础统计、Plan/Submission 诊断和带来源哈希的 rolling summary。
  R15c 前不创建治理草稿，R15d 前不开放任何正式写入。

## 2026-07-23 / R15b：实验分析与对话压缩

版本：working tree，revision `20260723_16`。

### 更新内容

* 新增纯领域模块 `agent_analysis.py`。两实验比较先执行分层门禁：dataset、protocol、完成状态、
  指标语义、指标方向冲突和追溯完整性属于硬阻断；model、Context、Intent、模式、seed、Git、
  checkpoint、命令和非 seed 配置差异作为 caveat。
* 配置 diff 复用现有无碰撞路径编码并采用 JSON 类型严格比较，`True`、`1`、`1.0` 不再在分析
  中被视为相同；最多返回 50 项并明确截断。
* 新增 `experiments_compare_v1` 和 `experiment_group_stats_v1`。统计只接受显式 2 至 20 个
  Experiment ID，严格重复组才计算 mean、sample stddev、median、min/max/range；不自动分组，
  不计算显著性，不输出因果结论。
* 新增 `plan_check_explain_v1`，从 Plan 当前审批列、ApprovalRecord 和 Manifest 动态合成资格，
  不信任旧 report 中可能过期的审批派生字段。
* 新增 `submission_diagnose_v1`，检查 Submission 追溯、Artifact 固定版本、未解决风险、
  WorkflowJob、摘要/embedding/回执；不下载 Artifact 或日志。Owner 可看项目内记录，
  Researcher 仍只能看自己的 Submission。
* 确定性工具同时返回正式记录 `CONFIRMED_FACT` 和计算结果 `ANALYSIS`。最终回答现在校验每个
  section 的 evidence kind、引用存在性及引用并集，摘要不能成为 Citation。
* revision 16 新增 `agent_context_summaries`、`agent_threads.current_summary_id` 和
  `agent_model_calls.purpose`。摘要保存消息范围、来源消息 ID/hash、prompt/provider/model、
  model call 和 READY/FAILED 结果，原始消息继续永久追加保存。
* Run 在至少 6 条较早消息或上下文达到预算 80% 时增量摘要；READY 后使用摘要加最近 12 条消息。
  失败时继续使用上个 READY 摘要或最近窗口，不令本轮 Run 失败，并通过 SSE 和 Web 显示降级。
* 新 Run 固化 `r15b-v1` Prompt/工具目录，运行时仍支持数据库中既有 `r15a-v1` Pending Run；
  对旧 Run 的人工 retry 继续保留原版本，避免部署升级改变其可见工具。
* Web 默认按事实、用户输入、分析和假设显示回答段落，引用保留高级展开视图；摘要失败展示
  非权威降级提示。R15a API 字段保持兼容，新增字段均有默认值。
* R15b 评测集扩展到 48 个 case，覆盖比较门禁、显式统计、动态审批、Submission 诊断、证据
  分层、摘要非事实源、跨项目权限、因果拒绝和 prompt injection。

### 修复的问题

* 描述性比较不会再跨 dataset/protocol 或不同指标语义强行排名；主指标方向未知时不输出
  better/worse，左侧值为零时相对变化返回 `null`。
* Plan 解释不会重放 report 中旧的 `approval_status/can_create_manifest`。
* 摘要模型返回错误范围、错误消息 ID、工具调用、畸形 JSON 或 provider 错误时，失败尝试可
  审计且不会污染 READY 指针。
* 摘要 JSON 可空字段使用 SQL NULL 语义，避免 SQLite/CockroachDB 的 `IS NULL` 状态约束被
  JSON `null` 绕过。
* 诊断分析和模型假设不能伪装成正式事实；服务端不再只检查“引用存在”，还检查证据类别。

### 验证结果

* Python 全量 259 项：`250 passed, 9 skipped`；Ruff 全仓通过。
* 新增纯函数矩阵覆盖类型严格比较、路径转义、零基线、硬阻断、caveat、精确统计、重复 seed、
  cohort 拒绝和未知指标方向。
* Agent 集成测试覆盖 rolling summary 成功/失败、ModelCall purpose、SSE 事件、旧 R15a 目录、
  动态 Plan 状态、Submission Job 死信诊断和 Researcher 所有权。
* SQLite Alembic head 与跨版本降级通过；真实 CockroachDB 26.2 完成
  `20260723_15 -> 16 -> 15 -> 16`，临时验收数据库已清理。
* Web ESLint、Vitest 2 tests、production build，Terraform 1.9.8 `validate` 和
  `docker compose --env-file .env.local config --quiet` 通过。

### 已知遗留项

* R15b 不创建治理草稿、不执行审批/确认、不自动形成实验组，也不读取原始日志内容。
* 描述统计只反映用户显式选择的已确认实验，不包含置信区间、显著性检验或因果推断。
* rolling summary 由模型生成且明确非权威；所有正式事实仍需本 Run 重新调用只读工具。
* 当前仍只有百炼 `AgentChatModel`。本轮未提供真实百炼凭据，因此真实模型质量和 token 成本
  未验收；默认 scripted tests 证明协议、持久化和安全降级边界。

### 下一步

* R15c 只实现完整 Policy Bundle 草稿、revision、歧义、确定性 diff 和影响分析及 Web 编辑；
  R15d 前不增加正式发布、审批或 Submission 确认执行入口。

## 2026-07-24 / R15c：治理草稿与影响分析

版本：working tree，revision `20260724_17`。

### 更新内容

* 新增完整 `PolicyDraftCandidate` 聚合，始终同时保存 Context、Intent 和 Constraints，不允许
  Agent 只提交局部正式策略。候选 JSON 最大 256 KiB，`True`、`1`、`1.0` 使用稳定 JSON
  语义严格区分。
* 新增 `agent_policy_drafts` 和 `agent_policy_draft_revisions`。草稿头冻结基准 Context/Intent
  ID、版本、完整快照和 SHA-256；revision 追加写并保存 author、Agent Run/ToolCall 或 Web
  来源、请求 hash、候选、校验、diff、说明和当时的影响快照。
* `PolicyDraftService` 统一执行实时 Membership、Researcher 自有范围、Owner 项目代管、Web
  Session、幂等和乐观 `expected_revision`。Owner 代管不会篡改作者，所有操作写 AuditLog。
* 草稿基准与当前正式 Bundle 不一致时返回 `STALE` 并阻止修订，不静默 rebase；取消不可逆，
  历史 revision 继续可读。
* 确定性校验覆盖重复约束路径、Intent 允许变量与保护级别冲突、active config 与
  `expected_value` 漂移。语义无效的候选仍保存为 `INVALID` 供用户修复，不会发布。
* 确定性 diff 按字段和参数路径稳定排序，显示新增、修改、删除、原值、新值、影响级别和说明；
  候选人类可读回执由模板生成，明确非权威且不调用 LLM。
* 影响分析只对最多 20 个当前 `PENDING` Plan 使用冻结输入调用既有 `evaluate_plan()` 做内存
  模拟，不写回原 Plan。进行中 Submission 只显示原 Manifest 的 Context/Intent 版本追溯。
* Agent 新增 `policy_draft_create_v1`、`policy_draft_update_v1`、
  `policy_draft_validate_v1` 和 `policy_draft_impact_get_v1`。`r15c-v1` Prompt 要求创建前同一
  Run 先读取正式 Bundle，每个 Run 最多一次候选写入，禁止正式发布、审批或确认。
* 新增 `CANDIDATE_DRAFT` evidence，服务端校验最终回答的分段类型和本 Run 引用。rolling
  summary 升级 schema v2，可保留有界草稿 ID/revision/status/歧义引用；R15a/R15b 目录兼容。
* Agent REST 增加草稿列表、详情、历史 revision、追加 revision 和取消。写 API 继续要求
  CSRF、Web Session 和 `Idempotency-Key`。
* Web Agent 页增加治理草稿工作台：当前/已取消列表、候选回执、结构化编辑、完整原始 JSON、
  确定性 diff、影响、校验、历史和取消。`STALE` 与历史 revision 只读，界面没有发布按钮。
* R15c 评测集增加 38 个 trajectory/security case，覆盖完整 Bundle、歧义、revision、影响、
  过期、项目隔离、正式写拒绝和 prompt injection。

### 修复的问题

* 正式发布和草稿校验复用同一纯领域校验入口，避免两条链路对重复路径或
  Intent/Constraint 冲突得出不同结论。
* Agent Runtime 现在把真实 Run ID 和 ToolCall ID 传给草稿服务，候选 revision 可追溯到具体
  模型调用轨迹，模型不能通过参数伪造 actor、项目或来源。
* 模型不能把候选草稿标为正式事实；`CANDIDATE_DRAFT`、`CONFIRMED_FACT` 与 `ANALYSIS`
  evidence 类型不匹配时最终回答会被拒绝。
* 真实 CockroachDB 首次迁移验收发现
  `fk_agent_policy_draft_revisions_source_tool_call_id_agent_tool_calls` 超过 63 字符限制；
  已改为稳定短名称 `fk_agent_policy_draft_revisions_source_tool_call`。
* Web revision 测试改用稳定角色控件等待，避免同一草稿标题在列表、详情和历史中出现时产生
  测试选择器歧义。
* Web 测试等待 revision invalidation 完成并显式清理 QueryClient/fetch stub，消除测试环境销毁
  后 React 调度继续访问 `window` 的异步泄漏。

### 验证结果

* Python 共收集 268 项，全量结果为 `259 passed, 9 skipped`；Ruff 规则和 mypy 通过。本轮新增
  Python 文件已执行 Ruff formatter，没有格式化无关历史文件。
* 集成测试覆盖 Agent Runtime 的“读取正式 Bundle -> 创建候选 -> CANDIDATE_DRAFT 引用”完整
  轨迹、Agent 幂等、Web 乐观并发、Researcher 隔离、Owner 代管作者、STALE、Plan 模拟及正式
  Context/Intent/Constraint 表行数不变。
* SQLite Alembic head、17→16→17 及后续历史降级回归通过。
* 真实 CockroachDB 26.2 完成 `20260723_16 -> 20260724_17 -> 20260723_16 ->
  20260724_17`；确认两张表和短外键存在，降级后两张表为零，临时数据库已清理。
* 现有隔离 CockroachDB 全链测试完成最后 `DROP DATABASE` 后 pytest 仍未退出，手工中断并改用
  专用迁移往返验收；这是测试基础设施退出挂起，不是 revision 17 DDL 失败。
* Web ESLint、4 项 Vitest 和 production build 通过。真实百炼 Agent 质量测试仍由
  `RUN_BAILIAN_AGENT_INTEGRATION=1` 显式开启，本轮未使用外部凭据。
* 本地 Compose 使用现有 `.env.local` 重建 migration/API，生产数据库升级到 head 17，API
  health 返回 200。该环境 `AGENT_ENABLED=false`，可选 Agent Worker 按设计拒绝启动后已停止，
  未为验收擅自修改用户的本地模型配置。

### 已知遗留项

* R15c 没有草稿发布、Plan 决定、Submission 最终审核或任何正式 execute 工具；正式操作仍只能
  使用已有管理页面和业务 API。
* 草稿过期后不自动 rebase。用户必须基于最新正式 Bundle 新建草稿，避免模型静默合并治理含义。
* Plan 影响只模拟当前最多 20 个待审批记录；Submission 影响只做不可变版本追溯，不声称验证
  实际训练行为。
* 候选说明采用确定性模板，没有增加第二次 LLM 润色；事实完整性优先于语言自由度。
* 当前仍只有百炼 `AgentChatModel`，长期 Agent Research Memory 和 provider parity 留到 R15e。

### 下一步

* R15d 只增加不可变 Action Proposal、digest/base-version 协议和独立人类确认 API，复用既有
  Policy 发布、Plan 决定和 Submission 审核服务。正式执行不得注册为模型工具。
* 确认前必须展示完整操作与影响；执行时重新校验 CSRF、近期认证、实时 RBAC、当前版本、风险、
  事务锁和幂等。R15d 不增加长期记忆、任意 SQL、训练或改代码能力。

## 2026-07-24 / R15d-a：Policy 发布提案与 Owner 独立确认

版本：working tree，revision `20260724_18`。

### 更新内容

* 新增 `agent_action_proposals`，保存 `POLICY_PUBLISH`、创建人和 Agent Thread/Run/ToolCall、
  来源草稿/revision、完整 `PolicyPublishRequest`、候选/基线/待办哈希、冻结 diff/影响、
  24 小时有效期、digest、确认/取消/执行结果和错误。
* 提案状态为 `PROPOSED/EXECUTED/CANCELED/STALE/EXPIRED/FAILED`。GET 只计算实时
  `READY/STALE/EXPIRED/TERMINAL`，不会隐式写库；确认过期或漂移提案时才固化终止状态。
* `ActionProposalService` 在准备时重算确定性校验和影响，仅接受 Active、CURRENT、READY、
  无歧义、有结构化差异且为当前 revision 的草稿。同一有效 revision 返回同一提案，同一
  Agent Run 最多一次草稿或提案写操作。
* Researcher 可以准备和取消自己的提案；Owner 可读取全部提案。只有实时 Owner、
  `project:write`、Web Session、CSRF 和近期认证全部满足时才能确认。
* 确认时重新验证 digest、TTL、正式 Context/Intent、草稿 revision/候选 hash 和待审批
  Plan/进行中 Submission 状态。版本或状态变化返回并持久化 `STALE/EXPIRED`，不发布。
* 抽取 `WebManagementService.publish_policy_in_session()`。设置页直接发布和提案确认共用
  唯一正式写入核心；新 Policy 版本、两个幂等记录、提案 `EXECUTED` 和审计原子提交。
* Agent 目录升级 `r15d-v1`，仅新增 `action_proposal_prepare_v1`；没有 confirm/execute
  工具。新增 `ACTION_PROPOSAL` evidence 和最终回答类型校验，旧 R15a/R15b/R15c 目录兼容。
* rolling summary 升级 schema v3，可保存有界 proposal ID、digest、来源草稿 revision、状态
  和有效期；摘要仍是非权威上下文，不能确认提案。
* Agent REST 新增提案列表、详情、确认和取消。Web Agent 工作台展示冻结差异、影响、完整
  结构化请求、有效期和摘要；Owner 必须勾选已复核，Researcher 只看到等待 Owner。
* 增加 24 个 R15d trajectory/security case，覆盖准备门槛、失效、权限、确认、幂等和
  prompt injection。

### 修复的问题

* 正式 Policy 发布不再存在“提案状态已执行但正式版本回滚”或相反的跨事务窗口。
* Agent 无法把用户消息中的“我确认”当成执行授权；确认 actor、角色和近期认证只取实际
  Web Session。
* 草稿准备后继续修改、正式版本变化或项目待办变化会使原提案失效，不会静默使用旧影响。
* 提案摘要绑定操作、项目、完整发布请求、来源 revision/候选 hash、基线 hash、待办 hash
  和过期时间，不能用同一确认请求替换发布内容。
* 新提案引用使用独立 evidence 类型，不能在最终回答中伪装成 `CONFIRMED_FACT`。

### 验证结果

* Ruff 全仓和 mypy 通过；Python 共收集 275 项，全量执行到 100%，结果为 266 passed、
  9 skipped。跳过项仍由真实 MinIO/S3/百炼/Cockroach 环境开关控制。
* 新集成测试覆盖 Owner 原子确认与幂等、同 revision 复用、草稿 revision 漂移、TTL 过期、
  Researcher 自有提案、Owner 代确认、Agent 提案 evidence，以及正式 Context 未提前变化。
* SQLite Alembic head 和历史降级回归通过。真实 CockroachDB 完成
  `20260724_17 -> 20260724_18 -> 20260724_17 -> 20260724_18`，确认 JSONB、固定短外键、
  唯一索引和降级建表行为。
* 完整 Cockroach 业务链验收因两个旧临时库中的历史 `ALTER TABLE ... provider` Schema Job
  处于 paused 而不退出，已手工终止。取消这两个明确属于临时库的 Job 后，全部 `eg_plan_it_*`
  和 `eg_r15d_*` 验收库均已清理；正式 `experiment_guardian` 数据未参与清理操作。
* Web ESLint、Vitest 6 项和 production build 通过；新增测试验证 Owner 复核勾选与
  Researcher 无确认按钮。
* 本地 Compose 已运行一次性 migration，现有 `experiment_guardian` 数据库从 head 17 升至
  head 18；随后重建 API/Web，API health 与 Web HTML 均可访问。Submission Worker 未重启，
  Agent 仍按用户的 `AGENT_ENABLED=false` 保持关闭，未调用外部模型。
* 使用真实 `local_owner` Session 完成只读冒烟：项目列表正常，新增
  `/agent/action-proposals` 返回空分页；临时 Session Cookie 随后已从 `/tmp` 删除。

### 已知遗留项

* 本轮只执行 `POLICY_PUBLISH`。Plan 批准/拒绝和 Submission 确认/拒绝留到 R15d-b。
* `FAILED` 状态为受控执行错误预留；Cockroach 序列化或连接失败会整体回滚并保留
  `PROPOSED` 供相同请求安全重试，不会为了记录失败破坏原子性。
* 提案过期或失效后不会自动 rebase。用户需要基于最新正式 Bundle 新建草稿和提案。
* 实际百炼模型是否稳定遵守新提案提示词仍由 `RUN_BAILIAN_AGENT_INTEGRATION=1` 验收；
  默认测试使用确定性 scripted/mock provider。
* 本轮不增加长期 Agent Memory、任意 SQL、自动训练、自动改代码或新模型 provider。

### 下一步

* R15d-b 仅评估 Plan Check 与 Submission 的白名单决策提案，必须继续复用现有状态机和风险
  权限：`BLOCKED/CRITICAL` 不可绕过、HIGH 仅 Owner、人类最终决定。
* 在新增操作前先解决真实 Cockroach 全链测试清理挂起，避免扩大测试不确定性。

## 2026-07-24 / R15d-b1：Plan Check 批准/拒绝提案

版本：working tree，基于 `cde6b75`。

### 更新内容

* revision `20260724_19` 扩展 `agent_action_proposals`：增加
  `PLAN_CHECK_DECISION`、目标 Plan、正式状态哈希和执行 ApprovalRecord；Policy 专属字段
  改为按 operation 约束，历史 Policy digest 算法保持不变。
* 新增 Plan 决策提案契约和 digest。批准、拒绝都必须提供非空理由，digest 绑定完整决定、
  Plan 状态哈希、Context/Intent 版本和 24 小时有效期。
* `ActionProposalService` 新增 Plan 准备、实时 confirmability、状态漂移检测和 operation
  分派；只接受 `NEEDS_APPROVAL/PENDING` 且没有 ApprovalRecord/Manifest 的 Plan。
* 抽取 `PlanApprovalService.decide_in_session()`。直接审批和 Proposal 确认复用相同权限、
  状态机、幂等和审计逻辑；Proposal、Plan、ApprovalRecord 与结果在同一事务提交。
* Agent 新增 `action_proposal_prepare_plan_decision_v1`，Prompt/目录升级为 `r15d-b1-v1`；
  普通建议请求只读，只有明确准备请求可写。没有 confirm、execute 或 Manifest 工具。
* rolling summary schema v4 支持 Policy Draft 引用与 Plan target/decision 引用；旧
  r15a/r15b/r15c/r15d Run 继续使用冻结 Prompt 和工具目录。
* Web Action Proposal Workspace 改为 operation 判别联合视图。Plan 决策显示正式版本、
  参数变化、风险、决定理由和不可逆影响；拒绝使用危险操作样式。
* CockroachDB 集成测试的 Alembic 子进程增加 180 秒上限，清理时只取消随机验收库自身的
  Schema Job，避免遗留 paused Job 阻塞后续验收。
* 新增 20 个 R15d-b1 trajectory/security case，并补充 Plan 提案准备、原子确认、幂等、
  直接审批抢先失效和 Agent 工具只准备不执行的集成测试。

### 修复的问题

* Agent 可以给出 Plan 审核建议，但无法把建议直接写成正式 ApprovalRecord。
* Plan 在提案准备后发生审批、内容或追溯变化时，旧提案不能继续执行。
* Plan Proposal 与正式审批不再存在跨事务部分成功窗口。
* 批准和拒绝理由、目标 Plan 和版本不能在 Owner 确认请求中被替换。
* Policy 和 Plan 提案在同一工作台展示时不会混用标题、按钮或执行回执。
* 提案幂等重放现在先验证 Agent Run/ToolCall 与当前身份的归属，不能用其他用户的 Run ID
  命中既有提案后绕过来源校验。

### 验证结果

* Python 收集 281 项，全量执行退出码 0：272 passed、9 skipped。
* Ruff 全仓和 mypy 76 个源文件通过。
* SQLite Alembic head、历史降级和再升级测试通过。
* 真实 CockroachDB 26.2 完成 revision 18→19→18→19；隔离数据库全链完成
  `head -> 05 -> head -> 03 -> head -> base -> head` 和完整 Plan/审批/Manifest/
  Submission 工作流回归。
* Web ESLint、Vitest 7 项和 production build 通过。
* 聚焦测试覆盖 Policy 历史提案兼容、Plan APPROVED/REJECTED、双幂等记录、直接审批抢先、
  状态未提前改变、Agent evidence 和 rolling summary v4。

### 已知遗留项

* R15d-b1 不包含 Submission 确认/拒绝提案；它单独留到 R15d-b2。
* 本机 Docker 数据盘执行全链时仅剩 4.7%，低于 CockroachDB bulk DDL 默认 5% 保护阈值；
  验收时临时降至 4%，完成后已恢复 5%。后续应清理 Docker 数据盘，不能把降低生产保护阈值
  当作长期方案。
* Cockroach 全链仍由 `RUN_COCKROACH_INTEGRATION=1` 显式触发，不进入默认测试。
* 真实百炼模型行为仍由 `RUN_BAILIAN_AGENT_INTEGRATION=1` 可选验收；默认测试不访问外网。
* 提案失效后不自动 rebase，用户需要重新读取正式 Plan 并创建新提案。

### 下一步

* R15d-b2 只实现 Submission 审核提案，必须复用现有 Review Eligibility 和正式 Experiment
  确认事务：LOW/MEDIUM 保持现有权限、HIGH 仅 Owner、CRITICAL/blocking 禁止批准。

## 2026-07-27 / R15d-b2：Submission 批准/拒绝提案

版本：working tree，基于 `6f48e66`。

### 更新内容

* revision `20260727_20` 扩展 `agent_action_proposals`：增加 `SUBMISSION_DECISION`、
  `target_submission_id`、`executed_experiment_id`、Submission 复合索引和按 operation 分离的数据库约束。
  历史 Policy/Plan 提案的 digest 算法不变；downgrade 只删除候选 Submission Proposal，不回滚
  已执行的 ApprovalRecord 或 Experiment。
* 从 `ExperimentReviewService` 抽取 `decide_in_session()` 和只读 `SubmissionReviewBasis`。
  直接审核与 Proposal 确认复用同一权限、状态机、幂等、审计和正式 Experiment 入库核心。
* Submission Proposal 冻结完整审核回执、风险详情、Artifact 大小/类型/SHA-256/固定
  VersionId、Manifest/Plan/Context/Intent 追溯、embedding provider/model/dimension/输入哈希、
  决定和必填理由。状态哈希和 proposal digest 同时绑定目标、版本、payload 和 TTL。
* 准备和确认均重算审核回执来源哈希、Review Eligibility、追溯、Artifact 和 embedding。
  风险、回执、材料、Submission 状态或正式记录变化后，旧提案不再执行。
* Researcher 可准备自己 HIGH 风险批准提案供 Owner 复核，只能确认自己 LOW/MEDIUM
  批准或拒绝提案；HIGH 批准仅 Owner，CRITICAL/blocking 批准提案在准备时就被拒绝。
  所有 Agent Proposal 确认统一要求近期认证，不改动现有直接审核 API。
* Agent 新增 `action_proposal_prepare_submission_decision_v1`；`submission_diagnose_v1` 增加
  动态 eligibility、回执来源一致性和批准材料完整性。Prompt/工具目录升级 `r15d-b2-v1`，
  旧 Run 仍使用冻结的 r15a-r15d-b1 目录。
* rolling summary schema v5 增加 Submission target、decision 和 review eligibility 的有界引用，
  不把 Proposal 升级为已执行事实。新增 21 个 Submission 工具选择、风险门禁、权限、过期和
  prompt injection trajectory/security case。
* Web Action Proposal Workspace 扩展为三操作判别联合视图。Submission 默认展示目标、
  决定、资格和人类可读回执，强制展开 HIGH/CRITICAL/blocking 风险，保留固定版本材料、
  完整追溯和原始 JSON 高级视图。

### 修复的问题

* Agent 对 Submission 的诊断和建议现在可以变成可审阅、可追溯的候选决定，但不会提前
  改变 Submission 或创建正式记录。
* Proposal 确认与正式 Experiment 创建不再存在跨事务部分成功窗口；任一步失败均回滚。
* 审核回执、风险、Artifact VersionId、embedding 或追溯变化后，旧提案不会基于过期依据执行。
* 提案确认的幂等重放现在返回首次保存的完整响应，不因 `updated_at` 或后续派生视图差异
  产生不等价结果。

### 验证结果

* Python 共收集 289 项，全量执行退出码 0：280 passed、9 skipped。Submission Proposal
  聚焦集成测试覆盖无正式写入准备、LOW/MEDIUM Researcher 确认、HIGH Owner 接管、
  CRITICAL 批准门禁与拒绝路径、近期认证、Artifact 漂移失效、幂等和注入失败整体回滚。
* Agent 工具测试验证只生成 `ACTION_PROPOSAL` evidence，不创建 ApprovalRecord/Experiment。
* Ruff 全仓通过；mypy 检查 76 个源文件通过。
* Web ESLint、Vitest 8 项和 production TypeScript/Vite build 通过。
* Compose 重新构建 API、Worker、Agent Worker 和 Web 镜像；一次性 migration 成功执行
  `20260724_19 -> 20260727_20`，local-init 幂等成功，API 与 Web 代理健康检查均返回 200，
  CockroachDB 中 `alembic_version=20260727_20`。
* 真实 CockroachDB 隔离测试完成 revision 20 升降级、
  `head -> 05 -> head -> 03 -> head -> base -> head` 全迁移链，以及完整
  Plan/审批/Manifest/Submission 业务回归。

### 已知遗留项

* 提案失效后不自动 rebase；用户需重新诊断当前 Submission 并创建新提案。
* 真实百炼 Agent 遵守 `r15d-b2-v1` 提示词的稳定性仍通过
  `RUN_BAILIAN_AGENT_INTEGRATION=1` 可选验收，默认测试不访问外网。
* R15e 尚未实现；长期 Agent Research Memory 不与正式 Experiment Memory 混用。
* rootless Docker 所在 `/dev/md0` 共享文件系统仍已使用约 97%；本地 CockroachDB 已通过
  独立 store 容量口径避免受共享盘总容量比例误判，但其他 Docker 工作负载仍需监控物理空间。

### 下一步

* 先详细规划 R15e 的显式实验集总结、引用绑定、结论状态、独立 Research Memory、
  结构化过滤先行的向量召回和 Bedrock provider parity，不在本轮提前落实。

## 2026-07-27 / 本地 CockroachDB store 容量修复

版本：working tree，与 R15d-b2 同批交付。

### 更新内容

* 审计确认 CockroachDB 数据卷实际仅使用约 2.5GB，但 rootless Docker 位于 11TB 的共享
  `/dev/md0`，该文件系统剩余约 387GB/3.6%。CockroachDB 按整个文件系统容量计算 5% DDL
  保护阈值，导致本项目 schema backfill 被错误阻止。
* `docker-compose.yml` 为本地单节点新增
  `--store=path=/cockroach/cockroach-data,size=${COCKROACH_STORE_SIZE:-200GiB}`；配置不预分配
  空间、不删除命名卷，也不降低 CockroachDB 的 5% 安全阈值。
* `.env.local.example` 和本地部署文档增加 `COCKROACH_STORE_SIZE=200GiB` 及调整边界说明。

### 验证结果

* 原命名卷重建容器后数据保留，节点健康；store 报告容量 200GiB、可用约 199.65GiB。
* API、Worker、Web 在数据库短暂重建后保持运行，CockroachDB revision 仍为 `20260727_20`。
* 先前因 3.6% 容量比例失败的真实 CockroachDB 全迁移链测试重新执行并通过。

### 已知遗留项

* 这是本地开发单节点的容量口径修复，不是 `/dev/md0` 的物理扩容。共享盘仍为 97% 使用率；
  若其真实剩余空间接近 200GiB，必须先清理或扩容底层存储，再提高或继续使用该配额。

## 2026-07-27 / R15e-a：显式实验集候选研究报告

版本：working tree。

### 更新内容

* 新增 `agent_research_reports` 和 Alembic revision `20260727_21`。报告不可变地绑定 Team、
  Project、创建人、来源 Thread/Run/ToolCall/最终 Message、显式 Experiment 集、确定性来源
  快照、报告正文、双 SHA-256 以及 provider/model/prompt/schema 元数据。
* 新增严格的报告领域契约。用户必须显式选择 2-8 个不重复正式 Experiment；默认只接受
  `COMPLETED/FAILED`，`DEPRECATED/SUPERSEDED` 必须显式允许，其他状态拒绝。
* 新增 `research_report_prepare_v1`。工具按 confirmed_at/ID 稳定排序，冻结指标、失败原因、
  正式摘要和 Submission/Manifest/Plan/Intent/Context 追溯，并复用既有两两可比性和整组重复
  统计；最多仅展示五个差异路径，不把完整配置值扩散到模型上下文。
* Prompt 和工具目录升级 `r15e-a-v1`，此前所有目录保持冻结。报告工具不能与 Policy Draft
  或 Action Proposal 工具在同一批调用中混用，也不允许单轮准备多个报告。
* 报告正文固定为阶段摘要、支持结论、冲突、开放问题、建议和限制。结论/冲突至少引用两个
  正式 Experiment 事实及一个确定性分析，且全部选择实验都必须被引用覆盖；source hash、
  实验集合或引用越界时只允许一次模型修复，失败后不写报告。
* 最终 Assistant Message、Citation、Report、Run 完成状态与两类 AuditLog 在同一事务中提交。
  rolling summary 升级 schema v6，只保存有界报告引用，不复制报告全文或把它变成正式事实。
* 增加项目成员共享的报告列表/详情只读 API 和 Agent list/get 工具。读取时复核正文哈希、来源
  ToolCall 输出哈希和引用契约；来源 Experiment 状态变化或缺失时显示警告，不追溯修改历史。
* Web 治理 Agent 页增加研究报告入口和消息级报告链接；新增桌面/移动自适应的共享报告工作台，
  默认显示人类可读结论、引用与限制，并保留来源快照、provider 元数据和原始 JSON 高级视图。
* 增加 24 个 R15e-a trajectory/security case、领域引用校验单测、真实业务对象工具集成测试、
  Agent 原子落库/项目共享权限测试和 Web 组件测试。
* README、迭代状态、内部 Agent 计划、本地部署说明和文本架构图均同步到 revision 21；下一轮
  唯一目标收敛为 R15e-b 独立 Research Memory，不与 provider parity 同轮开发。

### 修复的问题

* 阶段性总结不再只存在于一次对话文本中，而是有独立、不可变、可共享和可审计的候选报告。
* 模型不能自行扩大实验集合或引用未读取记录；报告遗漏所选实验、把单实验观察写成跨实验结论、
  或缺少确定性分析依据时会在持久化前被拒绝。
* 后续状态变化不再静默让旧报告看似仍基于当前状态；读取端明确展示来源变化警告。
* 长对话压缩不再丢失报告身份，但也不会把旧报告全文反复注入模型上下文。
* 报告读取会独立重算去除本次 evidence ID 后的规范化来源哈希，并核对标题、目标、实验集合、
  指标和历史开关与正文/来源一致；冗余元数据误写不会造成列表与详情表达分叉。

### 验证结果

* Python 全量收集 296 项：287 passed、9 skipped；外部服务测试仍按既有环境开关跳过。
* R15e-a 聚焦后端测试 21 项通过；Ruff 全仓通过；mypy 检查 78 个源文件通过。
* 真实 CockroachDB 隔离测试通过，显式验证 `21 -> 20 -> 21`，并继续跑通完整迁移、Plan、
  Approval、Manifest、Submission、数据库队列和向量查询回归。Inspector 对既有 `vector`
  类型有已知识别警告，但实际 1024 维向量读写和查询通过。
* Web ESLint、Vitest 9 项和 TypeScript/Vite production build 通过；Playwright 在桌面和移动
  视口各 1 项通过，覆盖治理 Agent 入口、研究报告空状态、弹窗开关和页面横向溢出检查。
* 本机 Compose 完成 migration/API/Worker/Web 重建；持久数据库
  `alembic_version=20260727_21` 且存在 `agent_research_reports`。API health、Web 根页面、
  local_owner Session/RBAC、项目列表和研究报告列表真实 HTTP 链路均返回成功。

### 已知遗留项

* R15e-a 报告没有 embedding，也不参与跨会话长期语义召回；独立候选 Research Memory 留到
  R15e-b，不能与正式 Experiment `Memory` 混用。
* 当前仅有百炼 `AgentChatModel`。Bedrock provider parity、同套真实模型评测及成本/延迟观测
  留到 R15e-c。
* 来源变化只显示警告，不自动重新生成、rebase 或废弃报告；用户需基于当前正式记录显式创建
  新报告。
* 本机 `.env.local` 的 `AGENT_ENABLED=false`，因此没有执行真实百炼生成验收，也未擅自启动
  Agent Worker。可通过 `RUN_BAILIAN_AGENT_INTEGRATION=1` 和现有 Agent profile 显式验收。

### 下一步

* R15e-b 只实现独立候选 Research Memory、来源/状态/过期规则、结构化过滤优先的向量召回和
  embedding 失败降级；不加入正式事实晋升、自动实验分组或 Bedrock provider parity。

## 2026-07-27 / R15e-b：独立候选 Research Memory 与结构化过滤召回

版本：working tree。

### 更新内容

* 新增 revision `20260727_22`、`agent_research_memories` 和
  `agent_research_memory_embeddings`。每个研究报告 finding 确定性物化为一条不可变候选记忆，
  正文、来源引用和内容哈希与可变的 provider/model/document version 索引任务分离。
* 新报告在原子事务中只做本地记忆物化，不调用模型；Agent Worker 幂等补建旧报告和当前模型
  任务，并复用 claim、`FOR UPDATE SKIP LOCKED`、lease、generation、退避、最大尝试和死信。
* 新增 Research Memory 检索服务。查询先按 team/project/CANDIDATE/type、协议、实验引用、
  来源状态、当前 provider/model/dimension/document version 和输入哈希过滤，再对最多 200 条
  候选执行 CockroachDB `<=>` 精确排序；空候选不会调用模型。
* 新增候选记忆搜索 API 和 Owner 索引重试 API。重试要求 Web Session、实时 Owner、CSRF 与
  `Idempotency-Key`；终态失败和人工重试均保留审计，历史模型版本不被覆盖。
* Agent 增加 `research_memories_search_v1`，Prompt/目录升级 `r15e-b-v1`。返回值固定标记
  `ANALYSIS/CANDIDATE_EVIDENCE`；rolling summary schema v7 只保留有界 memory/report/
  finding/content hash 引用，不复制正文。
* Web 研究报告工作台增加 finding 索引状态、错误、来源新鲜度、Owner 重试和候选语义检索；
  结果明确显示候选证据，原始向量不下发浏览器。
* 增加 20 个 R15e-b trajectory/security case、确定性文档单测、索引成功/死信/幂等重试、
  来源失效过滤和 Web 搜索/重试测试。旧 `r15e-a-v1` Prompt 与工具目录保持冻结。

### 修复的问题

* 阶段结论不再只能通过报告 ID 或对话上下文定位，可跨会话按语义召回并追溯到报告和实验。
* embedding 超时、畸形响应或 Worker 崩溃不会损坏报告，也不会留下永久 `RUNNING` 或伪造向量。
* 旧报告补建查询只选择尚未物化的报告，避免固定扫描最早十条造成后续报告饥饿。
* 检索前复核报告、记忆、embedding 输入三层哈希；来源状态变化默认排除，显式历史查询也会
  返回警告，不会把过期分析静默描述为当前事实。
* Agent Worker 改为使用完整依赖装配，避免后台进程缺失 R15c-R15e 工具服务。

### 验证结果

* Python 全量收集 301 项并通过：292 passed、9 skipped；外部服务测试继续受显式开关控制。
* Ruff 全仓通过；mypy 严格检查 80 个源文件通过。
* Web ESLint、Vitest 10 项和 TypeScript/Vite production build 通过。
* 真实 CockroachDB 隔离测试通过，覆盖 `21 -> 22 -> 21 -> 22`、完整迁移降级链、1024 维
  VECTOR 建表及原有 Plan/Submission/Outbox/正式实验向量查询。Inspector 仍有既有 vector
  类型识别警告，不影响实际读写和查询。
* 本机 Compose 已重建 migration/API/Worker/Web；持久数据库为
  `alembic_version=20260727_22`，两张 Research Memory 表存在。API health、Web 静态页和两个
  新路由的认证边界均通过真实 HTTP 验收，容器启动日志无应用异常。

### 已知遗留项

* 候选 Research Memory 只有 `CANDIDATE`，不支持确认、晋升或自动 superseded；这避免候选
  分析被误当作正式事实。
* 当前按结构过滤后的最近 200 条候选做精确排序，未启用 CockroachDB Distributed Vector
  Index；待单项目记忆规模或 p95 延迟形成真实瓶颈后再评估。
* `AgentChatModel` 仍只有百炼实现。Bedrock provider parity、统一成本/延迟/失败观测和同套
  评测对照留到 R15e-c。

### 下一步

* R15e-c 只实现 Bedrock `AgentChatModel` provider parity、统一模型调用观测和百炼/Bedrock
  对照验收；不增加候选记忆晋升、自动实验分组或治理规则旁路。

## 2026-07-27 / R15e-c：Agent provider parity 与模型运行观测

版本：working tree。

### 更新内容

* `AgentChatModel` 新增 provider 无关的 `AgentResponseFormat`。治理回答和 rolling summary 都传入
  Pydantic 导出的 JSON Schema；调用审计同时冻结 schema 名称与规范化 SHA-256。
* 新增 `BedrockAgentChatModel`，使用 ConverseStream 映射 system/user/assistant/tool、严格
  Tool Spec、碎片化 tool input、usage、finish reason 和 provider request ID。Bedrock 调用必须
  使用 Structured Outputs；缺少 Schema 会直接失败，不提供 prompt-only JSON fallback。
* Settings 增加 `AGENT_PROVIDER=bailian|bedrock`、`BEDROCK_AGENT_MODEL_ID` 和成对的输入/输出
  每百万 token 配置费率。组合根只实例化选中的 adapter；本地模式仍只允许百炼，Run 执行前
  核对持久化 provider/model，禁止重试时静默切换平台。
* revision `20260727_23` 为 `agent_model_calls` 增加 provider/model、延迟、币种、输入/输出冻结
  费率和估算费用，为 ModelCall 与项目 Run 观测增加索引及非负约束。历史 ModelCall 从所属 Run
  回填 provider/model；历史费用保持空值，不按当前费率追溯重算。
* 模型调用成功和失败均记录单调时钟延迟、已收到 usage 和费用估算；Run 详情返回最多 50 条
  去内容化调用元数据。新增 Owner-only 项目观测服务/API，支持 1-90 天及 provider/model 过滤，
  聚合调用、token、延迟、失败、放弃、重试、缺失 usage、未计价和分币种估算。
* Web Agent 页增加 Owner“模型观测”入口和消息级 Run 详情。项目面板提供 7/30/90 天窗口、
  provider/model/purpose 分组和失败分类；Run 面板显示调用状态与成本。两者不展示提示词、回答、
  工具输入或工具输出，费用明确标记为配置费率估算而非云平台账单。
* Terraform 支持云端百炼/Bedrock 条件装配、仅百炼路径注入 API Key，并验证 provider 必填项和
  成对费率。`.env.example`/`.env.local.example` 已同步；本地配置 Bedrock 会在启动时被拒绝。
* 新增 16 个百炼/Bedrock 共享 provider trajectory/security case、Bedrock 事件流合约测试、
  Web 观测组件测试和 `RUN_BEDROCK_AGENT_INTEGRATION=1` 可选真实验收。没有修改 Prompt 版本、
  工具目录、权限、治理状态机或正式确认事务。

### 修复的问题

* 云端治理 Agent 不再被百炼单一 provider 锁定，同时避免“一个 provider 失败后换模型继续写入”
  导致同一 Run 执行条件漂移。
* Bedrock 结构化回答不依赖提示词自觉输出 JSON；不支持 Structured Outputs 的模型会明确失败。
* 模型使用量、延迟、失败和重试不再只能从日志推断，Owner 可按项目查看有界结构化数据。
* 费用不再使用事后当前价格猜测；每次调用冻结配置费率和币种，未配置或缺失 usage 时明确标记
  未计价，并始终与真实云账单区分。
* Provider 观测不会把敏感请求/响应快照下发前端；MCP/OAuth 身份也不能调用 Owner Web 观测。
* revision 23 的项目观测索引已同时写入迁移和 ORM 元数据，避免后续 Alembic 误报 schema drift；
  回填 SQL 使用非关键字别名，CockroachDB 升降级已验证。

### 验证结果

* Python 默认全量收集 315 项并通过：305 passed、10 skipped；真实云、MinIO 和 Cockroach 集成
  仍由既有显式环境开关控制。Ruff 全仓通过，mypy 检查 81 个源文件通过。
* 聚焦 provider/config/Agent 集成共 68 项通过。真实 CockroachDB 隔离链通过，覆盖完整建库、
  `22 -> 23 -> 22 -> 23`、既有迁移降级链和 Plan/Submission/Outbox/向量查询回归；仅有 SQLAlchemy
  Inspector 对既有 `vector` 类型的已知警告。
* Web Vitest 12 项、ESLint、TypeScript/Vite production build 全部通过；Playwright 桌面/移动
  2 项通过并覆盖模型观测弹窗和横向溢出检查。Terraform 1.9.8 fmt 检查和 AWS/Random provider
  schema validate 通过。
* 本机 Compose 已重建 migration/API/Worker/Web；持久数据库为 `20260727_23` 且四个核心观测列
  存在。API health、Web 根页、local_owner 登录、项目列表和 Owner 观测 API 真实 HTTP 请求成功。

### 已知遗留项

* 本机 `.env.local` 保持 `AGENT_ENABLED=false`，没有调用真实百炼或 Bedrock。真实模型行为和费用
  只通过显式 `RUN_BAILIAN_AGENT_INTEGRATION=1` / `RUN_BEDROCK_AGENT_INTEGRATION=1` 验收。
* 百炼 OpenAI-compatible 接口当前不暴露原生 JSON Schema Structured Outputs，因此仍由服务端
  Pydantic 严格验证；Bedrock 则强制原生 Structured Outputs。两者失败都不会自动互相回退。
* 费用来自静态配置费率，不含缓存、批处理、区域折扣、免费额度或税费，不能用于财务对账。
* 观测聚合当前是请求时 SQL 聚合，适合 MVP 数据量；形成真实 p95 或数据规模瓶颈后再评估预聚合。
* R16 只做 release candidate hardening：真实 provider 对照、并发/恢复压测、告警阈值、部署
  Runbook 和安全回归，不增加 Agent 工具、候选事实晋升或自动审批。

## 2026-07-27 / R16-L：本地百炼 release candidate hardening

版本：`working tree`。

### 更新内容

* 新增 `scripts/verify_r16_local.py`：默认预检 local backend、Alembic head、MinIO、Web/API、
  local_owner、初始化项目和 Session 撤销；显式 `--live-bailian` 再创建只读 Agent Run，并输出
  不含提示词、回答或凭据的 JSON 验收报告。
* 新增真实 CockroachDB Agent 测试：双 Worker 轮询 10 个 Run，验证唯一 claim、lease 过期
  接管、generation fencing、最大重试、DEAD_LETTER 和实体不重复。
* 扩展真实百炼测试到摘要、1024 维归一化 embedding、Function Calling、严格 AgentAnswer、
  三类读取工具选择和三类越权拒绝；新增可选的真实 Compose Agent 全链路测试。
* MinIO 显式集成测试默认读取 Git 忽略的 `.env.local`，避免本机凭据与示例值漂移，同时保留
  `MINIO_TEST_*` 覆盖入口。
* `AgentChatModel` 增加 Provider 协议能力：百炼将 auto 工具选择与严格 JSON 最终回合分离，
  Bedrock 保持同回合原生 Structured Outputs。运行时不按 deployment mode 或厂商名分支。
* `httpx[socks]` 和锁文件补入 `socksio`，使显式 SOCKS 代理环境可以实际访问百炼。

### 修复的问题

* 修复百炼 `json_object + tool_choice=auto` 会把工具请求写入正文、导致工具无法执行的问题。
* 修复 Qwen 同时返回说明正文和原生 ToolCall 时整次调用被拒绝的问题；只要存在合法原生
  ToolCall，说明草稿不会进入工具事件或最终回答。
* 修复百炼在已发送终态 `finish_reason` 后干净关闭 SSE、但没有补 `[DONE]` 时被误判为截断；
  未看到两类终态信号的响应仍按可重试截断处理。
* 修复 auto 回合没有继续调用工具时直接被当作严格最终 JSON 的协议错配；百炼会另起被审计的
  `tool_choice=none` 最终回合，并明确约束 evidence_id 字符串引用。
* 修复本机 `.env.local` 的 Web/API/MinIO 外部端口与实际 Compose 映射不一致，RC 验收能够通过
  当前回环入口。该文件被 Git 忽略，真实 Key 未写入仓库。

### 验证结果

* 真实百炼适配器 10 项全部通过；模型为 `qwen3.7-plus`，覆盖摘要、embedding、Function
  Calling、严格 JSON、工具选择和越权拒绝。
* 真实 Compose 只读 Agent 闭环通过：revision `20260727_23`、MinIO、Web/API、local_owner、
  `project_status_get_v1`、1 条正式引用、3 次成功模型调用、观测聚合、正式状态不变及 Session
  撤销均为 PASS；Run 耗时 81.229 秒。
* 真实 CockroachDB 并发/恢复测试与真实 MinIO 固定版本测试通过。新增协议和独立最终回合的
  聚焦单元/集成测试 36 项通过。
* Python 默认全量收集 327 项并通过：308 passed、19 skipped；Ruff 检查本轮全部 Python 文件
  通过，mypy 检查 82 个源文件通过。
* Web Vitest 12 项、ESLint 和 TypeScript/Vite production build 通过；Playwright 桌面/移动
  2 项在本轮前端无变更的候选构建上通过。

### 数据库迁移

本轮没有 schema 变更或新迁移，Alembic head 保持 `20260727_23`。新增可靠性行为仅使用既有
Agent Run、ModelCall、ToolCall、Citation、Event、lease、generation 和审计记录。

### 已知遗留项

* 本轮按当前范围只验收本地百炼线路；没有声称真实 Bedrock、Cognito、SQS 或 AWS 部署已验收。
* 百炼不提供与 Bedrock 等价的原生完整 JSON Schema 保证，最终答案仍必须经过服务端 Pydantic
  和 evidence 校验；额外严格最终回合会增加一次模型调用、token、延迟与费用。
* 首次失败的真实 Agent Run 已按设计进入 DEAD_LETTER 并保留完整追加式审计；它不是正式事实，
  也没有改变 Context、Intent、Constraint、Plan、Submission 或 Experiment。
* 本机运行配置已恢复为本轮开始时的 `AGENT_ENABLED=false`；需要真实 Agent 时必须显式配置模型
  并启动 Compose `agent` profile。

## 2026-07-28 / R17a：外部 Coding Agent 协作入口与带引用问答

版本：`working tree`。

### 更新内容

* revision `20260728_24` 为 Agent Thread 增加来源、任务启动幂等键、正式策略快照和来源哈希；
  Agent Run 可分别关联 Web Session、MCP AccessToken 或 MCP OAuth Grant。
* 新增 `external_agent_task_start`、`external_agent_ask`、`external_agent_task_get`。首个工具在
  Agent 模型完成前即返回版本化 `ProjectContextBundle`，后两个工具支持追问和增量轮询。
* 新增 `r17a-external-v1` Prompt/工具目录，只开放项目、正式实验、确定性比较统计、候选研究
  报告和记忆读取；没有草稿、提案、审批或正式写工具。
* MCP Thread 继续复用现有消息、Run、Lease、generation、重试、死信、Citation、rolling
  summary 和观测链。Web 增加 MCP 来源、初始版本和 `CURRENT/STALE` 提示，并允许同一用户续聊。

### 修复与安全边界

* 原 Agent Run 只能把 `token_id` 当作 `web_sessions.id`。现改为显式凭据类型和外键，Worker
  每次尝试前重新检查撤销、过期、项目绑定、OAuth Client/Grant 和 Membership。
* Run 有效权限取创建时 scope 快照与当前有效权限交集；`experiment:query` 只映射为内部只读
  `experiment:read`，不会产生 Plan、Submission 或项目写权限。
* 初始上下文哈希排除派生的 `human_readable` 元数据，只覆盖正式结构化 Context、Intent 和
  Constraints；正式版本变化时显示过期，不覆盖历史快照。
* 同一用户和项目只允许一个活动外部 Run；所有写入口保留幂等冲突检测，审计不保存原始 Token。

### 验证结果

* 新增 MCP 工具身份边界、Token/OAuth Run 恢复、撤销失败、只读目录、幂等、并发限制、上下文
  过期和 Web 可见性测试；原治理 Agent 集成切片保持通过。
* 默认 Python 全量共收集 331 项：312 项通过、19 项按外部服务开关跳过；SQLite 全迁移升降级
  包含 revision 24 并通过。
* Ruff 全仓和 mypy 81 个源码文件通过；Web ESLint、Vitest 13 项和 production build 通过。
* 真实 CockroachDB Agent 队列测试通过；revision 24 的新增列、外键、约束 Schema Jobs 均成功。
  原全链路测试在多轮历史 migration 往返的后续 revision 21 降级阶段耗时异常，本轮中断并清理
  临时数据库，不把它记录为完整通过。
* 新增 `scripts/verify_r17a_external_agent.py` 作为真实 stdio MCP + 百炼验收入口；当前本机
  `AGENT_ENABLED=false`，因此本轮没有产生真实百炼调用费用。

### 已知遗留项

* 远程 OAuth 使用本地 Client/Grant/JWT 测试，不声称真实 Cognito 已重新部署验收；真实 stdio
  MCP + 百炼需在显式启用 Agent 后运行新验收脚本。
* 外部 Agent 只能获得治理信息和建议。系统不能阻止它绕过 MCP 在本机直接执行命令。
* 自然语言计划、两轮自动修订、用户计划审批和三阶段关键不变量核对尚未实现，依次留给
  R17b、R17c。

## 2026-07-28 / R17b：版本化自然语言实验计划、有限自动修订与人工决定

版本：`working tree`。

### 更新内容

* 新增 revision `20260728_25` 和四张追加式业务表：`experiment_plans`、
  `experiment_plan_revisions`、`experiment_plan_reviews`、`experiment_plan_decisions`。
  Agent Run 增加 `CONVERSATION/EXPERIMENT_PLAN_REVIEW` 类型和审核目标 revision。
* 新增计划领域契约和 `ExperimentPlanService`。每个 revision 保存完整正文、证据、Context/Intent
  版本、完整正式策略快照、policy/content/evidence hash；修订不会覆盖历史。
* 可选配置证据复用既有严格 YAML/JSON 解析、重复键拒绝、Core Schema、无碰撞路径和类型严格
  比较。正式 LOCKED 冲突产生确定性 `BLOCKED`，APPROVAL_REQUIRED 明确保留后续正式审批。
* 新增 `r17b-plan-review-v1` Prompt 和 Run 类型。内部 Agent 只使用 R17a 已冻结的只读工具目录，
  输出主线、重复、已知失败、公平性、风险、低成本验证、自由探索范围和候选关键不变量。
* 自动修订只有在所有问题均可自动修正且没有用户研究决定时触发，最多两轮，并且只替换计划
  正文；配置、命令、Git、哈希、baseline 和关联实验等证据原样继承。
* MCP 新增 `external_agent_plan_submit`、`external_agent_plan_revise`、
  `external_agent_plan_get`。身份来自 MCP Token/OAuth 上下文，写操作要求项目绑定和
  `project:read + experiment:query + experiment:check`，不接受 actor 参数。
* Web API 增加计划列表、详情、历史 revision、Web 修订、失败重试和人类决定。Web Agent 页增加
  实验计划工作区，默认展示审核、硬检查、高风险项、候选不变量和自由探索，保留正文、历史和
  原始 JSON 视图。
* 人类决定绑定精确 revision、review hash、approval digest 和全部候选不变量选择，并保存
  approved snapshot 和 decision hash。Owner 可处理项目全部计划，Researcher 只能处理自己
  创建的计划；决定继续要求 Web Session、CSRF、实时 RBAC、近期认证和幂等键。

### 修复的问题

* 正式策略在计划排队后发生变化时，Worker 现在会在任何模型调用前终止 Run、持久化 `STALE`
  和审计记录。重试或决定时发现漂移也在独立事务保存 `STALE`，不会因抛出冲突而回滚状态。
* Agent 不得降低服务端硬检查：确定性 `BLOCKED` 必须保持 `BLOCKED`；计划批准也不能替代正式
  Plan Check、绕过 LOCKED 或创建 Run Manifest。
* Agent `REVISE` 输出必须包含完整正文和至少一个明确可自动修复 finding，避免空 finding 触发
  Python `all([])` 的真值而进行无依据修订。
* Web 拒绝或要求修改时不再携带候选不变量决定；批准按钮在全部候选逐项处理前保持禁用。

### 数据库迁移

* 旧 Agent Run 以数据库默认值回填为 `CONVERSATION`，目标 plan revision 为空；新计划审核 Run
  必须同时具有 `EXPERIMENT_PLAN_REVIEW` 类型和目标 revision ID。
* revision 25 降级会先删除决定和审核，再移除 Run 新列，最后删除 revision 和 plan 表。计划表
  是本轮新增数据，部署产生正式计划后不应把降级当成无损回滚。
* 真实 CockroachDB 空库完成 `base -> 25`，随后完成 `25 -> 24 -> 25`；最终确认四张表、Run 两列
  和 Alembic head `20260728_25` 均存在。临时验收数据库已删除。

### 验证结果

* Python 默认全量 339 项完成到 100%，其中 19 项按真实云/MinIO/Cockroach 环境开关跳过；
  R17b 新增领域测试覆盖配置 hash、类型严格 LOCKED、YAML 隐式标量和自动修订契约。
* 持久化集成测试覆盖 MCP 外部任务、计划提交、一次自动修订、第二轮 READY 审核、候选不变量
  确认、recent-auth 人类批准、revision/review/decision 数量，以及策略漂移时零模型调用。
* MCP 注册和服务端身份测试覆盖全部 13 个工具及新计划 submit/revise/get 参数边界。
* Ruff 全仓通过，mypy 检查 83 个源码文件通过。
* Web ESLint、14 项 Vitest 和 production build 通过；新增交互测试验证候选不变量全部处理后才可
  批准，并核对发送的 revision、review hash 和 candidate IDs。
* Playwright 桌面/移动回归已尝试，但宿主机 Chromium 缺少 `libatk-1.0.so.0`；自动安装系统依赖
  需要当前环境未提供的 sudo 密码，因此本轮不能把浏览器 E2E 记录为通过。容器化 Web、API
  health 和静态入口已实际重建并验证。

### 已知遗留项

* 本轮默认测试使用 scripted provider，没有产生真实百炼费用；真实百炼对计划审核 Prompt 的
  质量、延迟和 token 成本应在显式 live gate 下另行验收。
* R17b 的计划批准是计划级人类授权，不是训练正确性保证。计划、正式 Plan Check/Manifest 和
  Submission 三阶段关键不变量核对、运行进度与产物回传留给 R17c。
* 本轮不增加自动训练、代码修改、任意 SQL、委托审批、候选不变量自动发布为正式 Constraint，
  也不改变现有 Plan Check、Manifest、Submission 或 Experiment 状态机。

## 新日志模板

```text
## YYYY-MM-DD / Rn：主题

版本：commit 或 working tree。

### 更新内容
### 修复的问题
### 验证结果
### 已知遗留项
```
