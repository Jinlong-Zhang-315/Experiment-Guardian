"""不依赖外部服务的 API 启动冒烟测试。"""

from uuid import uuid4

import httpx
import pytest

import experiment_guardian.api.routes.agent as agent_routes
import experiment_guardian.main as main_module
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.domain.enums import AgentRunStatus

create_app = main_module.create_app


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    # 直接使用 ASGITransport，测试不会打开端口，也不依赖同步 TestClient 的线程门户。
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_capabilities_exposes_exactly_seven_mcp_tools() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/capabilities")

    assert response.status_code == 200
    assert len(response.json()["mcp_tools"]) == 7
    assert "submission_get_status" in response.json()["mcp_tools"]
    assert response.json()["plan_results"] == ["PASS", "NEEDS_APPROVAL", "BLOCKED"]
    assert "不代表" in response.json()["verification_disclaimer"]


@pytest.mark.asyncio
async def test_local_owner_mode_rejects_dns_rebinding_host_before_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = main_module.get_settings().model_copy(
        update={
            "deployment_mode": "local",
            "web_auth_mode": "local_owner",
            "web_public_base_url": "http://127.0.0.1:8000",
            "web_frontend_url": "http://localhost:5173",
        }
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    transport = httpx.ASGITransport(app=main_module.create_app())
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://127.0.0.1",
    ) as client:
        allowed = await client.get("/api/v1/health")
        rebinding = await client.get(
            "/api/v1/auth/login",
            headers={"Host": "guardian.attacker.example"},
        )

    assert allowed.status_code == 200
    assert rebinding.status_code == 400
    assert rebinding.text == "Invalid host header"


@pytest.mark.asyncio
async def test_agent_sse_replays_persisted_events_and_prechecks_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    run_id = uuid4()
    identity = RequestIdentity(
        user_id=uuid4(),
        team_id=uuid4(),
        token_id=uuid4(),
        scopes=frozenset({"project:read"}),
        authentication_method="WEB_SESSION",
    )

    class FakeAgentService:
        get_calls = 0
        event_calls = 0

        def get_run(self, **_: object) -> object:
            self.get_calls += 1
            return object()

        def list_run_events(self, **_: object) -> tuple[list[dict[str, object]], AgentRunStatus]:
            self.event_calls += 1
            if self.event_calls == 1:
                return (
                    [
                        {
                            "id": 4,
                            "event": "run.completed",
                            "data": {"status": "SUCCEEDED"},
                        }
                    ],
                    AgentRunStatus.SUCCEEDED,
                )
            return [], AgentRunStatus.SUCCEEDED

    service = FakeAgentService()
    monkeypatch.setattr(
        agent_routes, "get_agent_conversation_service", lambda: service
    )
    response = await agent_routes.stream_agent_run_events(
        project_id=project_id,
        run_id=run_id,
        identity=identity,
        after=0,
        last_event_id="3",
    )
    chunks = [chunk async for chunk in response.body_iterator]
    body = "".join(item.decode() if isinstance(item, bytes) else item for item in chunks)

    assert response.status_code == 200
    assert response.media_type == "text/event-stream"
    assert "id: 4" in body
    assert "event: run.completed" in body
    assert service.get_calls == 1
