"""项目正式上下文的 SQLAlchemy 仓储。"""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from experiment_guardian.application.errors import (
    AuthorizationError,
    ConflictError,
    ResourceNotFoundError,
)
from experiment_guardian.domain.contracts import (
    ExperimentIntentPayload,
    ExperimentIntentReference,
    ParameterConstraint,
    ProjectContextBundle,
    ProjectContextPayload,
    ProjectContextReference,
)
from experiment_guardian.domain.enums import (
    ContextStatus,
    IntentStatus,
    TeamRole,
    VerificationStatus,
)
from experiment_guardian.infrastructure.models import (
    ExperimentIntent,
    Project,
    ProjectContext,
    ProtectedParameter,
    TeamMember,
)


class SqlAlchemyProjectRepository:
    @staticmethod
    def require_member(
        session: Session,
        *,
        user_id: UUID,
        team_id: UUID,
        allowed_roles: set[TeamRole] | None = None,
    ) -> TeamRole:
        member = session.get(TeamMember, {"team_id": team_id, "user_id": user_id})
        if member is None or (allowed_roles is not None and member.role not in allowed_roles):
            raise AuthorizationError("当前用户没有该团队所需权限")
        return member.role

    def load_context_bundle(
        self, session: Session, *, project_id: UUID, user_id: UUID
    ) -> ProjectContextBundle:
        project = session.get(Project, project_id)
        if project is None or not project.active:
            raise ResourceNotFoundError("项目不存在或已停用")
        self.require_member(session, user_id=user_id, team_id=project.team_id)

        contexts = session.scalars(
            select(ProjectContext).where(
                ProjectContext.project_id == project_id,
                ProjectContext.status == ContextStatus.ACTIVE,
            )
        ).all()
        if not contexts:
            raise ResourceNotFoundError("项目没有生效中的正式上下文")
        if len(contexts) != 1:
            raise ConflictError("项目存在多个 ACTIVE 上下文，请先修复数据完整性")
        context = contexts[0]
        if (
            context.confirmed_by is None
            or context.confirmed_at is None
            or context.effective_at is None
        ):
            raise ConflictError("ACTIVE 上下文缺少确认或生效信息")

        intents = session.scalars(
            select(ExperimentIntent).where(
                ExperimentIntent.project_id == project_id,
                ExperimentIntent.context_id == context.id,
                ExperimentIntent.context_version == context.version,
                ExperimentIntent.status == IntentStatus.ACTIVE,
                ExperimentIntent.verification_status == VerificationStatus.CONFIRMED,
            )
        ).all()
        if not intents:
            raise ResourceNotFoundError("项目没有绑定当前上下文的 Active Intent")
        if len(intents) != 1:
            raise ConflictError("项目存在多个 ACTIVE Intent，请先修复数据完整性")
        intent = intents[0]

        constraints = session.scalars(
            select(ProtectedParameter)
            .where(
                ProtectedParameter.project_id == project_id,
                ProtectedParameter.context_id == context.id,
                ProtectedParameter.context_version == context.version,
                ProtectedParameter.active.is_(True),
                ProtectedParameter.verification_status == VerificationStatus.CONFIRMED,
                or_(
                    ProtectedParameter.intent_id.is_(None),
                    (
                        (ProtectedParameter.intent_id == intent.id)
                        & (ProtectedParameter.intent_version == intent.version)
                    ),
                ),
            )
            .order_by(ProtectedParameter.parameter_path)
        ).all()

        return ProjectContextBundle(
            context=ProjectContextReference(
                context_id=context.id,
                version=context.version,
                confirmed_by=context.confirmed_by,
                confirmed_at=context.confirmed_at,
                effective_at=context.effective_at,
                change_reason=context.change_reason,
            ),
            active_intent=ExperimentIntentReference(
                intent_id=intent.id,
                version=intent.version,
                context_id=context.id,
                context_version=context.version,
                status=intent.status,
                mode=intent.experiment_mode,
            ),
            constraints=[self._constraint_contract(item) for item in constraints],
            context_payload=ProjectContextPayload(
                project_id=project.id,
                project_name=project.name,
                description=project.description,
                repository_url=project.repository_url,
                goal=context.goal,
                non_goals=context.non_goals,
                mainline_model=context.mainline_model,
                baseline=context.baseline,
                dataset=context.dataset,
                protocol=context.protocol,
                primary_metric=context.primary_metric,
                default_seeds=context.default_seeds,
                active_branch=context.active_branch,
                active_config=context.active_config,
                deprecated_items=context.deprecated_items,
                key_decisions=context.key_decisions,
            ),
            intent_payload=ExperimentIntentPayload(
                name=intent.name,
                objective=intent.objective,
                hypothesis=intent.hypothesis,
                allowed_variables=intent.allowed_variables,
                controlled_variables=intent.controlled_variables,
                expected_outputs=intent.expected_outputs,
                acceptance_criteria=intent.acceptance_criteria,
                original_message=intent.original_message,
                intent_receipt=intent.intent_receipt,
            ),
        )

    @staticmethod
    def _constraint_contract(item: ProtectedParameter) -> ParameterConstraint:
        if item.confirmed_by is None or item.confirmed_at is None:
            raise ConflictError("已确认约束缺少确认人或确认时间")
        allowed_range = item.allowed_range or {}
        return ParameterConstraint(
            parameter_path=item.parameter_path,
            context_id=item.context_id,
            context_version=item.context_version,
            intent_id=item.intent_id,
            intent_version=item.intent_version,
            protection_level=item.protection_level,
            expected_value=item.expected_value,
            allowed_values=allowed_range.get("allowed_values"),
            minimum=allowed_range.get("minimum"),
            maximum=allowed_range.get("maximum"),
            reason=item.reason,
            source_type=item.source_type,
            verification_status=item.verification_status,
            original_message=item.original_message,
            inference_basis=item.inference_basis,
            confidence=item.confidence,
            confirmed_by=item.confirmed_by,
            confirmed_at=item.confirmed_at,
        )
