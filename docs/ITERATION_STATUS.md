# Experiment Guardian 迭代实现与计划

更新时间：2026-07-30
当前完成轮次：R18b.1 大型 Policy 的计划审核上下文修复
下一步：重试 TDSM revision 3 审核并继续阶段 C/D 本地真实数据验证

本文维护每轮交付和紧邻下一步。详细修改见 `DEVELOPMENT_LOG.md`，当前文本框架图见
`ARCHITECTURE.md`。

## 总体进度

| 轮次 | 目标 | 状态 | 主要结果 |
| --- | --- | --- | --- |
| R0-R5 | 需求收敛、骨架、配置和证据正确性 | 完成 | 确定性规则、版本、来源、严格配置解析 |
| R6 | 基础数据、Token 和正式上下文 | 完成 | CockroachDB、CLI、项目初始化、Context MCP |
| R7 | 训练前检查持久化 | 完成 | 完整历史快照、幂等、严格类型、事务重试 |
| R8 | 计划审批和 Run Manifest | 完成 | Owner 最终决定、不可变 Manifest |
| R9-R10 | S3 草稿上传与验证 | 完成 | 防覆盖、哈希、VersionId、失败审计和恢复 |
| R11 | 可恢复确定性分析 | 完成 | 解析、Manifest 校验、查重和风险 |
| R12a | Outbox/SQS/Bedrock 摘要 | 完成 | 可租约 Worker、至少一次、安全摘要边界 |
| R12b | embedding 与审核回执 | 完成 | VECTOR(1024)、确定性权限回执、NEEDS_REVIEW |
| R13 | 正式实验确认与查询 | 完成 | 单事务确认、结构化过滤优先的向量候选 |
| R14a | Cognito Web 认证与管理 API | 完成 | OIDC+PKCE、服务端 Session、CSRF、近期认证 |
| R14b | 四个收敛 Web 页面 | 完成 | React/Vite 设置、计划、审核、查询页面 |
| R14c | 远程 MCP OAuth | 完成 | RFC9728、Cognito RS、预注册客户端、本地撤销 |
| R14d | AWS 部署定义 | 完成 | Terraform ECS/CloudFront/Cognito/S3/SQS/WAF |
| R14e | 最终演示与运维材料 | 完成 | 验收脚本、双角色 Runbook、文档同步 |
| R14 Local | 单机可替换基础设施 | 完成 | local_owner、MinIO、DB Queue、百炼、Compose |
| R14f | 正式策略双表示 | 完成 | 结构化事实、确定性说明、来源哈希、失败重生成 |
| R15a | 内部治理 Agent 只读对话 | 完成 | 持久化、百炼工具调用、只读工具、引用、独立 Worker |
| R15b | 比较、统计与诊断 | 完成 | 分层可比性、显式组统计、审核诊断、滚动摘要 |
| R15c | 治理草稿与影响分析 | 完成 | 追加式完整 Bundle 草稿、diff、歧义、影响与 Web 工作台 |
| R15d-a | Policy 发布提案 | 完成 | 不可变提案、版本复核、Owner 近期认证、原子发布 |
| R15d-b1 | Plan Check 决策提案 | 完成 | 批准/拒绝冻结提案、状态哈希、原子审批 |
| R15d-b2 | Submission 审核提案 | 完成 | 冻结审核依据、风险权限、人工确认和原子正式入库 |
| R15e-a | 显式实验集候选研究报告 | 完成 | 来源冻结、逐条引用、共享只读报告、失效提示 |
| R15e-b | 独立 Research Memory 与召回 | 完成 | finding 级候选记忆、结构过滤、过期与降级 |
| R15e-c | Agent provider parity 与观测 | 完成 | Bedrock 严格输出、统一调用观测、同套评测 |
| R16-L | 本地百炼候选版加固 | 完成 | RC 预检、真实百炼、CRDB 并发恢复、MinIO 与 Web 回归 |
| R17a | 外部 Coding Agent 协作入口 | 完成 | MCP 任务、正式快照、异步引用问答、Web 续聊、多凭据恢复 |
| R17b | 版本化自然语言实验计划 | 完成 | 硬检查、带引用审核、最多两轮自动修订、不可变人工决定 |
| R17c | 三阶段关键不变量核对 | 完成 | 批准快照、增强 Plan Check、v2 Manifest、最终证据阻断 |
| R17d | 本地端到端与首版发布 | 完成 | 公共接口闭环、真实百炼门、恢复安全回归、v1.0.0 |
| R18a | 内部 Agent 能力域隔离 | 完成 | 会话级确定性路由、专用目录/Schema、Proposal 前置硬门禁 |
| R18b | 真实百炼架构验证 | 完成 | 60 Run 成对评测、Proposal 实链、默认 ANALYSIS、迁移约束修复 |
| R18b.1 | 大型 Policy 计划审核修复 | 完成 | v3 紧凑正式策略投影、完整快照与硬检查保持不变 |

## R15：内部实验治理 Agent 路线

完整设计见 `INTERNAL_GOVERNANCE_AGENT_PLAN.md`。

关键决策：

* 采用单 Agent 和受控工具，不在首版引入多 Agent 或任意 SQL/代码执行。
* 新增 `AgentChatModel`，保留现有禁止工具的 `SummaryTextGenerator`。
* R15a 只开放项目状态、正式实验、实验详情和当前用户待办四个只读工具。
* Agent 使用当前 Web 身份，工具执行器重新验证实时 RBAC 和项目隔离。
* CockroachDB 保存所有原始消息；R15b 的 rolling summary 只压缩模型上下文，带消息范围和
  来源 hash，失败时保留旧摘要或退回最近窗口。百炼 provider 对话或缓存不是真实记忆源。
* R15c 只写独立候选草稿表；R15d-a/b1/b2 只增加 Policy、Plan 和 Submission 白名单提案。
* R15d 的正式操作不暴露为模型执行工具：Agent 生成提案，人类通过独立 CSRF + recent-auth
  请求确认，服务端重查版本和状态后调用既有业务服务。
* R15e-b Research Memory 与正式 Experiment Memory 分离；不支持候选记忆晋升为正式事实。
* R15e-c provider 在进程启动时选择，不对失败调用执行静默 provider 回退；旧 Run 继续绑定
  创建时的 provider/model，避免重试时悄然改变执行条件。

## R15d-a：Policy 发布提案与 Owner 确认

交付：

* revision `20260724_18` 新增 `agent_action_proposals`，冻结当前草稿 revision、候选发布请求、
  确定性 diff、影响、基线版本、待办状态哈希、24 小时有效期和 SHA-256 提案摘要。
* Agent 仅新增 `action_proposal_prepare_v1`。它会在服务端重算草稿校验和影响，拒绝
  `STALE`、`INVALID`、含歧义、旧 revision 或无差异草稿；没有 confirm/execute 工具。
* Researcher 可为自己的草稿准备提案和取消提案，Owner 可查看项目全部提案；只有 Owner
  能确认。确认必须经过 Web Session、CSRF、`project:write`、近期认证和摘要匹配。
* 确认时重新检查 Context/Intent、草稿 revision、候选哈希及待审批 Plan/进行中 Submission
  状态；漂移提案持久化为 `STALE`，过期提案持久化为 `EXPIRED`，不会发布。
* Policy 发布核心支持使用调用方 Session；正式 Context/Intent/Constraints、Policy 发布
  幂等记录、提案状态、确认幂等记录和审计在同一个 CockroachDB 事务中提交。
* 现有项目设置页直接发布入口未改变。Agent 工作台增加提案列表、冻结差异/影响、原始
  结构化请求、Researcher 等待状态和 Owner 复核勾选。
* R15d Prompt/工具目录保持 R15a–R15c 兼容；evidence 增加 `ACTION_PROPOSAL`，rolling
  summary schema v3 只保存有界提案引用，不把提案描述为已执行。
* R15d 评测集增加 24 个提案准备、失效、权限、确认、幂等和 prompt injection case。

## R15d-b1：Plan Check 批准/拒绝提案

交付：

* revision `20260724_19` 将 `agent_action_proposals` 扩展为按 operation 校验的联合记录，
  增加 Plan 目标、完整正式依据哈希和执行 ApprovalRecord 追溯；历史 Policy digest 不变。
* 新增 `action_proposal_prepare_plan_decision_v1`。它只接受明确的批准/拒绝和非空理由，
  只处理 `NEEDS_APPROVAL/PENDING` 且没有 ApprovalRecord/Manifest 的 Plan。
* Researcher 只能准备自己的 Plan 提案；只有实时 Owner Web Session 可以在 CSRF 和近期
  认证后确认。Agent 没有 confirm/execute/Manifest 工具。
* `PlanApprovalService.decide_in_session()` 成为直接审批和 Proposal 确认共享的唯一事务核心；
  Proposal、Plan、ApprovalRecord、双幂等结果和审计原子提交。
* 提案 digest 绑定决定、理由、Plan 正式状态哈希、Context/Intent 版本和 TTL。直接审批抢先、
  Plan 状态/内容变化或过期都会阻止执行。
* Web 工作台按 operation 展示 Policy 或 Plan；Plan 拒绝明确显示不可逆影响并使用危险操作
  样式，Owner 必须核对正式依据、决定和理由。
* Agent 目录升级 `r15d-b1-v1`，旧 Run 保留冻结目录；rolling summary schema v4 保存有界
  Plan target/decision 引用。新增 20 个 Plan 决策 trajectory/security case。

## R15d-b2：Submission 批准/拒绝提案

交付：

* revision `20260727_20` 在联合提案中增加 `SUBMISSION_DECISION`、目标 Submission 和
  执行 Experiment 追溯，不改变历史 Policy/Plan digest。
* Proposal 冻结审核回执、当前风险、Artifact 哈希与固定 VersionId、Manifest/Plan/
  Context/Intent 追溯、embedding 元数据、决定和理由，并用状态哈希和 digest 防篡改。
* `ExperimentReviewService.decide_in_session()` 为直接审核和 Proposal 确认共享的事务核心；
  Proposal、ApprovalRecord、Experiment、Metric、Memory、Artifact 关联、幂等和审计原子提交。
* Researcher 可准备自己 Submission 的 HIGH 批准提案供 Owner 复核；只能确认自己
  LOW/MEDIUM 批准或拒绝提案。HIGH 批准仅 Owner，CRITICAL/blocking 不得准备批准。
* 所有 Agent Proposal 确认都要求 Web Session、CSRF 和近期认证；现有直接 Submission
  审核 API 的权限和近期认证规则不变。
* Agent 新增 `action_proposal_prepare_submission_decision_v1`，提示词/目录升级
  `r15d-b2-v1`，rolling summary schema v5 保存 Submission target/decision/eligibility 引用。
* Web 作业台默认展示人类可读回执，强制展开 HIGH/CRITICAL/blocking 风险，可查看
  固定版本材料、完整追溯和原始结构化决定。新增 21 个 Submission trajectory/security case。

## R15e-a：显式实验集候选研究报告

交付：

* revision `20260727_21` 新增 `agent_research_reports`，以不可变记录冻结团队/项目、创建人、
  来源 Thread/Run/ToolCall/最终 Message、显式 Experiment 集、来源快照、报告正文、双哈希和
  provider/model/prompt/schema 元数据；不修改正式 Experiment `Memory`。
* `research_report_prepare_v1` 只接受用户明确选择的 2-8 个正式 Experiment，不自动扩组；
  默认仅接受当前 `COMPLETED/FAILED`，历史状态必须由用户显式允许。
* 服务端按稳定顺序冻结指标、失败原因、正式摘要和完整追溯，复用既有两两可比性及整组重复
  统计。报告中的支持结论/冲突至少引用两个正式实验事实和一个确定性分析，且必须覆盖全部
  选择的 Experiment。
* Agent Prompt/工具目录升级 `r15e-a-v1`。报告工具不得与草稿或操作提案工具在同一轮混用；
  模型返回缺少报告、越界引用、错配 source hash 或实验集合时只修复一次，仍失败则整轮失败。
* 助手消息、引用、Report、Run 完成状态和审计在一个事务中持久化。报告内容不可修改，来源
  实验状态后续变化只显示警告，不静默改写历史结论。
* 项目成员可通过只读 API、Agent 工具和 Web 研究报告工作台查看共享报告、来源、限制、
  provider 元数据与原始 JSON；无 POST/PATCH/DELETE 报告管理旁路。
* rolling summary schema v6 只保存有界报告引用，不复制报告全文。新增 24 个报告工具选择、
  引用、越权和 prompt injection case；正式写操作边界保持不变。

## R15e-b：独立候选 Research Memory

交付：

* revision `20260727_22` 新增 `agent_research_memories` 和
  `agent_research_memory_embeddings`；每个报告 finding 对应一条不可变候选记忆，正文与
  provider/model/document version 的可恢复向量任务分离，正式 `memories` 表保持不变。
* 报告事务只执行确定性 finding 物化，不调用外部模型；Agent Worker 为旧报告幂等补建，使用
  claim、lease、generation、退避和死信处理 embedding，失败不会影响报告读取。
* 查询先按 team/project/CANDIDATE/type/protocol/实验引用/来源有效性和当前模型版本过滤，再在
  最多 200 个候选中精确余弦排序；无候选时不调用模型，来源变化默认不召回。
* Agent 新增 `research_memories_search_v1`，Prompt/目录升级 `r15e-b-v1`，rolling summary v7
  只保留有界 memory/report/finding/hash 引用。结果固定为 `ANALYSIS/CANDIDATE_EVIDENCE`。
* Web 研究报告工作台显示 finding 索引状态、错误和来源新鲜度，支持候选语义检索；只有实时
  Owner 可通过 CSRF 和幂等接口重试失败索引。
* 本轮不采用 CockroachDB Distributed Vector Index：结构候选上限为 200，精确排序更易验证；
  达到真实规模和延迟瓶颈后再基于观测数据评估。

## R15e-c：Agent provider parity 与模型运行观测

交付：

* `AgentChatModel` 增加显式 `AgentResponseFormat`；百炼继续由服务端执行严格 Pydantic 校验，
  Bedrock 使用 ConverseStream `outputConfig` JSON Schema，并拒绝 prompt-only JSON 降级。
* 增加 `BedrockAgentChatModel`，统一映射 system/user/assistant/tool、严格工具 Schema、碎片化
  tool input、usage、finish reason 和 provider request ID；畸形或未知事件归一化为可重试失败。
* `AGENT_PROVIDER=bailian|bedrock` 只在 Settings 与组合根装配。Run 执行前核对持久化 provider/
  model 与实际适配器，禁止重试时静默换模型；本地部署仍只允许百炼。
* revision `20260727_23` 为 ModelCall 增加 provider/model、延迟、币种、冻结费率和估算费用；
  schema 名称/hash、usage 和调用结果继续保留完整审计，历史调用不会按新费率追溯改价。
* 新增 Owner-only 项目观测 API 与 Web 7/30/90 天面板，并扩展 Run 详情。接口只查询有界元数据
  列，不下发提示词、回答或工具载荷；费用明确是配置估算，不是云平台账单。
* 新增百炼/Bedrock 共享 provider case 目录、Bedrock 流契约测试、可选真实 Bedrock Agent gate，
  Terraform 支持两种 provider 与成对费率配置；没有增加任何 Agent 工具或正式写入旁路。

## R15a：只读内部实验治理 Agent

交付：

* 新增独立 `AgentChatModel` 端口和百炼 OpenAI-compatible 流式 Function Calling 适配器，
  不改变禁止工具调用的摘要模型契约。
* 增加 Thread、Message、Run、ModelCall、ToolCall、Citation 和 durable Event 七组实体；
  Run 具有 claim、lease、generation、最大重试和永久失败语义。
* 仅注册项目状态、正式实验列表、正式实验详情和当前用户待办四个只读工具；工具参数不能
  指定身份或项目，执行时使用 Web Session 身份重新校验实时 Membership 和项目隔离。
* 使用有界单 Agent LangGraph loop，限制模型调用数、工具数、输出和总时长；不注册 SQL、
  Shell、草稿或正式写工具。
* 最终回答使用严格 `AgentAnswer`，正式陈述只能引用本 Run 实际取得的 evidence；消息、模型
  调用、工具输入输出、引用、事件和最终 AuditLog 均持久化。
* API 提供会话创建/归档/恢复、幂等消息入队、Run 查询/重试和可断线重放的 SSE；浏览器断开
  不会中断独立 Agent Worker。
* Web 增加第五页治理 Agent，显示会话、运行状态、回答、正式引用、失败原因和重试操作。
* 建立 24 个版本化权限、工具选择、引用、澄清和 prompt injection eval case；默认测试使用
  scripted provider，真实百炼 Function Calling 通过显式 integration gate 验收。
* revision `20260723_15` 增加 Agent 表，已在真实 CockroachDB 完成升级、降级和再次升级。

## R15b：实验分析与对话压缩

交付：

* 新增 `experiments_compare_v1`：dataset/protocol/完成状态/指标语义和方向冲突属于硬阻断；
  Context、Intent、model、seed、Git、checkpoint、命令和非 seed 配置差异作为显式注意项。
* 新增 `experiment_group_stats_v1`：只接受用户明确提供的 2 至 20 个 Experiment ID；严格
  重复组才计算 count、mean、sample stddev、median、min/max/range 和方向已知时的最佳记录。
* 新增 `plan_check_explain_v1` 和 `submission_diagnose_v1`；前者以当前审批列和审批记录为准，
  不重放旧 report 的派生状态，后者检查追溯、Artifact 固定版本、风险和后台任务但不下载日志。
* 工具结果分别产生 `CONFIRMED_FACT` 与 `ANALYSIS` 证据，回答服务端校验分段证据类型和引用
  并集；Web 默认按事实、用户输入、分析和假设分层展示。
* revision `20260723_16` 增加 `agent_context_summaries`、Thread READY 摘要指针和 ModelCall
  purpose。摘要记录消息范围、消息 ID、来源 hash、prompt/provider/model 和成功/失败状态。
* R15b Run 使用 `r15b-v1` Prompt/工具目录；数据库中尚未执行的 `r15a-v1` Run 继续取得原四个
  工具。摘要失败不令正式 Run 失败，也不能作为 Citation。
* 评测集扩展为 48 个 R15b case，新增不可比拒绝、显式统计、诊断、证据分层、摘要边界和
  prompt injection 场景。

## R15c：治理草稿与影响分析

交付：

* revision `20260724_17` 增加 `agent_policy_drafts` 和追加式
  `agent_policy_draft_revisions`；草稿冻结完整基准 Policy Bundle、版本和来源 hash。
* Agent 新增 create/update/validate/impact 四个受控草稿工具。创建前必须在同一 Run 读取
  当前正式 Bundle，每个 Run 最多一次草稿写入，候选证据使用 `CANDIDATE_DRAFT`。
* 含糊请求保留正式值并进入 `unresolved_ambiguities`；重复路径、Intent/Constraint 冲突、
  active config 与 expected value 漂移由确定性校验报告，不会生成正式规则。
* diff 使用 JSON 类型严格比较并标记影响级别；待审批 Plan 只做内存模拟，Submission 只展示
  不可变版本追溯，不改写既有 Plan、Manifest、Submission 或正式策略。
* Researcher 只能管理自己的草稿，Owner 可审阅和修订项目内草稿；所有 revision 保留真实作者、
  Agent Run/ToolCall 或 Web Session 来源和审计。
* Web Agent 页增加治理草稿工作台，提供回执、结构化编辑、原始 JSON、diff、影响、历史和取消；
  `STALE` 草稿只读，界面没有发布动作。
* R15c rolling summary 使用 schema v2 保留有界草稿引用；R15a/R15b Prompt 和工具目录仍可
  恢复旧 Pending Run。
* 增加 38 个 R15c trajectory/security case，并覆盖真实 Runtime 草稿轨迹、权限、幂等、
  乐观并发、失效和正式表不变性。

## R14f：结构化 JSON + 人类可读自然语言

交付：

* `policy_narratives` 将派生 Markdown 绑定到 Context/Intent ID 与版本、结构化来源 SHA-256
  和 `policy-narrative-v1` 模板版本；结构化表仍是唯一事实源。
* 初始化和策略发布自动生成确定性说明，完整覆盖目标、协议、基线、Intent、受控变量及三类
  参数保护级别，不调用 LLM，也不推断正式数据中不存在的事实。
* `READY/FAILED/STALE/MISSING` 明确区分展示状态；来源或模板不一致时不返回旧内容。
* 模板失败不会回滚正式版本，Owner 可通过受 CSRF、实时 RBAC 和审计保护的接口重新生成。
* Web 默认展示说明，完整 JSON 保留在高级视图；历史 Context 同时显示各自绑定的说明。
* `project_get_context` 同时返回双表示，并明确所有执行和治理决定以结构化字段为准。
* 暂不为 Context/Intent 增加向量：当前没有策略语义查询消费路径，现有实验 Memory 查询不混入
  不同类型记录；未来可在独立历史策略查询中使用 project/status/protocol 前缀过滤后接入。

## R14 Local：可替换基础设施部署

交付：

* `DEPLOYMENT_MODE=cloud/local` 条件配置校验，本地启动不读取或实例化 Cognito、SQS、AWS
  S3 或 Bedrock 客户端。
* `local_owner` 只接受唯一 Team 的真实 Owner Membership，继续使用 WebSession、CSRF、实时
  RBAC、近期认证和审计；production 启动直接拒绝。
* local_owner 只允许配置的回环 URL；Nginx 和 FastAPI 两层 Host 白名单在 Session 签发前
  拒绝 DNS rebinding Host。
* MinIO 复用 S3 协议，要求真实 VersionId、固定版本读取、SHA-256/大小/类型验证和防覆盖。
* DatabaseOutboxQueue 复用 Outbox、WorkflowJob、lease、generation、重试和死信状态。
* 百炼 OpenAI-compatible 摘要/embedding 适配器；固定 1024 维、有限数值和范数校验，保存
  provider/model/dimension/document version；畸形成功响应统一进入可持久化重试和死信路径。
* Compose 明确串行 database-init、migration、local-init，并由 minio-init 开启 Versioning。
* `bootstrap-local` 幂等创建 User、Team、Owner Membership、Project 和首版正式策略。

## R14a：托管身份认证与管理 API

交付：

* 删除最终计划中的自建密码方案；仓库没有密码列、set-password CLI 或应用密码流程。
* Cognito Managed Login 使用 Authorization Code + PKCE S256，后端校验 state、nonce、issuer、
  audience、签名、auth_time 和 verified email 后交换授权码。
* Browser 只保存 HttpOnly Session Cookie；数据库只保存 Session SHA-256。
* Session idle 8 小时、absolute 7 天、recent auth 10 分钟；写请求使用 Session 绑定 CSRF。
* 首次登录只绑定管理员预建的相同 verified email User，不允许自助加入团队。
* 角色和成员关系每个请求实时读取；Session 撤销和成员删除立即生效。
* 策略发布、Plan 最终决定和 Owner High-risk 批准使用 Cognito `prompt=login` 近期认证。
* Web 管理 API 提供项目、设置历史、策略版本发布、Plan/Submission/Experiment 列表详情和
  固定 S3 VersionId 的短期下载地址。
* 策略发布不会覆盖 Context/Intent/约束旧版本，也不会修改既有 Manifest。
* revision `20260722_11` 增加 `User.cognito_sub`、`web_sessions`、`oidc_transactions`。

## R14b：四个 Web 页面

交付：

* React 19、TypeScript、Vite、TanStack Query、React Router 和 Lucide。
* 项目设置页显示生效版本、确认信息、约束和历史，Owner 可发布完整新版本。
* 计划审批页显示参数变化、当前动态审批状态、风险、命令和决定操作。
* 实验审核页显示短回执、强制展开 High/Critical 风险、Artifact 和权限动作。
* 实验查询页提供正式记录浏览及结构化条件先行的向量候选查询。
* 401 显示 Cognito 登录入口，428 自动进入 reauth；前端不持久化 Cognito Token。
* Desktop/Mobile 自适应，按钮和状态尺寸稳定，紧凑工作台布局，无 Dashboard 扩展。
* Playwright 在桌面和移动视口跑通四页导航并检查页面级横向溢出，设置页截图已人工复核。

## R14c：标准 OAuth + 预注册 MCP 客户端

交付：

* Streamable HTTP MCP 作为 Cognito OAuth Resource Server，stdio 保留本地 Token。
* MCP SDK 自动公开 RFC 9728 Protected Resource Metadata 和规范 401 challenge。
* Cognito OIDC discovery、PKCE S256、RFC 8707 resource audience 和七个 OAuth scope。
* JWT 之后再次检查本地预注册 client、单项目绑定、User sub、TeamMembership、scope 和 Grant。
* 本地 Client/Grant 即时撤销优先于 Access Token 自然过期。
* CLI 支持 register/revoke MCP OAuth client 和 revoke user grant，记录具体审计。
* R14 客户端固定使用完整七 scope，避免 FastMCP 全局 scope 门槛下的无效子集配置。
* 明确不实现 DCR 或 Client ID Metadata Documents。
* revision `20260722_12` 增加 `mcp_oauth_clients` 和 `mcp_oauth_grants`。

## R14d：AWS 演示部署

交付：

* 后端和 Web Dockerfile；后端按锁文件安装依赖并以非 root 用户运行。
* Terraform 定义 VPC、公私子网、NAT、CloudFront、WAF、私有 Web S3、HTTPS ALB、私网
  ECS API/MCP/Worker、ECR、CloudWatch、IAM 和 Secrets Manager。
* Artifact S3 开启 KMS、Versioning 和 Public Access Block；SQS Standard 配置 KMS 与 DLQ。
* Cognito User Pool 使用 Managed Login v2、仅管理员建用户、Web confidential client 和显式
  `mcp_clients` public client map。
* CloudFront 转发 API/MCP/OAuth metadata，ALB 规则要求随机 Origin Header，减少直接绕过。
* Cockroach Cloud 作为外部依赖，不在 Terraform 中创建。
* Terraform 1.9.8、AWS Provider 6.55 和 Random Provider 3.9 schema 验证通过。
* 后端/Web 镜像均完成实际构建和启动；后端 health 与 Web 根路径/SPA 深层路由返回 200。
* Server lock 已补齐 boto3 传递依赖；Web 构建上下文和 Nginx 延迟解析已完成运行级修复。

## R14e：验收与文档

交付：

* `scripts/verify_r14_deployment.py` 验证 Web、API、RFC9728、Cognito discovery、scope 与
  DCR 禁用边界，可选验证双角色 Session。
* `demo/r14/` 保存 BLOCKED、NEEDS_APPROVAL、结果、日志和说明演示输入。
* `R14_DEPLOYMENT.md`、`R14_SECURITY.md`、`R14_DEMO.md` 分别维护部署、安全和六场景演示。
* README、开发日志和框架图同步至 R14。
* 本地真实 CockroachDB 完成 revision 12 升级、跨版本降级和再升级验收。

## R16-L：本地百炼候选版加固

交付：

* 新增 `scripts/verify_r16_local.py`。默认验证本地后端装配、Alembic head、MinIO、Web/API、
  local_owner、项目初始化和 Session 撤销，不产生模型费用；显式 `--live-bailian` 时再验证真实
  Agent Run、模型调用 metadata、token/延迟、只读工具、正式引用、观测聚合和正式状态不变。
* 百炼真实协议覆盖摘要、1024 维 embedding、原生 Function Calling、严格结构化回答、三类
  只读工具选择和三类越权请求；测试只有在显式环境开关下访问模型服务。
* 百炼工具选择回合不再与 `json_object` 混用；Provider 通过能力字段声明是否需要独立严格
  最终回合。运行时保留每次调用审计，并继续由 Pydantic 和 evidence validator 拒绝无效引用。
* 百炼 SSE 在收到终态 `finish_reason` 后允许缺少 `[DONE]` 的干净 EOF；没有终态标记的截断仍
  归一化为可重试错误。HTTPX 增加 SOCKS 可选依赖，兼容本机显式代理环境。
* 新增真实 CockroachDB Agent 队列测试，覆盖双 Worker 唯一 claim、lease 过期接管、generation
  阻止旧 Worker、最大重试与 DEAD_LETTER，并检查消息、引用和工具调用不重复。
* 真实 MinIO 测试可直接读取不入库的 `.env.local`，仍支持测试专用环境变量覆盖。Web 单元、
  lint、build 与桌面/移动 Playwright 保持通过。
* 本轮没有迁移、Prompt 版本、工具目录、正式状态机、权限或确认事务变更；数据库 head 仍为
  `20260727_23`。

## R17a：外部 Coding Agent 协作入口

交付：

* revision `20260728_24` 为 Agent Thread 增加 `WEB/EXTERNAL_MCP` 来源、任务创建幂等键和初始
  正式策略快照；Run 可分别绑定 Web Session、数据库 MCP Token 或本地 OAuth Grant。
* 新增 `external_agent_task_start`、`external_agent_ask`、`external_agent_task_get`。任务启动先
  返回确定性 `ProjectContextBundle`，百炼回答由现有 Agent Worker 异步处理。
* `r17a-external-v1` 只开放项目、正式实验、比较统计、研究报告和候选研究记忆读取工具；没有
  Policy Draft、Action Proposal、审批、Manifest、Submission 或 Experiment 写工具。
* Worker 在每次尝试前重新检查 Token/Grant、OAuth Client、过期时间、项目绑定和 Membership；
  有效权限是创建时快照与当前权限的交集。
* MCP 任务归属真实用户，现有 Web Agent 页面显示来源、初始 Context/Intent 版本和过期警告，
  并允许用户继续对话；Web 续聊仍沿用外部只读 Prompt 和工具目录。

## R17b：版本化自然语言实验计划

交付：

* revision `20260728_25` 新增 `experiment_plans`、追加式 revisions、每 revision 一份成功审核和
  一份不可变人类决定；Agent Run 明确区分普通对话与计划审核。
* 外部 MCP 新增计划 submit/revise/get。写操作要求项目绑定 MCP 身份和
  `project:read + experiment:query + experiment:check`，不接受客户端传入 actor。
* 每个 revision 冻结完整正式策略快照/hash；严格解析可选配置证据，正式 LOCKED 冲突不能被
  LLM 或计划审批降低。策略漂移会在模型调用前终止审核，读取端也动态显示 `STALE`。
* `r17b-plan-review-v1` 只使用 R17a 既有只读目录。内部 Agent 最多追加两轮正文修订，不得改变
  配置、命令、Git、哈希或其他证据；存在用户决定或不可自动修复问题时停止。
* Web Agent 页增加实验计划工作区，展示高风险项、计划正文、证据、全部 revision 和原始 JSON。
  批准前逐项处理候选关键不变量，决定绑定 revision、review hash 和 approval digest。
* Owner 可决定项目内计划，Researcher 只能决定自己创建的计划；决定要求 CSRF、实时权限与
  recent-auth。计划批准仍不创建 Manifest，也不替代正式 Plan Check。

## R17c：三阶段关键不变量核对

交付：

* `experiment_check_plan` 可选绑定已批准计划决定，并重查项目归属、用户权限、精确 revision、
  Context/Intent 版本和 policy hash；经典调用不受影响。
* 结构化确认不变量严格比较；自然语言条件的满足声明保持 `LOCAL_ATTESTED`。违反关键边界为
  `BLOCKED`，缺失或无法判断为 `NEEDS_APPROVAL`，LLM 不能改变结果。
* schema v2 Manifest 将批准计划、decision/review/policy hash 和运行前检查加入不可变哈希；
  schema v1 历史记录继续可读。
* Submission 在既有分析状态机内复核最终固定版本配置和本地 Git/命令/checkpoint/不变量声明；
  关键偏离和缺失证据生成 `CRITICAL + blocking` 风险。
* Plan 和 Submission Web 页面显示阶段结论、来源和值；审核回执追溯到计划决定和 revision。

## R17d：v1.0.0 本地版发布加固

交付：

* `verify_r17d_local.py` 只允许专用验收项目，通过现有 Web/MCP/Worker 公共接口跑完整本地链。
* 真实百炼 Agent、摘要和 embedding 是发布强制门；每 Run 最多 5 次、整条链最多 20 次调用。
* 正向链携带完整最终运行证据，负向链修改 LOCKED protocol 并必须得到 `BLOCKED`。
* Host 白名单、CSRF、人工决定、固定 Artifact 版本、幂等重放和正式策略不变均纳入验收。
* DB Queue/Agent Worker 恢复并发测试与桌面/移动 Playwright 回归纳入发布清单。
* 包、API 与 Web 版本同步为 `1.0.0`；R17d 无数据库迁移，不自动 commit/tag。
* 真实发布门以 `qwen3.7-plus`、`text-embedding-v4`、MinIO 和 CockroachDB 跑通，9 次 Agent
  模型调用完成正式 Experiment；去敏 PASS 报告保存到 `artifacts/r17d-acceptance-report.json`。
* 百炼 Agent 关闭 thinking，并为外部只读 Run 使用裁剪 Schema。新 Run 使用
  `r17a-external-v2` 和 `r17b-plan-review-v2` 安全目录；v1 仅保留历史还原。

## R18a：内部 Agent 能力域隔离基础

交付：

* 保留单一有界 ReAct Runtime；Web Thread 可固定 `GENERAL/ANALYSIS/POLICY/RESEARCH/PROPOSAL`。
* 四个专业配置分别冻结 Prompt、5 至 9 个工具、输出 Schema 和摘要引用策略；模型不能切换域。
* 外部协作与计划审核维持现有独立配置，不增加 Supervisor 或模型路由调用。
* Proposal 的校验/影响/诊断前置步骤改为同 Run、同目标服务端硬门禁，拒绝请求也保存审计。
* revision 27 回填旧 Thread 为 `GENERAL`；旧 Prompt/目录版本继续支持历史 Run。
* 新增统一架构轨迹评测器；R18a 当时保留 `GENERAL` 默认，没有用静态结构判断替代真实模型效果。

## R18b：真实百炼架构验证

交付：

* 同一本地 CockroachDB 快照、同一 `qwen3.7-plus` 模型下完成 10 个 case、每 case 3 次的
  `GENERAL` 与专业配置成对评测，共 60 个 Run；两组任务、工具、Citation 和一致性均为 100%。
* 专业配置平均输入 Token 降低 45.91%，输出 Token 降低 39.43%，延迟降低 36.20%；模型调用
  从 3.0 增至 3.1，主要来自 Proposal 输出修复，未用成本指标交换安全指标。
* 真实 Submission Proposal 额外验证同 Run 诊断顺序、候选创建、Action Proposal Citation
  持久化和取消；未确认 Submission，正式对象快照保持不变。
* 修复空报告/记忆查询无 Evidence、专业输出 Citation Schema、数据库 Evidence 类型约束和
  IntegrityError 无效重试。Web 新会话默认切到 `ANALYSIS`，兼容 API 和旧 Thread 保持 `GENERAL`。
* Proposal 硬门禁和真实链均通过，不新增 Supervisor 或专用 Proposal Workflow。详细样本边界与
  指标见 `AGENT_ARCHITECTURE_REVIEW.md` 和 `R18B_BAILIAN_EVALUATION.md`。

## 唯一下一步

当前不继续重构 Agent 拓扑。后续只在积累更多正式 Experiment、Policy Draft、Research Report
和高风险 Submission 样本后扩大真实回归集；在没有更广数据前不删除 `GENERAL` 历史兼容模式。

## 每轮更新规则

完成修改后必须同步：

1. 本文件：交付状态和唯一下一步。
2. `DEVELOPMENT_LOG.md`：具体更新、修复、验证和遗留。
3. `ARCHITECTURE.md`：当前模块、数据关系和调用链。
4. README：使用者可见能力和启动/部署入口。
