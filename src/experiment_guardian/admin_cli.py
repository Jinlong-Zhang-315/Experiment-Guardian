"""可信本地管理员 CLI：只引导身份和 Token，不创建业务上下文。"""

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from experiment_guardian.application.container import get_token_service
from experiment_guardian.application.errors import (
    ApplicationError,
    AuthorizationError,
    ConflictError,
)
from experiment_guardian.domain.enums import TeamRole, TokenAudience
from experiment_guardian.infrastructure.database import get_session_factory
from experiment_guardian.infrastructure.mcp_oauth import MCP_APPLICATION_SCOPES
from experiment_guardian.infrastructure.models import (
    AuditLog,
    McpOAuthClient,
    McpOAuthGrant,
    Project,
    Team,
    TeamMember,
    User,
)
from experiment_guardian.infrastructure.security import IssuedToken

OWNER_API_SCOPES = frozenset(
    {"plan:approve", "project:initialize", "submission:review"}
)
OWNER_PROJECT_API_SCOPES = frozenset({"plan:approve", "submission:review"})
RESEARCHER_API_SCOPES = frozenset({"submission:review"})
OWNER_MCP_SCOPES = frozenset(
    {
        "experiment:check",
        "experiment:query",
        "manifest:create",
        "project:read",
        "submission:create",
        "submission:finalize",
        "submission:read",
    }
)
RESEARCHER_MCP_SCOPES = OWNER_MCP_SCOPES


def _token_output(kind: str, issued: IssuedToken, *, scopes: frozenset[str]) -> dict[str, Any]:
    return {
        "kind": kind,
        "token_id": str(issued.token_id),
        "access_token": issued.raw_token,
        "token_prefix": issued.token_prefix,
        "scopes": sorted(scopes),
        "expires_at": issued.expires_at.isoformat(),
        "warning": "原始 Token 仅展示本次；数据库只保存哈希。",
    }


def _bootstrap_owner(args: argparse.Namespace) -> dict[str, Any]:
    email = args.email.strip().lower()
    factory = get_session_factory()
    with factory() as session, session.begin():
        user = session.scalar(select(User).where(func.lower(User.email) == email))
        if user is None:
            user = User(name=args.name.strip(), email=email)
            session.add(user)
            session.flush()

        team = session.scalar(
            select(Team).where(Team.owner_id == user.id, Team.name == args.team_name.strip())
        )
        if team is None:
            team = Team(name=args.team_name.strip(), owner_id=user.id)
            session.add(team)
            session.flush()

        member = session.get(TeamMember, {"team_id": team.id, "user_id": user.id})
        if member is None:
            session.add(TeamMember(team_id=team.id, user_id=user.id, role=TeamRole.OWNER))
        elif member.role is not TeamRole.OWNER:
            raise ConflictError("现有团队成员不是 Owner，不能执行 bootstrap-owner")

        issued = get_token_service().issue(
            session,
            user_id=user.id,
            team_id=team.id,
            project_id=None,
            audience=TokenAudience.API,
            name=args.token_name,
            scopes=set(OWNER_API_SCOPES),
            lifetime_days=args.ttl_days,
            created_by=user.id,
        )
        return {
            "user_id": str(user.id),
            "team_id": str(team.id),
            **_token_output("API", issued, scopes=OWNER_API_SCOPES),
        }


def _issue_mcp_token(args: argparse.Namespace) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session, session.begin():
        owner = session.scalar(
            select(User).where(func.lower(User.email) == args.owner_email.strip().lower())
        )
        project = session.get(Project, UUID(args.project_id))
        if owner is None or project is None:
            raise ConflictError("Owner 或项目不存在")
        owner_member = session.get(
            TeamMember, {"team_id": project.team_id, "user_id": owner.id}
        )
        if owner_member is None or owner_member.role is not TeamRole.OWNER:
            raise AuthorizationError("只有项目团队 Owner 可以签发 MCP Token")
        member_email = (getattr(args, "member_email", None) or args.owner_email).strip().lower()
        user = session.scalar(select(User).where(func.lower(User.email) == member_email))
        member = (
            session.get(TeamMember, {"team_id": project.team_id, "user_id": user.id})
            if user is not None
            else None
        )
        if user is None or member is None:
            raise ConflictError("目标用户不是项目团队成员")
        scopes = (
            OWNER_MCP_SCOPES if member.role is TeamRole.OWNER else RESEARCHER_MCP_SCOPES
        )
        issued = get_token_service().issue(
            session,
            user_id=user.id,
            team_id=project.team_id,
            project_id=project.id,
            audience=TokenAudience.MCP,
            name=args.token_name,
            scopes=set(scopes),
            lifetime_days=args.ttl_days,
            created_by=owner.id,
        )
        return {
            "project_id": str(project.id),
            "user_id": str(user.id),
            "role": member.role.value,
            **_token_output("MCP", issued, scopes=scopes),
        }


def _add_researcher(args: argparse.Namespace) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session, session.begin():
        owner = session.scalar(
            select(User).where(func.lower(User.email) == args.owner_email.strip().lower())
        )
        project = session.get(Project, UUID(args.project_id))
        if owner is None or project is None:
            raise ConflictError("Owner 或项目不存在")
        owner_member = session.get(
            TeamMember, {"team_id": project.team_id, "user_id": owner.id}
        )
        if owner_member is None or owner_member.role is not TeamRole.OWNER:
            raise AuthorizationError("只有项目团队 Owner 可以添加 Researcher")

        email = args.email.strip().lower()
        user = session.scalar(select(User).where(func.lower(User.email) == email))
        if user is None:
            user = User(name=args.name.strip(), email=email)
            session.add(user)
            session.flush()
        member = session.get(TeamMember, {"team_id": project.team_id, "user_id": user.id})
        if member is None:
            member = TeamMember(
                team_id=project.team_id,
                user_id=user.id,
                role=TeamRole.RESEARCHER,
            )
            session.add(member)
        elif member.role is not TeamRole.RESEARCHER:
            raise ConflictError("目标用户已是 Owner，不能改为 Researcher")
        return {
            "project_id": str(project.id),
            "user_id": str(user.id),
            "email": user.email,
            "role": TeamRole.RESEARCHER.value,
        }


def _issue_api_token(args: argparse.Namespace) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session, session.begin():
        owner = session.scalar(
            select(User).where(func.lower(User.email) == args.owner_email.strip().lower())
        )
        project = session.get(Project, UUID(args.project_id))
        if owner is None or project is None:
            raise ConflictError("Owner 或项目不存在")
        owner_member = session.get(
            TeamMember, {"team_id": project.team_id, "user_id": owner.id}
        )
        if owner_member is None or owner_member.role is not TeamRole.OWNER:
            raise AuthorizationError("只有项目团队 Owner 可以签发 API Token")
        user = session.scalar(
            select(User).where(func.lower(User.email) == args.member_email.strip().lower())
        )
        member = (
            session.get(TeamMember, {"team_id": project.team_id, "user_id": user.id})
            if user is not None
            else None
        )
        if user is None or member is None:
            raise ConflictError("目标用户不是项目团队成员")
        scopes = (
            OWNER_PROJECT_API_SCOPES
            if member.role is TeamRole.OWNER
            else RESEARCHER_API_SCOPES
        )
        issued = get_token_service().issue(
            session,
            user_id=user.id,
            team_id=project.team_id,
            project_id=project.id,
            audience=TokenAudience.API,
            name=args.token_name,
            scopes=set(scopes),
            lifetime_days=args.ttl_days,
            created_by=owner.id,
        )
        return {
            "project_id": str(project.id),
            "user_id": str(user.id),
            "role": member.role.value,
            **_token_output("API", issued, scopes=scopes),
        }


def _revoke_token(args: argparse.Namespace) -> dict[str, Any]:
    factory = get_session_factory()
    token_id = UUID(args.token_id)
    with factory() as session, session.begin():
        get_token_service().revoke(session, token_id)
    return {"token_id": str(token_id), "revoked": True}


def _require_project_owner(session: Any, owner_email: str, project_id: str) -> tuple[User, Project]:
    owner = session.scalar(
        select(User).where(func.lower(User.email) == owner_email.strip().lower())
    )
    project = session.get(Project, UUID(project_id))
    if owner is None or project is None:
        raise ConflictError("Owner 或项目不存在")
    membership = session.get(TeamMember, (project.team_id, owner.id))
    if membership is None or membership.role is not TeamRole.OWNER:
        raise AuthorizationError("只有项目团队 Owner 可以管理远程 MCP OAuth 客户端")
    return owner, project


def _register_mcp_oauth_client(args: argparse.Namespace) -> dict[str, Any]:
    requested_scopes = {
        item.strip() for item in args.scopes.split(",") if item.strip()
    }
    # FastMCP 当前把 Protected Resource Metadata 中声明的 scope 同时作为全局请求门槛。
    # R14 因而固定使用完整七 scope；具体工具仍在应用层逐项检查，避免 CLI 产生无法登录的
    # “子集客户端”。后续若 SDK 支持按工具声明 scope，再单独开放最小权限客户端。
    if requested_scopes != MCP_APPLICATION_SCOPES:
        raise ValueError("R14 预注册 MCP 客户端必须配置完整七个应用 scope")
    client_id = args.client_id.strip()
    if not client_id or len(client_id) > 128:
        raise ValueError("Cognito client_id 长度无效")
    factory = get_session_factory()
    with factory() as session, session.begin():
        owner, project = _require_project_owner(session, args.owner_email, args.project_id)
        existing = session.scalar(
            select(McpOAuthClient).where(McpOAuthClient.cognito_client_id == client_id)
        )
        if existing is not None:
            raise ConflictError("该 Cognito client_id 已在 Experiment Guardian 中注册")
        client = McpOAuthClient(
            cognito_client_id=client_id,
            name=args.name.strip(),
            team_id=project.team_id,
            project_id=project.id,
            allowed_scopes=sorted(requested_scopes),
            created_by=owner.id,
        )
        session.add(client)
        session.flush()
        session.add(
            AuditLog(
                team_id=project.team_id,
                project_id=project.id,
                actor_type="USER",
                actor_id=owner.id,
                action="mcp.oauth.client.registered",
                target_type="MCP_OAUTH_CLIENT",
                target_id=client.id,
                before_value=None,
                after_value={
                    "cognito_client_id": client_id,
                    "allowed_scopes": sorted(requested_scopes),
                    "pre_registered": True,
                },
            )
        )
        return {
            "mcp_oauth_client_id": str(client.id),
            "cognito_client_id": client.cognito_client_id,
            "project_id": str(project.id),
            "allowed_scopes": client.allowed_scopes,
            "dynamic_client_registration": False,
        }


def _revoke_mcp_oauth_client(args: argparse.Namespace) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session, session.begin():
        owner, project = _require_project_owner(session, args.owner_email, args.project_id)
        client = session.scalar(
            select(McpOAuthClient).where(
                McpOAuthClient.cognito_client_id == args.client_id.strip(),
                McpOAuthClient.project_id == project.id,
            )
        )
        if client is None:
            raise ConflictError("远程 MCP OAuth 客户端不存在")
        now = datetime.now(UTC)
        client.revoked_at = now
        client.revoke_reason = args.reason.strip()
        session.add(
            AuditLog(
                team_id=project.team_id,
                project_id=project.id,
                actor_type="USER",
                actor_id=owner.id,
                action="mcp.oauth.client.revoked",
                target_type="MCP_OAUTH_CLIENT",
                target_id=client.id,
                before_value={"revoked": False},
                after_value={"revoked": True, "reason": client.revoke_reason},
            )
        )
        return {"cognito_client_id": client.cognito_client_id, "revoked": True}


def _revoke_mcp_oauth_grant(args: argparse.Namespace) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session, session.begin():
        owner, project = _require_project_owner(session, args.owner_email, args.project_id)
        client = session.scalar(
            select(McpOAuthClient).where(
                McpOAuthClient.cognito_client_id == args.client_id.strip(),
                McpOAuthClient.project_id == project.id,
            )
        )
        user = session.scalar(
            select(User).where(func.lower(User.email) == args.member_email.strip().lower())
        )
        if client is None or user is None:
            raise ConflictError("客户端或成员不存在")
        grant = session.scalar(
            select(McpOAuthGrant).where(
                McpOAuthGrant.mcp_oauth_client_id == client.id,
                McpOAuthGrant.user_id == user.id,
            )
        )
        if grant is None:
            raise ConflictError("该成员尚未建立远程 MCP OAuth Grant")
        grant.revoked_at = datetime.now(UTC)
        grant.revoke_reason = args.reason.strip()
        session.add(
            AuditLog(
                team_id=project.team_id,
                project_id=project.id,
                actor_type="USER",
                actor_id=owner.id,
                action="mcp.oauth.grant.revoked",
                target_type="MCP_OAUTH_GRANT",
                target_id=grant.id,
                before_value={"revoked": False, "user_id": str(user.id)},
                after_value={"revoked": True, "reason": grant.revoke_reason},
            )
        )
        return {"grant_id": str(grant.id), "revoked": True}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="experiment-guardian-admin")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser("bootstrap-owner")
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--name", required=True)
    bootstrap.add_argument("--team-name", required=True)
    bootstrap.add_argument("--token-name", default="local-admin")
    bootstrap.add_argument("--ttl-days", type=int, default=7)
    bootstrap.set_defaults(handler=_bootstrap_owner)

    issue = subparsers.add_parser("issue-mcp-token")
    issue.add_argument("--owner-email", required=True)
    issue.add_argument("--project-id", required=True)
    issue.add_argument("--member-email")
    issue.add_argument("--token-name", default="local-agent")
    issue.add_argument("--ttl-days", type=int, default=30)
    issue.set_defaults(handler=_issue_mcp_token)

    add_researcher = subparsers.add_parser("add-researcher")
    add_researcher.add_argument("--owner-email", required=True)
    add_researcher.add_argument("--project-id", required=True)
    add_researcher.add_argument("--email", required=True)
    add_researcher.add_argument("--name", required=True)
    add_researcher.set_defaults(handler=_add_researcher)

    issue_api = subparsers.add_parser("issue-api-token")
    issue_api.add_argument("--owner-email", required=True)
    issue_api.add_argument("--project-id", required=True)
    issue_api.add_argument("--member-email", required=True)
    issue_api.add_argument("--token-name", default="review-client")
    issue_api.add_argument("--ttl-days", type=int, default=7)
    issue_api.set_defaults(handler=_issue_api_token)

    revoke = subparsers.add_parser("revoke-token")
    revoke.add_argument("--token-id", required=True)
    revoke.set_defaults(handler=_revoke_token)

    register_oauth = subparsers.add_parser("register-mcp-oauth-client")
    register_oauth.add_argument("--owner-email", required=True)
    register_oauth.add_argument("--project-id", required=True)
    register_oauth.add_argument("--client-id", required=True)
    register_oauth.add_argument("--name", required=True)
    register_oauth.add_argument(
        "--scopes", default=",".join(sorted(MCP_APPLICATION_SCOPES))
    )
    register_oauth.set_defaults(handler=_register_mcp_oauth_client)

    revoke_oauth = subparsers.add_parser("revoke-mcp-oauth-client")
    revoke_oauth.add_argument("--owner-email", required=True)
    revoke_oauth.add_argument("--project-id", required=True)
    revoke_oauth.add_argument("--client-id", required=True)
    revoke_oauth.add_argument("--reason", required=True)
    revoke_oauth.set_defaults(handler=_revoke_mcp_oauth_client)

    revoke_grant = subparsers.add_parser("revoke-mcp-oauth-grant")
    revoke_grant.add_argument("--owner-email", required=True)
    revoke_grant.add_argument("--project-id", required=True)
    revoke_grant.add_argument("--client-id", required=True)
    revoke_grant.add_argument("--member-email", required=True)
    revoke_grant.add_argument("--reason", required=True)
    revoke_grant.set_defaults(handler=_revoke_mcp_oauth_grant)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.handler(args)
    except (ApplicationError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
