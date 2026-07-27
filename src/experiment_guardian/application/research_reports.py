"""项目级候选研究报告读取服务。"""

import base64
import hashlib
import json
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import (
    AuthorizationError,
    ConflictError,
    InputValidationError,
    ResourceNotFoundError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.research_memories import ResearchMemoryService
from experiment_guardian.domain.agent_research import (
    AgentResearchReportPayload,
    ResearchReportPage,
    ResearchReportSourceWarning,
    ResearchReportSummary,
    ResearchReportView,
    research_report_source_hash,
    validate_report_against_source,
)
from experiment_guardian.infrastructure.models import (
    AgentResearchReport,
    AgentToolCall,
    Experiment,
)
from experiment_guardian.infrastructure.repositories import SqlAlchemyProjectRepository


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"offset": offset}).encode()).decode()


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        offset = value["offset"]
        if type(offset) is not int or offset < 0:
            raise ValueError
        return offset
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise InputValidationError("无效的研究报告分页游标") from exc


class ResearchReportService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        projects: SqlAlchemyProjectRepository,
        research_memories: ResearchMemoryService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._projects = projects
        self._research_memories = research_memories

    def list_reports(
        self,
        *,
        project_id: UUID,
        identity: RequestIdentity,
        cursor: str | None,
        limit: int,
    ) -> ResearchReportPage:
        self._require_identity(identity)
        offset = _decode_cursor(cursor)
        with self._session_factory() as session:
            self._require_project(session, project_id, identity)
            rows = list(
                session.scalars(
                    select(AgentResearchReport)
                    .where(AgentResearchReport.project_id == project_id)
                    .order_by(
                        AgentResearchReport.created_at.desc(),
                        AgentResearchReport.id.desc(),
                    )
                    .offset(offset)
                    .limit(limit + 1)
                ).all()
            )
            return ResearchReportPage(
                items=[self._summary(item) for item in rows[:limit]],
                next_cursor=_encode_cursor(offset + limit) if len(rows) > limit else None,
            )

    def get_report(
        self,
        *,
        project_id: UUID,
        report_id: UUID,
        identity: RequestIdentity,
    ) -> ResearchReportView:
        self._require_identity(identity)
        with self._session_factory() as session:
            self._require_project(session, project_id, identity)
            row = session.scalar(
                select(AgentResearchReport).where(
                    AgentResearchReport.id == report_id,
                    AgentResearchReport.project_id == project_id,
                )
            )
            if row is None:
                raise ResourceNotFoundError("项目中不存在该研究报告")
            try:
                payload = AgentResearchReportPayload.model_validate(row.report_payload)
                validate_report_against_source(payload, row.source_snapshot)
            except (ValidationError, ValueError) as exc:
                raise ConflictError("研究报告的不可变内容或引用已损坏") from exc
            source_tool = session.get(AgentToolCall, row.source_tool_call_id)
            content = row.source_snapshot.get("content")
            selected_ids = [str(item) for item in payload.selected_experiment_ids]
            if (
                self._json_hash(row.report_payload) != row.payload_hash
                or payload.source_hash != row.source_hash
                or not isinstance(content, dict)
                or research_report_source_hash(content) != row.source_hash
                or row.title != payload.title
                or [str(item) for item in row.experiment_ids] != selected_ids
                or content.get("experiment_ids") != selected_ids
                or row.objective != content.get("objective")
                or row.metric_name != content.get("metric_name")
                or row.include_historical != content.get("include_historical")
                or source_tool is None
                or source_tool.output_hash != self._json_hash(row.source_snapshot)
            ):
                raise ConflictError("研究报告的来源或内容哈希不一致")
            warnings = self._source_warnings(session, row)
            memory_indexes, materialization_pending = (
                self._research_memories.report_indexes(session, row)
                if self._research_memories is not None
                else ([], bool(payload.findings))
            )
            summary = self._summary(row)
            return ResearchReportView(
                **summary.model_dump(),
                schema_version=row.schema_version,
                source_snapshot=row.source_snapshot,
                report=payload,
                payload_hash=row.payload_hash,
                source_thread_id=row.source_thread_id,
                source_run_id=row.source_run_id,
                final_message_id=row.final_message_id,
                source_warnings=warnings,
                research_memories=memory_indexes,
                memory_materialization_pending=materialization_pending,
            )

    def _require_project(
        self, session: Session, project_id: UUID, identity: RequestIdentity
    ) -> None:
        if identity.project_id is not None and identity.project_id != project_id:
            raise AuthorizationError("当前身份绑定到其他项目")
        self._projects.require_project_member(
            session,
            project_id=project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )

    @staticmethod
    def _require_identity(identity: RequestIdentity) -> None:
        if identity.authentication_method != "WEB_SESSION":
            raise AuthorizationError("研究报告只允许通过服务端 Web Session 使用")
        if "experiment:read" not in identity.scopes:
            raise AuthorizationError("当前身份缺少 experiment:read scope")

    @staticmethod
    def _summary(row: AgentResearchReport) -> ResearchReportSummary:
        return ResearchReportSummary(
            report_id=row.id,
            project_id=row.project_id,
            created_by=row.created_by,
            title=row.title,
            objective=row.objective,
            experiment_ids=[UUID(str(item)) for item in row.experiment_ids],
            metric_name=row.metric_name,
            include_historical=row.include_historical,
            source_hash=row.source_hash,
            provider=row.provider,
            model_id=row.model_id,
            prompt_version=row.prompt_version,
            created_at=row.created_at,
        )

    @staticmethod
    def _source_warnings(
        session: Session, row: AgentResearchReport
    ) -> list[ResearchReportSourceWarning]:
        snapshots = {
            str(item.get("experiment_id")): str(item.get("status"))
            for item in row.source_snapshot.get("content", {}).get("experiments", [])
            if isinstance(item, dict) and item.get("experiment_id") and item.get("status")
        }
        warnings: list[ResearchReportSourceWarning] = []
        for experiment_id in row.experiment_ids:
            key = str(experiment_id)
            snapshot_status = snapshots.get(key, "UNKNOWN")
            experiment = session.get(Experiment, UUID(key))
            if experiment is None:
                warnings.append(
                    ResearchReportSourceWarning(
                        code="SOURCE_MISSING",
                        experiment_id=UUID(key),
                        snapshot_status=snapshot_status,
                        message="报告来源实验当前不可读取；报告仍保留生成时快照。",
                    )
                )
            elif experiment.status.value != snapshot_status:
                warnings.append(
                    ResearchReportSourceWarning(
                        code="SOURCE_STATUS_CHANGED",
                        experiment_id=experiment.id,
                        snapshot_status=snapshot_status,
                        current_status=experiment.status.value,
                        message=(
                            f"实验状态已从 {snapshot_status} 变为 {experiment.status.value}；"
                            "报告内容未被追溯修改。"
                        ),
                    )
                )
        return warnings

    @staticmethod
    def _json_hash(value: object) -> str:
        return hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
