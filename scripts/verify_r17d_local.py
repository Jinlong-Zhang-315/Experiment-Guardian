"""执行 v1.0.0 本地版发布候选的真实公共接口端到端验收。

该脚本会调用真实百炼 Agent、创建计划/Plan Check/Manifest/Submission/Experiment，
并向 MinIO 上传小型验收 Artifact。它只适用于隔离的 R17d 验收项目，不应指向日常项目。
原始 MCP Token 只能通过环境变量传入，报告不会保存 Cookie、CSRF 或 Token。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
from dotenv import dotenv_values
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy.engine import make_url

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from experiment_guardian.core.config import Settings  # noqa: E402
from experiment_guardian.domain.enums import ReviewEligibility, RiskSeverity  # noqa: E402

ACCEPTANCE_PROJECT_NAME = "Experiment Guardian R17d Acceptance"
TERMINAL_AGENT_STATUSES = {"SUCCEEDED", "FAILED", "DEAD_LETTER"}
FAILED_PLAN_STATUSES = {
    "NEEDS_USER_INPUT",
    "REVIEW_FAILED",
    "STALE",
    "REJECTED",
    "CHANGES_REQUESTED",
}
FAILED_WORKFLOW_STATUSES = {"RETRYABLE_FAILURE", "TERMINAL_FAILURE"}
CONFIG_CONTENT = (
    "dataset:\n"
    "  protocol: 40/20\n"
    "model:\n"
    "  backbone: shift-gcn\n"
    "  fusion: 0.3\n"
)
BLOCKED_CONFIG_CONTENT = CONFIG_CONTENT.replace("40/20", "48/12")
RUN_COMMAND = "python train.py --config fusion.yaml"
GIT_COMMIT = "a1b2c3d4"
CHECKPOINT = "checkpoints/baseline.pt"
CONDITION = "最终运行必须使用已批准配置、命令和 Git commit。"
RESULT_CONTENT = json.dumps(
    {
        "schema_version": 1,
        "status": "COMPLETED",
        "metrics": {"top1": 0.83},
        "failure_reason": None,
    },
    ensure_ascii=False,
    separators=(",", ":"),
).encode("utf-8")
LOG_CONTENT = b"R17d acceptance run completed successfully.\n"


def _json_object(response: httpx.Response, *, label: str) -> dict[str, Any]:
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError(f"{label} 未返回 JSON object")
    return value


def _items(client: httpx.Client, url: str) -> list[dict[str, Any]]:
    value = _json_object(client.get(url), label=url)
    items = value.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError(f"{url} 的 items 无效")
    return items


def _tool_payload(result: object) -> dict[str, Any]:
    if bool(getattr(result, "isError", False)):
        raise RuntimeError(f"MCP 工具调用失败: {getattr(result, 'content', None)}")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None)
    if not isinstance(content, list) or not content:
        raise RuntimeError("MCP 工具没有返回内容")
    raw_text = getattr(content[0], "text", None)
    if not isinstance(raw_text, str):
        raise RuntimeError("MCP 工具没有返回 JSON 文本")
    value = json.loads(raw_text)
    if not isinstance(value, dict):
        raise RuntimeError("MCP 工具返回值不是 JSON object")
    return value


async def _call(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
    *,
    timeout_seconds: float = 60,
) -> dict[str, Any]:
    return _tool_payload(
        await session.call_tool(
            name,
            arguments,
            read_timeout_seconds=timedelta(seconds=timeout_seconds),
        )
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _condition_id(condition: str) -> str:
    encoded = json.dumps(
        condition,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "condition:" + hashlib.sha256(encoded).hexdigest()[:24]


def _evidence(value: Any, *, collected_at: str, tool: str = "r17d-verifier") -> dict[str, Any]:
    return {
        "value": value,
        "evidence_type": "LOCAL_ATTESTED",
        "source": "R17d release acceptance verifier",
        "collected_at": collected_at,
        "collection_tool": tool,
        "applicability": "APPLICABLE",
    }


def _not_applicable(reason: str, *, collected_at: str) -> dict[str, Any]:
    return {
        "value": None,
        "evidence_type": "LOCAL_ATTESTED",
        "source": "R17d release acceptance verifier",
        "collected_at": collected_at,
        "collection_tool": "r17d-verifier",
        "applicability": "NOT_APPLICABLE",
        "not_applicable_reason": reason,
    }


def _attestation(invariant_id: str, *, collected_at: str) -> dict[str, Any]:
    return {
        "invariant_id": invariant_id,
        "status": "SATISFIED",
        "explanation": "验收器核对最终配置、命令和 Git commit 与批准计划一致。",
        "evidence_references": ["config.yaml", "run-command", "git-commit"],
        "evidence_type": "LOCAL_ATTESTED",
        "source": "R17d release acceptance verifier",
        "collected_at": collected_at,
        "collection_tool": "r17d-verifier",
    }


def _local_attestation(config_content: str, *, collected_at: str) -> dict[str, Any]:
    return {
        "working_tree_clean": _evidence(True, collected_at=collected_at, tool="git-status"),
        "git_branch": _evidence("main", collected_at=collected_at, tool="git-branch"),
        "git_commit": _evidence(GIT_COMMIT, collected_at=collected_at, tool="git-rev-parse"),
        "run_command": _evidence(RUN_COMMAND, collected_at=collected_at),
        "output_directory_exists": _evidence(False, collected_at=collected_at),
        "checkpoint_exists": _evidence(True, collected_at=collected_at),
        "checkpoint_path": _evidence(CHECKPOINT, collected_at=collected_at),
        "config_sha256": _evidence(
            _sha256(config_content.encode("utf-8")), collected_at=collected_at
        ),
        "git_diff_sha256": _evidence("0" * 64, collected_at=collected_at, tool="sha256sum"),
        "environment": {
            "python": _evidence("3.12", collected_at=collected_at, tool="python --version"),
            "cuda": _not_applicable("发布验收不执行 GPU 训练", collected_at=collected_at),
            "pytorch": _not_applicable(
                "发布验收不加载 PyTorch 训练运行时", collected_at=collected_at
            ),
        },
    }


def _final_run_evidence(config_payload: bytes, *, collected_at: str) -> dict[str, Any]:
    condition_attestation = _attestation(_condition_id(CONDITION), collected_at=collected_at)
    return {
        "git_commit": _evidence(GIT_COMMIT, collected_at=collected_at, tool="git-rev-parse"),
        "run_command": _evidence(RUN_COMMAND, collected_at=collected_at),
        "config_sha256": _evidence(
            _sha256(config_payload), collected_at=collected_at, tool="sha256sum"
        ),
        "checkpoint": _evidence(CHECKPOINT, collected_at=collected_at),
        "baseline_reference": _evidence(CHECKPOINT, collected_at=collected_at),
        "environment": _evidence(
            {"python": "3.12", "cuda": None, "pytorch": None}, collected_at=collected_at
        ),
        "invariant_attestations": [condition_attestation],
    }


def _load_child_environment(env_file: Path, settings: Settings) -> dict[str, str]:
    values = {
        key: value
        for key, value in dotenv_values(env_file).items()
        if isinstance(key, str) and isinstance(value, str)
    }
    child = {**os.environ, **values}
    database_url = make_url(settings.database_url)
    if database_url.host in {"cockroachdb", "database"}:
        database_url = database_url.set(
            host="127.0.0.1",
            port=int(values.get("COCKROACH_SQL_PORT", "26257")),
        )
    minio_port = int(values.get("MINIO_API_PORT", "9000"))
    child.update(
        {
            "PYTHONPATH": f"{REPOSITORY_ROOT / 'src'}:{REPOSITORY_ROOT}",
            "DATABASE_URL": database_url.render_as_string(hide_password=False),
            "S3_ENDPOINT_URL": f"http://127.0.0.1:{minio_port}",
            "S3_PRESIGN_ENDPOINT_URL": f"http://127.0.0.1:{minio_port}",
            "MCP_TRANSPORT": "stdio",
            "MCP_ACCESS_TOKEN": os.environ["MCP_ACCESS_TOKEN"],
        }
    )
    return child


def _formal_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "context": value.get("context"),
        "active_intent": value.get("active_intent"),
        "constraints": value.get("constraints"),
        "context_payload": value.get("context_payload"),
        "intent_payload": value.get("intent_payload"),
    }


def _model_call_count(client: httpx.Client, api: str, project_id: str) -> int:
    payload = _json_object(
        client.get(
            f"{api}/projects/{project_id}/agent/model-observability",
            params={"window_days": 1},
        ),
        label="Agent 模型观测",
    )
    totals = payload.get("totals")
    if not isinstance(totals, dict) or type(totals.get("model_call_count")) is not int:
        raise ValueError("Agent 模型观测缺少 model_call_count")
    return int(totals["model_call_count"])


async def _poll_external_task(
    session: ClientSession, task_id: str, *, timeout_seconds: float
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        value = await _call(
            session,
            "external_agent_task_get",
            {"task_id": task_id, "after_sequence": 0, "limit": 50},
        )
        latest = value.get("latest_run")
        if isinstance(latest, dict) and latest.get("status") in TERMINAL_AGENT_STATUSES:
            return value
        await asyncio.sleep(1)
    raise TimeoutError("等待外部 Agent 首次回答超时")


async def _poll_plan(
    session: ClientSession, plan_id: str, *, timeout_seconds: float
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        value = await _call(session, "external_agent_plan_get", {"plan_id": plan_id})
        summary = value.get("summary")
        status = summary.get("status") if isinstance(summary, dict) else None
        if status == "READY_FOR_APPROVAL":
            return value
        if status in FAILED_PLAN_STATUSES:
            raise RuntimeError(f"实验计划未进入可批准状态: {status}")
        await asyncio.sleep(1)
    raise TimeoutError("等待实验计划审核超时")


async def _poll_submission(
    session: ClientSession, submission_id: str, *, timeout_seconds: float
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        value = await _call(
            session,
            "submission_get_status",
            {"submission_id": submission_id},
        )
        workflow = value.get("workflow_status")
        if workflow == "COMPLETED":
            return value
        if workflow in FAILED_WORKFLOW_STATUSES:
            raise RuntimeError(
                "Submission 分析失败: "
                + json.dumps(value.get("processing_error"), ensure_ascii=False)
            )
        await asyncio.sleep(1)
    raise TimeoutError("等待 Submission 分析完成超时")


def _web_login(
    client: httpx.Client, *, base_url: str, api: str, settings: Settings
) -> tuple[str, list[dict[str, Any]]]:
    health = _json_object(client.get(f"{api}/health"), label="API health")
    if health.get("status") != "ok":
        raise ValueError("API health 状态异常")
    web = client.get(f"{base_url}/")
    web.raise_for_status()
    if "Experiment Guardian" not in web.text:
        raise ValueError("Web 首页缺少产品标识")
    if client.get(f"{base_url}/", headers={"Host": "attacker.invalid"}).status_code != 421:
        raise ValueError("nginx 未拒绝非白名单 Host")
    login = client.get(f"{api}/auth/login", params={"return_to": "/"})
    if login.status_code not in {302, 303}:
        raise ValueError("local_owner 登录入口未创建正常 Session")
    session = _json_object(client.get(f"{api}/auth/me"), label="Web Session")
    if session.get("role") != "OWNER" or session.get("email") != settings.local_owner_email:
        raise ValueError("local_owner 身份与配置不一致")
    csrf = session.get("csrf_token")
    if not isinstance(csrf, str) or not csrf:
        raise ValueError("Web Session 缺少 CSRF Token")
    projects = _items(client, f"{api}/projects")
    return csrf, projects


def _verify_direct_api_host(settings: Settings, env_file: Path, timeout: float) -> None:
    values = dotenv_values(env_file)
    api_port = int(str(values.get("API_PORT") or "8000"))
    with httpx.Client(timeout=timeout, trust_env=False) as direct:
        response = direct.get(
            f"http://127.0.0.1:{api_port}{settings.api_prefix}/health",
            headers={"Host": "attacker.invalid"},
        )
    if response.status_code != 400:
        raise ValueError(f"FastAPI TrustedHost 未拒绝非白名单 Host: {response.status_code}")


def _select_project(projects: list[dict[str, Any]], project_id: str) -> dict[str, Any]:
    selected = next((item for item in projects if str(item.get("project_id")) == project_id), None)
    if selected is None:
        raise ValueError("Web Session 无权访问指定验收项目")
    if selected.get("name") != ACCEPTANCE_PROJECT_NAME:
        raise ValueError(
            f"R17d 只能写入专用验收项目 {ACCEPTANCE_PROJECT_NAME!r}，"
            f"实际为 {selected.get('name')!r}"
        )
    return selected


def _approve_plan(
    client: httpx.Client,
    *,
    api: str,
    project_id: str,
    csrf: str,
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    summary = plan.get("summary")
    review = plan.get("review")
    if not isinstance(summary, dict) or not isinstance(review, dict):
        raise ValueError("可批准计划缺少 summary/review")
    candidates = review.get("candidate_invariants")
    if not isinstance(candidates, list):
        raise ValueError("计划审核缺少候选不变量")
    candidate_ids = [str(item["candidate_id"]) for item in candidates if isinstance(item, dict)]
    if len(candidate_ids) != len(candidates):
        raise ValueError("计划审核包含无效候选不变量")
    request = {
        "expected_revision": summary["current_revision"],
        "review_hash": review["review_hash"],
        "approval_digest": review["approval_digest"],
        "decision": "CONDITIONALLY_APPROVED",
        "reason": "R17d 发布验收已核对正式边界；模型候选不自动固化。",
        "conditions": [CONDITION],
        "confirmed_candidate_ids": [],
        "rejected_candidate_ids": candidate_ids,
    }
    key = str(uuid4())
    url = f"{api}/projects/{project_id}/agent/experiment-plans/{summary['plan_id']}/decisions"
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": key}
    approved = _json_object(client.post(url, headers=headers, json=request), label="批准实验计划")
    replay = _json_object(client.post(url, headers=headers, json=request), label="重放计划批准")
    if approved.get("decision", {}).get("decision_id") != replay.get("decision", {}).get(
        "decision_id"
    ):
        raise ValueError("实验计划决定幂等重放创建了不同决定")
    if approved.get("summary", {}).get("status") != "CONDITIONALLY_APPROVED":
        raise ValueError("实验计划未形成不可变的有条件批准")
    return approved, request


def _upload_artifacts(
    client: httpx.Client,
    targets: list[dict[str, Any]],
    payloads: dict[str, bytes],
) -> None:
    if set(payloads) != {str(item.get("filename")) for item in targets}:
        raise ValueError("Submission 返回的上传目标与声明文件不一致")
    for target in targets:
        filename = str(target["filename"])
        headers = target.get("required_headers")
        if not isinstance(headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
        ):
            raise ValueError(f"{filename} 缺少合法 required_headers")
        response = client.put(
            str(target["upload_url"]), headers=headers, content=payloads[filename]
        )
        response.raise_for_status()


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    raw_token = os.environ.get("MCP_ACCESS_TOKEN", "").strip()
    if not raw_token:
        raise ValueError("必须通过环境变量 MCP_ACCESS_TOKEN 提供项目级 MCP Token")
    env_file = Path(args.env_file).resolve()
    settings = Settings(_env_file=env_file)
    expected_config = {
        "deployment_mode": "local",
        "web_auth_mode": "local_owner",
        "object_storage_backend": "s3_compatible",
        "queue_backend": "database",
        "llm_provider": "bailian",
        "agent_provider": "bailian",
    }
    mismatches = {
        key: {"actual": getattr(settings, key), "expected": value}
        for key, value in expected_config.items()
        if getattr(settings, key) != value
    }
    if mismatches:
        raise ValueError(f"R17d 本地后端配置不一致: {json.dumps(mismatches, ensure_ascii=False)}")
    if not settings.agent_enabled:
        raise ValueError("R17d 发布门要求 AGENT_ENABLED=true 且 agent-worker 已启动")
    if settings.agent_max_wall_seconds < 180:
        raise ValueError(
            "R17d 真实百炼门要求 AGENT_MAX_WALL_SECONDS>=180；"
            "本地推荐值为 300，避免合法严格 JSON 回合被提前终止"
        )
    if settings.agent_max_model_calls < 5:
        raise ValueError(
            "R17d 真实百炼门要求 AGENT_MAX_MODEL_CALLS>=5，"
            "为严格 JSON 最终回答保留一次有界修复机会"
        )

    base_url = args.base_url.rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("R17d 本地验收只接受回环 HTTP Web 地址")
    api = f"{base_url}{settings.api_prefix}"
    _verify_direct_api_host(settings, env_file, args.http_timeout)

    child_environment = _load_child_environment(env_file, settings)
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "experiment_guardian.mcp_server.server"],
        cwd=str(REPOSITORY_ROOT),
        env=child_environment,
    )
    async with (
        stdio_client(server) as (reader, writer),
        ClientSession(reader, writer) as mcp_session,
    ):
        await mcp_session.initialize()
        with httpx.Client(
            timeout=args.http_timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            csrf, projects = _web_login(
                client, base_url=base_url, api=api, settings=settings
            )
            _select_project(projects, args.project_id)
            before_calls = _model_call_count(client, api, args.project_id)

            context = await _call(
                mcp_session, "project_get_context", {"project_id": args.project_id}
            )
            formal_before = _formal_snapshot(context)
            active_intent = context.get("active_intent")
            if not isinstance(active_intent, dict) or not active_intent.get("intent_id"):
                raise ValueError("验收项目没有 Active Intent")
            if not isinstance(context.get("human_readable"), dict):
                raise ValueError("project_get_context 未返回版本绑定的人类可读说明")

            task_receipt = await _call(
                mcp_session,
                "external_agent_task_start",
                {
                    "project_id": args.project_id,
                    "title": "R17d 发布验收任务",
                    "task_description": (
                        "读取正式策略并设计 fusion=0.3 的单变量验收实验。必须保持 "
                        "dataset.protocol=40/20、model.backbone=shift-gcn，不得自动执行或批准。"
                    ),
                    "idempotency_key": str(uuid4()),
                },
            )
            task_id = str(task_receipt["task_id"])
            task = await _poll_external_task(
                mcp_session, task_id, timeout_seconds=args.agent_timeout
            )
            latest = task.get("latest_run")
            if not isinstance(latest, dict) or latest.get("status") != "SUCCEEDED":
                raise RuntimeError(f"外部 Agent 首次回答失败: {latest}")
            messages = task.get("messages")
            if not isinstance(messages, list):
                raise ValueError("外部 Agent 任务缺少消息记录")
            assistant_messages = [
                item
                for item in messages
                if isinstance(item, dict)
                if item.get("role") == "ASSISTANT"
            ]
            if not assistant_messages or not assistant_messages[-1].get("citations"):
                raise ValueError("外部 Agent 回答缺少正式引用")

            plan_receipt = await _call(
                mcp_session,
                "external_agent_plan_submit",
                {
                    "task_id": task_id,
                    "title": "R17d fusion=0.3 单变量发布验收",
                    "plan_markdown": (
                        "## 目标\n验证 fusion=0.3 的完整治理闭环。\n\n"
                        "## 不可变条件\n保持 dataset.protocol=40/20 和 "
                        "model.backbone=shift-gcn；只修改 model.fusion。\n\n"
                        "## 低成本验证\n先核对配置哈希、命令和 Git commit，再上传小型结果。"
                    ),
                    "evidence": {
                        "configuration": {"format": "yaml", "content": CONFIG_CONTENT},
                        "run_command": RUN_COMMAND,
                        "git_commit": GIT_COMMIT,
                        "baseline_reference": CHECKPOINT,
                    },
                    "idempotency_key": str(uuid4()),
                },
            )
            plan_id = str(plan_receipt["plan_id"])
            reviewed_plan = await _poll_plan(
                mcp_session, plan_id, timeout_seconds=args.agent_timeout
            )
            approved_plan, decision_request = _approve_plan(
                client,
                api=api,
                project_id=args.project_id,
                csrf=csrf,
                plan=reviewed_plan,
            )
            decision = approved_plan.get("decision")
            if not isinstance(decision, dict):
                raise ValueError("已批准计划缺少决定快照")
            decision_id = str(decision["decision_id"])

            collected_at = datetime.now(UTC).isoformat()
            plan_check_request = {
                "project_id": args.project_id,
                "experiment_intent_id": str(active_intent["intent_id"]),
                "idempotency_key": str(uuid4()),
                "config_format": "yaml",
                "config_content": CONFIG_CONTENT,
                "command": RUN_COMMAND,
                "git_commit": GIT_COMMIT,
                "local_attestation": _local_attestation(
                    CONFIG_CONTENT, collected_at=collected_at
                ),
                "experiment_plan_decision_id": decision_id,
                "invariant_attestations": [
                    _attestation(_condition_id(CONDITION), collected_at=collected_at)
                ],
            }
            checked = await _call(mcp_session, "experiment_check_plan", plan_check_request)
            replayed_check = await _call(
                mcp_session, "experiment_check_plan", plan_check_request
            )
            if checked.get("plan_check_id") != replayed_check.get("plan_check_id"):
                raise ValueError("Plan Check 幂等重放创建了不同记录")
            if checked.get("check_result") != "PASS":
                raise ValueError(f"正式 Plan Check 未通过: {checked.get('check_result')}")
            invariant_check = checked.get("invariant_check")
            if not isinstance(invariant_check, dict) or invariant_check.get(
                "overall_status"
            ) != "CONSISTENT":
                raise ValueError("运行前关键不变量检查未得到 CONSISTENT")

            blocked_request = {
                **plan_check_request,
                "idempotency_key": str(uuid4()),
                "config_content": BLOCKED_CONFIG_CONTENT,
                "local_attestation": _local_attestation(
                    BLOCKED_CONFIG_CONTENT, collected_at=collected_at
                ),
            }
            blocked = await _call(mcp_session, "experiment_check_plan", blocked_request)
            if blocked.get("check_result") != "BLOCKED":
                raise ValueError("修改 LOCKED protocol 的负向检查未被 BLOCKED")

            manifest_request = {
                "plan_check_id": str(checked["plan_check_id"]),
                "idempotency_key": str(uuid4()),
            }
            manifest = await _call(mcp_session, "run_manifest_create", manifest_request)
            replayed_manifest = await _call(
                mcp_session, "run_manifest_create", manifest_request
            )
            if manifest.get("manifest_id") != replayed_manifest.get("manifest_id"):
                raise ValueError("Run Manifest 幂等重放创建了不同记录")
            if manifest.get("schema_version") != 2:
                raise ValueError("已批准实验计划未生成 schema v2 Run Manifest")

            config_payload = CONFIG_CONTENT.encode("utf-8")
            payloads = {
                "config.yaml": config_payload,
                "result.json": RESULT_CONTENT,
                "run.txt": LOG_CONTENT,
            }
            files = [
                {
                    "filename": filename,
                    "artifact_type": artifact_type,
                    "mime_type": mime_type,
                    "size_bytes": len(payloads[filename]),
                    "sha256": _sha256(payloads[filename]),
                }
                for filename, artifact_type, mime_type in (
                    ("config.yaml", "CONFIG", "application/yaml"),
                    ("result.json", "RESULT", "application/json"),
                    ("run.txt", "LOG", "text/plain"),
                )
            ]
            submission = await _call(
                mcp_session,
                "submission_prepare",
                {
                    "project_id": args.project_id,
                    "run_manifest_id": str(manifest["manifest_id"]),
                    "idempotency_key": str(uuid4()),
                    "source_agent": "experiment-guardian-r17d/1.0.0",
                    "collected_at": collected_at,
                    "experiment_status": "COMPLETED",
                    "metrics_summary": {"top1": 0.83},
                    "files": files,
                    "final_run_evidence": _final_run_evidence(
                        config_payload, collected_at=collected_at
                    ),
                },
            )
            targets = submission.get("artifact_uploads")
            if not isinstance(targets, list):
                raise ValueError("submission_prepare 未返回 Artifact 上传目标")
            _upload_artifacts(client, targets, payloads)

            finalize_request = {
                "submission_id": str(submission["submission_id"]),
                "idempotency_key": str(uuid4()),
            }
            finalized = await _call(mcp_session, "submission_finalize", finalize_request)
            if finalized.get("verification_result") != "PASS":
                raise ValueError(f"MinIO Artifact 验证失败: {finalized.get('issues')}")
            replayed_finalize = await _call(
                mcp_session, "submission_finalize", finalize_request
            )
            if replayed_finalize.get("submission_id") != finalized.get("submission_id"):
                raise ValueError("Submission finalize 幂等重放返回了不同提交")

            submission_status = await _poll_submission(
                mcp_session,
                str(submission["submission_id"]),
                timeout_seconds=args.workflow_timeout,
            )
            if submission_status.get("submission_status") != "NEEDS_REVIEW":
                raise ValueError("Submission 分析完成但未进入 NEEDS_REVIEW")
            if not isinstance(submission_status.get("generated_summary"), dict):
                raise ValueError("Submission 缺少真实百炼摘要")
            embedding = submission_status.get("embedding")
            if not isinstance(embedding, dict) or embedding.get("dimension") != 1024:
                raise ValueError("Submission 缺少合法 1024 维百炼 embedding 元数据")
            receipt = submission_status.get("review_receipt")
            if not isinstance(receipt, dict):
                raise ValueError("Submission 缺少人工审核回执")
            if receipt.get("review_eligibility") not in {
                ReviewEligibility.RESEARCHER_OR_OWNER.value,
                ReviewEligibility.OWNER_ONLY.value,
            }:
                raise ValueError("Submission 风险不允许正常人工确认")
            if receipt.get("highest_risk") == RiskSeverity.CRITICAL.value:
                raise ValueError("正确证据链仍产生 CRITICAL 风险")

            decision_key = str(uuid4())
            decision_url = (
                f"{api}/projects/{args.project_id}/submissions/"
                f"{submission['submission_id']}/decision"
            )
            decision_headers = {
                "X-CSRF-Token": csrf,
                "Idempotency-Key": decision_key,
            }
            decision_body = {
                "decision": "APPROVED",
                "decision_reason": "R17d 发布验收确认固定版本证据链完整。",
            }
            confirmed = _json_object(
                client.post(decision_url, headers=decision_headers, json=decision_body),
                label="确认正式实验",
            )
            replayed_confirmation = _json_object(
                client.post(decision_url, headers=decision_headers, json=decision_body),
                label="重放正式实验确认",
            )
            if confirmed.get("experiment_id") != replayed_confirmation.get("experiment_id"):
                raise ValueError("正式实验确认幂等重放创建了不同 Experiment")
            experiment_id = str(confirmed["experiment_id"])
            experiments = await mcp_session.call_tool(
                "experiments_query",
                {
                    "project_id": args.project_id,
                    "experiment_id": experiment_id,
                    "top_k": 10,
                },
                read_timeout_seconds=timedelta(seconds=60),
            )
            if bool(getattr(experiments, "isError", False)):
                raise RuntimeError(f"experiments_query 失败: {experiments.content}")
            structured_experiments = getattr(experiments, "structuredContent", None)
            if isinstance(structured_experiments, dict):
                queried = structured_experiments.get("result", structured_experiments)
            else:
                content = getattr(experiments, "content", None)
                queried = json.loads(content[0].text) if content else None
            if not isinstance(queried, list) or not any(
                isinstance(item, dict) and str(item.get("experiment_id")) == experiment_id
                for item in queried
            ):
                raise ValueError("experiments_query 未返回刚确认的正式实验")

            formal_after = _formal_snapshot(
                await _call(
                    mcp_session, "project_get_context", {"project_id": args.project_id}
                )
            )
            if formal_after != formal_before:
                raise ValueError("验收链静默修改了正式 Context、Intent 或 Constraint")
            after_calls = _model_call_count(client, api, args.project_id)
            model_call_delta = after_calls - before_calls
            if model_call_delta <= 0 or model_call_delta > args.max_agent_model_calls:
                raise ValueError(
                    "真实百炼 Agent 调用数不在发布预算内: "
                    f"delta={model_call_delta}, max={args.max_agent_model_calls}"
                )
            logout = client.post(
                f"{api}/auth/logout", headers={"X-CSRF-Token": csrf}
            )
            logout.raise_for_status()

    return {
        "schema_version": 1,
        "result": "PASS",
        "release": "v1.0.0-local",
        "generated_at": datetime.now(UTC).isoformat(),
        "project_id": args.project_id,
        "provider": "bailian",
        "models": {
            "agent": settings.agent_model_id,
            "summary": settings.bailian_summary_model,
            "embedding": settings.bailian_embedding_model,
        },
        "model_call_count": model_call_delta,
        "trace": {
            "external_task_id": task_id,
            "experiment_plan_id": plan_id,
            "experiment_plan_revision": decision_request["expected_revision"],
            "experiment_plan_decision_id": decision_id,
            "plan_check_id": checked["plan_check_id"],
            "blocked_plan_check_id": blocked["plan_check_id"],
            "run_manifest_id": manifest["manifest_id"],
            "submission_id": submission["submission_id"],
            "experiment_id": experiment_id,
        },
        "checks": {
            "local_owner_and_csrf": "PASS",
            "host_allowlist": "PASS",
            "external_agent_citations": "PASS",
            "human_plan_decision": "PASS",
            "locked_negative_check": "PASS",
            "schema_v2_manifest": "PASS",
            "minio_fixed_version_artifacts": "PASS",
            "database_worker_analysis": "PASS",
            "bailian_summary_embedding": "PASS",
            "owner_confirmation": "PASS",
            "idempotency_replays": "PASS",
            "formal_policy_unchanged": "PASS",
        },
        "disclaimer": "该验收证明治理和证据闭环可用，不保证真实训练行为或实验结果正确。",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument(
        "--base-url", required=True, help="本地 Web 入口，例如 http://127.0.0.1:5199"
    )
    parser.add_argument("--env-file", default=".env.local")
    parser.add_argument("--report", default="artifacts/r17d-acceptance-report.json")
    parser.add_argument("--http-timeout", type=float, default=30)
    parser.add_argument("--agent-timeout", type=float, default=300)
    parser.add_argument("--workflow-timeout", type=float, default=300)
    parser.add_argument("--max-agent-model-calls", type=int, default=20)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        UUID(args.project_id)
        if args.max_agent_model_calls < 1:
            raise ValueError("--max-agent-model-calls 必须大于 0")
        result = asyncio.run(_run(args))
    except Exception as exc:
        # MCP stdio 使用 TaskGroup；业务异常会在退出上下文时包进 ExceptionGroup。
        # 发布报告只保留有界叶子错误，不输出包含内部调用栈的整组异常。
        leaves: list[str] = []

        def collect(current: BaseException) -> None:
            if isinstance(current, BaseExceptionGroup):
                for nested in current.exceptions:
                    collect(nested)
                return
            message = str(current).strip() or current.__class__.__name__
            if message not in leaves:
                leaves.append(message)

        collect(exc)
        result = {
            "schema_version": 1,
            "result": "FAILED",
            "release": "v1.0.0-local",
            "generated_at": datetime.now(UTC).isoformat(),
            "error": "; ".join(leaves)[:4000],
        }
        exit_code = 1
    else:
        exit_code = 0
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    report_path = Path(args.report).expanduser()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
