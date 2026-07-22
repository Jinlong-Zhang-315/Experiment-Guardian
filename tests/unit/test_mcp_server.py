"""MCP Server 工具注册测试。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.domain.contracts import (
    ExperimentCheckPlanCommand,
    ExperimentQueryCommand,
    SubmissionFinalizeCommand,
    SubmissionPrepareCommand,
)
from experiment_guardian.mcp_server import server

mcp = server.mcp


@pytest.mark.asyncio
async def test_mcp_exposes_only_the_seven_p0_tools() -> None:
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}

    assert names == {
        "project_get_context",
        "experiment_check_plan",
        "run_manifest_create",
        "submission_prepare",
        "submission_finalize",
        "submission_get_status",
        "experiments_query",
    }

    for tool in tools:
        properties = tool.inputSchema.get("properties", {})
        assert "actor_id" not in properties
        assert "requester_id" not in properties
        if tool.name == "submission_finalize":
            assert set(properties) == {"submission_id", "idempotency_key"}


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


def test_submission_prepare_uses_server_authenticated_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = RequestIdentity(
        user_id=uuid4(),
        team_id=uuid4(),
        token_id=uuid4(),
        project_id=uuid4(),
        scopes=frozenset({"submission:create"}),
    )
    captured: dict[str, object] = {}

    class FakeUseCases:
        def submission_prepare(
            self, command: SubmissionPrepareCommand, request_identity: RequestIdentity
        ) -> SimpleNamespace:
            captured["command"] = command
            captured["identity"] = request_identity
            return SimpleNamespace(model_dump=lambda **_: {"status": "RECEIVED"})

    monkeypatch.setattr(
        server,
        "get_identity_provider",
        lambda: SimpleNamespace(current_identity=lambda: identity),
    )
    monkeypatch.setattr(server, "get_guardian_use_cases", lambda: FakeUseCases())

    result = server.submission_prepare(
        project_id=str(identity.project_id),
        run_manifest_id=str(uuid4()),
        idempotency_key=str(uuid4()),
        source_agent="test-agent/0.1",
        collected_at="2026-07-21T12:00:00+00:00",
        experiment_status="COMPLETED",
        metrics_summary={"top1": 0.8},
        files=[
            {
                "filename": "config.json",
                "artifact_type": "CONFIG",
                "mime_type": "application/json",
                "size_bytes": 10,
                "sha256": "a" * 64,
            },
            {
                "filename": "result.json",
                "artifact_type": "RESULT",
                "mime_type": "application/json",
                "size_bytes": 10,
                "sha256": "b" * 64,
            },
        ],
    )

    assert isinstance(captured["command"], SubmissionPrepareCommand)
    assert captured["identity"] is identity
    assert result == {"status": "RECEIVED"}


def test_submission_finalize_uses_server_identity_and_no_client_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = RequestIdentity(
        user_id=uuid4(),
        team_id=uuid4(),
        token_id=uuid4(),
        project_id=uuid4(),
        scopes=frozenset({"submission:finalize"}),
    )
    captured: dict[str, object] = {}

    class FakeUseCases:
        def submission_finalize(
            self, command: SubmissionFinalizeCommand, request_identity: RequestIdentity
        ) -> SimpleNamespace:
            captured["command"] = command
            captured["identity"] = request_identity
            return SimpleNamespace(model_dump=lambda **_: {"verification_result": "PASS"})

    monkeypatch.setattr(
        server,
        "get_identity_provider",
        lambda: SimpleNamespace(current_identity=lambda: identity),
    )
    monkeypatch.setattr(server, "get_guardian_use_cases", lambda: FakeUseCases())
    submission_id, idempotency_key = uuid4(), uuid4()

    result = server.submission_finalize(str(submission_id), str(idempotency_key))

    command = captured["command"]
    assert isinstance(command, SubmissionFinalizeCommand)
    assert command.submission_id == submission_id
    assert command.idempotency_key == idempotency_key
    assert captured["identity"] is identity
    assert result == {"verification_result": "PASS"}


def test_submission_get_status_uses_server_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = RequestIdentity(
        user_id=uuid4(),
        team_id=uuid4(),
        token_id=uuid4(),
        project_id=uuid4(),
        scopes=frozenset({"submission:read"}),
    )
    captured: dict[str, object] = {}

    class FakeUseCases:
        def submission_get_status(
            self, *, submission_id: UUID, identity: RequestIdentity
        ) -> SimpleNamespace:
            captured["submission_id"] = submission_id
            captured["identity"] = identity
            return SimpleNamespace(model_dump=lambda **_: {"workflow_status": "QUEUED"})

    monkeypatch.setattr(
        server,
        "get_identity_provider",
        lambda: SimpleNamespace(current_identity=lambda: identity),
    )
    monkeypatch.setattr(server, "get_guardian_use_cases", lambda: FakeUseCases())
    submission_id = uuid4()

    result = server.submission_get_status(str(submission_id))

    assert result == {"workflow_status": "QUEUED"}
    assert captured == {"submission_id": submission_id, "identity": identity}


def test_experiments_query_uses_server_identity_and_supports_detail_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = RequestIdentity(
        user_id=uuid4(),
        team_id=uuid4(),
        token_id=uuid4(),
        project_id=uuid4(),
        scopes=frozenset({"experiment:query"}),
    )
    captured: dict[str, object] = {}

    class FakeUseCases:
        def experiments_query(
            self, command: ExperimentQueryCommand, request_identity: RequestIdentity
        ) -> list[SimpleNamespace]:
            captured["command"] = command
            captured["identity"] = request_identity
            return [SimpleNamespace(model_dump=lambda **_: {"detail_level": "FULL"})]

    monkeypatch.setattr(
        server,
        "get_identity_provider",
        lambda: SimpleNamespace(current_identity=lambda: identity),
    )
    monkeypatch.setattr(server, "get_guardian_use_cases", lambda: FakeUseCases())
    experiment_id = uuid4()

    result = server.experiments_query(
        project_id=str(identity.project_id),
        experiment_id=str(experiment_id),
    )

    command = captured["command"]
    assert isinstance(command, ExperimentQueryCommand)
    assert "actor_id" not in type(command).model_fields
    assert command.experiment_id == experiment_id
    assert captured["identity"] is identity
    assert result == [{"detail_level": "FULL"}]
