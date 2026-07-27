"""治理 Agent 模型调用的只读、去内容化运行观测。"""

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import AuthorizationError
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.core.config import Settings
from experiment_guardian.domain.agent import (
    AgentCostTotal,
    AgentModelObservabilityView,
    AgentObservabilityGroup,
    AgentObservabilityTotals,
)
from experiment_guardian.domain.enums import AgentCallStatus, TeamRole
from experiment_guardian.infrastructure.models import AgentModelCall, AgentRun, TeamMember
from experiment_guardian.infrastructure.repositories import SqlAlchemyProjectRepository


class AgentObservabilityService:
    """只聚合元数据；请求、回答和工具内容不离开审计表。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        projects: SqlAlchemyProjectRepository,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._projects = projects
        self._settings = settings

    def get_project_observability(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        window_days: int,
        provider: str | None,
        model_id: str | None,
    ) -> AgentModelObservabilityView:
        self._require_owner_web_identity(identity)
        window_to = datetime.now(UTC)
        window_from = window_to - timedelta(days=window_days)
        with self._session_factory() as session:
            project = self._projects.require_project_member(
                session,
                project_id=project_id,
                user_id=identity.user_id,
                team_id=identity.team_id,
            )
            membership = session.scalar(
                select(TeamMember).where(
                    TeamMember.team_id == project.team_id,
                    TeamMember.user_id == identity.user_id,
                )
            )
            if membership is None or membership.role is not TeamRole.OWNER:
                raise AuthorizationError("只有项目 Owner 可以查看模型运行观测")

            run_statement = select(
                AgentRun.id,
                AgentRun.attempt_count,
            ).where(
                AgentRun.project_id == project.id,
                AgentRun.created_at >= window_from,
                AgentRun.created_at <= window_to,
            )
            call_statement = (
                select(
                    AgentModelCall.run_id,
                    AgentRun.attempt_count,
                    AgentModelCall.provider,
                    AgentModelCall.model_id,
                    AgentModelCall.purpose,
                    AgentModelCall.status,
                    AgentModelCall.usage,
                    AgentModelCall.latency_ms,
                    AgentModelCall.cost_currency,
                    AgentModelCall.estimated_cost,
                    AgentModelCall.error,
                )
                .join(AgentRun, AgentRun.id == AgentModelCall.run_id)
                .where(
                    AgentRun.project_id == project.id,
                    AgentModelCall.created_at >= window_from,
                    AgentModelCall.created_at <= window_to,
                )
            )
            if provider:
                run_statement = run_statement.where(AgentRun.provider == provider)
                call_statement = call_statement.where(AgentModelCall.provider == provider)
            if model_id:
                run_statement = run_statement.where(AgentRun.model_id == model_id)
                call_statement = call_statement.where(AgentModelCall.model_id == model_id)
            run_rows = list(session.execute(run_statement).all())
            call_rows = list(session.execute(call_statement).all())

        totals = self._totals(call_rows, {item.id: item.attempt_count for item in run_rows})
        grouped: dict[tuple[str, str, Any], list[Any]] = defaultdict(list)
        for row in call_rows:
            grouped[(row.provider, row.model_id, row.purpose)].append(row)
        groups = [
            AgentObservabilityGroup(
                provider=key[0],
                model_id=key[1],
                purpose=key[2],
                **self._totals(
                    rows,
                    {
                        row.run_id: row.attempt_count
                        for row in rows
                    },
                ).model_dump(),
            )
            for key, rows in sorted(grouped.items(), key=lambda item: tuple(map(str, item[0])))
        ]
        costs: dict[str, Decimal] = defaultdict(Decimal)
        failures: Counter[str] = Counter()
        for row in call_rows:
            if row.estimated_cost is not None and row.cost_currency is not None:
                costs[row.cost_currency] += row.estimated_cost
            if row.status is AgentCallStatus.FAILED:
                code = (
                    str(row.error.get("code", "AGENT_MODEL_FAILED"))
                    if isinstance(row.error, dict)
                    else "AGENT_MODEL_FAILED"
                )
                failures[code] += 1
        return AgentModelObservabilityView(
            project_id=project_id,
            window_from=window_from,
            window_to=window_to,
            current_provider=self._settings.agent_provider,
            current_model_id=self._settings.agent_model_id,
            pricing_configured=(
                self._settings.agent_input_cost_per_million_tokens is not None
                and self._settings.agent_output_cost_per_million_tokens is not None
            ),
            totals=totals,
            groups=groups,
            costs=[
                AgentCostTotal(currency=currency, estimated_cost=amount)
                for currency, amount in sorted(costs.items())
            ],
            failure_categories=dict(sorted(failures.items())),
        )

    @staticmethod
    def _totals(
        calls: list[Any],
        runs: dict[UUID, int],
    ) -> AgentObservabilityTotals:
        input_tokens = 0
        output_tokens = 0
        missing_usage = 0
        unpriced = 0
        latencies: list[int] = []
        terminal = {
            AgentCallStatus.SUCCEEDED,
            AgentCallStatus.FAILED,
            AgentCallStatus.ABANDONED,
        }
        for row in calls:
            usage = row.usage if isinstance(row.usage, dict) else {}
            input_value = usage.get("input_tokens")
            output_value = usage.get("output_tokens")
            valid_input = isinstance(input_value, int) and not isinstance(input_value, bool)
            valid_output = isinstance(output_value, int) and not isinstance(output_value, bool)
            if isinstance(input_value, int) and not isinstance(input_value, bool):
                input_tokens += input_value
            if isinstance(output_value, int) and not isinstance(output_value, bool):
                output_tokens += output_value
            if row.status in terminal and not (valid_input and valid_output):
                missing_usage += 1
            if row.status in terminal and row.estimated_cost is None:
                unpriced += 1
            if row.latency_ms is not None:
                latencies.append(row.latency_ms)
        return AgentObservabilityTotals(
            run_count=len(runs),
            model_call_count=len(calls),
            succeeded_call_count=sum(
                row.status is AgentCallStatus.SUCCEEDED for row in calls
            ),
            failed_call_count=sum(row.status is AgentCallStatus.FAILED for row in calls),
            abandoned_call_count=sum(
                row.status is AgentCallStatus.ABANDONED for row in calls
            ),
            retry_count=sum(max(attempts - 1, 0) for attempts in runs.values()),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            missing_usage_call_count=missing_usage,
            unpriced_call_count=unpriced,
            average_latency_ms=(round(sum(latencies) / len(latencies)) if latencies else None),
            maximum_latency_ms=max(latencies) if latencies else None,
        )

    @staticmethod
    def _require_owner_web_identity(identity: RequestIdentity) -> None:
        if identity.authentication_method != "WEB_SESSION":
            raise AuthorizationError("模型运行观测只接受 Web Session")
        if "project:write" not in identity.scopes:
            raise AuthorizationError("当前身份缺少 project:write scope")
