# Experiment Guardian v1.0.0 本地版发布验收

更新时间：2026-07-29

R17d 不增加业务能力。它把 R17a-R17c 的外部任务、自然语言计划、人工决定、三阶段不变量、
正式 Plan Check、schema v2 Manifest、MinIO Artifact、数据库 Worker、百炼摘要/embedding 和
正式实验确认串成一个可重复的发布门。

2026-07-29 本机发布门已通过：真实 `qwen3.7-plus` Agent/摘要、`text-embedding-v4`、MinIO 和
CockroachDB 共完成 9 次 Agent 模型调用及完整正式实验闭环。去敏结果保存在
`artifacts/r17d-acceptance-report.json`。

## 发布范围

`v1.0.0` 当前定义为单机本地版：CockroachDB、MinIO、数据库队列、FastAPI、Web、MCP 和两个
Worker 由 Compose 管理，摘要、embedding 和内部治理 Agent 使用阿里云百炼。AWS/Cognito/SQS/
Bedrock 适配器与 Terraform 继续保留，但真实云部署不属于本地版发布通过声明。

发布声明仍是“提高实验一致性、可追溯性和风险可见性”，不表示系统已经证明真实训练行为或
实验结果正确。

## 准备独立验收环境

不要在日常项目上运行发布验收。创建不入库的 `.env.r17d`，开启真实 Agent：

```bash
cp .env.local .env.r17d
# 编辑 .env.r17d：
# AGENT_ENABLED=true
# AGENT_PROVIDER=bailian
# BAILIAN_AGENT_MODEL=<支持 Function Calling 的模型>
# AGENT_MAX_MODEL_CALLS=5
# AGENT_MAX_WALL_SECONDS=300
# BAILIAN_API_KEY/BASE_URL/SUMMARY_MODEL/EMBEDDING_MODEL 必须是真实可用配置

EG_ENV_FILE=.env.r17d docker compose --env-file .env.r17d \
  --profile agent up -d --build
EG_ENV_FILE=.env.r17d docker compose --env-file .env.r17d ps -a
```

`EG_ENV_FILE` 只改变容器读取的环境文件；未设置时 Compose 仍默认使用 `.env.local`。它不会使
业务层动态切换 provider，也不会把凭据写入镜像。

## 初始化专用项目和 Token

```bash
BOOTSTRAP_JSON=$(EG_ENV_FILE=.env.r17d docker compose --env-file .env.r17d run --rm api \
  experiment-guardian-admin bootstrap-local \
  --project-config examples/r17d-acceptance-project.json)
PROJECT_ID=$(printf '%s' "$BOOTSTRAP_JSON" | jq -r .project_id)

TOKEN_JSON=$(EG_ENV_FILE=.env.r17d docker compose --env-file .env.r17d run --rm api \
  experiment-guardian-admin issue-mcp-token \
  --owner-email owner@example.com --project-id "$PROJECT_ID" \
  --token-name r17d-release-gate)
export MCP_ACCESS_TOKEN=$(printf '%s' "$TOKEN_JSON" | jq -r .access_token)
```

若 `LOCAL_OWNER_EMAIL` 不是 `owner@example.com`，两个命令都改为实际邮箱。`bootstrap-local` 按
邮箱和项目配置哈希幂等；Token 原文只在签发时显示，发布报告不会保存它。

## 必须通过的真实闭环

```bash
python scripts/verify_r17d_local.py \
  --project-id "$PROJECT_ID" \
  --base-url http://127.0.0.1:5173 \
  --env-file .env.r17d \
  --max-agent-model-calls 20 \
  --report artifacts/r17d-acceptance-report.json
```

若 `.env.r17d` 使用了自定义 `WEB_PORT`，同步修改 `--base-url`。脚本会真实产生少量模型费用和
追加式验收记录，并严格检查：

* Nginx 与 FastAPI Host 白名单、local_owner Session 和 CSRF；
* 外部 MCP 任务的正式快照、真实百炼回答和 Citation；
* 真实百炼计划审核、最多两轮自动修订、Owner 明确决定和决定幂等；
* 绑定决定的 PASS Plan Check，以及修改 LOCKED protocol 的 BLOCKED 负例；
* schema v2 Manifest、CONFIG/RESULT/LOG 的真实 MinIO 上传和固定版本验证；
* 数据库 Worker 的摘要、1024 维 embedding、审核回执和人工确认；
* 正式 Experiment 查询、关键写操作幂等重放和正式策略不变；
* 本次外部任务与最多三轮计划审核的 Agent 模型调用总数不超过 20；每个 Run 最多 5 次，最后
  一次只用于严格 JSON 的有界修复。

百炼治理 Agent 显式发送 `enable_thinking=false`，避免 reasoning token 挤占结构化 JSON 输出。
外部新 Run 使用 `r17a-external-v2` Prompt/目录；v2 只暴露项目、正式实验、比较和统计读取，
不会向 MCP 身份展示仅支持 Web Session 的研究报告或候选记忆工具。v1 只保留用于历史审计。

任何一项失败时报告为 `FAILED`，不得创建 `v1.0.0` tag。模型调用失败不能以 Mock 结果替代。

## 自动化回归门

```bash
env PYTHONPATH=src:. pytest -q
ruff check .
mypy src

cd web
npm run lint
npm test -- --run
npm run build
npm run test:e2e
```

没有浏览器系统库的宿主机可使用与仓库 Playwright 版本一致的官方容器运行最后一项；这只是
浏览器运行环境替代，不改变测试或跳过用例：

```bash
docker run --rm --ipc=host \
  -v "$PWD:/work" -w /work \
  mcr.microsoft.com/playwright:v1.61.1-noble npm run test:e2e
```

真实基础设施定向门：

```bash
RUN_MINIO_INTEGRATION=1 pytest -q tests/integration/test_minio_storage.py

RUN_COCKROACH_INTEGRATION=1 \
DATABASE_URL='cockroachdb+psycopg://root@127.0.0.1:26257/defaultdb?sslmode=disable' \
pytest -q tests/integration/test_plan_check_cockroach.py \
  tests/integration/test_agent_local_cockroach.py
```

端口应按 `.env.r17d` 的 `COCKROACH_SQL_PORT` 调整。默认自动化中的百炼 HTTP mock 继续验证畸形
响应、超时、维度和有限数值；真实百炼发布门由 `verify_r17d_local.py` 承担，不能跳过。

## 发布与回滚

1. 保存已去敏的 `artifacts/r17d-acceptance-report.json` 和测试摘要。
2. 确认 Alembic head 为 `20260728_26`。R17d 没有新增迁移。
3. 提交工作树后再创建 `v1.0.0` tag；本实现过程不自动 commit 或 tag。
4. 应用回滚使用前一镜像；已经写入的计划、Manifest、Submission、Experiment 和 Artifact 不
   原地删除或改写。存在 schema v2 Manifest 时不要降级 revision 26。

本地版未提供自动备份、MinIO 多节点高可用或 CockroachDB 多节点运维。长期运行前应由部署者
自行建立卷备份、恢复演练、磁盘和模型费用监控。
