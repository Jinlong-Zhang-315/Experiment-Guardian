# 正式策略双表示设计

更新时间：2026-07-23

## 事实边界

`ProjectContext`、`ExperimentIntent` 和 `ProtectedParameter` 是唯一正式事实源。Plan Check、
Approval、Run Manifest、Submission 和 Experiment 确认不读取 `PolicyNarrative.content`。

`policy_narratives` 只保存派生阅读表示：

```text
project_id
context_id + context_version
intent_id + intent_version
source_hash
format = MARKDOWN
generator = DETERMINISTIC_TEMPLATE
generator_version = policy-narrative-v1
status = READY | FAILED
content / error
generated_by / generated_at
```

API 根据当前结构化来源重新计算哈希，并对数据库状态扩展出四种展示状态：

* `READY`：来源哈希、Context/Intent 版本和模板版本一致，可以展示。
* `FAILED`：本次派生失败，结构化正式版本仍然有效。
* `STALE`：来源或模板已变化，旧内容被隐藏，不能无提示展示。
* `MISSING`：升级前的旧 Context 尚未生成派生表示。

Owner 可以调用重生成接口。该操作只更新派生记录并写 AuditLog，不能修改正式结构化对象。

## 生成策略

本轮采用确定性模板，不调用 Bedrock 或百炼。原因：

* 字段和保护级别可以完整、稳定地从结构化内容映射；
* 不存在幻觉、风险弱化或提示词注入改变正式含义的问题；
* 不需要新增 Worker Job、模型重试和部署凭据；
* 同一来源和模板版本可以得到相同文本，便于审计。

模板覆盖项目目标、数据集、协议、主线模型、基线、非目标、Intent、受控变量、预期输出、
接受标准、LOCKED、APPROVAL_REQUIRED、EXPERIMENT_VARIABLE、关键决策和弃用事项。允许实验
参数会明确说明“允许”不等于“推荐”。

渲染异常会写入 `FAILED`。结构化发布继续提交，Web/MCP 显示失败原因；Owner 后续可以重试。
数据库本身不可用时仍按原有事务规则失败，因为此时无法可靠提交任何正式版本或审计记录。

## 兼容性

revision `20260723_14` 只新增表，不修改既有 Context、Intent、Constraint 或 Manifest。
升级前数据不会被猜测性回填：读取时返回 `MISSING`，Owner 可按原版本确定性重生成。

`ProjectContextBundle.human_readable` 是新增可选字段，因此旧幂等响应和旧客户端仍能解析。
`project_get_context` 的既有结构化字段和七个 MCP 工具没有删除或改名。

## CockroachDB 能力取舍

### Distributed Vector Indexing

本轮不接入。CockroachDB 支持 `VECTOR` 和带 prefix columns 的分布式 ANN 索引，适合在大规模
数据上先按 project、状态或协议精确过滤再做相似度排序：
<https://www.cockroachlabs.com/docs/stable/vector-indexes>

当前真实消费路径只有正式 Experiment `Memory` 查询；它已经按 project、protocol、status、
model、seed 等结构化条件筛选后排序。把 Context/Intent 说明混入该索引会混合不同记录类型，
而新增独立策略搜索 API、embedding Job 和权限契约超出本轮展示需求。

后续只有在出现“历史 Context/Intent 语义搜索”用例后再接入，位置应为独立
`PolicyNarrativeEmbedding`：

```text
project/status/protocol/context_version structured filters
-> matching policy narrative candidates
-> vector ordering
-> CANDIDATE_EVIDENCE
```

embedding 失败时仍返回结构化历史列表，不影响策略发布或精确查询。向量索引不得参与约束判断。

### CockroachDB Agent Skills

当前 Codex 环境没有安装 CockroachDB Agent Skills，本轮未把它作为工具或依赖。现有迁移测试、
SQLAlchemy metadata、SQLite 升降级和真实 CockroachDB integration gate 已承担 Schema、事务和
兼容性检查。Agent Skills 未来可用于迁移、索引、事务重试和诊断审查，但只能作为开发辅助：
<https://www.cockroachlabs.com/docs/v26.2/agent-skills>

### CockroachDB Cloud Managed MCP

当前数据库是本地 CockroachDB，Cloud Managed MCP 不能作为本地运行依赖。未来迁移到
CockroachDB Cloud 后，可以为只读诊断、Schema 检查、慢查询和审计核对配置独立受限 Agent：
<https://www.cockroachlabs.com/docs/cockroachcloud/connect-to-the-cockroachdb-cloud-mcp-server>

该 Agent 不得直接写正式 Context、Intent、Constraint、Manifest、Submission 或 Experiment；
业务 Agent 仍必须使用 Experiment Guardian MCP 和应用权限边界。

### ccloud CLI

本轮不涉及 CockroachDB Cloud 集群创建、扩缩容或 AWS 部署调整，因此不引入 `ccloud`。
它只在未来 Cloud 环境配置和运维验收时有价值：
<https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-reference>
