"""候选 Research Memory 物化、检索和可恢复 embedding 处理。"""

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import Float, bindparam, cast, or_, select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import (
    AuthorizationError,
    ConflictError,
    ResourceNotFoundError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.ports import EmbeddingGenerator
from experiment_guardian.application.transactions import run_with_serialization_retry
from experiment_guardian.core.config import Settings
from experiment_guardian.domain.agent_research import (
    AgentResearchReportPayload,
    research_report_source_hash,
)
from experiment_guardian.domain.enums import (
    IdempotencyOperationStatus,
    ResearchMemoryEmbeddingStatus,
    ResearchMemoryStatus,
    TeamRole,
)
from experiment_guardian.domain.research_memory import (
    MAX_RESEARCH_MEMORY_CANDIDATES,
    RESEARCH_MEMORY_DOCUMENT_VERSION,
    ResearchMemoryIndexView,
    ResearchMemoryRetryResult,
    ResearchMemorySearchRequest,
    ResearchMemorySearchResponse,
    ResearchMemorySearchResult,
    build_research_memory_document,
    canonical_hash,
    research_memory_type,
    text_hash,
)
from experiment_guardian.infrastructure.models import (
    AgentResearchMemory,
    AgentResearchMemoryEmbedding,
    AgentResearchReport,
    AuditLog,
    Experiment,
    IdempotencyRecord,
    TeamMember,
)
from experiment_guardian.infrastructure.models.base import VectorType
from experiment_guardian.infrastructure.repositories import SqlAlchemyProjectRepository

RETRY_OPERATION = "AGENT_RESEARCH_MEMORY_EMBEDDING_RETRY"
SourceFreshness = Literal["CURRENT", "SOURCE_CHANGED", "SOURCE_MISSING"]


def materialize_report_memories(
    session: Session,
    report: AgentResearchReport,
    payload: AgentResearchReportPayload,
) -> list[AgentResearchMemory]:
    """在当前事务中幂等物化 finding，不调用任何外部服务。"""

    existing = {
        item.finding_id: item
        for item in session.scalars(
            select(AgentResearchMemory).where(AgentResearchMemory.report_id == report.id)
        ).all()
    }
    source_experiments = report.source_snapshot.get("content", {}).get("experiments", [])
    if not isinstance(source_experiments, list):
        raise ValueError("研究报告来源实验格式无效")
    references: list[dict[str, object]] = []
    protocols: set[str] = set()
    for item in source_experiments:
        if not isinstance(item, dict) or not isinstance(item.get("experiment_id"), str):
            raise ValueError("研究报告来源实验格式无效")
        protocol = item.get("protocol")
        if isinstance(protocol, str):
            protocols.add(protocol)
        references.append(
            {
                "experiment_id": item["experiment_id"],
                "status": item.get("status"),
                "dataset": item.get("dataset"),
                "protocol": protocol,
                "trace": item.get("trace"),
            }
        )
    experiment_ids = [str(item) for item in payload.selected_experiment_ids]
    result: list[AgentResearchMemory] = []
    for finding in payload.findings:
        if finding.finding_id in existing:
            result.append(existing[finding.finding_id])
            continue
        memory_type = research_memory_type(finding.kind)
        document = build_research_memory_document(
            title=payload.title,
            objective=report.objective,
            memory_type=memory_type,
            statement=finding.statement,
            rationale=finding.rationale,
            limitations=finding.limitations,
            experiment_ids=experiment_ids,
        )
        content_hash = canonical_hash(
            {
                "finding_id": finding.finding_id,
                "memory_type": memory_type.value,
                "statement": finding.statement,
                "rationale": finding.rationale,
                "limitations": finding.limitations,
                "citation_ids": finding.citation_ids,
                "experiment_ids": experiment_ids,
                "source_references": references,
                "document": document,
                "document_version": RESEARCH_MEMORY_DOCUMENT_VERSION,
            }
        )
        memory = AgentResearchMemory(
            team_id=report.team_id,
            project_id=report.project_id,
            report_id=report.id,
            created_by=report.created_by,
            finding_id=finding.finding_id,
            memory_type=memory_type,
            status=ResearchMemoryStatus.CANDIDATE,
            statement=finding.statement,
            rationale=finding.rationale,
            limitations=list(finding.limitations),
            citation_ids=list(finding.citation_ids),
            experiment_ids=experiment_ids,
            protocols=sorted(protocols),
            source_references=references,
            report_source_hash=report.source_hash,
            report_payload_hash=report.payload_hash,
            embedding_document=document,
            document_version=RESEARCH_MEMORY_DOCUMENT_VERSION,
            content_hash=content_hash,
        )
        session.add(memory)
        result.append(memory)
    session.flush()
    return result


def research_memory_integrity_valid(
    session: Session, memory: AgentResearchMemory
) -> bool:
    report = session.get(AgentResearchReport, memory.report_id)
    try:
        expected_hash = canonical_hash(
            {
                "finding_id": memory.finding_id,
                "memory_type": memory.memory_type.value,
                "statement": memory.statement,
                "rationale": memory.rationale,
                "limitations": memory.limitations,
                "citation_ids": memory.citation_ids,
                "experiment_ids": memory.experiment_ids,
                "source_references": memory.source_references,
                "document": memory.embedding_document,
                "document_version": memory.document_version,
            }
        )
        return bool(
            report is not None
            and report.source_hash == memory.report_source_hash
            and report.payload_hash == memory.report_payload_hash
            and canonical_hash(report.report_payload) == report.payload_hash
            and research_report_source_hash(report.source_snapshot["content"])
            == report.source_hash
            and expected_hash == memory.content_hash
        )
    except (KeyError, TypeError, ValueError):
        return False


@dataclass(frozen=True, slots=True)
class ResearchMemoryEmbeddingClaim:
    embedding_id: UUID
    memory_id: UUID
    generation: int
    worker_id: str
    input_text: str


class ResearchMemoryService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        projects: SqlAlchemyProjectRepository,
        generator: EmbeddingGenerator,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._projects = projects
        self._generator = generator
        self._settings = settings

    def report_indexes(
        self, session: Session, report: AgentResearchReport
    ) -> tuple[list[ResearchMemoryIndexView], bool]:
        memories = list(
            session.scalars(
                select(AgentResearchMemory)
                .where(AgentResearchMemory.report_id == report.id)
                .order_by(AgentResearchMemory.finding_id)
            ).all()
        )
        indexes: list[ResearchMemoryIndexView] = []
        for memory in memories:
            if not research_memory_integrity_valid(session, memory):
                raise ConflictError("候选研究记忆内容或来源哈希不一致")
            embedding = session.scalar(self._current_embedding_statement(memory.id))
            freshness, _ = self._freshness(session, memory)
            indexes.append(
                ResearchMemoryIndexView(
                    memory_id=memory.id,
                    finding_id=memory.finding_id,
                    memory_type=memory.memory_type,
                    status=memory.status,
                    source_freshness=freshness,
                    embedding_status=(
                        embedding.status if embedding else "NOT_SCHEDULED"
                    ),
                    provider=embedding.provider if embedding else None,
                    model_id=embedding.model_id if embedding else None,
                    document_version=memory.document_version,
                    last_error=embedding.last_error if embedding else None,
                )
            )
        expected = len(report.report_payload.get("findings", []))
        return indexes, len(memories) != expected

    def search(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        request: ResearchMemorySearchRequest,
    ) -> ResearchMemorySearchResponse:
        self._authorize(project_id, identity)
        with self._session_factory() as session:
            statement = (
                select(AgentResearchMemory, AgentResearchMemoryEmbedding)
                .join(
                    AgentResearchMemoryEmbedding,
                    AgentResearchMemoryEmbedding.memory_id == AgentResearchMemory.id,
                )
                .where(
                    AgentResearchMemory.project_id == project_id,
                    AgentResearchMemory.team_id == identity.team_id,
                    AgentResearchMemory.status == ResearchMemoryStatus.CANDIDATE,
                    AgentResearchMemoryEmbedding.status
                    == ResearchMemoryEmbeddingStatus.READY,
                    AgentResearchMemoryEmbedding.provider == self._generator.provider,
                    AgentResearchMemoryEmbedding.model_id == self._generator.model_id,
                    AgentResearchMemoryEmbedding.dimension == self._generator.dimension,
                    AgentResearchMemoryEmbedding.normalized.is_(True),
                    AgentResearchMemoryEmbedding.document_version
                    == RESEARCH_MEMORY_DOCUMENT_VERSION,
                )
                .order_by(AgentResearchMemory.created_at.desc(), AgentResearchMemory.id)
                .limit(MAX_RESEARCH_MEMORY_CANDIDATES + 1)
            )
            if request.memory_types:
                statement = statement.where(
                    AgentResearchMemory.memory_type.in_(request.memory_types)
                )
            rows = list(session.execute(statement).all())
            truncated = len(rows) > MAX_RESEARCH_MEMORY_CANDIDATES
            candidates: list[
                tuple[
                    AgentResearchMemory,
                    AgentResearchMemoryEmbedding,
                    SourceFreshness,
                    list[str],
                ]
            ] = []
            requested_ids = {str(item) for item in request.experiment_ids}
            for memory, embedding in rows[:MAX_RESEARCH_MEMORY_CANDIDATES]:
                if (
                    not research_memory_integrity_valid(session, memory)
                    or embedding.input_sha256 != text_hash(memory.embedding_document)
                ):
                    raise ConflictError("候选研究记忆或 embedding 哈希不一致")
                if request.protocol and request.protocol not in memory.protocols:
                    continue
                if requested_ids and not requested_ids.issubset(
                    {str(item) for item in memory.experiment_ids}
                ):
                    continue
                freshness, warnings = self._freshness(session, memory)
                if freshness != "CURRENT" and not request.include_stale:
                    continue
                candidates.append((memory, embedding, freshness, warnings))
            candidate_count = len(candidates)
            if not candidates:
                return ResearchMemorySearchResponse(
                    items=[], candidate_count=0, candidate_truncated=truncated
                )

        query_vector = self._validated_vector(self._generator.embed(request.query).vector)
        with self._session_factory() as session:
            embedding_ids = [item[1].id for item in candidates]
            ranked = self._rank(session, embedding_ids, query_vector, request.top_k)
        by_embedding = {item[1].id: item for item in candidates}
        items: list[ResearchMemorySearchResult] = []
        for embedding_id, similarity in ranked:
            memory, embedding, freshness, warnings = by_embedding[embedding_id]
            items.append(
                ResearchMemorySearchResult(
                    memory_id=memory.id,
                    report_id=memory.report_id,
                    finding_id=memory.finding_id,
                    memory_type=memory.memory_type,
                    statement=memory.statement,
                    rationale=memory.rationale,
                    limitations=[str(item) for item in memory.limitations],
                    citation_ids=[str(item) for item in memory.citation_ids],
                    experiment_ids=[UUID(str(item)) for item in memory.experiment_ids],
                    protocols=[str(item) for item in memory.protocols],
                    source_references=memory.source_references,
                    source_freshness=freshness,
                    source_warnings=warnings,
                    similarity=similarity,
                    provider=embedding.provider,
                    model_id=embedding.model_id,
                    document_version=embedding.document_version,
                    content_hash=memory.content_hash,
                )
            )
        return ResearchMemorySearchResponse(
            items=items,
            candidate_count=candidate_count,
            candidate_truncated=truncated,
        )

    def retry_embedding(
        self,
        *,
        project_id: UUID,
        memory_id: UUID,
        identity: RequestIdentity,
        idempotency_key: UUID,
    ) -> ResearchMemoryRetryResult:
        self._authorize(project_id, identity, owner=True)
        request_hash = canonical_hash({"memory_id": str(memory_id)})

        def operation() -> ResearchMemoryRetryResult:
            with self._session_factory() as session, session.begin():
                replay = session.scalar(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.actor_id == identity.user_id,
                        IdempotencyRecord.operation == RETRY_OPERATION,
                        IdempotencyRecord.idempotency_key == idempotency_key,
                    )
                )
                if replay is not None:
                    if replay.request_hash != request_hash:
                        raise ConflictError("相同 Idempotency-Key 已用于其他索引重试")
                    if replay.response_snapshot is None:
                        raise ConflictError("索引重试仍在处理中")
                    return ResearchMemoryRetryResult.model_validate(replay.response_snapshot)
                memory = session.scalar(
                    select(AgentResearchMemory).where(
                        AgentResearchMemory.id == memory_id,
                        AgentResearchMemory.project_id == project_id,
                    )
                )
                if memory is None:
                    raise ResourceNotFoundError("项目中不存在该候选研究记忆")
                embedding = session.scalar(
                    self._current_embedding_statement(memory.id).with_for_update()
                )
                if embedding is None:
                    embedding = self._new_embedding(memory)
                    session.add(embedding)
                elif embedding.status not in {
                    ResearchMemoryEmbeddingStatus.FAILED,
                    ResearchMemoryEmbeddingStatus.DEAD_LETTER,
                    ResearchMemoryEmbeddingStatus.RETRYABLE_FAILURE,
                }:
                    raise ConflictError("当前索引状态不允许手工重试")
                else:
                    embedding.status = ResearchMemoryEmbeddingStatus.PENDING
                    embedding.attempt_count = 0
                    embedding.generation += 1
                    embedding.available_at = datetime.now(UTC)
                    embedding.lease_owner = None
                    embedding.lease_expires_at = None
                    embedding.last_error = None
                    embedding.completed_at = None
                    embedding.embedding = None
                    embedding.normalized = False
                session.flush()
                result = ResearchMemoryRetryResult(
                    memory_id=memory.id,
                    embedding_status=embedding.status,
                    provider=embedding.provider,
                    model_id=embedding.model_id,
                )
                session.add(
                    IdempotencyRecord(
                        actor_id=identity.user_id,
                        operation=RETRY_OPERATION,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        response_snapshot=result.model_dump(mode="json"),
                        operation_status=IdempotencyOperationStatus.COMPLETED,
                    )
                )
                session.add(
                    AuditLog(
                        team_id=identity.team_id,
                        project_id=project_id,
                        actor_type="USER",
                        actor_id=identity.user_id,
                        action="agent.research_memory.embedding_retried",
                        target_type="AGENT_RESEARCH_MEMORY",
                        target_id=memory.id,
                        before_value=None,
                        after_value={
                            "provider": embedding.provider,
                            "model_id": embedding.model_id,
                            "session_id": str(identity.token_id),
                        },
                    )
                )
                return result

        return run_with_serialization_retry(operation)

    def _authorize(
        self, project_id: UUID, identity: RequestIdentity, *, owner: bool = False
    ) -> None:
        if identity.authentication_method != "WEB_SESSION":
            raise AuthorizationError("研究记忆只允许通过服务端 Web Session 使用")
        if "experiment:read" not in identity.scopes:
            raise AuthorizationError("当前身份缺少 experiment:read scope")
        with self._session_factory() as session:
            self._projects.require_project_member(
                session,
                project_id=project_id,
                user_id=identity.user_id,
                team_id=identity.team_id,
            )
            if owner:
                membership = session.get(TeamMember, (identity.team_id, identity.user_id))
                if membership is None or membership.role is not TeamRole.OWNER:
                    raise AuthorizationError("只有 Owner 可以重试研究记忆索引")

    def _current_embedding_statement(self, memory_id: UUID):  # type: ignore[no-untyped-def]
        return select(AgentResearchMemoryEmbedding).where(
            AgentResearchMemoryEmbedding.memory_id == memory_id,
            AgentResearchMemoryEmbedding.provider == self._generator.provider,
            AgentResearchMemoryEmbedding.model_id == self._generator.model_id,
            AgentResearchMemoryEmbedding.dimension == self._generator.dimension,
            AgentResearchMemoryEmbedding.document_version
            == RESEARCH_MEMORY_DOCUMENT_VERSION,
        )

    def _new_embedding(
        self, memory: AgentResearchMemory
    ) -> AgentResearchMemoryEmbedding:
        return AgentResearchMemoryEmbedding(
            memory_id=memory.id,
            provider=self._generator.provider,
            model_id=self._generator.model_id,
            dimension=self._generator.dimension,
            document_version=memory.document_version,
            input_sha256=text_hash(memory.embedding_document),
            normalized=False,
            status=ResearchMemoryEmbeddingStatus.PENDING,
            attempt_count=0,
            max_attempts=self._settings.agent_run_max_attempts,
            generation=0,
        )

    @staticmethod
    def _freshness(
        session: Session, memory: AgentResearchMemory
    ) -> tuple[
        SourceFreshness,
        list[str],
    ]:
        warnings: list[str] = []
        freshness: SourceFreshness = "CURRENT"
        for reference in memory.source_references:
            experiment_id = reference.get("experiment_id")
            if not isinstance(experiment_id, str):
                freshness = "SOURCE_MISSING"
                warnings.append("来源实验标识缺失")
                continue
            try:
                parsed_experiment_id = UUID(experiment_id)
            except ValueError:
                freshness = "SOURCE_MISSING"
                warnings.append(f"来源实验标识无效: {experiment_id}")
                continue
            experiment = session.get(Experiment, parsed_experiment_id)
            if experiment is None:
                freshness = "SOURCE_MISSING"
                warnings.append(f"来源实验 {experiment_id} 当前不可读取")
            elif experiment.status.value != reference.get("status"):
                if freshness != "SOURCE_MISSING":
                    freshness = "SOURCE_CHANGED"
                warnings.append(
                    f"来源实验 {experiment_id} 状态已从 {reference.get('status')} "
                    f"变为 {experiment.status.value}"
                )
        return freshness, warnings

    @staticmethod
    def _validated_vector(vector: list[float]) -> list[float]:
        if len(vector) != 1024 or any(not math.isfinite(item) for item in vector):
            raise ValueError("查询 embedding 必须是 1024 维有限向量")
        return vector

    @staticmethod
    def _rank(
        session: Session, embedding_ids: list[UUID], vector: list[float], top_k: int
    ) -> list[tuple[UUID, float]]:
        if session.bind is not None and session.bind.dialect.name == "sqlite":
            entity_rows = session.scalars(
                select(AgentResearchMemoryEmbedding).where(
                    AgentResearchMemoryEmbedding.id.in_(embedding_ids)
                )
            ).all()
            ranked = sorted(
                (
                    (
                        item.id,
                        sum(
                            a * b
                            for a, b in zip(item.embedding or [], vector, strict=True)
                        ),
                    )
                    for item in entity_rows
                ),
                key=lambda item: (-item[1], str(item[0])),
            )
            return ranked[:top_k]
        query_vector = bindparam("query_vector", type_=VectorType(1024))
        distance = cast(
            AgentResearchMemoryEmbedding.embedding.op("<=>")(
                cast(query_vector, VectorType(1024))
            ),
            Float,
        )
        distance_rows = session.execute(
            select(AgentResearchMemoryEmbedding.id, distance.label("distance"))
            .where(AgentResearchMemoryEmbedding.id.in_(embedding_ids))
            .order_by(distance, AgentResearchMemoryEmbedding.id)
            .limit(top_k),
            {"query_vector": vector},
        ).all()
        return [
            (row.id, max(-1.0, min(1.0, 1.0 - float(row.distance))))
            for row in distance_rows
        ]


class ResearchMemoryEmbeddingProcessor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        generator: EmbeddingGenerator,
        settings: Settings,
        *,
        worker_id: str,
    ) -> None:
        self._session_factory = session_factory
        self._generator = generator
        self._settings = settings
        self._worker_id = worker_id

    def process_once(self) -> bool:
        reconciled = run_with_serialization_retry(self._reconcile_once)
        claim = run_with_serialization_retry(self._claim)
        if claim is None:
            return reconciled
        started = time.monotonic()
        try:
            output = self._generator.embed(claim.input_text)
            vector = ResearchMemoryService._validated_vector(output.vector)
        except Exception as caught:
            error = caught

            def mark_failure() -> None:
                self._mark_failure(claim, error)

            run_with_serialization_retry(mark_failure)
            return True
        run_with_serialization_retry(
            lambda: self._mark_ready(
                claim,
                vector,
                output.input_tokens,
                int((time.monotonic() - started) * 1000),
            )
        )
        return True

    def _reconcile_once(self) -> bool:
        with self._session_factory() as session, session.begin():
            changed = False
            reports = list(
                session.scalars(
                    select(AgentResearchReport)
                    .where(
                        ~AgentResearchReport.id.in_(
                            select(AgentResearchMemory.report_id)
                        )
                    )
                    .order_by(AgentResearchReport.created_at, AgentResearchReport.id)
                    .limit(10)
                    .with_for_update(skip_locked=True)
                ).all()
            )
            for report in reports:
                count = session.scalar(
                    select(AgentResearchMemory.id)
                    .where(AgentResearchMemory.report_id == report.id)
                    .limit(1)
                )
                if count is None:
                    payload = AgentResearchReportPayload.model_validate(report.report_payload)
                    materialize_report_memories(session, report, payload)
                    changed = True
            memories = list(
                session.scalars(
                    select(AgentResearchMemory)
                    .where(
                        ~AgentResearchMemory.id.in_(
                            select(AgentResearchMemoryEmbedding.memory_id).where(
                                AgentResearchMemoryEmbedding.provider
                                == self._generator.provider,
                                AgentResearchMemoryEmbedding.model_id
                                == self._generator.model_id,
                                AgentResearchMemoryEmbedding.document_version
                                == RESEARCH_MEMORY_DOCUMENT_VERSION,
                            )
                        )
                    )
                    .order_by(AgentResearchMemory.created_at, AgentResearchMemory.id)
                    .limit(10)
                    .with_for_update(skip_locked=True)
                ).all()
            )
            for memory in memories:
                session.add(
                    AgentResearchMemoryEmbedding(
                        memory_id=memory.id,
                        provider=self._generator.provider,
                        model_id=self._generator.model_id,
                        dimension=self._generator.dimension,
                        document_version=memory.document_version,
                        input_sha256=text_hash(memory.embedding_document),
                        normalized=False,
                        status=ResearchMemoryEmbeddingStatus.PENDING,
                        attempt_count=0,
                        max_attempts=self._settings.agent_run_max_attempts,
                        generation=0,
                    )
                )
                changed = True
            return changed

    def _claim(self) -> ResearchMemoryEmbeddingClaim | None:
        now = datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            row = session.scalar(
                select(AgentResearchMemoryEmbedding)
                .where(
                    AgentResearchMemoryEmbedding.available_at <= now,
                    or_(
                        AgentResearchMemoryEmbedding.status.in_(
                            {
                                ResearchMemoryEmbeddingStatus.PENDING,
                                ResearchMemoryEmbeddingStatus.RETRYABLE_FAILURE,
                            }
                        ),
                        (
                            AgentResearchMemoryEmbedding.status
                            == ResearchMemoryEmbeddingStatus.RUNNING
                        )
                        & (AgentResearchMemoryEmbedding.lease_expires_at <= now),
                    ),
                )
                .order_by(
                    AgentResearchMemoryEmbedding.available_at,
                    AgentResearchMemoryEmbedding.created_at,
                    AgentResearchMemoryEmbedding.id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            if row.attempt_count >= row.max_attempts:
                row.status = ResearchMemoryEmbeddingStatus.DEAD_LETTER
                row.completed_at = now
                row.lease_owner = None
                row.lease_expires_at = None
                memory = session.get(AgentResearchMemory, row.memory_id)
                if memory is not None:
                    self._audit_terminal(session, memory, row)
                return None
            memory = session.get(AgentResearchMemory, row.memory_id)
            if (
                memory is None
                or not research_memory_integrity_valid(session, memory)
                or text_hash(memory.embedding_document) != row.input_sha256
            ):
                row.status = ResearchMemoryEmbeddingStatus.FAILED
                row.last_error = {
                    "code": "RESEARCH_MEMORY_INPUT_INVALID",
                    "message": "研究记忆 embedding 输入缺失或哈希不一致",
                    "retryable": False,
                }
                row.completed_at = now
                if memory is not None:
                    self._audit_terminal(session, memory, row)
                return None
            row.status = ResearchMemoryEmbeddingStatus.RUNNING
            row.generation += 1
            row.attempt_count += 1
            row.lease_owner = self._worker_id
            row.lease_expires_at = now + timedelta(
                seconds=self._settings.agent_run_lease_seconds
            )
            return ResearchMemoryEmbeddingClaim(
                embedding_id=row.id,
                memory_id=memory.id,
                generation=row.generation,
                worker_id=self._worker_id,
                input_text=memory.embedding_document,
            )

    def _mark_ready(
        self,
        claim: ResearchMemoryEmbeddingClaim,
        vector: list[float],
        input_tokens: int | None,
        latency_ms: int,
    ) -> None:
        with self._session_factory() as session, session.begin():
            row = session.get(
                AgentResearchMemoryEmbedding, claim.embedding_id, with_for_update=True
            )
            if not self._owns(row, claim):
                return
            assert row is not None
            row.embedding = vector
            row.normalized = True
            row.status = ResearchMemoryEmbeddingStatus.READY
            row.input_tokens = input_tokens
            row.latency_ms = latency_ms
            row.last_error = None
            row.completed_at = datetime.now(UTC)
            row.lease_owner = None
            row.lease_expires_at = None

    def _mark_failure(
        self, claim: ResearchMemoryEmbeddingClaim, error: Exception
    ) -> None:
        now = datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            row = session.get(
                AgentResearchMemoryEmbedding, claim.embedding_id, with_for_update=True
            )
            if not self._owns(row, claim):
                return
            assert row is not None
            exhausted = row.attempt_count >= row.max_attempts
            row.status = (
                ResearchMemoryEmbeddingStatus.DEAD_LETTER
                if exhausted
                else ResearchMemoryEmbeddingStatus.RETRYABLE_FAILURE
            )
            row.last_error = {
                "code": getattr(error, "code", "RESEARCH_MEMORY_EMBEDDING_FAILED"),
                "message": str(error)[:1000],
                "retryable": not exhausted,
            }
            if exhausted:
                row.completed_at = now
                memory = session.get(AgentResearchMemory, row.memory_id)
                if memory is not None:
                    self._audit_terminal(session, memory, row)
            else:
                delay = (5, 30, 120)[min(row.attempt_count - 1, 2)]
                row.available_at = now + timedelta(seconds=delay)
            row.lease_owner = None
            row.lease_expires_at = None

    @staticmethod
    def _audit_terminal(
        session: Session,
        memory: AgentResearchMemory,
        embedding: AgentResearchMemoryEmbedding,
    ) -> None:
        session.add(
            AuditLog(
                team_id=memory.team_id,
                project_id=memory.project_id,
                actor_type="SYSTEM",
                actor_id=memory.created_by,
                action="agent.research_memory.embedding_failed",
                target_type="AGENT_RESEARCH_MEMORY",
                target_id=memory.id,
                before_value=None,
                after_value={
                    "status": embedding.status.value,
                    "provider": embedding.provider,
                    "model_id": embedding.model_id,
                    "attempt_count": embedding.attempt_count,
                    "error": embedding.last_error,
                },
            )
        )

    @staticmethod
    def _owns(
        row: AgentResearchMemoryEmbedding | None,
        claim: ResearchMemoryEmbeddingClaim,
    ) -> bool:
        return bool(
            row is not None
            and row.status is ResearchMemoryEmbeddingStatus.RUNNING
            and row.generation == claim.generation
            and row.lease_owner == claim.worker_id
        )
