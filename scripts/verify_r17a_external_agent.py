"""通过真实 stdio MCP 验收 R17a 外部 Agent 任务和带引用问答。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "DEAD_LETTER"}


def _tool_payload(result: object) -> dict[str, Any]:
    if bool(getattr(result, "isError", False)):
        raise RuntimeError(f"MCP 工具调用失败: {getattr(result, 'content', None)}")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None)
    if not isinstance(content, list) or not content:
        raise RuntimeError("MCP 工具没有返回内容")
    text = getattr(content[0], "text", None)
    if not isinstance(text, str):
        raise RuntimeError("MCP 工具没有返回 JSON 文本")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RuntimeError("MCP 工具返回值不是 JSON object")
    return payload


async def _poll(
    session: ClientSession,
    *,
    task_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        payload = _tool_payload(
            await session.call_tool(
                "external_agent_task_get",
                {"task_id": task_id, "after_sequence": 0, "limit": 50},
                read_timeout_seconds=timedelta(seconds=30),
            )
        )
        run = payload.get("latest_run")
        if isinstance(run, dict) and run.get("status") in TERMINAL_STATUSES:
            return payload
        await asyncio.sleep(1)
    raise TimeoutError("等待外部 Agent Run 完成超时")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    raw_token = os.environ.get("MCP_ACCESS_TOKEN", "").strip()
    if not raw_token:
        raise ValueError("必须通过环境变量 MCP_ACCESS_TOKEN 提供数据库签发的 MCP Token")
    environment = {**os.environ, "PYTHONPATH": f"{REPOSITORY_ROOT / 'src'}:{REPOSITORY_ROOT}"}
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "experiment_guardian.mcp_server.server"],
        cwd=str(REPOSITORY_ROOT),
        env=environment,
    )
    async with (
        stdio_client(server) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        started = _tool_payload(
            await session.call_tool(
                "external_agent_task_start",
                {
                    "project_id": args.project_id,
                    "task_description": args.task,
                    "title": "R17a stdio 验收",
                    "idempotency_key": str(uuid4()),
                },
                read_timeout_seconds=timedelta(seconds=30),
            )
        )
        initial = started.get("initial_context")
        if not isinstance(initial, dict) or initial.get("authoritative_scope") != (
            "FORMAL_POLICY_ONLY"
        ):
            raise RuntimeError("任务启动未返回正式策略快照")
        task_id = str(started["task_id"])
        first = await _poll(
            session,
            task_id=task_id,
            timeout_seconds=args.timeout_seconds,
        )
        if first.get("latest_run", {}).get("status") != "SUCCEEDED":
            raise RuntimeError(f"首次 Agent Run 失败: {first.get('latest_run')}")
        assistant = [
            item
            for item in first.get("messages", [])
            if isinstance(item, dict) and item.get("role") == "ASSISTANT"
        ]
        if not assistant or not assistant[-1].get("citations"):
            raise RuntimeError("首次回答没有正式引用")

        await session.call_tool(
            "external_agent_ask",
            {
                "task_id": task_id,
                "question": args.question,
                "idempotency_key": str(uuid4()),
            },
            read_timeout_seconds=timedelta(seconds=30),
        )
        second = await _poll(
            session,
            task_id=task_id,
            timeout_seconds=args.timeout_seconds,
        )
        if second.get("latest_run", {}).get("status") != "SUCCEEDED":
            raise RuntimeError(f"追问 Agent Run 失败: {second.get('latest_run')}")
        return {
            "status": "PASS",
            "task_id": task_id,
            "context_freshness": second.get("context_freshness"),
            "message_count": len(second.get("messages", [])),
            "latest_run_id": second.get("latest_run", {}).get("run_id"),
            "provider": second.get("latest_run", {}).get("provider"),
            "model_id": second.get("latest_run", {}).get("model_id"),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--task", default="读取当前项目目标并给出本任务需要遵守的正式边界")
    parser.add_argument("--question", default="当前 baseline 和数据协议分别是什么？")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
