# Experiment Guardian 迭代实现与计划

更新时间：2026-07-21  
当前完成轮次：R7
下一轮：R8 计划审批和不可变 Run Manifest

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
| R7 | 训练前检查持久化 | 完成 | 已认证、幂等、版本化 Plan Check |
| R8 | 计划审批和 Run Manifest | 下一轮 | 尚未开始 |
| R9 | S3 草稿提交 | 排队 | 不属于下一轮 |
| R10 | 提交分析和人工确认 | 排队 | 不属于下一轮 |
| R11 | 正式实验查询和向量候选 | 排队 | 不属于下一轮 |
| R12 | Web 页面与 AWS 演示部署 | 排队 | 不属于下一轮 |

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
* 53 项自动化测试和 CockroachDB revision `20260721_02` 实库升级。

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

## 下一轮 R8：计划审批和不可变 Run Manifest

### 单一目标

打通 `NEEDS_APPROVAL` Plan Check 的 Owner 决策与 `run_manifest_create`，但不开始
Submission 或 S3，使“检查 -> 审批 -> Manifest”成为可独立验收的下一个窄切片。

### 本轮包含

1. 新增只包含 `approval_records` 和 `run_manifests` 的 Alembic revision。
2. 增加 Owner 计划审批应用用例和最小管理 API，支持批准或拒绝 Pending Plan Check。
3. 审批必须校验 Owner 角色、项目归属、当前状态和 Idempotency-Key。
4. 每次 Owner 决策创建不可修改的 `approval_records`，保存请求人、决策人、原因和时间。
5. 实现 MCP `run_manifest_create`，身份仍只来自服务端 Token。
6. Manifest 只能来自 `PASS/NOT_REQUIRED` 或 `NEEDS_APPROVAL/APPROVED` 的 Plan Check。
7. Manifest 保存原 Plan Check、Context/Intent 版本、配置快照/哈希、Git、命令、
   checkpoint 和环境声明，并生成可重复计算的 `manifest_hash`。
8. 审批与 Manifest 创建分别幂等，重试不能产生重复决策或 Manifest。

### 明确不包含

* 计划审批 Web 页面；本轮只提供最小管理 API。
* S3、文件上传、Submission 和 LangGraph 执行。
* Bedrock、摘要、embedding 和向量查询。
* 实验草稿审核、正式实验确认和所有其他 Web 页面。
* 自动修改配置、代码或启动训练。

### 实施顺序

```text
1. approval_records/run_manifests migration + migration tests
2. approval state transition service + immutable audit records
3. minimal Owner approval API + auth/idempotency tests
4. manifest canonical payload/hash + repository transaction
5. MCP run_manifest_create wiring + server identity tests
6. CockroachDB migration and end-to-end slice verification
```

### 验收条件

* Researcher 不能决策 Plan Check，Owner 只能决策本团队本项目的 Pending 记录。
* `BLOCKED` 不能进入审批；已决策记录不能被二次改写。
* 批准和拒绝均保存完整、不可修改的审计记录。
* `PASS/NOT_REQUIRED` 可直接创建 Manifest，`NEEDS_APPROVAL/PENDING` 不可创建。
* `NEEDS_APPROVAL/APPROVED` 可创建 Manifest，`REJECTED` 和 `BLOCKED` 永远不可创建。
* 历史 Plan Check 在当前 Context 变更后仍使用原始快照创建 Manifest，不静默切换版本。
* 相同 Idempotency-Key 和相同请求返回同一审批结果或 `manifest_id`；异体请求返回冲突。
* Manifest 哈希在服务重启后保持稳定，且所有现有 53 项测试继续通过。

### 完成定义

只有 migration、审批用例、最小管理 API、Manifest 用例、MCP 接线、CockroachDB
验证和上述测试全部通过，R8 才能标记完成。只迁移表或返回占位 Manifest 不算完成。

## 后续队列

后续轮次只表示顺序，不在 R8 同时开发：

```text
R9  Submission prepare/finalize + S3 artifact verification
R10 Recoverable analysis + review + transactional confirmation
R11 Structured experiment query + filtered vector candidates
R12 Four Web pages + AWS deployment + final demonstration
```

## 每轮更新要求

完成一轮后必须同步更新：

1. 本文件：当前轮状态、实际交付、下一轮唯一目标。
2. `docs/DEVELOPMENT_LOG.md`：逐项更新、修复和验证结果。
3. `docs/ARCHITECTURE.md`：模块状态、数据表和调用链。
4. README：只保留面向使用者的当前能力和启动方法。
