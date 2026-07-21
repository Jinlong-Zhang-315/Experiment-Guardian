# Experiment Guardian 迭代实现与计划

更新时间：2026-07-21  
当前完成轮次：R8
下一轮：R9 S3 草稿提交

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
| R9 | S3 草稿提交 | 下一轮 | 只接通 prepare 与上传槽位 |
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

## 下一轮 R9：S3 草稿提交

### 单一目标

只实现 `submission_prepare`：根据有效 Manifest 创建实验草稿和白名单文件上传槽位，
返回 S3 预签名 URL。下一轮不启动分析工作流，也不确认正式实验。

### 本轮包含

1. 为 `experiment_submissions` 和 `artifacts` 增加独立 migration。
2. 固定 CONFIG/RESULT/LOG/NOTE/MANIFEST 文件类型、大小和内容类型白名单。
3. 校验 Manifest 项目归属、调用者成员资格和 `submission:prepare` scope。
4. 原子创建 RECEIVED submission、artifact 声明、审计和幂等结果。
5. 使用 S3 适配器生成短期预签名 PUT URL；数据库只保存 object key 和声明哈希。
6. 增加 fake storage 测试与最小真实 S3 兼容性测试配置，但默认测试不访问云服务。

### 明确不包含

* `submission_finalize`、对象 SHA-256 二次验证和可恢复分析工作流。
* Bedrock 摘要、embedding、查重、风险分析和人工确认。
* Web 页面、自动训练、自动修改配置或代码。

### 验收条件

* 无效、跨项目或不存在的 Manifest 不能创建 submission。
* 同 key 同请求返回原 submission 和上传槽位；异体请求返回冲突。
* 文件类型、扩展名、大小、重复文件名或非法哈希在事务写入前被拒绝。
* 数据库失败不遗留半条 submission；预签名失败不把草稿误标为可上传。
* R8 的审批和 Manifest 链路保持全量回归通过。

## 后续队列

后续轮次只表示顺序，不在 R9 同时开发：

```text
R10 Submission finalize + S3 verification + recoverable analysis
R11 Review + transactional confirmation + structured experiment query
R12 Four Web pages + AWS deployment + final demonstration
```

## 每轮更新要求

完成一轮后必须同步更新：

1. 本文件：当前轮状态、实际交付、下一轮唯一目标。
2. `docs/DEVELOPMENT_LOG.md`：逐项更新、修复和验证结果。
3. `docs/ARCHITECTURE.md`：模块状态、数据表和调用链。
4. README：只保留面向使用者的当前能力和启动方法。
