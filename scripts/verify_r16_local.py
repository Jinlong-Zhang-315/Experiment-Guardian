"""验证 R16-L 本地部署、治理 Agent 和百炼调用链。

默认只做无模型费用的部署预检。传入 ``--live-bailian`` 后会创建一条独立 Agent
会话并调用当前配置的百炼模型；该操作会产生模型费用和追加式审计记录，但不会确认或
发布任何正式治理对象。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError

# 允许按照文档直接执行 ``python scripts/verify_r16_local.py``，无需先安装 editable 包。
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from experiment_guardian.core.config import Settings  # noqa: E402

TERMINAL_RUN_STATUSES = {"SUCCEEDED", "FAILED", "DEAD_LETTER"}


def _json_object(response: httpx.Response, *, label: str) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"{label} 未返回 JSON object")
    return payload


def _items(client: httpx.Client, url: str) -> list[dict[str, Any]]:
    payload = _json_object(client.get(url), label=url)
    items = payload.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError(f"{url} 的 items 无效")
    return items


def _host_database_url(settings: Settings, env_file: Path) -> URL:
    """把 Compose 内部 CockroachDB 地址转换为宿主机验收地址。"""

    database_url = make_url(settings.database_url)
    if database_url.host not in {"cockroachdb", "database"}:
        return database_url
    values: dict[str, str] = {}
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    port = int(values.get("COCKROACH_SQL_PORT", "26257"))
    return database_url.set(host="127.0.0.1", port=port)


def _verify_migration(settings: Settings, env_file: Path) -> dict[str, str]:
    expected = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    engine = create_engine(_host_database_url(settings, env_file), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
    if not expected or current != expected:
        raise ValueError(f"数据库迁移版本不一致: current={current}, expected={expected}")
    return {"status": "PASS", "revision": current}


def _verify_configuration(settings: Settings, *, live_bailian: bool) -> dict[str, Any]:
    expected = {
        "deployment_mode": "local",
        "web_auth_mode": "local_owner",
        "object_storage_backend": "s3_compatible",
        "queue_backend": "database",
        "llm_provider": "bailian",
        "agent_provider": "bailian",
    }
    mismatches = {
        name: {"actual": getattr(settings, name), "expected": value}
        for name, value in expected.items()
        if getattr(settings, name) != value
    }
    if mismatches:
        raise ValueError(f"本地部署后端配置不一致: {json.dumps(mismatches, ensure_ascii=False)}")
    if live_bailian and not settings.agent_enabled:
        raise ValueError("--live-bailian 要求 AGENT_ENABLED=true 并启动 agent-worker")
    return {
        "status": "PASS",
        "deployment_mode": settings.deployment_mode,
        "model_provider": settings.agent_provider,
        "model_id": settings.agent_model_id if settings.agent_enabled else None,
        "agent_enabled": settings.agent_enabled,
    }


def _verify_minio(settings: Settings, timeout: float) -> dict[str, str]:
    endpoint = settings.s3_presign_endpoint_url.rstrip("/")
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.get(f"{endpoint}/minio/health/live")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError(f"无法访问配置的 MinIO 预签名端点: {endpoint}") from exc
    return {"status": "PASS", "endpoint": endpoint}


def _snapshot_formal_state(client: httpx.Client, api: str, project_id: str) -> dict[str, Any]:
    settings = _json_object(client.get(f"{api}/projects/{project_id}/settings"), label="项目设置")
    current = settings.get("current")
    if not isinstance(current, dict):
        raise ValueError("项目设置缺少 current Context bundle")
    context = current.get("context")
    intent = current.get("active_intent")
    if not isinstance(context, dict) or not isinstance(intent, dict):
        raise ValueError("项目设置缺少正式 Context 或 Active Intent 引用")
    return {
        "context_id": context.get("context_id"),
        "context_version": context.get("version"),
        "intent_id": intent.get("intent_id"),
        "intent_version": intent.get("version"),
        "plan_check_ids": sorted(
            str(item.get("plan_check_id"))
            for item in _items(client, f"{api}/projects/{project_id}/plan-checks?limit=50")
        ),
        "submission_ids": sorted(
            str(item.get("submission_id"))
            for item in _items(client, f"{api}/projects/{project_id}/submissions?limit=50")
        ),
        "experiment_ids": sorted(
            str(item.get("experiment_id"))
            for item in _items(client, f"{api}/projects/{project_id}/experiments?limit=50")
        ),
        "policy_draft_ids": sorted(
            str(item.get("draft_id"))
            for item in _items(client, f"{api}/projects/{project_id}/agent/policy-drafts?limit=50")
        ),
        "action_proposal_ids": sorted(
            str(item.get("proposal_id"))
            for item in _items(
                client, f"{api}/projects/{project_id}/agent/action-proposals?limit=50"
            )
        ),
    }


def _parse_sse_events(payload: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*payload.splitlines(), ""]:
        if not line:
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.lstrip()
        if field == "id":
            current["id"] = value
        elif field == "event":
            current["event"] = value
        elif field == "data":
            current["data"] = json.loads(value)
    return events


def _wait_for_run(
    client: httpx.Client,
    *,
    api: str,
    project_id: str,
    run_id: str,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = _json_object(
            client.get(f"{api}/projects/{project_id}/agent/runs/{run_id}"),
            label="Agent Run",
        )
        if run.get("status") in TERMINAL_RUN_STATUSES:
            return run
        time.sleep(0.5)
    raise ValueError(f"Agent Run 在 {timeout:.0f} 秒内未进入终态")


def _verify_live_agent(
    client: httpx.Client,
    *,
    api: str,
    project_id: str,
    csrf_token: str,
    settings: Settings,
    timeout: float,
) -> dict[str, Any]:
    before = _snapshot_formal_state(client, api, project_id)
    thread = _json_object(
        client.post(
            f"{api}/projects/{project_id}/agent/threads",
            headers={"X-CSRF-Token": csrf_token},
            json={"title": f"R16-L RC {datetime.now(UTC).isoformat()}"},
        ),
        label="创建 Agent Thread",
    )
    thread_id = str(thread.get("thread_id"))
    receipt = _json_object(
        client.post(
            f"{api}/projects/{project_id}/agent/threads/{thread_id}/messages",
            headers={
                "X-CSRF-Token": csrf_token,
                "Idempotency-Key": str(uuid4()),
            },
            json={
                "content": (
                    "请读取当前正式项目状态，简要说明项目目标、活动实验意图和 Context 版本，"
                    "所有正式事实都必须引用工具返回的证据。不要创建草稿或操作提案。"
                )
            },
        ),
        label="创建 Agent Message",
    )
    run_id = str(receipt.get("run_id"))
    run = _wait_for_run(
        client,
        api=api,
        project_id=project_id,
        run_id=run_id,
        timeout=timeout,
    )
    if run.get("status") != "SUCCEEDED":
        raise ValueError(f"Agent Run 未成功: {json.dumps(run.get('error'), ensure_ascii=False)}")
    if run.get("provider") != "bailian" or run.get("model_id") != settings.agent_model_id:
        raise ValueError("Agent Run 的 provider/model 与本地配置不一致")
    calls = run.get("model_calls")
    if not isinstance(calls, list) or len(calls) < 2:
        raise ValueError("项目状态问答应至少包含工具选择和最终回答两次模型调用")
    for call in calls:
        if not isinstance(call, dict) or call.get("provider") != "bailian":
            raise ValueError("模型调用缺少百炼 provider 元数据")
        if call.get("status") != "SUCCEEDED":
            raise ValueError("本次成功 Run 包含未成功的模型调用")
        if not isinstance(call.get("latency_ms"), int) or call["latency_ms"] < 0:
            raise ValueError("模型调用缺少合法 latency_ms")
        for key in ("input_tokens", "output_tokens"):
            value = call.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"模型调用缺少合法 {key}")

    view = _json_object(
        client.get(f"{api}/projects/{project_id}/agent/threads/{thread_id}"),
        label="Agent Thread",
    )
    messages = view.get("messages")
    if not isinstance(messages, list):
        raise ValueError("Agent Thread 未返回消息")
    assistant = next(
        (
            item
            for item in reversed(messages)
            if isinstance(item, dict)
            and item.get("role") == "ASSISTANT"
            and item.get("run_id") == run_id
        ),
        None,
    )
    if assistant is None:
        raise ValueError("成功 Run 未生成 Assistant Message")
    citations = assistant.get("citations")
    if not isinstance(citations, list) or not citations:
        raise ValueError("正式项目状态回答缺少 Citation")
    if not all(
        isinstance(item, dict) and item.get("evidence_kind") == "CONFIRMED_FACT"
        for item in citations
    ):
        raise ValueError("项目状态 Citation 未标记为 CONFIRMED_FACT")

    response = client.get(f"{api}/projects/{project_id}/agent/runs/{run_id}/events")
    response.raise_for_status()
    events = _parse_sse_events(response.text)
    tool_names = {
        str(item["data"].get("tool"))
        for item in events
        if item.get("event") == "tool.completed" and isinstance(item.get("data"), dict)
    }
    if "project_status_get_v1" not in tool_names:
        raise ValueError("项目状态问答没有完成 project_status_get_v1")
    if any(name.endswith(("confirm", "execute")) for name in tool_names):
        raise ValueError("本地验收期间出现禁止的正式执行工具")

    after = _snapshot_formal_state(client, api, project_id)
    if before != after:
        raise ValueError("只读 Agent 验收改变了正式状态或创建了候选写对象")

    observability = _json_object(
        client.get(
            f"{api}/projects/{project_id}/agent/model-observability",
            params={"window_days": 1, "provider": "bailian", "model_id": settings.agent_model_id},
        ),
        label="模型观测",
    )
    totals = observability.get("totals")
    if not isinstance(totals, dict) or int(totals.get("model_call_count", 0)) < len(calls):
        raise ValueError("模型观测没有包含本次 Run")
    return {
        "status": "PASS",
        "thread_id": thread_id,
        "run_id": run_id,
        "provider": run.get("provider"),
        "model_id": run.get("model_id"),
        "model_call_count": len(calls),
        "tool_names": sorted(tool_names),
        "citation_count": len(citations),
        "usage": {
            "input_tokens": run.get("usage", {}).get("input_tokens"),
            "output_tokens": run.get("usage", {}).get("output_tokens"),
            "latency_ms": run.get("usage", {}).get("latency_ms"),
        },
    }


def verify(args: argparse.Namespace) -> dict[str, Any]:
    env_file = Path(args.env_file).resolve()
    settings = Settings(_env_file=env_file)
    base_url = args.base_url.rstrip("/")
    parsed_base = urlparse(base_url)
    if parsed_base.scheme not in {"http", "https"} or parsed_base.hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        raise ValueError("R16-L 验收地址必须是 127.0.0.1 或 localhost")

    checks: dict[str, Any] = {
        "configuration": _verify_configuration(settings, live_bailian=args.live_bailian),
        "database_migration": _verify_migration(settings, env_file),
        "minio": _verify_minio(settings, args.timeout),
    }
    api = f"{base_url}{settings.api_prefix}"
    with httpx.Client(
        timeout=args.timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        health = _json_object(client.get(f"{api}/health"), label="API health")
        if health.get("status") != "ok":
            raise ValueError("API health 状态异常")
        checks["api_health"] = {"status": "PASS", "version": health.get("version")}

        web = client.get(f"{base_url}/")
        web.raise_for_status()
        if "Experiment Guardian" not in web.text:
            raise ValueError("Web 首页不包含产品标识")
        checks["web"] = {"status": "PASS", "base_url": base_url}

        login = client.get(f"{api}/auth/login", params={"return_to": "/"})
        if login.status_code not in {302, 303}:
            raise ValueError(f"local_owner 登录未返回重定向: {login.status_code}")
        redirect_url = login.headers.get("Location", "")
        if not redirect_url.startswith(f"{base_url}/"):
            raise ValueError(
                "WEB_FRONTEND_URL 与当前 Web 入口不一致: "
                f"redirect={redirect_url}, expected={base_url}/"
            )
        session = _json_object(client.get(f"{api}/auth/me"), label="Web Session")
        if session.get("role") != "OWNER" or session.get("email") != settings.local_owner_email:
            raise ValueError("local_owner Session 身份与配置不一致")
        csrf_token = session.get("csrf_token")
        if not isinstance(csrf_token, str) or not csrf_token:
            raise ValueError("Web Session 缺少 CSRF Token")
        checks["local_owner"] = {
            "status": "PASS",
            "role": session.get("role"),
            "agent_enabled": session.get("agent_enabled"),
        }

        projects = _items(client, f"{api}/projects")
        if not projects:
            raise ValueError("本地 Owner 没有可验收项目，请先执行 bootstrap-local")
        project_id = str(projects[0].get("project_id"))
        checks["project"] = {"status": "PASS", "project_id": project_id}
        checks["live_bailian_agent"] = (
            _verify_live_agent(
                client,
                api=api,
                project_id=project_id,
                csrf_token=csrf_token,
                settings=settings,
                timeout=args.agent_timeout,
            )
            if args.live_bailian
            else {
                "status": "SKIPPED",
                "reason": "未传入 --live-bailian，不产生真实模型调用费用",
            }
        )
        logout = client.post(
            f"{api}/auth/logout",
            headers={"X-CSRF-Token": csrf_token},
        )
        logout.raise_for_status()
        checks["session_revocation"] = {"status": "PASS"}

    return {
        "schema_version": 1,
        "result": "PASS",
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "checks": checks,
        "disclaimer": ("该验收证明本地部署和治理链可用，不代表真实训练行为或实验结果正确。"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", required=True, help="本地 Web 入口，例如 http://127.0.0.1:5199"
    )
    parser.add_argument("--env-file", default=".env.local")
    parser.add_argument("--live-bailian", action="store_true")
    parser.add_argument("--report", help="可选的去敏 JSON 报告输出路径")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--agent-timeout", type=float, default=180.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = verify(args)
    except (httpx.HTTPError, OSError, SQLAlchemyError, ValueError) as exc:
        result = {
            "schema_version": 1,
            "result": "FAILED",
            "generated_at": datetime.now(UTC).isoformat(),
            "error": str(exc),
        }
        exit_code = 1
    else:
        exit_code = 0
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        report_path = Path(args.report).expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
