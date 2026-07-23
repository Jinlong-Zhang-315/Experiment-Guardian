"""治理 Agent 对话、租约和事件的 SQLAlchemy 仓储。"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from experiment_guardian.domain.enums import AgentCallStatus, AgentRunStatus
from experiment_guardian.infrastructure.models import (
    AgentModelCall,
    AgentRun,
    AgentRunEvent,
    AgentToolCall,
)


@dataclass(frozen=True, slots=True)
class AgentRunClaim:
    run_id: UUID
    generation: int
    worker_id: str


class SqlAlchemyAgentRepository:
    @staticmethod
    def get_run_for_update(session: Session, run_id: UUID) -> AgentRun | None:
        return session.scalar(
            select(AgentRun).where(AgentRun.id == run_id).with_for_update()
        )

    @staticmethod
    def append_event(
        session: Session,
        *,
        run: AgentRun,
        event_type: str,
        payload: dict[str, object],
    ) -> AgentRunEvent:
        next_sequence = (
            session.scalar(
                select(func.max(AgentRunEvent.sequence)).where(
                    AgentRunEvent.run_id == run.id
                )
            )
            or 0
        ) + 1
        event = AgentRunEvent(
            run_id=run.id,
            sequence=next_sequence,
            generation=run.generation,
            event_type=event_type,
            payload=payload,
        )
        session.add(event)
        session.flush()
        return event

    @staticmethod
    def list_events(
        session: Session, *, run_id: UUID, after: int, limit: int = 100
    ) -> list[AgentRunEvent]:
        return list(
            session.scalars(
                select(AgentRunEvent)
                .where(
                    AgentRunEvent.run_id == run_id,
                    AgentRunEvent.sequence > after,
                )
                .order_by(AgentRunEvent.sequence)
                .limit(limit)
            ).all()
        )

    @staticmethod
    def claim_next(
        session: Session,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> AgentRunClaim | None:
        now = datetime.now(UTC)
        run = session.scalar(
            select(AgentRun)
            .where(
                AgentRun.available_at <= now,
                or_(
                    AgentRun.status.in_(
                        {
                            AgentRunStatus.PENDING,
                            AgentRunStatus.RETRYABLE_FAILURE,
                        }
                    ),
                    (
                        (AgentRun.status == AgentRunStatus.RUNNING)
                        & (AgentRun.lease_expires_at <= now)
                    ),
                ),
            )
            .order_by(AgentRun.available_at, AgentRun.created_at, AgentRun.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if run is None:
            return None
        if run.attempt_count >= run.max_attempts:
            run.status = AgentRunStatus.DEAD_LETTER
            run.completed_at = now
            run.lease_owner = None
            run.lease_expires_at = None
            run.error = {
                "code": "AGENT_MAX_ATTEMPTS_EXCEEDED",
                "message": "治理 Agent 已达到最大重试次数",
                "retryable": False,
            }
            SqlAlchemyAgentRepository.append_event(
                session,
                run=run,
                event_type="run.failed",
                payload={"status": run.status.value, "error": run.error},
            )
            return None

        if run.status is AgentRunStatus.RUNNING:
            session.query(AgentModelCall).filter(
                AgentModelCall.run_id == run.id,
                AgentModelCall.generation == run.generation,
                AgentModelCall.status == AgentCallStatus.RUNNING,
            ).update(
                {
                    AgentModelCall.status: AgentCallStatus.ABANDONED,
                    AgentModelCall.completed_at: now,
                    AgentModelCall.error: {
                        "code": "AGENT_LEASE_EXPIRED",
                        "message": "Worker 租约过期，调用已废弃",
                    },
                },
                synchronize_session=False,
            )
            session.query(AgentToolCall).filter(
                AgentToolCall.run_id == run.id,
                AgentToolCall.generation == run.generation,
                AgentToolCall.status == AgentCallStatus.RUNNING,
            ).update(
                {
                    AgentToolCall.status: AgentCallStatus.ABANDONED,
                    AgentToolCall.completed_at: now,
                    AgentToolCall.error: {
                        "code": "AGENT_LEASE_EXPIRED",
                        "message": "Worker 租约过期，工具调用已废弃",
                    },
                },
                synchronize_session=False,
            )

        run.generation += 1
        run.attempt_count += 1
        run.status = AgentRunStatus.RUNNING
        run.lease_owner = worker_id
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        run.started_at = run.started_at or now
        run.error = None
        SqlAlchemyAgentRepository.append_event(
            session,
            run=run,
            event_type="run.started" if run.attempt_count == 1 else "run.retrying",
            payload={
                "status": run.status.value,
                "attempt": run.attempt_count,
                "max_attempts": run.max_attempts,
            },
        )
        session.flush()
        return AgentRunClaim(run_id=run.id, generation=run.generation, worker_id=worker_id)

    @staticmethod
    def renew_lease(
        session: Session,
        *,
        claim: AgentRunClaim,
        lease_seconds: int,
    ) -> bool:
        run = session.get(AgentRun, claim.run_id, with_for_update=True)
        if (
            run is None
            or run.status is not AgentRunStatus.RUNNING
            or run.generation != claim.generation
            or run.lease_owner != claim.worker_id
        ):
            return False
        run.lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        return True

    @staticmethod
    def owns_claim(session: Session, claim: AgentRunClaim) -> bool:
        run = session.get(AgentRun, claim.run_id)
        return bool(
            run is not None
            and run.status is AgentRunStatus.RUNNING
            and run.generation == claim.generation
            and run.lease_owner == claim.worker_id
        )
