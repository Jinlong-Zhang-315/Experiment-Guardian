# Experiment Guardian

## 深度学习实验记忆与意图防护 Agent——MVP 产品需求文档

**文档版本：** v1.0
**产品阶段：** 黑客松 MVP
**目标用户：** 深度学习实验室团队
**云端部署：** AWS
**核心数据库：** CockroachDB
**Agent 编排：** LangGraph
**本地接入方式：** MCP
**文档状态：** 可进入架构设计与开发拆分

---

# 1. 产品概述

## 1.1 产品定义

Experiment Guardian 是一个提高实验一致性、可追溯性和风险可见性的治理系统。

系统不保证实验一定正确，也不声称能够完整验证真实训练行为。配置检查、风险分析和
向量检索均为治理证据，不能替代用户确认或确定性实验复现。

系统通过 MCP 与用户本地的 Coding Agent、实验 Agent 或命令行工具连接，使本地 Agent 能够：

1. 查询团队当前确认的项目上下文；
2. 查询当前实验意图和约束；
3. 在训练前检查配置是否偏离实验目标；
4. 在训练后自动收集并提交实验记录草稿；
5. 查询历史实验、结果和失败经验。

云端系统负责：

1. 对本地 Agent 提交的信息进行确定性校验；
2. 检测项目上下文、实验意图和配置之间的冲突；
3. 识别重复实验和异常结果；
4. 生成风险报告和回执摘要；
5. 等待用户确认；
6. 将确认后的内容写入正式实验库和长期记忆。

## 1.2 核心价值

本产品解决两个主要问题：

### 问题一：实验记录分散

实验配置、日志、结果、代码版本和结论散落在不同服务器、目录、文件和聊天中，团队难以确认：

* 做过哪些实验；
* 使用了什么配置；
* 哪个结果是正式结果；
* 某个结果对应哪一版代码；
* 某个失败是否已经出现过。

### 问题二：大模型上下文漂移

本地大模型或 Coding Agent 依赖当前会话上下文。随着对话变长或参与成员增多，容易出现：

* 忘记早期约束；
* 误解实验目标；
* 错误修改配置；
* 修改不应修改的参数；
* 使用过时的项目决策；
* 不同成员的 Agent 对同一项目理解不一致；
* 训练出错误模型后才发现问题。

产品需要建立一份团队共享、版本化、可追溯的实验事实和研究意图来源。

---

# 2. 产品目标

## 2.1 MVP 必须实现的目标

MVP 必须完成以下闭环：

```text
维护项目标准上下文
→ 创建实验意图
→ 本地 Agent 查询上下文与约束
→ 本地 Agent 提交实验计划或配置
→ 云端执行训练前检查
→ 检查通过后生成 Run Manifest
→ 用户在本地自行运行实验
→ 本地 Agent 自动提交实验草稿
→ 云端纠错、查重和风险分析
→ 生成回执摘要
→ 用户确认
→ 正式实验记录入库
→ 后续 Agent 可查询和使用该记忆
```

## 2.2 产品成功条件

MVP 成功必须满足：

1. 本地 Agent 不需要用户手工重复填写实验记录；
2. 不同团队成员可以读取相同的项目正式上下文；
3. 系统能够阻止明显违反项目约束的配置；
4. 本地 Agent 上传的数据不能直接污染正式实验库；
5. 用户能够快速确认或修正实验草稿；
6. 所有正式结果能够追溯到配置、代码版本和原始文件；
7. AWS 服务重启后，任务和数据能够恢复；
8. CockroachDB 实际承担项目记忆、实验状态、向量检索和审计数据。

---

# 3. MVP 范围边界

## 3.1 MVP 包含

### P0：必须完成

* 团队与项目管理；
* 项目标准上下文管理；
* 实验意图管理；
* 参数约束管理；
* 面向本地 Agent 的 MCP 服务；
* 训练前配置检查；
* Run Manifest 生成；
* 实验草稿自动提交；
* 云端信息校验；
* 风险报告；
* 回执摘要；
* 用户确认；
* 正式实验记录；
* 历史实验查询；
* CockroachDB 向量检索；
* Agent 工作流持久化；
* AWS 部署；
* 审计记录。

### P1：时间允许时完成

* 相似失败实验检索；
* 多 seed 自动聚合；
* 项目周报；
* Git diff 摘要检查；
* 简单的团队活动面板；
* 对实验结果异常波动进行提示。

## 3.2 MVP 不包含

第一版明确不实现：

* 自动修改用户代码；
* 自动生成完整代码补丁；
* 自动提交 Git commit；
* 自动创建 Pull Request；
* 自动启动 GPU 训练；
* 自动停止训练；
* 自动调度 GPU；
* 自动连接任意实验服务器；
* 自动执行任意 shell 命令；
* 完整代码 AST 语义审查；
* 自动决定研究方向；
* 自动设计新模型；
* 自动生成完整论文；
* 复杂多级审批流程；
* 跨实验室数据共享；
* 替代 MLflow 或 Weights & Biases；
* 上传模型权重或完整数据集；
* 将本地 Agent 推断直接写入正式实验库。

---

# 4. 用户角色与权限

## 4.1 Owner

Owner 可以：

* 创建和删除项目；
* 邀请团队成员；
* 修改项目标准上下文；
* 创建和修改受保护参数；
* 激活或关闭实验意图；
* 审批高风险变更；
* 确认正式实验记录；
* 标记实验为 Deprecated 或 Superseded；
* 查看全部审计记录。

## 4.2 Researcher

Researcher 可以：

* 查询项目上下文；
* 创建实验意图草稿；
* 提交训练前检查；
* 提交实验草稿；
* 修改本人提交的草稿；
* 查询实验记录；
* 确认普通实验草稿；
* 查看风险报告。

Researcher 不能：

* 修改 Locked 参数；
* 覆盖项目正式上下文；
* 删除正式实验；
* 修改其他团队成员权限。

## 4.3 Viewer

Viewer 可以：

* 查看项目；
* 查看正式实验；
* 查询实验历史；
* 查看实验摘要和来源。

Viewer 不能提交、确认或修改数据。

## 4.4 本地 Agent

本地 Agent 使用绑定用户和项目的 MCP Token。

默认允许：

* 读取项目上下文；
* 读取实验意图；
* 读取约束；
* 提交检查请求；
* 创建实验草稿；
* 查询实验；
* 获取提交回执。

默认禁止：

* 修改项目上下文；
* 修改约束；
* 确认正式实验；
* 删除实验；
* 管理成员；
* 直接写正式实验表。

---

# 5. 核心数据定义

## 5.1 项目标准上下文

项目标准上下文是项目当前有效的正式事实来源。

必须包含：

* 项目名称；
* 研究目标；
* 当前主线模型；
* 当前非目标；
* 数据集；
* 数据划分；
* 主要评价指标；
* 当前 baseline；
* 默认随机种子；
* 当前有效 Git branch；
* 当前有效配置；
* 已废弃方案；
* 关键研究决策。

项目标准上下文必须：

* 有版本号；
* 有创建人；
* 有确认人；
* 有确认时间；
* 有生效时间；
* 有修改原因；
* 保留历史版本；
* 不允许云端或本地 Agent 自动覆盖。

## 5.2 实验意图

实验意图描述一次计划实验准备验证什么。

云端 Agent 对自然语言的解释首先生成候选结果，必须分为：

* 用户明确表达的约束（`EXPLICIT`）；
* 模型推断的约束（`INFERRED`）；
* 尚未解决的歧义；
* 面向用户的简短意图回执。

候选结果在用户确认前不能成为 Active Experiment Intent 或正式参数约束。

必须包含：

* 实验目标；
* 实验假设；
* 允许修改的参数；
* 必须保持不变的参数；
* 预期输出；
* 验收条件；
* 关联项目上下文版本。

实验意图状态：

* `DRAFT`
* `ACTIVE`
* `CLOSED`
* `CANCELLED`

只有 `ACTIVE` 状态的实验意图可以生成正式 Run Manifest。

实验意图必须标记为 `FORMAL` 或 `EXPLORATORY`。探索意图不能静默修改正式上下文，
探索结果不能自动替代当前正式 baseline 或主线。

## 5.3 参数约束

参数约束分为三类。

每条约束还必须保存：

* 来源类型：`EXPLICIT` 或 `INFERRED`；
* 确认状态：`PENDING`、`CONFIRMED`、`REJECTED` 或 `SUPERSEDED`；
* 原始用户消息；
* 可选推断依据和置信度；
* 确认人和确认时间。

只有 `CONFIRMED` 约束可以产生强制 `BLOCKED`。待确认的明确约束或推断约束只能产生
提醒或 `NEEDS_APPROVAL`；`REJECTED` 和 `SUPERSEDED` 约束不得影响新的检查。

### LOCKED

未经 Owner 明确修改，任何实验都不能改变。

典型参数：

* 数据集划分；
* unseen class 列表；
* 正式评价协议；
* baseline checkpoint；
* 主评价指标。

### APPROVAL_REQUIRED

允许修改，但生成 Run Manifest 前必须由 Owner 批准。

典型参数：

* backbone；
* loss function；
* optimizer；
* 融合公式；
* 测试逻辑。

### EXPERIMENT_VARIABLE

当前实验意图允许修改。

典型参数：

* learning rate；
* fusion coefficient；
* loss weight；
* batch size；
* epoch。

## 5.4 Run Manifest

Run Manifest 是一次实验运行前生成的不可变清单。

必须包含：

* `run_manifest_id`
* `project_id`
* `experiment_intent_id`
* 项目上下文版本；
* 实验意图版本；
* 配置文件；
* 配置哈希；
* Git branch；
* Git commit；
* Git diff 哈希；
* 数据集；
* 数据协议；
* seed；
* checkpoint；
* 运行命令；
* 环境摘要；
* 创建者；
* 创建时间；
* 检查结果。

Run Manifest 生成后不得静默修改。

配置变化后必须重新生成新的 Run Manifest。

## 5.5 实验草稿

本地 Agent 提交的数据首先进入实验草稿区。

实验草稿可能包含：

* Run Manifest；
* 配置文件；
* Git 信息；
* 运行命令；
* 日志；
* 指标；
* checkpoint 路径；
* 运行环境；
* 实验说明；
* 开始和结束时间；
* 失败原因。

实验草稿不能直接成为正式实验。

## 5.6 正式实验记录

正式实验记录必须经过用户确认。

必须包含：

* 实验名称；
* 项目；
* 实验意图；
* Run Manifest；
* 模型；
* 数据集；
* 协议；
* seed；
* 主指标；
* 结果；
* 配置；
* Git commit；
* 原始文件；
* 运行状态；
* 确认者；
* 确认时间。

---

# 6. 核心状态机

## 6.1 实验提交状态

```text
RECEIVED
→ PROCESSING
→ NEEDS_REVIEW
→ APPROVED
```

异常分支：

```text
PROCESSING → FAILED
NEEDS_REVIEW → REJECTED
```

状态定义：

### RECEIVED

服务器已接收提交元数据，但文件可能尚未全部上传。

### PROCESSING

系统正在执行解析、校验、查重和风险分析。

### NEEDS_REVIEW

分析完成，等待用户确认或修改。

### APPROVED

用户已确认，内容已写入正式实验库。

### REJECTED

用户拒绝该提交。

### FAILED

系统无法完成处理。

## 6.2 实验状态

正式实验记录使用：

* `COMPLETED`
* `FAILED`
* `DEPRECATED`
* `SUPERSEDED`

不在正式实验表中保留 `DRAFT` 状态，草稿由 submission 表管理。

## 6.3 检查结果

训练前检查返回：

* `PASS`
* `NEEDS_APPROVAL`
* `BLOCKED`

### PASS

所有变化符合当前实验意图和项目约束。

### NEEDS_APPROVAL

修改了已确认的 `APPROVAL_REQUIRED` 参数，命中尚未确认的候选约束，或存在需要用户
决策的本地声明风险。Owner 批准前不能生成 Run Manifest。

### BLOCKED

至少有一项变化违反 Locked 约束，禁止生成正式 Run Manifest。

## 6.4 风险等级

风险报告使用：

* `LOW`
* `MEDIUM`
* `HIGH`
* `CRITICAL`

---

# 7. 本地 Agent MCP 接口

本产品需要向本地 Agent 提供独立 MCP Server。

注意：

> 产品自己的 MCP Server 与 CockroachDB Cloud Managed MCP Server 是两个不同组件。

产品 MCP Server 面向用户本地 Agent。

CockroachDB Cloud Managed MCP Server 用于满足比赛工具要求，并支持云端 Agent 受控访问 CockroachDB。

MCP 工具参数不得接受 `actor_id`、`requester_id` 等调用者身份字段。用户身份必须由
服务端从已经校验的 MCP Token 或 Session 中读取，再传入应用层执行权限检查和审计。

---

## 7.1 `project_get_context`

### 作用

读取项目当前正式上下文。

### 输入

* `project_id`
* 可选 `version`

### 输出

* 项目目标；
* 当前主线；
* 非目标；
* baseline；
* 数据集；
* 数据协议；
  -主指标；
* 默认 seed；
* 当前有效配置；
* 已废弃方案；
* 上下文版本；
* 更新时间。

### 权限

`project:read`

### 失败条件

* 项目不存在；
* 用户无权限；
* 指定版本不存在。

---

## 7.2 `experiment_get_intent`

### 作用

读取当前实验意图。

### 输入

* `project_id`
* `experiment_intent_id`

### 输出

* 实验目标；
* 实验假设；
* 允许修改参数；
* 控制变量；
* 验收条件；
* 意图状态；
* 意图版本。

### 权限

`intent:read`

---

## 7.3 `constraints_get`

### 作用

读取项目当前参数约束。

### 输入

* `project_id`
* 可选 `experiment_intent_id`

### 输出

每个参数返回：

* 参数路径；
* 参数名称；
* 保护等级；
* 期望值；
* 允许范围；
* 约束原因；
* 来源版本。

### 权限

`constraints:read`

---

## 7.4 `experiment_check_plan`

### 作用

在修改配置或运行实验前检查计划。

### 输入

* `project_id`
* `experiment_intent_id`
* 当前 Git commit；
* 计划修改文件；
* 计划参数变化；
* 配置内容或配置哈希；
* 运行命令；
* 可选说明。

### 输出

* `PASS`、`NEEDS_APPROVAL` 或 `BLOCKED`
* 参数级差异；
* 违反的约束；
* 缺失字段；
* 风险等级；
* 建议修正；
* 是否允许生成 Run Manifest。

### 权限

`experiment:check`

### 强制规则

只要任意已确认的 `LOCKED` 参数发生变化，结果必须为 `BLOCKED`。未确认的推断规则
不得产生强制阻断。

---

## 7.5 `run_manifest_create`

### 作用

为检查通过的实验创建 Run Manifest。

### 输入

* 检查请求 ID；
* 项目 ID；
* 实验意图 ID；
* 配置文件；
* Git commit；
* Git diff 哈希；
* 运行命令；
* dataset；
* protocol；
* seed；
* checkpoint；
* 环境摘要。

### 输出

* `run_manifest_id`
* Manifest 版本；
* Manifest 哈希；
* 创建时间；
* 最终检查结果。

### 权限

`manifest:create`

### 创建条件

* 检查结果为 `PASS` 且无需审批，或者 `NEEDS_APPROVAL` 已由 Owner 批准；
* 实验意图必须为 `ACTIVE`；
* 必填字段必须完整；
* 配置哈希必须与检查时一致。

---

## 7.6 `submission_prepare`

### 作用

创建实验草稿上传任务。

### 输入

* `project_id`
* `run_manifest_id`
* 文件清单；
* 文件名；
* 文件类型；
* 文件大小；
* 文件哈希；
* 实验状态；
* 指标摘要。

### 输出

* `submission_id`
* 每个文件的 S3 预签名上传地址；
* 上传过期时间；
* 必填文件检查结果。

### 权限

`submission:create`

---

## 7.7 `submission_finalize`

### 作用

在文件上传完成后启动云端分析。

### 输入

* `submission_id`
* 已上传文件哈希；
* 本地 Agent 摘要；
* 指标；
* 开始时间；
* 结束时间；
* 可选失败原因。

### 输出

* 当前状态；
* 预计处理阶段；
* 回执查询标识。

### 权限

`submission:create`

### 幂等要求

同一个 `submission_id` 重复调用不得创建重复记录。

---

## 7.8 `submission_get_receipt`

### 作用

获取实验提交的分析结果和用户回执。

### 输入

* `submission_id`

### 输出

* 实验摘要；
* 风险等级；
* 风险列表；
* 缺失字段；
* 重复实验候选；
* 与实验意图的偏差；
* 与历史实验的冲突；
* 建议操作；
* 确认页面地址；
* 当前状态。

### 权限

`submission:read`

---

## 7.9 `experiments_query`

### 作用

查询正式实验和已确认项目记忆。

### 输入

至少支持：

* 自然语言查询；
* project_id；
* model；
* dataset；
* protocol；
* seed；
* metric；
* status；
* 时间范围；
* top_k。

### 输出

每个结果必须包含：

* experiment_id；
* 实验名称；
* 状态；
* 核心配置；
* 主指标；
* 来源；
* 确认状态；
* 相关度；
* 是否为当前有效结果。

### 权限

`experiment:query`

### 约束

不能返回其他团队的数据。

---

# 8. 训练前检查规则

训练前检查分为两部分。

## 8.1 确定性规则

必须使用程序规则完成，不依赖大模型。

必须检查：

1. 配置文件是否可解析；
2. 参数类型是否正确；
3. 必填字段是否存在；
4. dataset 是否匹配；
5. protocol 是否匹配；
6. seed 是否符合实验意图；
7. checkpoint 是否正确；
8. Locked 参数是否变化；
9. 配置哈希是否一致；
10. 是否可能覆盖已有输出目录；
11. Git commit 格式是否有效；
12. 是否存在未提交代码变化；
13. 同一 Manifest 是否绑定不同配置；
14. 运行命令是否包含必要配置；
15. 当前实验意图是否为 ACTIVE。

## 8.2 语义规则

使用大模型辅助判断：

* 计划描述是否与实验目标一致；
* 修改文件是否超出实验范围；
* 配置变化是否可能改变实验语义；
* 用户说明是否存在明显歧义；
* 是否使用了已废弃方案。

语义规则只能生成候选意图、解释风险或提高风险等级。

语义规则不能降低确定性规则产生的 Blocked 结果。

无法可靠映射为配置路径的自然语言不得直接生成 Locked 规则，必须保留为未解决歧义并
等待用户确认。

---

# 9. 实验草稿校验与纠错

## 9.1 第一层：文件和字段校验

检查：

* 文件完整性；
* 文件哈希；
* 文件类型；
* 指标数据类型；
* 指标范围；
* seed；
* Git commit；
* 配置哈希；
* Run Manifest 关联；
* 时间范围；
* 必填字段。

## 9.2 第二层：项目上下文校验

检查：

* 是否属于正确项目；
* 是否使用正确实验意图；
* 是否符合项目当前主线；
* 是否使用已废弃配置；
* 是否违反受保护参数；
* baseline 是否一致；
* 数据协议是否一致。

## 9.3 第三层：实验记录校验

检查：

* 是否重复提交；
* 是否为同一配置的不同 seed；
* 是否为旧实验的新版本；
* 是否将 best epoch 当成 final result；
* 是否将单 seed 当成多 seed 均值；
* 是否将验证集结果当成测试结果；
* 是否与历史结果明显冲突；
* 是否缺少必要的可复现信息。

## 9.4 第四层：语义风险分析

大模型用于：

* 生成实验摘要；
* 识别描述和配置是否矛盾；
* 判断失败原因是否合理；
* 发现结论是否超出结果支持；
* 生成面向用户的风险说明。

---

# 10. 风险报告要求

每条风险必须包含：

* `risk_id`
* 风险等级；
* 风险类型；
* 具体字段或文件；
* 当前值；
* 预期值；
* 触发规则；
* 影响说明；
* 建议操作；
* 是否阻止确认。

风险类型至少包括：

* `CONSTRAINT_VIOLATION`
* `MISSING_FIELD`
* `DUPLICATE_SUBMISSION`
* `CONFIG_MISMATCH`
* `MANIFEST_MISMATCH`
* `METRIC_ANOMALY`
* `RESULT_CONFLICT`
* `DEPRECATED_CONTEXT`
* `UNTRACEABLE_RESULT`
* `POSSIBLE_DATA_LEAKAGE`
* `INSUFFICIENT_SEEDS`

## 10.1 阻止规则

以下风险必须阻止直接确认：

* 修改 Locked 参数；
* 配置与 Run Manifest 不一致；
* 无法确定结果所属项目；
* 结果与配置无法关联；
* 使用错误数据协议；
* 指标明显非法；
* 文件哈希不一致。

---

# 11. 用户回执摘要

实验草稿分析完成后，系统必须生成简短回执。

回执至少包含：

* 提交编号；
* 项目；
* 实验目标；
* 配置；
* Git commit；
* seed；
* 主指标；
* 实验状态；
* 风险等级；
* 关键检查结果；
* 缺失字段；
* 是否可能重复；
* 用户可执行操作。

用户操作只保留：

* 确认并入库；
* 修改信息；
* 拒绝提交；
* 请求 Owner 审批。

用户不应被要求重新填写本地 Agent 已经可靠采集的信息。

回执默认只突出实验目标、允许变化、关键结果和最高风险。LOW/MEDIUM 详情可以折叠，
HIGH/CRITICAL 风险必须展开并显示原值、新值、来源和影响。CRITICAL 风险不能通过普通
确认绕过。

---

# 12. 正式实验确认规则

实验草稿满足以下条件后可以写入正式实验库：

1. 用户具有确认权限；
2. 不存在未解决的 Critical 风险；
3. 不存在 Locked 约束冲突；
4. Run Manifest 有效；
5. 配置哈希一致；
6. 关键来源文件存在；
7. 必填实验字段完整；
8. 用户完成最终确认。

确认操作必须在一个数据库事务中完成：

1. 创建正式实验记录；
2. 创建实验指标；
3. 关联 artifacts；
4. 保存实验摘要；
5. 创建向量记忆；
6. 更新 submission 状态为 APPROVED；
7. 写入审计记录。

任意一步失败时，整个事务必须回滚。

---

# 13. 自然语言查询要求

系统必须支持以下查询类型。

## 13.1 精确查询

* 某个 seed 的结果；
* 某个配置的结果；
* 某个协议的实验；
* 当前 baseline；
* 当前正式主线；
* 某个结果的来源。

## 13.2 比较查询

* 两个配置的结果对比；
* 哪个参数最好；
* 哪个模型最稳定；
* 哪些实验高于 baseline；
* 哪些实验失败。

## 13.3 历史查询

* 最近完成的实验；
* 某个方案为什么被废弃；
* 是否做过类似配置；
* 某个错误是否出现过。

## 13.4 查询回答约束

回答必须：

* 优先使用正式实验；
* 标明 Draft 与正式记录的区别；
* 引用具体 experiment_id；
* 提供来源文件或 Run Manifest；
* 不得根据模型自身知识编造实验数值；
* 不得混淆已废弃结果与当前结果。

---

# 14. Web 管理端

MVP 需要以下页面。

## 14.1 登录页

支持用户登录。

## 14.2 项目列表页

展示：

* 项目名称；
* 项目状态；
* 用户角色；
* 最近更新时间。

## 14.3 项目上下文页

展示和管理：

* 项目目标；
* 当前主线；
* baseline；
* 数据协议；
* 关键决策；
* 受保护参数；
* 上下文版本。

## 14.4 实验意图页

支持：

* 创建实验意图；
* 编辑草稿；
* 激活；
* 关闭；
* 查看允许变量和控制变量。

## 14.5 提交审核页

展示：

* 本地 Agent 提交摘要；
* 文件；
* 配置；
* 指标；
* 风险报告；
* 重复候选；
* 修改表单；
* 确认或拒绝操作。

## 14.6 实验详情页

展示：

* 实验配置；
* Run Manifest；
* 指标；
* artifacts；
* 实验摘要；
* 风险记录；
* 修改历史；
* 关联实验。

## 14.7 查询页

支持自然语言查询并展示引用记录。

---

# 15. 数据模型

MVP 至少需要以下表。

## 15.1 `users`

* id
* name
* email
* created_at

## 15.2 `teams`

* id
* name
* owner_id
* created_at

## 15.3 `team_members`

* team_id
* user_id
* role
* created_at

## 15.4 `projects`

* id
* team_id
* name
* description
* repository_url
* status
* created_at
* updated_at

## 15.5 `project_contexts`

* id
* project_id
* version
* goal
* non_goals
* mainline_model
* baseline
* dataset
* protocol
* primary_metric
* default_seeds
* active_branch
* active_config
* deprecated_items
* key_decisions
* change_reason
* status
* supersedes_context_id
* created_by
* confirmed_by
* confirmed_at
* effective_at
* created_at

## 15.6 `protected_parameters`

* id
* project_id
* context_id
* context_version
* intent_id
* intent_version
* version
* supersedes_constraint_id
* parameter_path
* protection_level
* expected_value
* allowed_range
* reason
* source_type
* verification_status
* original_message
* inference_basis
* confidence
* created_by
* confirmed_by
* confirmed_at
* active
* created_at

## 15.7 `experiment_intents`

* id
* project_id
* context_id
* context_version
* version
* supersedes_intent_id
* experiment_mode
* name
* objective
* hypothesis
* allowed_variables
* controlled_variables
* expected_outputs
* acceptance_criteria
* source_type
* verification_status
* original_message
* inference_basis
* confidence
* unresolved_ambiguities
* intent_receipt
* status
* created_by
* confirmed_by
* confirmed_at
* activated_by
* activated_at
* created_at

## 15.8 `plan_checks`

* id
* project_id
* intent_id
* context_id
* context_version
* intent_version
* experiment_mode
* requester_id
* idempotency_key
* request_hash
* input_config_hash
* git_commit
* command
* local_attestation
* constraint_snapshot
* planned_changes
* check_result
* approval_status
* risk_level
* report
* approved_by
* approved_at
* created_at

## 15.9 `run_manifests`

* id
* project_id
* intent_id
* plan_check_id
* approval_record_id
* context_id
* context_version
* intent_version
* experiment_mode
* idempotency_key
* config_s3_key
* config_snapshot
* config_hash
* git_branch
* git_commit
* git_diff_hash
* dataset
* protocol
* seed
* checkpoint
* command
* environment
* evidence_snapshot
* manifest_hash
* created_by
* created_at

## 15.10 `experiment_submissions`

* id
* project_id
* run_manifest_id
* submitted_by
* source_agent
* idempotency_key
* request_hash
* manifest_hash
* evidence_snapshot
* status
* processing_step
* processing_error
* workflow_checkpoint
* local_summary
* generated_summary
* risk_level
* receipt
* embedding_payload
* created_at
* updated_at

## 15.11 `artifacts`

* id
* submission_id
* experiment_id
* filename
* mime_type
* size_bytes
* sha256
* s3_key
* artifact_type
* cloud_hash_verified
* created_at

## 15.12 `submission_risks`

* id
* submission_id
* risk_type
* severity
* field_path
* previous_value
* current_value
* expected_value
* rule_id
* message
* impact
* evidence_type
* evidence_source
* collected_at
* collection_tool
* constraint_source
* constraint_status
* inference_basis
* confidence
* recommendation
* blocking
* resolved
* created_at

## 15.13 `experiments`

* id
* project_id
* intent_id
* run_manifest_id
* submission_id
* project_context_id
* project_context_version
* intent_version
* experiment_mode
* eligible_as_baseline
* name
* model_name
* dataset
* protocol
* seed
* status
* config_hash
* git_commit
* checkpoint
* command
* started_at
* completed_at
* confirmed_by
* confirmed_at
* created_at

## 15.14 `experiment_metrics`

* id
* experiment_id
* name
* value
* split
* aggregation_type
* epoch
* is_primary
* created_at

## 15.15 `memories`

* id
* project_id
* experiment_id
* protocol
* model_name
* seed
* experiment_status
* current_valid
* memory_type
* content
* embedding
* verification_status
* source_type
* source_id
* created_at

## 15.16 `approval_records`

* id
* project_id
* target_type
* target_id
* approval_type
* status
* requested_by
* decided_by
* request_reason
* decision_reason
* created_at
* decided_at

## 15.17 `audit_logs`

* id
* team_id
* project_id
* actor_type
* actor_id
* action
* target_type
* target_id
* before_value
* after_value
* created_at

## 15.18 `idempotency_records`

* id
* actor_id
* operation
* idempotency_key
* request_hash
* response_snapshot
* operation_status
* expires_at
* created_at
* updated_at

---

# 16. 系统架构

## 16.1 云端组件

### Web Frontend

用于项目配置、审核、确认和查询。

### FastAPI Backend

负责：

* 身份认证；
* REST API；
* MCP Gateway；
* 权限校验；
* 业务逻辑；
* 文件上传；
* 调用 LangGraph。

### LangGraph Workflows

负责：

* 训练前检查工作流；
* 提交分析工作流；
* 实验查询工作流；
* 提交分析步骤的 checkpoint 与失败恢复。

P0 中 `NEEDS_REVIEW` 是提交分析图的终态交接，不使用 LangGraph 原生 `interrupt()`。
用户确认由独立、幂等的 CockroachDB 事务完成；“待确认任务可以继续”指草稿状态持久化后
仍可执行该确认事务，不表示从原图中的人工中断节点恢复。

### CockroachDB

负责：

* 团队和项目；
* 项目上下文；
* 约束；
* 实验意图；
* Run Manifest；
* 实验记录；
* Agent checkpoint；
* 向量记忆；
* 审计日志。

### Amazon S3

负责：

* 配置文件；
* 日志；
* 报告；
* 指标文件；
* 原始 artifacts。

### Amazon Bedrock

负责：

* 字段提取；
* 实验摘要；
* 语义风险分析；
* 自然语言查询解释。

### Amazon CloudWatch

负责：

* 服务日志；
* Agent 日志；
* 错误日志；
* 性能监控。

## 16.2 部署方式

MVP 推荐：

* 前端：AWS Amplify 或静态 S3 + CloudFront；
* 后端：AWS App Runner 或 ECS Fargate；
* 文件：Amazon S3；
* 模型：Amazon Bedrock；
* 数据库：CockroachDB Cloud，AWS 区域；
* 日志：CloudWatch。

---

# 17. CockroachDB 使用要求

## 17.1 Distributed Vector Indexing

必须用于：

* 相似实验检索；
* 重复实验辅助判断；
* 历史失败经验检索；
* 项目记忆查询；
* 自然语言实验查询。

向量查询必须附加：

* team_id 或 project_id 过滤；
* verification_status 过滤；
* 实验状态过滤；
* protocol 等实验条件过滤；
* 当前有效状态过滤。

默认只返回 `CONFIRMED` 且当前有效的记录。`DEPRECATED` 和 `SUPERSEDED` 必须明确标记。
向量相似度只能生成候选证据，不能替代结构化查询结果。

## 17.2 CockroachDB Cloud Managed MCP Server

必须用于云端 Agent 的受控数据库访问。

建议用于：

* 查询项目 Schema；
* 查询项目上下文；
* 查询实验状态；
* 查询 Agent checkpoint；
* 执行受控诊断。

云端 Agent 不应获得无边界的数据库写权限。

---

# 18. 安全要求

## 18.1 MCP Token

Token 必须绑定：

* user_id；
* team_id；
* project_id；
* scope；
* expiration。

Token 必须支持撤销。

## 18.2 数据隔离

所有查询必须包含 team_id 或 project_id 范围。

任何用户不得读取其他团队数据。

## 18.3 本地采集白名单

本地 Agent 允许采集：

* Git branch；
* Git commit；
* Git diff 摘要；
* 指定配置；
* 指定日志；
* 指标；
* Python、CUDA、PyTorch 版本；
* 运行命令；
* 输出目录。

禁止采集：

* API Key；
* AWS 凭据；
* 数据库密码；
  -完整环境变量；
* SSH 私钥；
* 与项目无关的文件；
* 用户 home 目录；
* 未经指定的代码文件。

## 18.4 证据类型

所有关键字段必须保存 `value`、证据类型、来源、采集时间和采集工具。证据类型只使用：

* `CLOUD_VERIFIED`；
* `LOCAL_ATTESTED`；
* `USER_PROVIDED`。

Git 状态、输出目录、checkpoint、运行命令和本地环境默认属于 `LOCAL_ATTESTED`。本地
声明缺失或互相冲突时必须提高风险并要求确认，风险报告不得将其描述为云端事实。
字段确实不适用于本次实验时，必须显式标记 `NOT_APPLICABLE` 并保存原因；这与字段缺失
不同。例如从头训练可以将 checkpoint 标为不适用，CPU 任务可以将 CUDA 标为不适用。

## 18.5 日志安全

CloudWatch 和审计日志不得记录：

* 密钥；
* Token；
* 数据库连接字符串；
* 完整私有代码；
* 原始未过滤环境变量。

---

# 19. 非功能需求

## NFR-001 持久化

AWS 后端服务重启后：

* 项目数据不得丢失；
* 实验草稿不得丢失；
* 提交分析工作流可以从最后成功的处理步骤恢复；
* 待确认草稿持久化后，可以继续执行独立的用户确认事务。

## NFR-002 幂等性

以下操作必须支持幂等：

* `submission_prepare`
* `submission_finalize`
* 正式实验确认；
* Run Manifest 创建。

## NFR-003 可追溯性

所有正式实验必须追溯到：

* Run Manifest；
* 配置哈希；
* Git commit；
* 原始 artifact；
* 提交用户；
* 确认用户。

## NFR-004 响应时间

在正常网络条件下：

* 项目上下文查询 P95 小于 2 秒；
* 约束查询 P95 小于 2 秒；
* 结构化配置检查 P95 小于 5 秒；
* 不包含 LLM 的实验查询 P95 小于 3 秒；
* 完整提交分析目标时间小于 60 秒。

## NFR-005 文件限制

MVP：

* 单文件最大 20 MB；
* 单次提交最大 10 个文件；
* 单次提交总大小最大 100 MB。

## NFR-006 审计

以下操作必须写入 audit_logs：

* 项目上下文修改；
* 约束修改；
* 实验意图激活；
* Run Manifest 创建；
* 草稿确认；
* 草稿拒绝；
* 正式实验状态修改；
* 权限修改。

## NFR-007 错误处理

任何 Agent 分析失败时：

* 原始提交必须保留；
* 状态设为 FAILED；
* 返回可读错误；
* 允许用户重新处理；
* 不得产生部分正式记录。

---

# 20. 核心验收场景

## AC-001 项目上下文共享

给定同一项目中的两个用户：

* 两人的本地 Agent 调用 `project_get_context`；
* 必须返回相同的当前正式上下文版本；
* 个人对话内容不能改变返回结果。

## AC-002 Locked 参数防护

给定实验意图只允许修改 `lambda_cfqm`：

新配置修改：

* `lambda_cfqm: 0.2 → 0.3`
* `dataset_split: 40/20 → 48/12`

系统必须：

* 将 lambda 变化标记为允许；
* 将 dataset_split 标记为 Locked 冲突；
* 返回 `BLOCKED`；
* 禁止生成 Run Manifest。

## AC-003 正确生成 Run Manifest

修正配置后：

* 所有 Locked 参数保持不变；
* 必填字段完整；
* 实验意图为 ACTIVE。

系统必须生成：

* 唯一 run_manifest_id；
* 配置哈希；
* Manifest 哈希；
* 项目上下文版本；
* 实验意图版本。

## AC-004 本地 Agent 自动提交

训练完成后，本地 Agent：

* 创建 submission；
* 上传配置、日志和指标；
* finalize submission。

系统必须：

* 进入 PROCESSING；
* 完成字段提取；
* 生成风险报告；
* 进入 NEEDS_REVIEW；
* 返回回执摘要。

## AC-005 草稿不能直接入库

在用户确认前：

* experiments 表中不得出现正式实验；
* experiments_query 默认不得将该草稿作为正式结果返回。

## AC-006 用户确认

用户确认后：

* 创建正式实验；
* 创建指标记录；
* 关联 artifacts；
* 创建向量记忆；
* submission 状态变为 APPROVED；
* 创建审计记录。

以上操作必须在同一事务中完成。

## AC-007 重复实验提示

重复上传相同配置、commit、seed 和文件哈希时：

* 系统必须识别完全重复；
* 风险报告中标记 `DUPLICATE_SUBMISSION`；
* 不得自动删除或合并；
* 用户可拒绝或保留。

## AC-008 跨会话查询

另一团队成员查询：

“这次实验为什么不能使用 48/12 协议？”

系统必须：

* 查询项目 Locked 约束；
* 查询实验意图；
* 返回原因；
* 引用对应约束和上下文版本。

## AC-009 服务重启恢复

在 submission 进入 PROCESSING 后重启后端：

* 工作流状态必须从 CockroachDB 恢复；
* 不得重复创建 submission；
* 能继续生成风险报告。

---

# 21. 黑客松演示流程

演示保持在三分钟内，只展示一条完整主线。

## 21.1 创建项目

创建项目并确认：

* 数据协议：NTU60 40/20；
* baseline：46.60%；
* dataset split：LOCKED；
* 本轮实验变量：fusion coefficient。

## 21.2 本地 Agent 查询上下文

本地 Agent 调用：

* `project_get_context`
* `experiment_get_intent`
* `constraints_get`

展示 Agent 获取团队正式上下文。

## 21.3 检测错误配置

本地 Agent 提交配置。

配置同时修改：

* λ：0.2 → 0.3；
* protocol：40/20 → 48/12。

系统返回：

* λ 允许；
* protocol 违反 Locked；
* 结果为 BLOCKED。

## 21.4 修正并生成 Manifest

修正配置后重新检查。

系统生成 Run Manifest。

## 21.5 模拟训练完成

本地 Agent 自动上传：

* 配置；
* Git commit；
* 日志；
* seed；
* Top-1 Accuracy。

## 21.6 云端风险分析

系统生成：

* 实验摘要；
* 配置一致性报告；
* 缺失字段；
* 重复检查；
* 风险等级；
* 回执摘要。

## 21.7 用户确认

用户点击确认。

系统将实验写入正式实验库。

## 21.8 团队查询

另一个用户询问：

* 该实验用了什么配置；
* 为什么协议不能修改；
* 结果来自哪个 Run Manifest。

Agent 返回带来源的答案。

---

# 22. 开发优先级

## 阶段一：基础数据与权限

* 用户、团队、项目；
* 项目上下文；
* 实验意图；
* 参数约束；
* CockroachDB Schema；
* 权限控制。

## 阶段二：训练前检查

* MCP Server；
* context、intent、constraints 工具；
* YAML/JSON 解析；
* 配置 diff；
* 约束引擎；
* Run Manifest。

## 阶段三：训练后提交

* S3 预签名上传；
* submission 状态机；
* artifact 管理；
* 字段提取；
* 风险引擎；
* 回执摘要。

## 阶段四：确认与正式实验

* 审核页面；
* 正式入库事务；
* 实验详情；
* 审计记录。

## 阶段五：记忆与查询

* embedding；
* CockroachDB 向量索引；
* 实验查询；
* 引用返回；
* 相似实验和重复实验识别。

## 阶段六：部署与演示

* AWS 部署；
* CloudWatch；
* 服务恢复测试；
* Demo 数据；
* 视频脚本；
* README。

---

# 23. 产品核心主线

Experiment Guardian 的第一版不负责替用户修改代码或运行训练。

第一版只负责：

1. 将团队确认的研究目标和实验约束保存为统一上下文；
2. 让本地 Agent 自动查询这些上下文；
3. 在训练前发现错误配置；
4. 在训练后自动提交实验草稿；
5. 对草稿进行纠错、风险分析和查重；
6. 让用户快速确认；
7. 将确认后的实验保存为可查询、可追溯的团队记忆。

产品的核心表述为：

> Experiment Guardian provides a shared and durable source of truth for AI research teams, enabling local agents to validate experiment intent before training and automatically submit traceable experiment records for cloud-side verification and human confirmation.
