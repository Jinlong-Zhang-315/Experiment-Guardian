# 内部 Agent 架构审计与优化路线

审计日期：2026-07-30  
当前实现：R18b 能力域隔离与真实百炼验证

## 结论

不改成 Supervisor 或自由协作的多 Agent，也不替换当前 `MODEL <-> TOOLS` 有界 ReAct
Runtime。现有内核具备调用次数、工具次数、墙钟时间、租约、generation、重试、严格输出、
Evidence/Citation 校验和完整审计，问题不在 LangGraph 只有两个节点。

真实问题集中在 Web 通用会话：`r15e-b-v1` 同时暴露 16 个查询、诊断、草稿、Proposal、
报告和记忆工具，System Prompt 叠加了各阶段规则。外部 Coding Agent 和实验计划审核已使用独立
Prompt、5 个只读工具、专用输入/输出约束和服务端硬检查，不需要再次拆分。

本轮采用会话级确定性能力域。所有能力域复用同一个 Runtime、Provider、持久化、Worker 和
Citation 机制，只替换 Prompt、工具目录、输出 Schema 和摘要引用策略：

| 能力域 | 工具数 | 主要能力 | 明确不可见 |
| --- | ---: | --- | --- |
| `GENERAL` | 16 | 历史兼容的全部通用能力 | 正式执行工具始终不存在 |
| `ANALYSIS` | 9 | 状态、实验分析、Plan/Submission 诊断、候选记忆 | 草稿、报告创建、Proposal |
| `POLICY` | 5 | 完整 Policy 草稿、校验、影响 | Proposal、报告、实验诊断 |
| `RESEARCH` | 9 | 实验比较、报告、候选记忆 | 草稿、Proposal、审批 |
| `PROPOSAL` | 8 | 读取冻结依据并准备三类候选 Proposal | 草稿写入、报告、正式执行 |
| 外部协作 | 5 | 当前策略、实验查询/比较/统计 | Web-only 报告/记忆和全部写能力 |
| 计划审核 | 5 | 当前策略及按需历史实验 | 草稿、Proposal 和全部正式写能力 |

旧会话和未指定能力域的 API 继续使用 `GENERAL`。R18b 真实成对评测通过质量门后，Web 新建
会话默认选择 `ANALYSIS`，用户仍可显式选择其他能力域；能力域保存在 Thread，并将实际值、
Prompt 和工具目录固化到 Run 快照和审计中。模型不能分类、切换或提升能力域。

## 八项审计回答

1. **现有隔离是否充分**：外部协作和计划审核充分；Web 通用对话不充分。Research Report 虽有
   专用输出校验，但仍与 Policy/Proposal 共用通用 Prompt 和目录。
2. **实际风险**：16 个工具并未直接形成越权，因为服务端权限和状态机仍会拒绝；主要风险是
   误选、漏选、冗余调用、输出修复和 Token/延迟增加。Proposal 前置读取此前主要由 Prompt
   约束，属于明确的可靠性缺口。
3. **继续共用的部分**：LangGraph ReAct 循环、模型 Provider Port、租约 Worker、上下文预算、
   滚动摘要、ToolCall/ModelCall/Event、Evidence/Citation 和最终持久化继续共用。
4. **独立配置边界**：实验分析、Policy 草稿、研究综合和 Proposal 准备需要独立配置；不为每个
   工具单独建 Agent。
5. **移出开放编排的流程**：权限、状态、新鲜度、风险资格、正式确认原本已在确定性服务中。
   本轮再将 Proposal 的同 Run、同目标前置读取顺序设为 Runtime 硬门禁。Proposal 正式执行仍
   只能走 Web recent-auth + RBAC + 幂等事务。
6. **是否需要大规模重构**：不需要。会话能力域提供确定性轻量路由，没有额外模型分类调用，
   也没有 Handoff 或循环委派。
7. **如何保持审计和恢复**：数据库队列、claim/lease/generation 和 Run 类型不变；每次模型调用
   仍冻结实际工具列表与输出 Schema hash。被编排门禁拒绝的 Proposal 也保存 FAILED ToolCall。
8. **如何证明更好**：仓库使用统一轨迹评测器，在相同模型、数据和重复次数下完成 60 Run
   成对对比；专业配置通过任务、工具、Citation、安全和一致性硬门后才成为 Web 新会话默认。

## Proposal 边界

当前 Proposal 不是正式操作。ActionProposalService 在准备时会重新读取正式对象、计算冻结
状态和 digest；确认时再次执行权限、recent-auth、版本、新鲜度、风险、幂等和事务检查。本轮
增加的前置顺序如下：

```text
Policy proposal     validate(draft) + impact(draft) -> prepare(draft)
Plan proposal       explain(plan_check)              -> prepare(plan_check)
Submission proposal diagnose(submission)             -> prepare(submission)
```

三个箭头均由服务端检查“同一 Run、前置调用已成功、目标 ID 相同”。模型跳步或混用目标时，
Proposal 不会创建，失败请求及原因会进入 ToolCall 和 Run 审计。正式确认链路没有变化。

## 评测门

`scripts/evaluate_agent_architecture.py` 接受基线和候选 JSON observation，统一计算：

* 任务成功率；
* 工具序列完全匹配率；
* 无效和冗余工具调用率；
* Citation 合规率；
* 非预期高风险工具调用次数；
* 输入/输出 Token、模型调用次数和延迟；
* 同一 case 多次执行的一致性。

示例命令：

```bash
python scripts/evaluate_agent_architecture.py \
  --baseline artifacts/agent-eval-general.json \
  --candidate artifacts/agent-eval-profiles.json \
  --output artifacts/agent-eval-comparison.json
```

候选只有在任务成功、工具选择、Citation 和一致性不低于基线，无效/冗余调用不高于基线，且
高风险错误为 0 时，才进入默认切换评审。Token 和延迟会报告，但不能用成本下降交换安全退化。
每个核心 case 至少重复 3 次；真实百炼评测仍需显式环境开关，不进入无凭据的默认 CI。

## R18b 真实评测结论

使用本地 CockroachDB 的同一数据快照和真实百炼 `qwen3.7-plus`，对 10 个查询、诊断、策略、
研究和 Proposal 边界 case 各重复 3 次，形成 30 组配对、60 个 Agent Run：

| 指标 | `GENERAL` | 专业配置 |
| --- | ---: | ---: |
| 任务成功率 | 100% | 100% |
| 工具序列完全匹配率 | 100% | 100% |
| Citation 合规率 | 100% | 100% |
| 重复一致性 | 100% | 100% |
| 无效/冗余调用率 | 0% / 0% | 0% / 0% |
| 高风险错误 | 0 | 0 |
| 平均输入 Token | 20,443.7 | 11,058.4 |
| 平均输出 Token | 1,215.4 | 736.1 |
| 平均模型调用 | 3.0 | 3.1 |
| 平均延迟 | 25,722.8 ms | 16,412.2 ms |

专业配置在不降低硬质量指标的情况下，将平均输入 Token 降低 45.91%、输出 Token 降低 39.43%、
延迟降低 36.20%。单独的真实 Submission Proposal 链中，两种配置都准确执行
`submission_diagnose_v1 -> action_proposal_prepare_submission_decision_v1`，Citation 成功入库，
评测 Proposal 随后通过正式取消 API 清理，未执行正式操作。

评测中发现并修复了空 Research Report/Memory 查询缺少 Evidence、专业最终输出顶层 Citation
偶发缺失、Citation 数据库约束未包含 `CANDIDATE_DRAFT/ACTION_PROPOSAL`，以及确定性数据库完整性
错误被错误重试的问题。完整结果和样本边界见 `R18B_BAILIAN_EVALUATION.md`，去敏原始指标见
`artifacts/r18b-agent-architecture-live.json` 与 `artifacts/r18b-proposal-workflow-live.json`。

结论：Web 新会话默认切到 `ANALYSIS`；现有 Proposal 硬门禁已满足当前证据，不新增 Workflow
节点。样本多样性仍有限，因此保留 `GENERAL` API 缺省值和所有历史配置，不删除兼容路径。
不规划 Supervisor、多 Agent Handoff、自动 SQL、自动训练或新的正式操作权限。
