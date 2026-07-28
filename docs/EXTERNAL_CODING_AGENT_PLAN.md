# 外部 Coding Agent 协作开发路线

更新时间：2026-07-28
需求基线：`add Requirement Analysis.md`
当前状态：R17b 已完成，计划共四个阶段，不扩大正式治理业务范围。

## 总体路线

| 阶段 | 目标 | 状态 |
| --- | --- | --- |
| R17a | MCP 任务入口、正式上下文快照、带引用问答、Web 可见 | 完成 |
| R17b | 版本化自然语言实验计划、审核、最多两轮自动修订、用户审批 | 完成 |
| R17c | 计划、运行前、结果提交三阶段关键不变量核对 | 下一步 |
| R17d | 本地端到端加固、恢复、安全验收和首版发布 | 计划 |

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

## R17c 下一步唯一目标

只实现计划、运行前和结果提交三个时点的关键不变量核对与来源展示。优先复用 R17b 批准快照、
现有 Plan Check、Run Manifest 和 Submission，不增加自动训练、自动改代码或新的正式审批层。

## 持续边界

* 外部消息、报告、记忆和模型输出都是不可信输入。
* 内部 Agent 不获得任意 SQL、Shell、文件系统或正式写工具。
* 候选不变量不能由模型自行提升为硬约束。
* `LOCAL_ATTESTED` 不描述为服务端已经验证。
* 外部 Agent 的工程自主权不覆盖数据集、协议、baseline 等经用户确认的关键边界。
