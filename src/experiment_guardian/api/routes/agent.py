"""内部治理 Agent 的 Web 会话、Run 和 SSE 接口。"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, status
from fastapi.responses import StreamingResponse

from experiment_guardian.api.dependencies import ApiIdentity, CsrfIdentity
from experiment_guardian.application.agent import TERMINAL_RUN_STATUSES
from experiment_guardian.application.container import get_agent_conversation_service
from experiment_guardian.core.config import get_settings
from experiment_guardian.domain.agent import (
    AgentMessageCreateRequest,
    AgentRunReceipt,
    AgentRunView,
    AgentThreadCreateRequest,
    AgentThreadPage,
    AgentThreadSummary,
    AgentThreadUpdateRequest,
    AgentThreadView,
)

router = APIRouter(prefix="/projects/{project_id}/agent", tags=["governance-agent"])


@router.get("/threads", response_model=AgentThreadPage)
async def list_agent_threads(
    project_id: UUID,
    identity: ApiIdentity,
    archived: bool = False,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> AgentThreadPage:
    return get_agent_conversation_service().list_threads(
        project_id=project_id,
        identity=identity,
        archived=archived,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/threads",
    response_model=AgentThreadSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_thread(
    project_id: UUID,
    request: AgentThreadCreateRequest,
    identity: CsrfIdentity,
) -> AgentThreadSummary:
    return get_agent_conversation_service().create_thread(
        project_id=project_id, identity=identity, request=request
    )


@router.get("/threads/{thread_id}", response_model=AgentThreadView)
async def get_agent_thread(
    project_id: UUID, thread_id: UUID, identity: ApiIdentity
) -> AgentThreadView:
    return get_agent_conversation_service().get_thread(
        project_id=project_id, thread_id=thread_id, identity=identity
    )


@router.patch("/threads/{thread_id}", response_model=AgentThreadSummary)
async def update_agent_thread(
    project_id: UUID,
    thread_id: UUID,
    request: AgentThreadUpdateRequest,
    identity: CsrfIdentity,
) -> AgentThreadSummary:
    return get_agent_conversation_service().update_thread(
        project_id=project_id,
        thread_id=thread_id,
        identity=identity,
        request=request,
    )


@router.post(
    "/threads/{thread_id}/messages",
    response_model=AgentRunReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_agent_message(
    project_id: UUID,
    thread_id: UUID,
    request: AgentMessageCreateRequest,
    identity: CsrfIdentity,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> AgentRunReceipt:
    return get_agent_conversation_service().create_message(
        project_id=project_id,
        thread_id=thread_id,
        identity=identity,
        idempotency_key=idempotency_key,
        request=request,
    )


@router.get("/runs/{run_id}", response_model=AgentRunView)
async def get_agent_run(
    project_id: UUID, run_id: UUID, identity: ApiIdentity
) -> AgentRunView:
    return get_agent_conversation_service().get_run(
        project_id=project_id, run_id=run_id, identity=identity
    )


@router.post(
    "/runs/{run_id}/retry",
    response_model=AgentRunReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_agent_run(
    project_id: UUID,
    run_id: UUID,
    identity: CsrfIdentity,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> AgentRunReceipt:
    return get_agent_conversation_service().retry_run(
        project_id=project_id,
        run_id=run_id,
        identity=identity,
        idempotency_key=idempotency_key,
    )


@router.get("/runs/{run_id}/events")
async def stream_agent_run_events(
    project_id: UUID,
    run_id: UUID,
    identity: ApiIdentity,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    settings = get_settings()
    # 响应头发送后无法再返回结构化 401/403/404，先完成 Feature 与 Run 所有权校验。
    get_agent_conversation_service().get_run(
        project_id=project_id, run_id=run_id, identity=identity
    )
    try:
        cursor = max(after, int(last_event_id or "0"))
    except ValueError:
        cursor = after

    async def stream() -> AsyncIterator[str]:
        nonlocal cursor
        last_heartbeat = time.monotonic()
        while True:
            events, run_status = get_agent_conversation_service().list_run_events(
                project_id=project_id,
                run_id=run_id,
                identity=identity,
                after=cursor,
            )
            for item in events:
                cursor = int(item["id"])
                data = json.dumps(
                    item["data"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                yield (
                    f"id: {cursor}\n"
                    f"event: {item['event']}\n"
                    f"data: {data}\n\n"
                )
                last_heartbeat = time.monotonic()
            if run_status in TERMINAL_RUN_STATUSES and not events:
                return
            if (
                time.monotonic() - last_heartbeat
                >= settings.agent_sse_heartbeat_seconds
            ):
                yield ": heartbeat\n\n"
                last_heartbeat = time.monotonic()
            await asyncio.sleep(settings.agent_sse_poll_interval_seconds)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
