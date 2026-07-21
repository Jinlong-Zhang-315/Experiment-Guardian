"""MCP Server 工具注册测试。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.domain.contracts import ExperimentCheckPlanCommand
from experiment_guardian.mcp_server import server

mcp = server.mcp


@pytest.mark.asyncio
async def test_mcp_exposes_only_the_six_p0_tools() -> None:
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}

    assert names == {
        "project_get_context",
        "experiment_check_plan",
        "run_manifest_create",
        "submission_prepare",
        "submission_finalize",
        "experiments_query",
    }

    for tool in tools:
        properties = tool.inputSchema.get("properties", {})
        assert "actor_id" not in properties
        assert "requester_id" not in properties


def test_experiment_check_plan_uses_server_authenticated_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = RequestIdentity(
        user_id=uuid4(),
        team_id=uuid4(),
        token_id=uuid4(),
        project_id=uuid4(),
        scopes=frozenset({"experiment:check"}),
    )
    captured: dict[str, object] = {}

    class FakeUseCases:
        def experiment_check_plan(
            self, command: ExperimentCheckPlanCommand, request_identity: RequestIdentity
        ) -> SimpleNamespace:
            captured["command"] = command
            captured["identity"] = request_identity
            return SimpleNamespace(model_dump=lambda **_: {"check_result": "PASS"})

    monkeypatch.setattr(
        server,
        "get_identity_provider",
        lambda: SimpleNamespace(current_identity=lambda: identity),
    )
    monkeypatch.setattr(server, "get_guardian_use_cases", lambda: FakeUseCases())

    collected_at = datetime(2026, 7, 21, tzinfo=UTC).isoformat()

    def evidence(value: object) -> dict[str, object]:
        return {
            "value": value,
            "evidence_type": "LOCAL_ATTESTED",
            "source": "mcp boundary test",
            "collected_at": collected_at,
            "collection_tool": "test-agent/0.1",
        }

    result = server.experiment_check_plan(
        project_id=str(identity.project_id),
        experiment_intent_id=str(uuid4()),
        idempotency_key=str(uuid4()),
        config_format="yaml",
        config_content="dataset:\n  protocol: 40/20\n",
        command="python train.py --config config.yaml",
        git_commit="abc1234",
        local_attestation={
            "working_tree_clean": evidence(True),
            "git_branch": evidence("main"),
            "git_commit": evidence("abc1234"),
            "run_command": evidence("python train.py --config config.yaml"),
            "output_directory_exists": evidence(False),
            "config_sha256": evidence("a" * 64),
            "environment": {"python": evidence("3.12.13")},
        },
    )

    command = captured["command"]
    assert isinstance(command, ExperimentCheckPlanCommand)
    assert "requester_id" not in type(command).model_fields
    assert captured["identity"] is identity
    assert result == {"check_result": "PASS"}


def test_run_manifest_create_uses_server_authenticated_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = RequestIdentity(
        user_id=uuid4(),
        team_id=uuid4(),
        token_id=uuid4(),
        project_id=uuid4(),
        scopes=frozenset({"manifest:create"}),
    )
    captured: dict[str, object] = {}

    class FakeUseCases:
        def run_manifest_create(self, **kwargs: object) -> SimpleNamespace:
            captured.update(kwargs)
            return SimpleNamespace(model_dump=lambda **_: {"schema_version": 1})

    monkeypatch.setattr(
        server,
        "get_identity_provider",
        lambda: SimpleNamespace(current_identity=lambda: identity),
    )
    monkeypatch.setattr(server, "get_guardian_use_cases", lambda: FakeUseCases())
    plan_check_id = uuid4()
    idempotency_key = uuid4()

    result = server.run_manifest_create(str(plan_check_id), str(idempotency_key))

    assert captured == {
        "plan_check_id": plan_check_id,
        "identity": identity,
        "idempotency_key": idempotency_key,
    }
    assert result == {"schema_version": 1}
