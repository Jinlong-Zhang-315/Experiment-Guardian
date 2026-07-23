"""治理 Agent 的最小只读工具目录。

工具参数不包含身份和项目 ID。执行器始终使用 Thread 绑定的项目与 Worker 实时恢复的
RequestIdentity，模型无法通过参数越权。
"""

from typing import Any
from uuid import UUID

from pydantic import Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.errors import (
    AuthorizationError,
    InputValidationError,
    ResourceNotFoundError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.domain.agent import (
    AgentEvidence,
    AgentToolResult,
    AgentToolSpec,
)
from experiment_guardian.domain.contracts import ContractModel
from experiment_guardian.domain.enums import (
    AgentEvidenceKind,
    ApprovalStatus,
    ExperimentStatus,
    SubmissionStatus,
    TeamRole,
)
from experiment_guardian.infrastructure.models import (
    Experiment,
    ExperimentMetric,
    ExperimentSubmission,
    PlanCheck,
    Project,
)
from experiment_guardian.infrastructure.repositories import SqlAlchemyProjectRepository

TOOL_CATALOG_VERSION = "r15a-v1"


class _EmptyArgs(ContractModel):
    pass


class _ExperimentListArgs(ContractModel):
    dataset: str | None = Field(default=None, min_length=1, max_length=200)
    protocol: str | None = Field(default=None, min_length=1, max_length=200)
    model_name: str | None = Field(default=None, min_length=1, max_length=300)
    status: ExperimentStatus | None = None
    limit: int = Field(default=10, ge=1, le=20)


class _ExperimentGetArgs(ContractModel):
    experiment_id: UUID


class _PendingWorkArgs(ContractModel):
    limit: int = Field(default=10, ge=1, le=20)


class AgentToolRegistry:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        projects: SqlAlchemyProjectRepository,
    ) -> None:
        self._session_factory = session_factory
        self._projects = projects
        self._definitions = {
            "project_status_get_v1": (
                _EmptyArgs,
                "读取当前正式项目目标、Context、Intent、约束及其版本。",
                self._project_status,
            ),
            "experiments_list_v1": (
                _ExperimentListArgs,
                "按正式结构化条件列出已确认实验及主要指标，最多返回 20 条。",
                self._experiments_list,
            ),
            "experiment_get_v1": (
                _ExperimentGetArgs,
                "读取一个正式实验的指标、摘要和完整版本追溯。",
                self._experiment_get,
            ),
            "pending_work_list_v1": (
                _PendingWorkArgs,
                "列出当前用户有权看到的待审批计划和待审核实验提交。",
                self._pending_work,
            ),
        }

    @property
    def specs(self) -> list[AgentToolSpec]:
        return [
            AgentToolSpec(
                name=name,
                version="1",
                description=description,
                input_schema=model.model_json_schema(),
            )
            for name, (model, description, _) in self._definitions.items()
        ]

    def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
    ) -> AgentToolResult:
        definition = self._definitions.get(tool_name)
        if definition is None:
            raise InputValidationError(f"Agent 请求了未注册工具: {tool_name}")
        model, _, handler = definition
        try:
            validated = model.model_validate(arguments)
        except ValidationError as exc:
            raise InputValidationError(f"{tool_name} 参数无效") from exc
        return handler(
            validated=validated,
            project_id=project_id,
            identity=identity,
            evidence_prefix=evidence_prefix,
        )

    def _project_status(
        self,
        *,
        validated: _EmptyArgs,
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
    ) -> AgentToolResult:
        del validated
        self._require_scope(identity, "project:read")
        with self._session_factory() as session:
            project = self._require_project(session, project_id, identity)
            bundle = self._projects.load_context_bundle(
                session,
                project_id=project_id,
                user_id=identity.user_id,
                team_id=identity.team_id,
            )
            evidence_id = f"{evidence_prefix}_1"
            structured = bundle.model_dump(mode="json")
            return AgentToolResult(
                content={
                    "project": {
                        "name": project.name,
                        "description": project.description,
                    },
                    "policy": structured,
                    "evidence_id": evidence_id,
                    "governance_notice": "所有治理判断以 policy 中的结构化数据为准。",
                },
                evidence=[
                    AgentEvidence(
                        evidence_id=evidence_id,
                        evidence_kind=AgentEvidenceKind.CONFIRMED_FACT,
                        entity_type="POLICY_BUNDLE",
                        entity_id=bundle.context.context_id,
                        entity_version=(
                            f"context:{bundle.context.version}/intent:"
                            f"{bundle.active_intent.version if bundle.active_intent else 'none'}"
                        ),
                        label=f"{project.name} 当前正式策略",
                        excerpt=(
                            f"Context v{bundle.context.version}；"
                            "Intent v"
                            f"{bundle.active_intent.version if bundle.active_intent else '无'}；"
                            f"{len(bundle.constraints)} 条正式约束"
                        ),
                        payload=structured,
                    )
                ],
            )

    def _experiments_list(
        self,
        *,
        validated: _ExperimentListArgs,
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
    ) -> AgentToolResult:
        self._require_scope(identity, "experiment:read")
        with self._session_factory() as session:
            self._require_project(session, project_id, identity)
            statement = select(Experiment).where(Experiment.project_id == project_id)
            if validated.dataset is not None:
                statement = statement.where(Experiment.dataset == validated.dataset)
            if validated.protocol is not None:
                statement = statement.where(Experiment.protocol == validated.protocol)
            if validated.model_name is not None:
                statement = statement.where(Experiment.model_name == validated.model_name)
            if validated.status is not None:
                statement = statement.where(Experiment.status == validated.status)
            rows = list(
                session.scalars(
                    statement.order_by(Experiment.confirmed_at.desc(), Experiment.id.desc())
                    .limit(validated.limit)
                ).all()
            )
            items: list[dict[str, Any]] = []
            evidence: list[AgentEvidence] = []
            for index, row in enumerate(rows, start=1):
                metrics = self._metrics(session, row.id)
                evidence_id = f"{evidence_prefix}_{index}"
                payload = self._experiment_payload(row, metrics)
                items.append({**payload, "evidence_id": evidence_id})
                evidence.append(self._experiment_evidence(evidence_id, row, payload))
            return AgentToolResult(
                content={
                    "items": items,
                    "count": len(items),
                    "limited_to": validated.limit,
                    "note": "结果为结构化过滤候选；本工具不进行统计或因果分析。",
                },
                evidence=evidence,
            )

    def _experiment_get(
        self,
        *,
        validated: _ExperimentGetArgs,
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
    ) -> AgentToolResult:
        self._require_scope(identity, "experiment:read")
        with self._session_factory() as session:
            self._require_project(session, project_id, identity)
            row = session.get(Experiment, validated.experiment_id)
            if row is None or row.project_id != project_id:
                raise ResourceNotFoundError("项目中不存在该正式 Experiment")
            payload = {
                **self._experiment_payload(row, self._metrics(session, row.id)),
                "summary": row.summary_snapshot,
                "review_receipt": row.review_receipt_snapshot,
                "trace": {
                    "submission_id": str(row.submission_id),
                    "run_manifest_id": str(row.run_manifest_id),
                    "context_id": str(row.project_context_id),
                    "context_version": row.project_context_version,
                    "intent_id": str(row.intent_id),
                    "intent_version": row.intent_version,
                },
            }
            evidence_id = f"{evidence_prefix}_1"
            return AgentToolResult(
                content={**payload, "evidence_id": evidence_id},
                evidence=[self._experiment_evidence(evidence_id, row, payload)],
            )

    def _pending_work(
        self,
        *,
        validated: _PendingWorkArgs,
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
    ) -> AgentToolResult:
        self._require_scope(identity, "plan:read")
        self._require_scope(identity, "submission:read")
        with self._session_factory() as session:
            project = self._require_project(session, project_id, identity)
            role = self._projects.require_member(
                session, user_id=identity.user_id, team_id=project.team_id
            )
            plans_statement = select(PlanCheck).where(
                PlanCheck.project_id == project_id,
                PlanCheck.approval_status == ApprovalStatus.PENDING,
            )
            submissions_statement = select(ExperimentSubmission).where(
                ExperimentSubmission.project_id == project_id,
                ExperimentSubmission.status == SubmissionStatus.NEEDS_REVIEW,
            )
            if role is TeamRole.RESEARCHER:
                plans_statement = plans_statement.where(
                    PlanCheck.requester_id == identity.user_id
                )
                submissions_statement = submissions_statement.where(
                    ExperimentSubmission.submitted_by == identity.user_id
                )
            plans = list(
                session.scalars(
                    plans_statement.order_by(PlanCheck.created_at.desc())
                    .limit(validated.limit)
                ).all()
            )
            submissions = list(
                session.scalars(
                    submissions_statement.order_by(ExperimentSubmission.created_at.desc())
                    .limit(validated.limit)
                ).all()
            )
            evidence: list[AgentEvidence] = []
            plan_items: list[dict[str, Any]] = []
            submission_items: list[dict[str, Any]] = []
            evidence_index = 1
            for row in plans:
                evidence_id = f"{evidence_prefix}_{evidence_index}"
                evidence_index += 1
                item = {
                    "plan_check_id": str(row.id),
                    "check_result": row.check_result.value,
                    "approval_status": row.approval_status.value,
                    "risk_level": row.risk_level.value,
                    "context_version": row.context_version,
                    "intent_version": row.intent_version,
                    "planned_changes": row.planned_changes[:20],
                    "created_at": row.created_at.isoformat(),
                    "evidence_id": evidence_id,
                }
                plan_items.append(item)
                evidence.append(
                    AgentEvidence(
                        evidence_id=evidence_id,
                        evidence_kind=AgentEvidenceKind.CONFIRMED_FACT,
                        entity_type="PLAN_CHECK",
                        entity_id=row.id,
                        entity_version=f"context:{row.context_version}/intent:{row.intent_version}",
                        label=f"Plan Check {row.id}",
                        excerpt=f"{row.check_result.value} / {row.approval_status.value}",
                        payload=item,
                    )
                )
            for row in submissions:
                evidence_id = f"{evidence_prefix}_{evidence_index}"
                evidence_index += 1
                item = {
                    "submission_id": str(row.id),
                    "status": row.status.value,
                    "workflow_status": row.workflow_status.value,
                    "processing_step": (
                        row.processing_step.value if row.processing_step else None
                    ),
                    "review_receipt": row.review_receipt,
                    "created_at": row.created_at.isoformat(),
                    "evidence_id": evidence_id,
                }
                submission_items.append(item)
                evidence.append(
                    AgentEvidence(
                        evidence_id=evidence_id,
                        evidence_kind=AgentEvidenceKind.CONFIRMED_FACT,
                        entity_type="SUBMISSION",
                        entity_id=row.id,
                        label=f"Submission {row.id}",
                        excerpt=f"{row.status.value} / {row.workflow_status.value}",
                        payload=item,
                    )
                )
            return AgentToolResult(
                content={
                    "plan_checks": plan_items,
                    "submissions": submission_items,
                    "role_visibility": role.value,
                },
                evidence=evidence,
            )

    def _require_project(
        self, session: Session, project_id: UUID, identity: RequestIdentity
    ) -> Project:
        if identity.project_id is not None and identity.project_id != project_id:
            raise AuthorizationError("当前身份绑定到其他项目")
        return self._projects.require_project_member(
            session,
            project_id=project_id,
            user_id=identity.user_id,
            team_id=identity.team_id,
        )

    @staticmethod
    def _require_scope(identity: RequestIdentity, scope: str) -> None:
        if scope not in identity.scopes:
            raise AuthorizationError(f"当前身份缺少 {scope} scope")

    @staticmethod
    def _metrics(session: Session, experiment_id: UUID) -> list[dict[str, Any]]:
        rows = session.scalars(
            select(ExperimentMetric)
            .where(ExperimentMetric.experiment_id == experiment_id)
            .order_by(ExperimentMetric.is_primary.desc(), ExperimentMetric.name)
        ).all()
        return [
            {
                "name": item.name,
                "value": item.value,
                "split": item.split,
                "aggregation_type": item.aggregation_type,
                "epoch": item.epoch,
                "is_primary": item.is_primary,
            }
            for item in rows
        ]

    @staticmethod
    def _experiment_payload(
        row: Experiment, metrics: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "experiment_id": str(row.id),
            "name": row.name,
            "model_name": row.model_name,
            "dataset": row.dataset,
            "protocol": row.protocol,
            "seed": row.seed,
            "status": row.status.value,
            "experiment_mode": row.experiment_mode.value,
            "metrics": metrics,
            "git_commit": row.git_commit,
            "context_version": row.project_context_version,
            "intent_version": row.intent_version,
            "confirmed_at": row.confirmed_at.isoformat(),
        }

    @staticmethod
    def _experiment_evidence(
        evidence_id: str, row: Experiment, payload: dict[str, Any]
    ) -> AgentEvidence:
        primary = next(
            (item for item in payload["metrics"] if item["is_primary"]),
            None,
        )
        primary_text = (
            f"{primary['name']}={primary['value']}" if primary is not None else "无主指标"
        )
        return AgentEvidence(
            evidence_id=evidence_id,
            evidence_kind=AgentEvidenceKind.CONFIRMED_FACT,
            entity_type="EXPERIMENT",
            entity_id=row.id,
            entity_version=(
                f"context:{row.project_context_version}/intent:{row.intent_version}"
            ),
            label=row.name,
            excerpt=(
                f"{row.dataset}/{row.protocol}，seed={row.seed}，{primary_text}"
            ),
            payload=payload,
        )
