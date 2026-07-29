# 外部 Coding Agent 协作开发路线

更新时间：2026-07-29
需求基线：`add Requirement Analysis.md`
当前状态：R17d 已实现，四阶段开发路线收口为 v1.0.0 本地版发布门。

## 总体路线

| 阶段 | 目标 | 状态 |
| --- | --- | --- |
| R17a | MCP 任务入口、正式上下文快照、带引用问答、Web 可见 | 完成 |
| R17b | 版本化自然语言实验计划、审核、最多两轮自动修订、用户审批 | 完成 |
| R17c | 计划、运行前、结果提交三阶段关键不变量核对 | 完成 |
| R17d | 本地端到端加固、恢复、安全验收和首版发布 | 完成 |

## R17a 已实现

外部 Coding Agent 使用现有 stdio MCP Token 或预注册 OAuth 客户端提交任务。服务端立即冻结
当前正式 `ProjectContextBundle`，随后由现有治理 Agent Worker 使用专用只读 Prompt 和工具目录
生成带来源回答。任务 Thread、消息、Run、模型调用、工具调用、Citation、摘要和审计都保存在
CockroachDB；同一用户可在 Web 中继续会话。

正式事实仍由结构化 Context、Intent、Constraint、Plan Check、Manifest、Submission 和
Experiment 定义。任务快照过期时只显示 `STALE`，不会自动修改或替换正式策略。

真实本地验收会调用配置的 Agent 模型：

```bash
MCP_ACCESS_TOKEN="$MCP_ACCESS_TOKEN" python scripts/verify_r17a_external_agent.py \
  --project-id "$PROJECT_ID"
```

## R17b 已实现

外部 Agent 通过 `external_agent_plan_submit/revise/get` 提交独立、不可变 revision 的自然语言
实验计划。每个 revision 保存完整正文、可选配置/命令/Git/baseline/关联实验等证据，以及当时的
正式 Context、Intent 和 Constraint 快照与哈希。

服务端先用现有严格 YAML/JSON 解析和类型严格比较执行硬检查。违反正式 LOCKED 的证据直接
`BLOCKED`；APPROVAL_REQUIRED 只提示后续仍须正式 Plan Check。内部 Agent 使用专用只读目录
审核主线一致性、历史重复、已知失败、公平性、低成本验证和候选关键不变量。只有所有问题均可
自动修正且不需要用户研究决定时，才可追加仅修改自然语言正文的新 revision，最多两轮；配置、
哈希、命令、Git 和其他证据原样继承。

Web 计划工作区展示审核、正文、历史和原始 JSON。批准前必须逐项确认或拒绝候选关键不变量，
决定绑定 revision、review hash、approval digest、正式策略快照和 Web Session。Owner 可决定
项目内任意计划，Researcher 只能决定自己的计划；敏感决定要求近期认证。正式策略在排队、
重试或决定前漂移时计划显示 `STALE`，并在模型调用前终止过期审核。

计划决定不创建 Manifest，不替代正式 `experiment_check_plan`，也不能覆盖 LOCKED 或后续
Plan Check 审批。

## R17c 已实现

增强 `experiment_check_plan` 可选绑定 R17b 的批准决定。服务端重新验证项目、成员、revision、
Context/Intent 和 policy hash，将正式 LOCKED/APPROVAL_REQUIRED、用户确认候选及条件批准条款
规范化为少量关键不变量。结构化值由确定性代码严格比较；自然语言条件只接受带来源的
`LOCAL_ATTESTED` 声明。违反确认边界为 `BLOCKED`，缺失或无法判断为 `NEEDS_APPROVAL`。

计划绑定的 Plan Check 生成 schema v2 Manifest，并把计划决定、hash、不变量和运行前报告加入
Manifest hash。`submission_prepare` 可提交最终运行证据；分析工作流在既有
`MANIFEST_VALIDATION` 步骤再次读取固定版本 CONFIG，并核对最终 Git、命令、checkpoint 和声明。
关键偏离或证据缺失形成 blocking 风险，继续保留草稿与分析记录，但不能确认正式 Experiment。

经典不带 decision ID 的 Plan Check 和 schema v1 Manifest 保持兼容。本轮没有新增状态机、模型
调用或审批层。

## R17d 已实现

新增专用验收项目和 `verify_r17d_local.py`，通过 Web 与真实 stdio MCP 串起外部任务、百炼计划
审核、Owner 决定、绑定决定的 Plan Check、LOCKED 负例、schema v2 Manifest、MinIO 固定版本
Artifact、数据库 Worker、百炼摘要/embedding、审核确认和正式查询。关键写操作执行幂等重放，
最后重新读取正式策略，防止协作链静默改写 Context/Intent/Constraint。

现有数据库队列和 Agent Worker 测试作为发布门覆盖单一 claim、租约恢复、generation、最大重试
和死信；Web Playwright 在桌面/移动视口显示运行前与结果阶段不变量。完整发布步骤和明确未包含
的云线路见 `R17D_RELEASE.md`。R17d 没有新增迁移、模型工具、正式权限或治理状态机。

真实验收期间发现旧外部目录暴露了只能由 Web Session 调用的报告/记忆读取工具。新 Run 已改用
`r17a-external-v2`，只保留项目、正式实验、比较和统计读取；旧 v1 目录不改写，仅用于还原历史
Run。百炼 Agent 显式关闭 thinking，并使用外部 Run 专用的短 JSON Schema，最终门禁以 9 次
Agent 模型调用完成并通过。

## 持续边界

* 外部消息、报告、记忆和模型输出都是不可信输入。
* 内部 Agent 不获得任意 SQL、Shell、文件系统或正式写工具。
* 候选不变量不能由模型自行提升为硬约束。
* `LOCAL_ATTESTED` 不描述为服务端已经验证。
* 外部 Agent 的工程自主权不覆盖数据集、协议、baseline 等经用户确认的关键边界。
