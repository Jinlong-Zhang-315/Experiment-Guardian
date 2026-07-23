# 内部实验治理 Agent 开发计划

更新时间：2026-07-24
计划轮次：R15a-R15e
当前状态：R15c 已完成；下一步只实施 R15d

## 1. 目标与定位

Experiment Guardian 已增加一个由系统自身托管的内部实验治理 Agent。人类用户通过现有 Web
Session 在项目工作台中与它对话。Agent 可以读取正式数据、调用受控分析工具、生成候选草稿
和操作建议，但不能直接访问任意 SQL、文件系统、Shell、云控制面或不受约束的 HTTP 地址。

Agent 的职责是提高查询、分析、解释和草稿准备效率，不成为新的事实源或权限主体：

* 正式 Context、Intent、Constraint、Plan Check、Manifest、Submission 和 Experiment 仍由
  现有业务服务和数据库状态机管理。
* Agent 使用发起对话的真实 `RequestIdentity`，不能从模型参数中接受 `user_id`、`team_id`、
  `project_id`、角色或 scope。
* 模型只能提出工具调用。工具执行器必须重新验证参数、项目边界、实时成员关系和权限。
* 高影响操作必须先形成不可变操作提案，随后由人类通过独立 Web 确认请求执行。
* 模型生成的分析、假设、阶段总结和长期记忆默认都是 `CANDIDATE_EVIDENCE`。

## 2. 当前仓库审计

### 可以复用

* Cognito/local_owner 最终都生成可撤销的服务端 `WebSession`，写请求已有 CSRF 和实时 RBAC。
* `RequestIdentity`、scope、Owner/Researcher 角色和近期认证已经覆盖正式策略发布、计划决定及
  高风险 Submission 确认。
* `SqlAlchemyProjectRepository`、`WebManagementService`、`ExperimentQueryService` 已提供
  项目策略、Plan、Submission 和正式 Experiment 的受权读取路径。
* `ProjectAdministrationService`、`PlanApprovalService` 和 `ExperimentReviewService` 已拥有
  正式写入、幂等、版本校验、风险资格和审计逻辑。
* `WorkflowJob`、Outbox、lease、generation 和 Worker 已验证异步任务恢复语义，但当前表结构
  明确绑定 Submission，不能直接伪装成 Agent Job。
* `SummaryTextGenerator` 和 `EmbeddingGenerator` 已有 Bailian/Bedrock 适配器，模型元数据和
  异常处理边界可复用。
* LangGraph 1.2 已存在，但当前只用于固定的 Submission 分析拓扑。
* `Memory VECTOR(1024)` 只保存已确认 Experiment 记忆，并执行 project/protocol/status 等
  结构化过滤后再做向量排序。

### R15c 后的当前缺口

* `SummaryTextGenerator` 继续拒绝 `tool_calls`；R15a 已新增独立 `AgentChatModel` 和百炼适配器。
* Thread、Message、Run、ModelCall、ToolCall、Citation、Event 和 Policy Draft 已完成；
  Action Proposal 仍未实现。
* 有界 Agent loop、版本化 Prompt/工具目录、rolling summary、确定性分析和候选草稿工具已完成。
* Web 治理 Agent 已支持只读问答、比较、统计、诊断和完整 Bundle 草稿工作台；没有正式确认页。
* 草稿已表达候选约束语义、未解决歧义、diff 和影响，但不能转换为可确认的正式操作提案。
* 现有业务服务适合复用，但不应直接把大量 ORM 对象或内部方法暴露为模型工具。

## 3. 本轮采纳、调整和拒绝

### 采纳

* 项目状态问答、实验查询、比较、基础统计、异常诊断和带来源回答。
* Agent 创建 Context/Intent/Constraint 候选草稿，用户可在 Web 中编辑。
* 发布前展示结构化差异、影响和冲突。
* 人类确认后，通过现有业务服务执行明确列入白名单的正式操作。
* 对话、模型调用、工具调用、引用、草稿、确认和执行结果全部持久化。
* 阶段性研究总结和下一轮实验建议，但必须引用正式 Experiment。

### 调整

* 第一版不把 Context、Intent 和 Constraints 拆成三套独立发布状态机。当前正式发布以完整
  Policy Bundle 为聚合边界，Agent 草稿也复制并修改完整 Bundle，避免产生部分生效状态。
* “分析参数与指标关系”只提供描述性统计和关联提示，不给出因果结论。
* “当前最佳结果”必须先读取正式主指标定义和方向，并在同 dataset/protocol/可比条件下计算。
* Agent 建议批准或拒绝时只生成审核意见，不自动提交决定。
* 人类确认不等于模型工具循环继续运行。确认请求是新的、带近期身份认证的服务端事务。

### 明确拒绝

* 任意 SQL、自然语言转 SQL、Shell、Python 解释器、自动训练和自动修改代码。
* 一个可以执行“其他高影响操作”的通用写工具。每一种正式写操作必须独立建模和列入白名单。
* 模型直接修改正式表、审计日志、不可变 Manifest、Run、Artifact 或 Experiment。
* 自动批准计划、自动确认 Submission、自动晋升 baseline 或自动替换项目主线。
* 将隐藏思维链作为“完整审计”保存或展示。系统只保存输入、输出、工具轨迹和简短决策说明。
* R15a 直接采用多 Agent、规划者/执行者/审查者链或开放式自主循环。
* 使用百炼 Conversations/Assistant 服务作为对话事实源。对话必须保存在本项目 CockroachDB，
  以保持 provider 可替换、项目隔离和统一审计。

## 4. 目标架构

```text
Browser / Web Session
        |
        | POST message / poll run / confirm proposal
        v
Agent Web API
        |
        +--> AgentConversationService
        |      +--> persist Thread / User Message / Run
        |      +--> build bounded context
        |      +--> invoke GovernanceAgentRuntime
        |
        +--> AgentConfirmationService
               +--> CSRF + recent auth + live RBAC
               +--> lock proposal + compare digest/base versions
               +--> existing business service
               +--> formal AuditLog + execution receipt

GovernanceAgentRuntime
        |
        +--> bounded LangGraph single-agent loop
        |      MODEL -> validated TOOL -> MODEL -> FINAL
        |
        +--> AgentChatModel port
        |      +--> BailianAgentChatModel       [R15a]
        |      +--> BedrockAgentChatModel       [later provider parity]
        |      +--> test fake
        |
        +--> AgentToolRegistry
               +--> READ tools -> AgentQueryService -> existing repositories/services
               +--> ANALYSIS tools -> deterministic comparison/statistics/diagnosis
               +--> DRAFT tools -> AgentDraftService
               +--> no formal execution tool exposed to the model

CockroachDB
        +--> agent_threads / agent_messages / agent_runs
        +--> agent_tool_calls / agent_citations
        +--> agent_policy_drafts                  [R15c]
        +--> agent_action_proposals               [R15d]
        +--> agent_research_memories               [R15e, separate from Memory]
```

内部 Agent 不调用产品 MCP。MCP 继续服务外部 Coding Agent；内部 Agent 直接调用同一应用层的
受控用例，避免二次 OAuth、序列化损失和两套权限语义。

## 5. 模型供应商抽象

新增独立端口，保留现有 `SummaryTextGenerator`：

```text
AgentChatModel
  provider
  model_id
  complete(messages, tools, tool_choice, max_output_tokens)
      -> text | typed tool calls | usage | finish reason
```

建议契约：

* `AgentMessage`：仅支持 system/user/assistant/tool 的已知内容类型。
* `AgentToolSpec`：稳定名称、版本、JSON Schema、操作等级和输出上限。
* `AgentToolRequest`：唯一 call ID、工具名和严格 JSON 对象参数。
* `AgentModelTurn`：文本、零到多个工具调用、token usage、provider request ID。
* 所有未知字段、重复 call ID、未知工具、非对象参数、超限响应和混合异常内容都明确失败。

R15a 使用百炼 OpenAI-compatible Chat Completions Function Calling。模型名称由
`BAILIAN_AGENT_MODEL` 配置，不在代码中锁死。工具顺序和 JSON 字段顺序保持稳定，以利用百炼
上下文缓存的公共前缀，但缓存命中不能影响正确性。

计划配置：

```text
AGENT_ENABLED=false
AGENT_PROVIDER=bailian
BAILIAN_AGENT_MODEL=
BEDROCK_AGENT_MODEL_ID=
AGENT_MAX_MODEL_CALLS=4
AGENT_MAX_TOOL_CALLS=8
AGENT_MAX_WALL_SECONDS=90
AGENT_CONTEXT_TOKEN_BUDGET=12000
AGENT_RECENT_MESSAGE_LIMIT=12
AGENT_SUMMARY_MIN_NEW_MESSAGES=6
AGENT_SUMMARY_MAX_OUTPUT_TOKENS=1200
AGENT_TOOL_OUTPUT_MAX_BYTES=32768
AGENT_MAX_OUTPUT_TOKENS=1800
```

只有 `AGENT_ENABLED=true` 时校验 Agent provider 配置。摘要模型、Agent 模型和 embedding
模型保持三个独立模型槽位，允许使用不同的成本和能力等级。

## 6. Agent 工具目录

所有工具名称带版本，参数使用 Pydantic strict contract，结果是提供给模型的紧凑快照。
每个结果都包含 `citations`，不得返回 ORM 对象或无限列表。

### R15a 只读工具

| 工具 | 作用 | 复用边界 |
| --- | --- | --- |
| `project_status_get_v1` | 当前目标、Context、Intent、正式约束及版本 | Project repository |
| `experiments_list_v1` | 按状态、协议、模型、seed、时间分页查询正式实验 | Agent query facade |
| `experiment_get_v1` | 单个正式实验、指标和追溯版本 | Web/Experiment query |
| `pending_work_list_v1` | 当前用户可见的待审批 Plan 和待审核 Submission | Web management |

`pending_work_list_v1` 必须保留现有 Researcher 只能看到自己草稿的规则。工具不接受用户或
团队 ID；project ID 固定来自 Thread。

同一个模型回合返回多个工具调用时，R15a 按响应顺序逐个校验和执行，不做并行执行。这样可
保持审计顺序稳定，并避免一个工具基于另一个工具尚未返回的结果进行错误推断。

### R15b 分析工具

| 工具 | 作用 | 确定性要求 |
| --- | --- | --- |
| `experiments_compare_v1` | 两个显式 ID 的配置、运行条件和指标差异 | 分层返回 COMPARABLE/CAVEATS/NOT |
| `experiment_group_stats_v1` | 显式 2-20 ID 的 count/mean/std/median/min/max | 严格重复组；不自动分组 |
| `plan_check_explain_v1` | 解释快照、diff、当前审批和 Manifest 资格 | 不信任旧 report 派生审批状态 |
| `submission_diagnose_v1` | 材料、工作流、风险、失败和计划一致性 | 事实/可能原因/待验证假设分栏 |

参数与指标关联只返回样本量、分组差异或简单相关系数，并明确混杂条件，不输出“参数导致结果”
之类因果陈述。

### R15c 草稿工具

* `policy_draft_create_v1`
* `policy_draft_update_v1`
* `policy_draft_validate_v1`
* `policy_draft_impact_get_v1`

这些工具只能操作 `agent_policy_drafts`。草稿包含完整 Policy Bundle、基准版本、结构化 diff、
确定性校验、歧义和人类可读说明。任何编辑都会产生新 draft revision，并使旧提案失效。

### R15d 正式操作

模型只可调用 `action_proposal_prepare_v1` 生成候选操作，不提供 `execute` 工具。首批白名单：

* 发布完整 Policy Bundle 新版本；
* 批准或拒绝 `NEEDS_APPROVAL` Plan Check；
* 确认或拒绝 `NEEDS_REVIEW` Submission。

操作由 Web 确认接口执行。CRITICAL、blocking、角色限制、近期认证和所有现有状态机规则继续
生效。

## 7. 持久化和审计模型

R15a 实际新增：

### `agent_threads`

* project/team/created_by；
* title、status、last_sequence；
* created_at、updated_at、archived_at。

R15b 已增加 `current_summary_id` 指向最近 READY 摘要；失败尝试不会覆盖该指针。

### `agent_messages`

* thread_id、sequence、role、content；
* created_by、run_id、created_at；
* content_sha256、visibility；
* append-only 唯一约束 `(thread_id, sequence)`。

不持久化 provider 的隐藏 reasoning 或 chain-of-thought。

### `agent_runs`

* thread_id、trigger_message_id、status；
* provider、model、prompt_version、tool_catalog_version；
* current_step、attempt_count、started/completed/error；
* input/output token、latency、request ID；
* context snapshot：Context/Intent 版本、消息范围、summary hash。

### `agent_tool_calls`

* run_id、call_id、sequence、tool_name/version；
* validated arguments、arguments hash；
* exact bounded output delivered to model、output hash；
* status、error、started/completed；
* actor user/session 和 project。

### `agent_citations`

* message_id 或 tool_call_id；
* source_type、source_id、source_version；
* label、field paths、snapshot hash。

### `agent_model_calls` 与 `agent_run_events`

* 每次模型调用保存 generation、ordinal、请求/响应快照、usage、provider request ID 和错误；
* durable event 使用 `(run_id, sequence)` 唯一顺序，支持 SSE 断线重放；
* 不保存隐藏 reasoning 或 chain-of-thought。

R15c 已增加 `agent_policy_drafts`；R15d 再增加 `agent_action_proposals`。这些表只保存候选和
执行回执，不能替代正式业务表。

迁移只做增量建表，不修改现有正式记录，也不回填历史对话。

### 最终回答和引用校验

Agent 的最终输出使用严格 `AgentAnswer` envelope，而不是接受任意 Markdown：

```text
answer_markdown
evidence_sections[]: CONFIRMED_FACT | USER_PROVIDED | ANALYSIS | HYPOTHESIS
citations[]: run-local citation ID
follow_up_required
```

服务端只接受本 Run 工具结果已经签发的 citation ID。未知 citation、引用其他项目对象或把
`ANALYSIS/HYPOTHESIS` 标为 `CONFIRMED_FACT` 时，最多执行一次格式修复；仍无效则 Run 失败，
不把回答作为成功消息保存。Web 根据服务端解析后的引用生成链接，不信任模型提供的 URL。

## 8. 对话上下文与记忆策略

### 每轮必须重新加载

* 当前 `RequestIdentity` 和实时 TeamMembership；
* Thread 绑定的 project；
* 当前 Context/Intent ID 和版本；
* 工具目录及其版本。

不能从旧对话摘要中继承“当前正式版本”。

### 短期上下文

模型输入由以下内容组成：

1. 稳定 system prompt 和工具清单；
2. 当前项目和身份的最小运行时元数据；
3. 已验证 rolling summary；
4. 最近 N 条原始消息；
5. 本轮工具结果。

达到 token 预算时先删除过期工具大结果，再减少普通历史消息，最后才更新 rolling summary。
不能截断当前用户消息、待确认提案或未解决歧义。

### Rolling summary

* 保存被压缩的消息 sequence 范围和 SHA-256。
* 必须保留未解决问题、用户明确选择、草稿 ID、引用和提案状态。
* 明确区分正式事实、用户陈述和 Agent 推测。
* 摘要失败时保留原始消息，退化为最近消息窗口，并提示上下文可能不完整。
* 修改 summary prompt 必须增加版本；不得无提示重写历史摘要。

### 长期记忆

R15e 才引入独立 `agent_research_memories`：

* 类型为 `RESEARCH_SYNTHESIS`、`OPEN_QUESTION`、`DECISION_RATIONALE`；
* 状态为 `CANDIDATE`、`CONFIRMED`、`SUPERSEDED`；
* 每条结论必须绑定 Experiment/Context/Intent/Plan/Submission 引用；
* 与正式 `Memory` 分表、分索引、分查询工具；
* 向量召回前必须做 project/type/status 等结构化过滤；
* 召回结果仍是候选证据，不能成为约束判断输入。

不在 R15a-R15d 引入 Agent 长期向量记忆，避免在检索消费语义尚未稳定前污染现有 Experiment
Memory。

## 9. 提示词管理

提示词作为版本化源码常量或独立只读文件维护，不能由普通用户在数据库中直接修改：

* `governance-agent-system-v1`：职责、权限、证据标签、引用格式和禁止行为。
* `governance-agent-thread-summary-v1`：忠实压缩，不新增事实，保留未解决事项。
* `governance-agent-policy-draft-v1`：输出严格 schema，显式列出歧义。
* `governance-agent-research-synthesis-v1`：逐条结论绑定证据，标记冲突和不确定性。

每个 `agent_run` 保存 prompt version，不保存或展示隐藏思维链。正式工具结果放入明确的
`UNTRUSTED_TOOL_DATA` 边界，Artifact 日志、用户消息和历史自然语言都视为不可信数据。

## 10. 正式操作确认协议

```text
Agent 生成草稿
-> 确定性校验、diff 和 impact
-> 创建 PROPOSED action proposal
-> Web 展示完整 payload、来源、风险、base version 和 digest
-> 用户点击确认
-> 新的 CSRF 请求 + recent auth
-> 事务锁定 proposal
-> 重读实时角色、scope、业务状态和目标版本
-> digest/base version 不匹配：标记 STALE，不执行
-> 调用现有业务服务和幂等协议
-> 写正式 AuditLog、proposal EXECUTED 和 execution receipt
```

确认请求必须包含用户看到的 proposal digest。模型不能生成或代替用户确认。用户编辑草稿、
目标版本变化、审批状态变化或提案过期都会使提案失效。

Agent 自身不是正式操作 actor；AuditLog 的 actor 仍是确认用户，并附带 thread/run/proposal
追溯 ID。

## 11. 安全和可靠性

* 只注册当前阶段允许的工具，不依赖 system prompt 隐藏危险工具。
* 工具参数严格校验，所有查询强制 project 过滤、分页和输出大小上限。
* Tool result、日志和用户文本均视为可能包含 prompt injection 的不可信数据。
* 不允许任意 URL、SQL、代码执行、文件写入或直接对象存储访问。
* R15a 不向模型发送原始 Artifact、完整日志、预签名 URL、Token、Cookie 或凭据。后续诊断
  默认使用服务端提取的结构化事实；需要日志片段时必须限长、脱敏并记录来源 Artifact 版本。
* 模型调用、工具调用、总轮次、上下文、输出和总墙钟时间均设硬上限。
* 模型超时、限流、畸形响应和未知工具不会修改正式数据；Run 进入明确失败状态并可安全重试。
* 同一 Thread 同时只运行一个 Run；重复用户请求使用幂等键。
* Agent 回答必须区分 `CONFIRMED_FACT`、`USER_PROVIDED`、`ANALYSIS` 和 `HYPOTHESIS`。
* Web 只渲染受限 Markdown/结构化块，不使用未清理 HTML。
* 所有敏感正式写操作继续要求近期认证，不能因 Agent 已经“询问过一次”而复用旧确认。

## 12. 分阶段实施

### R15a：只读对话纵向切片

已完成：

1. `AgentChatModel` 端口、严格 contract、Fake provider 和百炼 Function Calling 适配器。
2. Thread/Message/Run/ModelCall/ToolCall/Citation/Event 七组持久化实体和 migration。
3. 四个只读工具：项目状态、正式实验列表、实验详情、待办列表。
4. 单 Agent 有界循环，最多 4 次模型调用和 8 次工具调用。
5. 每个回答返回可点击 citation，不允许无来源声称项目正式事实。
6. Web 第五页“治理 Agent”：Thread 列表、对话、工具调用折叠区、引用和失败重试。
7. API：创建/归档/恢复 Thread、读取消息、幂等发送消息、读取/重试 Run 和 durable SSE。
8. 真实 Web Session；创建、发送、归档和重试使用 CSRF；每个工具调用执行项目 RBAC 和审计。

9. 专用 Agent Worker 通过 lease、generation、最大重试和永久失败恢复运行，不复用 Submission
   的 WorkflowJob 外键。

明确未实现统计、诊断、草稿或正式写入。

验收问题：

* “当前项目目标和 Active Intent 是什么？”
* “当前有哪些 LOCKED 和 APPROVAL_REQUIRED 参数？”
* “最近 5 个正式实验是什么？”
* “我能看到哪些待审批计划和待审核提交？”

每个正式陈述必须包含对象 ID/版本引用。Researcher 不能通过 Agent 看到其他成员的草稿。

建议代码边界：

```text
domain/agent.py                 Thread/Run/Answer/Tool/Citation contracts
application/agent.py           conversation service、identity resolver
application/agent_runtime.py   bounded runtime、processor、lease/retry
application/agent_tools.py     四个只读工具及 compact result
application/ports.py           AgentChatModel port
infrastructure/bailian.py      复用现有 HTTP client 增加 Agent adapter
infrastructure/repositories/   Agent persistence repository
api/routes/agent.py            Thread、Message、Run API
web/src/pages/AgentPage.tsx    第五个工作台页面
migrations/versions/...        只增加 R15a 表
tests/agent_eval_cases/        版本化 trajectory/security cases
```

R15a 继续使用现有 `httpx` 调用百炼，不为了 OpenAI-compatible 协议强制引入 OpenAI SDK。
只有当直接 HTTP 适配产生无法合理维护的协议复杂度时，才单独评估新增依赖。

### R15b：比较、统计、诊断和上下文压缩

已完成：

1. 四个新增只读工具采用分层可比性、显式重复组、动态审批解释和 Submission 元数据诊断。
2. dataset/protocol/完成状态/指标语义/指标方向冲突为硬阻断；其他运行差异明确列为 caveat。
3. 确定性分析产生独立 `ANALYSIS` evidence；服务端验证回答分段和 evidence kind 一致。
4. revision 16 增加 rolling summary、来源消息范围/ID/hash、provider/model/prompt 和失败记录。
5. 摘要更新失败保留旧 READY 摘要或退回最近消息，不影响 Run，不丢失原始对话。
6. R15a/R15b Prompt 和工具目录按 Run 固化兼容；旧 Pending Run 不会看到新增工具。

明确延期：Run 取消不是本轮分析与压缩的必要条件，留待出现真实交互需求后单独设计。

### R15c：治理草稿和影响分析

已完成：

1. 完整 Policy Bundle 草稿、追加式 revision、编辑和取消 API。
2. 模型输出通过严格 schema，含糊内容进入 `unresolved_ambiguities`。
3. 复用正式发布语义校验，生成确定性结构化 diff、候选说明和影响等级。
4. 对待审批 Plan 执行无写入模拟，对进行中 Submission 只展示不可变版本追溯。
5. Web 提供结构化编辑器、原始 JSON、差异、冲突、历史和取消。
6. `r15c-v1` Prompt/目录与摘要 schema v2 固化，旧 Run 继续使用 R15a/R15b 目录。
7. 本阶段没有任何正式发布入口。

### R15d：用户确认后的白名单正式操作

1. 增加 Action Proposal 和 digest/base-version 协议。
2. 接入 Policy Bundle 发布、Plan 批准/拒绝、Submission 确认/拒绝。
3. 独立确认 API 执行 CSRF、近期认证、实时 RBAC、事务锁和幂等。
4. 目标状态或版本变化时返回 STALE，不自动重做提案。
5. CRITICAL、blocking、HIGH 风险角色限制和不可变记录规则保持不变。
6. 完整记录模型建议、人类决定、执行 actor、业务 AuditLog 和结果。

### R15e：研究总结、长期记忆与 provider parity

1. 按用户选择的一组正式实验生成阶段性研究总结。
2. 每条稳定结论、冲突结论、开放问题和建议绑定正式引用。
3. 引入独立 Agent Research Memory 和结构化过滤优先的向量召回。
4. 提供按需项目进展报告，不自动发布为正式项目决策。
5. 实现 Bedrock AgentChatModel，并用同一 provider contract/eval suite 验证。
6. 增加成本、token、延迟、工具错误和模型回归观测。

一次只实现一个阶段。当前下一步只开始 R15d，R15d 的任何正式操作仍必须由独立人类确认请求
执行，模型本身不获得 execute 工具。

## 13. 测试与评测

每个阶段同时增加普通测试和 Agent 行为评测。

### 确定性测试

* Provider 请求、tool_calls 解析、超时、非 2xx、畸形 JSON、未知 finish reason。
* Tool schema、输出上限、项目隔离、Researcher 可见性、分页和引用完整性。
* 未知 citation、跨项目 citation、伪造来源版本和事实/假设错误分栏。
* Thread 序号、并发 Run、幂等、失败回滚和审计。
* Context 压缩的来源范围/hash、失败降级和版本变化。
* Draft diff、proposal digest、过期版本、重复确认和现有业务状态机。

### Agent eval 数据集

R15a 先维护 20 到 30 个仓库内 JSON case：

* 正确工具选择和参数；
* 必须引用的项目事实；
* 信息不足时要求澄清；
* 不允许的 SQL、跨项目查询和自动批准请求；
* Prompt injection、伪造工具结果和要求忽略系统规则；
* 模型应该拒绝或生成候选草稿的边界。

CI 使用 Fake/脚本化模型做严格 trajectory match，不访问外网。真实百炼评测仅在
`RUN_BAILIAN_AGENT_INTEGRATION=1` 时执行，记录模型 ID、prompt/tool catalog version 和结果，
但不作为无凭据环境的默认门禁。

质量门槛至少覆盖：

* 正式事实引用率；
* 越权工具调用次数必须为 0；
* 写操作未经确认执行次数必须为 0；
* 正确工具选择率；
* 工具参数有效率；
* 拒绝/澄清准确率；
* 平均模型调用数、token、延迟和失败率。

## 14. 研究依据与技术取舍

* 百炼 Function Calling 采用“模型提出工具和参数，应用执行，再把工具结果返回模型”的标准
  循环，因此工具权限必须在应用端执行：
  <https://help.aliyun.com/zh/model-studio/qwen-function-calling>
* 百炼 Context Cache 要求稳定公共前缀和稳定工具定义顺序。本计划只把它作为性能优化，不把
  provider 缓存当作记忆：
  <https://www.alibabacloud.com/help/en/model-studio/context-cache>
* LangGraph 将 checkpointer 用于 Thread 短期状态，将 Store 用于跨 Thread 长期记忆。本项目
  先以显式业务表保存审计，再按阶段接入专用 Agent Job：
  <https://docs.langchain.com/oss/python/langgraph/persistence>
* 长对话应使用裁剪、摘要和来源管理，而不是无限追加全部消息：
  <https://docs.langchain.com/oss/python/langgraph/add-memory>
* Agent 评测应同时检查最终回答、单步工具选择和完整轨迹：
  <https://docs.langchain.com/langsmith/evaluation-approaches>
* OWASP 将 Prompt Injection 和 Excessive Agency 视为主要风险；本计划通过工具最小化、参数
  校验和独立人类确认限制影响范围：
  <https://genai.owasp.org/llmrisk/llm01-prompt-injection/>
  <https://genai.owasp.org/llmrisk/llm062025-excessive-agency/>

## 15. R15c 验收结果与 R15d 门禁

* revision `20260724_17` 已在真实 CockroachDB 完成 16→17→16→17；新增两张表可逆且没有
  修改正式业务表。
* 八个只读/分析工具与四个候选草稿工具的 schema 和权限边界已冻结；模型参数不接受身份和项目。
* 已建立 38 个 R15c trajectory/security case；真实百炼调用使用
  `RUN_BAILIAN_AGENT_INTEGRATION=1` 显式验收，不进入默认 CI。
* Researcher 只能管理自己的草稿，Owner 可以代管项目草稿且 revision 保留真实 author。
* 草稿过期后只读，不静默 rebase；Plan 模拟和 Submission 影响不会回写正式状态。
* 七个产品 MCP 工具和正式 Policy、Plan、Manifest、Submission、Experiment 状态机未修改。
* R15d 必须使用不可变 Proposal + digest + base version + 独立人类确认 API；不得把正式执行注册
  为模型工具，也不得让草稿直接发布。
