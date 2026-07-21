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
| 2026-07-21 | R7.1 | `working tree` | Plan Check 历史依据与事务稳定性加固 |
| 2026-07-21 | R8 | `working tree` | Owner 计划审批和不可变 Run Manifest |

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

版本：`working tree`，当前修正轮次。

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

版本：`working tree`，当前实现轮次。

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

## 新日志模板

```text
## YYYY-MM-DD / Rn：主题

版本：commit 或 working tree。

### 更新内容
### 修复的问题
### 验证结果
### 已知遗留项
```
