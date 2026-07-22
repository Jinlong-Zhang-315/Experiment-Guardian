"""托管 OIDC 登录和服务端 Session 的应用服务。"""

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import (
    AuthenticationError,
    AuthorizationError,
    InputValidationError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.ports import OidcProvider
from experiment_guardian.core.config import Settings
from experiment_guardian.domain.enums import TeamRole
from experiment_guardian.domain.web_auth import AuthSessionView
from experiment_guardian.infrastructure.models import (
    AuditLog,
    OidcTransaction,
    TeamMember,
    User,
    WebSession,
)

OWNER_WEB_SCOPES = frozenset(
    {
        "project:read",
        "project:write",
        "plan:read",
        "plan:approve",
        "submission:read",
        "submission:review",
        "experiment:read",
        "experiment:query",
        "artifact:read",
    }
)
RESEARCHER_WEB_SCOPES = frozenset(
    {
        "project:read",
        "plan:read",
        "submission:read",
        "submission:review",
        "experiment:read",
        "experiment:query",
        "artifact:read",
    }
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class LoginCompletion:
    raw_session: str
    redirect_url: str
    max_age: int


class WebAuthService:
    """维护短期 OIDC 事务和可撤销的数据库会话。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        provider: OidcProvider,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self._settings = settings
        encryption_key = base64.urlsafe_b64encode(
            hashlib.sha256(settings.web_oidc_state_key.get_secret_value().encode("utf-8")).digest()
        )
        self._fernet = Fernet(encryption_key)
        self._csrf_secret = settings.web_csrf_secret.get_secret_value().encode("utf-8")

    @property
    def callback_uri(self) -> str:
        base_url = self._settings.web_public_base_url.rstrip("/")
        return f"{base_url}{self._settings.api_prefix}/auth/callback"

    def begin_login(
        self,
        *,
        return_to: str = "/",
        purpose: str = "LOGIN",
        session_id: object | None = None,
    ) -> str:
        if purpose not in {"LOGIN", "REAUTH"}:
            raise ValueError("OIDC purpose 无效")
        safe_return_to = self._safe_return_to(return_to)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        payload = self._fernet.encrypt(
            json.dumps(
                {"nonce": nonce, "verifier": verifier, "return_to": safe_return_to},
                separators=(",", ":"),
            ).encode("utf-8")
        ).decode("ascii")
        now = datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            session.add(
                OidcTransaction(
                    state_hash=_digest(state),
                    purpose=purpose,
                    session_id=session_id,
                    encrypted_payload=payload,
                    expires_at=now + timedelta(seconds=self._settings.oidc_transaction_ttl_seconds),
                )
            )
        return self._provider.authorization_url(
            state=state,
            nonce=nonce,
            code_challenge=challenge,
            redirect_uri=self.callback_uri,
            prompt="login" if purpose == "REAUTH" else None,
        )

    def complete_login(self, *, state: str, code: str, user_agent: str | None) -> LoginCompletion:
        if not state or not code:
            raise AuthenticationError("OIDC callback 缺少 state 或 code")
        now = datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            transaction = session.scalar(
                select(OidcTransaction)
                .where(OidcTransaction.state_hash == _digest(state))
                .with_for_update()
            )
            if (
                transaction is None
                or transaction.consumed_at is not None
                or _aware(transaction.expires_at) <= now
            ):
                raise AuthenticationError("OIDC 登录事务无效、已使用或已过期")
            try:
                payload = json.loads(
                    self._fernet.decrypt(transaction.encrypted_payload.encode("ascii")).decode(
                        "utf-8"
                    )
                )
                nonce = payload["nonce"]
                verifier = payload["verifier"]
                return_to = self._safe_return_to(payload["return_to"])
            except (InvalidToken, UnicodeError, ValueError, KeyError, TypeError) as exc:
                raise AuthenticationError("OIDC 登录事务内容损坏") from exc

            oidc_identity = self._provider.exchange_code(
                code=code,
                code_verifier=verifier,
                redirect_uri=self.callback_uri,
                expected_nonce=nonce,
            )
            if not oidc_identity.email_verified:
                raise AuthorizationError("Cognito 邮箱尚未验证，不能绑定应用用户")
            user = session.scalar(select(User).where(User.cognito_sub == oidc_identity.subject))
            if user is None:
                user = session.scalar(
                    select(User).where(func.lower(User.email) == oidc_identity.email.lower())
                )
                if user is None:
                    raise AuthorizationError("该 Cognito 用户尚未由管理员加入 Experiment Guardian")
                if user.cognito_sub is not None and user.cognito_sub != oidc_identity.subject:
                    raise AuthorizationError("该应用用户已经绑定其他 Cognito 身份")
                user.cognito_sub = oidc_identity.subject

            membership = self._single_membership(session, user.id)
            transaction.consumed_at = now
            if transaction.purpose == "REAUTH":
                if transaction.session_id is None:
                    raise AuthenticationError("重新认证事务未绑定 Session")
                web_session = session.get(WebSession, transaction.session_id)
                if (
                    web_session is None
                    or web_session.user_id != user.id
                    or web_session.revoked_at is not None
                    or _aware(web_session.absolute_expires_at) <= now
                ):
                    raise AuthenticationError("原 Web Session 已失效")
                web_session.reauthenticated_at = oidc_identity.authenticated_at
                web_session.last_seen_at = now
                raw_session = ""
                target_id = web_session.id
            else:
                raw_session = secrets.token_urlsafe(48)
                web_session = WebSession(
                    user_id=user.id,
                    team_id=membership.team_id,
                    session_hash=_digest(raw_session),
                    authenticated_at=oidc_identity.authenticated_at,
                    reauthenticated_at=oidc_identity.authenticated_at,
                    last_seen_at=now,
                    absolute_expires_at=now
                    + timedelta(seconds=self._settings.web_session_absolute_seconds),
                    user_agent_hash=_digest(user_agent) if user_agent else None,
                )
                session.add(web_session)
                session.flush()
                target_id = web_session.id
            session.add(
                AuditLog(
                    team_id=membership.team_id,
                    project_id=None,
                    actor_type="USER",
                    actor_id=user.id,
                    action="web.reauthenticate" if transaction.purpose == "REAUTH" else "web.login",
                    target_type="WEB_SESSION",
                    target_id=target_id,
                    before_value=None,
                    after_value={
                        "authentication_method": "COGNITO_OIDC",
                        "cognito_sub": oidc_identity.subject,
                    },
                )
            )
        redirect_url = urljoin(
            self._settings.web_frontend_url.rstrip("/") + "/", return_to.lstrip("/")
        )
        return LoginCompletion(
            raw_session=raw_session,
            redirect_url=redirect_url,
            max_age=self._settings.web_session_absolute_seconds,
        )

    def authenticate(self, raw_session: str) -> RequestIdentity:
        if not raw_session:
            raise AuthenticationError("缺少有效的 Web Session")
        now = datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            web_session = session.scalar(
                select(WebSession).where(WebSession.session_hash == _digest(raw_session))
            )
            if (
                web_session is None
                or web_session.revoked_at is not None
                or _aware(web_session.absolute_expires_at) <= now
                or _aware(web_session.last_seen_at)
                + timedelta(seconds=self._settings.web_session_idle_seconds)
                <= now
            ):
                raise AuthenticationError("Web Session 无效、已过期或已撤销")
            membership = session.get(TeamMember, (web_session.team_id, web_session.user_id))
            if membership is None:
                web_session.revoked_at = now
                web_session.revoke_reason = "TEAM_MEMBERSHIP_REMOVED"
                raise AuthorizationError("团队成员关系已失效")
            if _aware(web_session.last_seen_at) + timedelta(minutes=5) <= now:
                web_session.last_seen_at = now
            recent = (
                _aware(web_session.reauthenticated_at)
                + timedelta(seconds=self._settings.web_recent_auth_seconds)
                > now
            )
            return RequestIdentity(
                user_id=web_session.user_id,
                team_id=web_session.team_id,
                token_id=web_session.id,
                scopes=(
                    OWNER_WEB_SCOPES if membership.role is TeamRole.OWNER else RESEARCHER_WEB_SCOPES
                ),
                authentication_method="WEB_SESSION",
                recent_authentication=recent,
            )

    def session_view(self, *, identity: RequestIdentity, raw_session: str) -> AuthSessionView:
        if identity.authentication_method != "WEB_SESSION":
            raise AuthenticationError("当前身份不是 Web Session")
        with self._session_factory() as session:
            web_session = session.get(WebSession, identity.token_id)
            user = session.get(User, identity.user_id)
            membership = session.get(TeamMember, (identity.team_id, identity.user_id))
            if web_session is None or user is None or membership is None:
                raise AuthenticationError("Web Session 关联数据不存在")
            return AuthSessionView(
                user_id=user.id,
                team_id=identity.team_id,
                session_id=web_session.id,
                name=user.name,
                email=user.email,
                role=membership.role,
                csrf_token=self.csrf_token(raw_session),
                authenticated_at=web_session.authenticated_at,
                reauthenticated_at=web_session.reauthenticated_at,
                idle_expires_at=_aware(web_session.last_seen_at)
                + timedelta(seconds=self._settings.web_session_idle_seconds),
                absolute_expires_at=web_session.absolute_expires_at,
                recent_authentication=identity.recent_authentication,
            )

    def csrf_token(self, raw_session: str) -> str:
        return hmac.new(self._csrf_secret, raw_session.encode("utf-8"), hashlib.sha256).hexdigest()

    def validate_csrf(self, raw_session: str, supplied: str | None) -> None:
        if supplied is None or not hmac.compare_digest(self.csrf_token(raw_session), supplied):
            raise AuthorizationError("CSRF Token 缺失或无效")

    def revoke(self, *, identity: RequestIdentity, reason: str = "USER_LOGOUT") -> None:
        now = datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            web_session = session.get(WebSession, identity.token_id)
            if web_session is not None and web_session.user_id == identity.user_id:
                web_session.revoked_at = now
                web_session.revoke_reason = reason
                session.add(
                    AuditLog(
                        team_id=identity.team_id,
                        project_id=None,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action="web.logout",
                        target_type="WEB_SESSION",
                        target_id=identity.token_id,
                        before_value={"revoked": False},
                        after_value={"revoked": True, "reason": reason},
                    )
                )

    def logout_url(self) -> str:
        return self._provider.logout_url(redirect_uri=self._settings.web_frontend_url)

    @staticmethod
    def _single_membership(session: Session, user_id: object) -> TeamMember:
        memberships = session.scalars(select(TeamMember).where(TeamMember.user_id == user_id)).all()
        if len(memberships) != 1:
            raise AuthorizationError("MVP Web 登录要求用户恰好属于一个团队")
        return memberships[0]

    @staticmethod
    def _safe_return_to(value: str) -> str:
        if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
            raise InputValidationError("return_to 必须是站内绝对路径")
        if len(value) > 2000:
            raise InputValidationError("return_to 过长")
        return value
