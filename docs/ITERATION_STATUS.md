# Experiment Guardian 迭代实现与计划

更新时间：2026-07-21  
当前完成轮次：R6  
下一轮：R7 训练前检查持久化

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
| R7 | 训练前检查持久化 | 下一轮 | 尚未开始 |
| R8 | 计划审批和 Run Manifest | 排队 | 不属于下一轮 |
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

当前可演示的最短链路：

```text
CLI bootstrap Owner/API Token
-> Owner 调用项目初始化 API
-> CLI 签发项目绑定 MCP Token
-> 本地 Agent 调用 project_get_context
-> 返回 Context v1 + Intent v1 + confirmed constraints
```

## 下一轮 R7：训练前检查持久化

### 单一目标

将已经通过单元测试的纯规则引擎接入正式数据库上下文，使本地 Agent 能通过
`experiment_check_plan` 获得并重放一个可追溯、幂等的 Plan Check 结果。

### 本轮包含

1. 新增只包含 `plan_checks` 的 Alembic revision。
2. 新增 Plan Check repository，读取当前 Context、Active Intent 和确认/候选约束。
3. MCP 调用者必须来自项目绑定 Token，且 `project_id` 必须与 Token 一致。
4. 服务端重新解析 YAML/JSON、计算配置规范化哈希并调用确定性规则引擎。
5. 保存 Context/Intent 具体 ID 和版本、约束快照、配置快照、命令、Git commit、
   Local Attestation、风险和最终检查结果。
6. 对 `(requester_id, idempotency_key)` 执行幂等处理；相同 key 不同请求返回冲突。
7. 返回 `plan_check_id`、参数差异、风险、缺失信息、`check_result` 和 `approval_status`。
8. 保证 LLM 不参与确定性结论，不允许降低 `BLOCKED`。

### 明确不包含

* Owner 批准/拒绝页面和 `approval_records` 写入。
* `run_manifest_create`。
* S3、上传、Submission 和 LangGraph 执行。
* Bedrock、摘要、embedding 和向量查询。
* Web 管理页面。
* 自动修改配置、代码或启动训练。

### 实施顺序

```text
1. plan_checks migration + migration tests
2. repository + version/scope validation
3. application service + idempotency transaction
4. MCP experiment_check_plan wiring
5. PASS / NEEDS_APPROVAL / BLOCKED integration tests
6. CockroachDB migration and API/MCP smoke verification
```

### 验收条件

* 修改正式 `LOCKED` 参数稳定返回 `BLOCKED`。
* 只修改已确认 `APPROVAL_REQUIRED` 参数返回 `NEEDS_APPROVAL/PENDING`。
* 只修改允许实验变量且证据完整时返回 `PASS/NOT_REQUIRED`。
* baseline 与正式约束不一致时返回 `BLOCKED`，即使候选配置未继续修改。
* Pending 推断约束不能产生强制 `BLOCKED`。
* 重复键、路径冲突或非法 YAML/JSON 不写入成功的 Plan Check。
* 相同 Idempotency-Key 和相同请求返回同一 `plan_check_id`。
* 相同 Idempotency-Key 和不同请求返回 `409/CONFLICT` 语义。
* 服务重启后仍可从 CockroachDB 查询原检查结果。
* 现有 45 项测试继续通过，并新增该链路的集成测试。

### 完成定义

只有 migration、应用服务、MCP 接线、CockroachDB 验证和上述验收测试全部通过，R7 才能
标记完成。只增加 ORM 模型或接口占位不算完成。

## 后续队列

后续轮次只表示顺序，不在 R7 同时开发：

```text
R8  Plan approval + immutable Run Manifest
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
