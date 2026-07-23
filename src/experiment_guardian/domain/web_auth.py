"""人类登录与服务端 Web Session 的外部契约。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from experiment_guardian.domain.contracts import ContractModel
from experiment_guardian.domain.enums import TeamRole


class AuthSessionView(ContractModel):
    user_id: UUID
    team_id: UUID
    session_id: UUID
    name: str
    email: str
    role: TeamRole
    csrf_token: str
    authenticated_at: datetime
    reauthenticated_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    recent_authentication: bool
    agent_enabled: bool = False


class LogoutResult(ContractModel):
    logout_url: str


class AuthFailure(ContractModel):
    error: Literal["oidc_callback_failed"] = "oidc_callback_failed"
    message: str = Field(min_length=1, max_length=500)
