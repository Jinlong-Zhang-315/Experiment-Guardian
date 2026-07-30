# R18b 真实百炼 Agent 架构评测

评测日期：2026-07-30  
模型：阿里云百炼 `qwen3.7-plus`  
数据：本地 CockroachDB 固定项目快照  
候选：R18a `ANALYSIS/POLICY/RESEARCH/PROPOSAL` 专业配置

## 目的与边界

本评测验证专业运行配置是否能在不改变有界 ReAct Runtime、业务服务、权限和正式状态机的前提
下，降低无关上下文并保持工具选择与引用可靠性。它不验证真实训练行为正确，也不代表 AWS、
Cognito、SQS 或 Bedrock 云线路已验收。

固定快照包含 1 个正式 Experiment、4 个 Plan Check、2 个 Submission、0 个活动 Policy Draft、
0 个 Research Report。10 个 case 覆盖项目状态、当前策略、待办、实验详情、Plan 诊断、空报告/
记忆查询、研究证据和 Proposal 边界；每个 case 对 `GENERAL` 与目标专业配置各运行 3 次。

## 主评测结果

共执行 60 个真实 Agent Run。两组任务成功率、工具序列完全匹配率、Citation 合规率和重复一致性
均为 100%，无效/冗余工具调用率均为 0%，高风险错误为 0。

| 平均指标 | `GENERAL` | 专业配置 | 变化 |
| --- | ---: | ---: | ---: |
| 输入 Token | 20,443.7 | 11,058.4 | -45.91% |
| 输出 Token | 1,215.4 | 736.1 | -39.43% |
| 模型调用数 | 3.0 | 3.1 | +3.33% |
| 延迟 | 25,722.8 ms | 16,412.2 ms | -36.20% |

`PROPOSAL` 边界 case 的专业配置平均需要一次输出修复，因此整体模型调用数略升；硬质量、安全和
延迟指标没有退化。该配置目前仍为用户显式选择，不据此扩大其正式权限。

## Proposal 实链

另以一个 `NEEDS_REVIEW` Submission 执行 2 个真实 Run。两种配置均严格调用：

```text
submission_diagnose_v1
-> action_proposal_prepare_submission_decision_v1
```

两者任务、工具和 Citation 指标均为 100%。专业配置输入/输出 Token 为 22,225/996，延迟
20,957 ms；`GENERAL` 为 38,439/1,755，延迟 34,580 ms。候选 Proposal 通过正式取消接口清理，
Submission 未确认、Experiment 未创建，评测前后正式 Context、Intent、Plan Check、Submission
和 Experiment ID 快照一致。

## 发现与修复

1. 空 Research Report/Memory 查询原先没有 Evidence，导致正确的“无结果”回答无法满足 Citation
   规则；现在返回明确的查询级 `ANALYSIS` Evidence。
2. 百炼偶发在专业 Research 输出中省略顶层 `citations`；Prompt 和 Provider JSON Schema 现在都
   强制顶层引用等于各 section 引用并集。
3. 数据库旧 Citation check constraint 未包含已在领域层使用的 `CANDIDATE_DRAFT` 和
   `ACTION_PROPOSAL`；revision 28 对齐六种 Evidence，并对不安全降级 fail closed。
4. SQLAlchemy `IntegrityError` 原先会触发完整模型重试；现在一次失败后以去敏的
   `DATA_INTEGRITY_ERROR` 结束，不泄露 SQL 或参数。
5. 质量门原先允许“候选和同样失败的基线持平”；现在除无退化外，还要求任务和工具选择至少
   95%、Citation 100%、一致性至少 90%、高风险错误为 0。

## 决策

专业配置通过预设质量门，因此 Web 新会话默认选择 `ANALYSIS`。API 未显式指定能力域和所有旧
Thread 继续使用 `GENERAL`，避免兼容性和审计还原问题。当前 Proposal 硬门禁及实际链均通过，
不引入额外 Supervisor、Handoff 或 Proposal Workflow。

样本仍缺少活动 Policy Draft、已生成 Research Report、多 Experiment 统计及不同风险等级的
Submission。后续扩大这些样本的回归覆盖后，再判断是否进一步调整默认域或移除通用兼容路径。

去敏机器报告：

* `artifacts/r18b-agent-architecture-live.json`
* `artifacts/r18b-proposal-workflow-live.json`

配套回归结果：Python 默认套件 340 项通过、19 项显式跳过；Web 17 项通过；SQLite 迁移往返和
隔离 CockroachDB 完整迁移/事务链通过；Ruff、mypy、ESLint 与 production build 通过。
