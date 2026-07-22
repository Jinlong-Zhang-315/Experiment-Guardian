"""R14a Cognito OIDC 与服务端 Session 的无 AWS 集成测试。"""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from sqlalchemy import select

from experiment_guardian.application.errors import AuthenticationError, AuthorizationError
from experiment_guardian.application.ports import OidcIdentity
from experiment_guardian.application.web_auth import WebAuthService
from experiment_guardian.core.config import Settings
from experiment_guardian.domain.enums import TeamRole
from experiment_guardian.infrastructure.models import Team, TeamMember, User, WebSession


class FakeOidcProvider:
    def __init__(self, *, subject: str = "cognito-sub-1", email: str = "owner@example.com") -> None:
        self.subject = subject
        self.email = email
        self.authenticated_at = datetime.now(UTC)
        self.last_prompt: str | None = None

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
        prompt: str | None = None,
    ) -> str:
        self.last_prompt = prompt
        return "https://cognito.example/authorize?" + urlencode(
            {
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "redirect_uri": redirect_uri,
            }
        )

    def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        expected_nonce: str,
    ) -> OidcIdentity:
        assert code == "authorization-code"
        assert code_verifier
        assert redirect_uri.endswith("/api/v1/auth/callback")
        assert expected_nonce
        return OidcIdentity(
            subject=self.subject,
            email=self.email,
            email_verified=True,
            authenticated_at=self.authenticated_at,
        )

    def logout_url(self, *, redirect_uri: str) -> str:
        return "https://cognito.example/logout?" + urlencode({"logout_uri": redirect_uri})


def _settings() -> Settings:
    return Settings(
        app_env="test",
        web_oidc_state_key="test-oidc-key",
        web_csrf_secret="test-csrf-key",
        web_session_idle_seconds=300,
        web_session_absolute_seconds=3600,
        web_recent_auth_seconds=600,
    )


def _state(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


def _seed_user(session_factory: object, *, email: str = "owner@example.com") -> User:
    with session_factory() as session, session.begin():  # type: ignore[operator]
        user = User(name="Owner", email=email)
        session.add(user)
        session.flush()
        team = Team(name="Team", owner_id=user.id)
        session.add(team)
        session.flush()
        session.add(TeamMember(team_id=team.id, user_id=user.id, role=TeamRole.OWNER))
        return user


def test_login_binds_verified_cognito_subject_and_creates_hashed_session(
    plan_check_session_factory: object,
) -> None:
    user = _seed_user(plan_check_session_factory)
    provider = FakeOidcProvider()
    service = WebAuthService(plan_check_session_factory, provider, _settings())  # type: ignore[arg-type]

    authorization_url = service.begin_login(return_to="/projects")
    completion = service.complete_login(
        state=_state(authorization_url), code="authorization-code", user_agent="pytest"
    )
    assert completion.raw_session
    assert completion.redirect_url == "http://127.0.0.1:5173/projects"

    identity = service.authenticate(completion.raw_session)
    assert identity.user_id == user.id
    assert identity.authentication_method == "WEB_SESSION"
    assert identity.recent_authentication is True
    assert "plan:approve" in identity.scopes
    assert service.csrf_token(completion.raw_session)

    with plan_check_session_factory() as session:  # type: ignore[operator]
        stored_user = session.get(User, user.id)
        stored_session = session.scalar(select(WebSession))
        assert stored_user is not None and stored_user.cognito_sub == "cognito-sub-1"
        assert stored_session is not None
        assert stored_session.session_hash != completion.raw_session


def test_session_idle_expiry_and_csrf_are_enforced(plan_check_session_factory: object) -> None:
    _seed_user(plan_check_session_factory)
    service = WebAuthService(
        plan_check_session_factory, FakeOidcProvider(), _settings()  # type: ignore[arg-type]
    )
    completion = service.complete_login(
        state=_state(service.begin_login()),
        code="authorization-code",
        user_agent=None,
    )
    with pytest.raises(AuthorizationError):
        service.validate_csrf(completion.raw_session, "wrong")
    service.validate_csrf(completion.raw_session, service.csrf_token(completion.raw_session))

    with plan_check_session_factory() as session, session.begin():  # type: ignore[operator]
        stored_session = session.scalar(select(WebSession))
        assert stored_session is not None
        stored_session.last_seen_at = datetime.now(UTC) - timedelta(minutes=6)
    with pytest.raises(AuthenticationError):
        service.authenticate(completion.raw_session)


def test_reauthentication_updates_existing_session_without_rotating_cookie(
    plan_check_session_factory: object,
) -> None:
    _seed_user(plan_check_session_factory)
    provider = FakeOidcProvider()
    service = WebAuthService(plan_check_session_factory, provider, _settings())  # type: ignore[arg-type]
    login = service.complete_login(
        state=_state(service.begin_login()), code="authorization-code", user_agent=None
    )
    identity = service.authenticate(login.raw_session)

    provider.authenticated_at = datetime.now(UTC)
    reauth_url = service.begin_login(
        return_to="/plans", purpose="REAUTH", session_id=identity.token_id
    )
    assert provider.last_prompt == "login"
    completion = service.complete_login(
        state=_state(reauth_url), code="authorization-code", user_agent=None
    )
    assert completion.raw_session == ""
    assert service.authenticate(login.raw_session).recent_authentication is True


def test_unknown_cognito_user_cannot_self_register(plan_check_session_factory: object) -> None:
    service = WebAuthService(
        plan_check_session_factory,  # type: ignore[arg-type]
        FakeOidcProvider(email="unknown@example.com"),
        _settings(),
    )
    with pytest.raises(AuthorizationError):
        service.complete_login(
            state=_state(service.begin_login()), code="authorization-code", user_agent=None
        )
