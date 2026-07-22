"""R14c 远程 MCP OAuth：预注册客户端、项目绑定和即时撤销。"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.core.config import Settings
from experiment_guardian.domain.enums import TeamRole
from experiment_guardian.infrastructure.mcp_oauth import (
    MCP_APPLICATION_SCOPES,
    CognitoMcpTokenVerifier,
    oauth_scope_map,
)
from experiment_guardian.infrastructure.models import (
    McpOAuthClient,
    McpOAuthGrant,
    Project,
    Team,
    TeamMember,
    User,
)


class FakeClaimsVerifier(CognitoMcpTokenVerifier):
    def __init__(
        self,
        factory: sessionmaker[Session],
        settings: Settings,
        claims: dict[str, object],
    ) -> None:
        super().__init__(factory, settings)
        self.claims = claims

    def _decode_token(self, token: str) -> dict[str, object]:
        assert token == "signed-cognito-access-token"
        return self.claims


def _settings() -> Settings:
    return Settings(
        app_env="test",
        cognito_issuer_url="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_test",
        mcp_public_url="https://mcp.example.test/mcp",
        mcp_oauth_resource_identifier="https://mcp.example.test/mcp",
    )


def _seed(factory: sessionmaker[Session]) -> tuple[User, McpOAuthClient]:
    with factory() as session, session.begin():
        user = User(
            name="Researcher",
            email="researcher-oauth@example.com",
            cognito_sub="cognito-user-sub",
        )
        session.add(user)
        session.flush()
        team = Team(name="OAuth Team", owner_id=user.id)
        session.add(team)
        session.flush()
        session.add(TeamMember(team_id=team.id, user_id=user.id, role=TeamRole.OWNER))
        project = Project(team_id=team.id, name="OAuth Project", active=True)
        session.add(project)
        session.flush()
        client = McpOAuthClient(
            cognito_client_id="pre-registered-client",
            name="Codex Remote MCP",
            team_id=team.id,
            project_id=project.id,
            allowed_scopes=sorted(MCP_APPLICATION_SCOPES),
            created_by=user.id,
        )
        session.add(client)
        session.flush()
        return user, client


def _claims() -> dict[str, object]:
    full_scopes = sorted(oauth_scope_map("experiment-guardian"))
    return {
        "sub": "cognito-user-sub",
        "client_id": "pre-registered-client",
        "token_use": "access",
        "scope": " ".join(full_scopes),
        "exp": int((datetime.now(UTC) + timedelta(minutes=15)).timestamp()),
    }


@pytest.mark.asyncio
async def test_pre_registered_client_creates_project_bound_local_grant(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    user, client = _seed(plan_check_session_factory)
    verifier = FakeClaimsVerifier(plan_check_session_factory, _settings(), _claims())
    access = await verifier.verify_token("signed-cognito-access-token")
    assert access is not None
    assert access.client_id == client.cognito_client_id
    assert access.resource == "https://mcp.example.test/mcp"
    assert access.claims is not None
    assert access.claims["user_id"] == str(user.id)
    assert access.claims["project_id"] == str(client.project_id)
    assert set(access.claims["application_scopes"]) == MCP_APPLICATION_SCOPES

    with plan_check_session_factory() as session:
        grant = session.scalar(select(McpOAuthGrant))
        assert grant is not None
        assert grant.mcp_oauth_client_id == client.id
        assert grant.user_id == user.id


@pytest.mark.asyncio
async def test_revoked_local_grant_rejects_still_valid_cognito_token(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    _seed(plan_check_session_factory)
    verifier = FakeClaimsVerifier(plan_check_session_factory, _settings(), _claims())
    first = await verifier.verify_token("signed-cognito-access-token")
    assert first is not None
    with plan_check_session_factory() as session, session.begin():
        grant = session.scalar(select(McpOAuthGrant))
        assert grant is not None
        grant.revoked_at = datetime.now(UTC)
        grant.revoke_reason = "Owner revoked access"
    assert await verifier.verify_token("signed-cognito-access-token") is None


@pytest.mark.asyncio
async def test_unregistered_client_and_excess_scope_are_rejected(
    plan_check_session_factory: sessionmaker[Session],
) -> None:
    _seed(plan_check_session_factory)
    claims = _claims()
    claims["client_id"] = "unknown-client"
    verifier = FakeClaimsVerifier(plan_check_session_factory, _settings(), claims)
    assert await verifier.verify_token("signed-cognito-access-token") is None

    claims = _claims()
    with plan_check_session_factory() as session, session.begin():
        client = session.scalar(select(McpOAuthClient))
        assert client is not None
        client.allowed_scopes = ["project:read"]
    verifier = FakeClaimsVerifier(plan_check_session_factory, _settings(), claims)
    assert await verifier.verify_token("signed-cognito-access-token") is None
