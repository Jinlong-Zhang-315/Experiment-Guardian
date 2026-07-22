# R14 Owner / Researcher 最终演示

更新时间：2026-07-22

## 演示前准备

1. AWS 资源、revision `20260722_12`、API/MCP/Worker 和 Web 已部署。
2. Cognito 中有一名 Owner 和一名 Researcher；CockroachDB 中存在同邮箱 User 与成员关系。
3. 远程 MCP 客户端已同时在 Cognito 与 `mcp_oauth_clients` 预注册，并绑定演示 Project。
4. Artifact Bucket Versioning/KMS、SQS/DLQ 和 Bedrock 模型访问可用。
5. 本地 Agent 已配置 MCP Resource URL，不配置静态 `MCP_ACCESS_TOKEN`。

先执行：

```bash
python scripts/verify_r14_deployment.py --base-url https://guardian.example.com
```

## 场景 1：统一上下文

Owner 使用 Cognito Managed Login 进入项目设置页，发布或确认：

* `dataset.protocol = "40/20"`，`LOCKED`；
* `model.checkpoint = "baseline.pt"`，`LOCKED`；
* `model.fusion = 0.2`，`EXPERIMENT_VARIABLE`；
* `model.backbone = "shift-gcn"`，`APPROVAL_REQUIRED`。

页面必须显示 Context/Intent 版本、确认人、生效时间和修改原因。本地 Agent 通过 OAuth MCP
调用 `project_get_context`，返回相同版本和已确认约束。

验收证据：页面截图、MCP 返回的 Context/Intent ID 和版本，不记录 Cookie 或 Token。

## 场景 2：阻止错误实验

Researcher 用 `demo/r14/blocked-config.yaml` 调用 `experiment_check_plan`。该配置允许 fusion
从 0.2 改到 0.3，但把 Locked protocol 改成 48/12。

预期：

```text
fusion: allowed experiment variable
protocol: confirmed LOCKED violation
check_result: BLOCKED
manifest creation: forbidden
```

页面显示原值、新值、来源和影响，不显示“实验已验证正确”。

## 场景 3：审批型修改

Researcher 使用 `demo/r14/approval-config.yaml`，只修改 backbone。

预期 `NEEDS_APPROVAL/PENDING`。Owner 打开计划审批页；若最近认证超过 10 分钟，首次批准
返回 428 并跳转 Cognito `prompt=login`，成功后完成批准。Researcher 随后创建 Run Manifest。

验收证据：Plan Check、不可修改 ApprovalRecord、Manifest hash 及其 Context/Intent 版本。

## 场景 4：提交与云端分析

用户自行运行实验。Agent 上传：

* Run Manifest；
* 实际配置；
* `demo/r14/run.log`；
* `demo/r14/result.json`；
* `demo/r14/note.md`；
* 本地环境与 Git 声明。

Agent 依次调用 `submission_prepare` 和 `submission_finalize`。云端验证 S3 VersionId、大小、
SHA-256、配置/结果语法、Manifest 关联和重复记录，然后异步生成风险、摘要、embedding 与
确定性审核回执，最终状态为 `NEEDS_REVIEW`。

验收证据必须区分 `CLOUD_VERIFIED` 与 `LOCAL_ATTESTED`。

## 场景 5：人工确认

Researcher 在实验审核页查看目标、允许变化、关键结果和最高风险。Low/Medium 默认折叠，
High/Critical 强制展开。Researcher 确认无 High/Critical 的自有草稿，系统在一个数据库事务
中写入 Experiment、Metric、Memory、Artifact 关联、ApprovalRecord、AuditLog 和幂等回执。

额外负例：Critical 风险没有批准按钮；High 风险只能由 Owner 确认且需要近期认证。

## 场景 6：团队查询与撤销

Owner 在实验查询页按协议进行结构化过滤和向量候选查询，打开正式记录后可追溯到
Submission、Manifest、Plan Check、Intent、Context、Git commit 和已验证 Artifact 版本。

最后撤销 Researcher 本地 MCP Grant。使用仍未过期的 Cognito Access Token 再次访问 MCP，
预期 401；这证明本地撤销状态优先于 Token 自然过期。

## 演示完成标准

* 六个场景全部完成；
* Owner/Researcher 权限负例成立；
* Session Cookie、Cognito Token、数据库密码和预签名 URL 未进入演示日志；
* 所有措辞坚持“提高一致性、可追溯性和风险可见性”，不承诺实验一定正确；
* 不展示自动训练、自动改代码、自动批准或 baseline 自动晋升。
