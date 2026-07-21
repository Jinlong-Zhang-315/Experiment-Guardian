"""可信本地管理员 CLI：只引导身份和 Token，不创建业务上下文。"""

import argparse
import json
import sys
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
from experiment_guardian.infrastructure.models import Project, Team, TeamMember, User
from experiment_guardian.infrastructure.security import IssuedToken


def _token_output(kind: str, issued: IssuedToken) -> dict[str, Any]:
    return {
        "kind": kind,
        "token_id": str(issued.token_id),
        "access_token": issued.raw_token,
        "token_prefix": issued.token_prefix,
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
            scopes={"plan:approve", "project:initialize"},
            lifetime_days=args.ttl_days,
            created_by=user.id,
        )
        return {
            "user_id": str(user.id),
            "team_id": str(team.id),
            **_token_output("API", issued),
        }


def _issue_mcp_token(args: argparse.Namespace) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session, session.begin():
        user = session.scalar(
            select(User).where(func.lower(User.email) == args.owner_email.strip().lower())
        )
        project = session.get(Project, UUID(args.project_id))
        if user is None or project is None:
            raise ConflictError("Owner 或项目不存在")
        member = session.get(TeamMember, {"team_id": project.team_id, "user_id": user.id})
        if member is None or member.role is not TeamRole.OWNER:
            raise AuthorizationError("只有项目团队 Owner 可以签发 MCP Token")
        issued = get_token_service().issue(
            session,
            user_id=user.id,
            team_id=project.team_id,
            project_id=project.id,
            audience=TokenAudience.MCP,
            name=args.token_name,
            scopes={
                "experiment:check",
                "manifest:create",
                "project:read",
                "submission:create",
                "submission:finalize",
            },
            lifetime_days=args.ttl_days,
            created_by=user.id,
        )
        return {"project_id": str(project.id), **_token_output("MCP", issued)}


def _revoke_token(args: argparse.Namespace) -> dict[str, Any]:
    factory = get_session_factory()
    token_id = UUID(args.token_id)
    with factory() as session, session.begin():
        get_token_service().revoke(session, token_id)
    return {"token_id": str(token_id), "revoked": True}


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
    issue.add_argument("--token-name", default="local-agent")
    issue.add_argument("--ttl-days", type=int, default=30)
    issue.set_defaults(handler=_issue_mcp_token)

    revoke = subparsers.add_parser("revoke-token")
    revoke.add_argument("--token-id", required=True)
    revoke.set_defaults(handler=_revoke_token)
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
