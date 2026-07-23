"""项目正式上下文的 SQLAlchemy 仓储。"""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from experiment_guardian.application.errors import (
    AuthorizationError,
    ConflictError,
    ResourceNotFoundError,
)
from experiment_guardian.domain.contracts import (
    ExperimentIntentPayload,
    ExperimentIntentReference,
    HumanReadablePolicy,
    ParameterConstraint,
    ProjectContextBundle,
    ProjectContextPayload,
    ProjectContextReference,
)
from experiment_guardian.domain.enums import (
    ContextStatus,
    ExperimentMode,
    IntentStatus,
    ProtectionLevel,
    TeamRole,
    VerificationStatus,
)
from experiment_guardian.domain.policy_narrative import (
    POLICY_NARRATIVE_FORMAT,
    POLICY_NARRATIVE_GENERATOR,
    POLICY_NARRATIVE_NOTICE,
    POLICY_NARRATIVE_VERSION,
    build_policy_narrative_source,
    policy_narrative_source_hash,
    render_policy_narrative,
)
from experiment_guardian.infrastructure.models import (
    ExperimentIntent,
    PolicyNarrative,
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

    def require_project_member(
        self,
        session: Session,
        *,
        project_id: UUID,
        user_id: UUID,
        team_id: UUID,
    ) -> Project:
        project = session.get(Project, project_id)
        if project is None or not project.active:
            raise ResourceNotFoundError("项目不存在或已停用")
        if project.team_id != team_id:
            raise AuthorizationError("Token 团队与项目所属团队不一致")
        self.require_member(session, user_id=user_id, team_id=project.team_id)
        return project

    def load_context_bundle(
        self,
        session: Session,
        *,
        project_id: UUID,
        user_id: UUID,
        team_id: UUID,
    ) -> ProjectContextBundle:
        project = self.require_project_member(
            session,
            project_id=project_id,
            user_id=user_id,
            team_id=team_id,
        )

        context, intent = self._load_active_policy(session, project_id=project_id)
        constraints = session.scalars(
            self._constraint_query(
                project_id=project_id,
                context_id=context.id,
                context_version=context.version,
                intent_id=intent.id,
                intent_version=intent.version,
                verification_status=VerificationStatus.CONFIRMED,
            ).order_by(ProtectedParameter.parameter_path)
        ).all()

        constraint_contracts = [self._constraint_contract(item) for item in constraints]
        self._validate_policy_consistency(intent, constraint_contracts)

        context_reference, intent_reference, context_payload, intent_payload = (
            self._policy_contracts(project, context, intent)
        )
        human_readable = self._resolve_policy_narrative(
            session,
            context=context_reference,
            intent=intent_reference,
            context_payload=context_payload,
            intent_payload=intent_payload,
            constraints=constraint_contracts,
        )
        return ProjectContextBundle(
            context=context_reference,
            active_intent=intent_reference,
            constraints=constraint_contracts,
            context_payload=context_payload,
            intent_payload=intent_payload,
            human_readable=human_readable,
        )

    def load_policy_narrative(
        self,
        session: Session,
        *,
        project_id: UUID,
        context_id: UUID,
    ) -> HumanReadablePolicy:
        project, context, intent, constraints = self._load_policy_version(
            session,
            project_id=project_id,
            context_id=context_id,
        )
        context_reference, intent_reference, context_payload, intent_payload = (
            self._policy_contracts(project, context, intent)
        )
        return self._resolve_policy_narrative(
            session,
            context=context_reference,
            intent=intent_reference,
            context_payload=context_payload,
            intent_payload=intent_payload,
            constraints=constraints,
        )

    def regenerate_policy_narrative(
        self,
        session: Session,
        *,
        project_id: UUID,
        context_id: UUID,
        generated_by: UUID,
    ) -> HumanReadablePolicy:
        """重建派生文本；模板失败会持久化 FAILED，不影响正式结构化版本。"""

        project, context, intent, constraints = self._load_policy_version(
            session,
            project_id=project_id,
            context_id=context_id,
        )
        context_reference, intent_reference, context_payload, intent_payload = (
            self._policy_contracts(project, context, intent)
        )
        source_hash: str | None = None
        content: str | None = None
        error: str | None = None
        try:
            source = build_policy_narrative_source(
                context=context_reference,
                intent=intent_reference,
                context_payload=context_payload,
                intent_payload=intent_payload,
                constraints=constraints,
            )
            source_hash = policy_narrative_source_hash(source)
            content = render_policy_narrative(source)
        except Exception as exc:
            # 该边界只包围纯派生计算；后续数据库错误仍由事务正常上抛。
            error = f"人类可读说明生成失败：{str(exc)[:1800]}"

        record = session.scalar(
            select(PolicyNarrative)
            .where(
                PolicyNarrative.context_id == context.id,
                PolicyNarrative.intent_id == intent.id,
            )
            .with_for_update()
        )
        now = datetime.now(UTC)
        values = {
            "project_id": project.id,
            "context_id": context.id,
            "context_version": context.version,
            "intent_id": intent.id,
            "intent_version": intent.version,
            "source_hash": source_hash,
            "format": POLICY_NARRATIVE_FORMAT,
            "generator": POLICY_NARRATIVE_GENERATOR,
            "generator_version": POLICY_NARRATIVE_VERSION,
            "status": "FAILED" if error else "READY",
            "content": None if error else content,
            "error": error,
            "generated_by": generated_by,
            "generated_at": now,
        }
        if record is None:
            record = PolicyNarrative(**values)
            session.add(record)
        else:
            for key, value in values.items():
                setattr(record, key, value)
        session.flush()
        return self._resolve_policy_narrative(
            session,
            context=context_reference,
            intent=intent_reference,
            context_payload=context_payload,
            intent_payload=intent_payload,
            constraints=constraints,
        )

    @staticmethod
    def _validate_policy_consistency(
        intent: ExperimentIntent, constraints: list[ParameterConstraint]
    ) -> None:
        locked_allowed_paths = sorted(
            set(intent.allowed_variables)
            & {
                item.parameter_path
                for item in constraints
                if item.protection_level is ProtectionLevel.LOCKED
                and item.verification_status is VerificationStatus.CONFIRMED
            }
        )
        if locked_allowed_paths:
            raise ConflictError(
                "Active Intent 将已确认 LOCKED 参数声明为允许变量: "
                + ", ".join(locked_allowed_paths)
            )

    @staticmethod
    def _load_active_policy(
        session: Session, *, project_id: UUID
    ) -> tuple[ProjectContext, ExperimentIntent]:
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
        return context, intents[0]

    @staticmethod
    def _load_policy_version(
        session: Session,
        *,
        project_id: UUID,
        context_id: UUID,
    ) -> tuple[Project, ProjectContext, ExperimentIntent, list[ParameterConstraint]]:
        project = session.get(Project, project_id)
        context = session.get(ProjectContext, context_id)
        if project is None or context is None or context.project_id != project_id:
            raise ResourceNotFoundError("项目中不存在该 Context 版本")
        intents = session.scalars(
            select(ExperimentIntent)
            .where(
                ExperimentIntent.project_id == project_id,
                ExperimentIntent.context_id == context_id,
                ExperimentIntent.context_version == context.version,
                ExperimentIntent.experiment_mode == ExperimentMode.FORMAL,
                ExperimentIntent.verification_status == VerificationStatus.CONFIRMED,
            )
            .order_by(ExperimentIntent.version.desc())
        ).all()
        if not intents:
            raise ResourceNotFoundError("Context 没有已确认的正式 Experiment Intent")
        intent = intents[0]
        rows = session.scalars(
            select(ProtectedParameter)
            .where(
                ProtectedParameter.project_id == project_id,
                ProtectedParameter.context_id == context_id,
                ProtectedParameter.context_version == context.version,
                ProtectedParameter.confirmed_by.is_not(None),
                ProtectedParameter.verification_status.in_(
                    [VerificationStatus.CONFIRMED, VerificationStatus.SUPERSEDED]
                ),
                or_(
                    ProtectedParameter.intent_id.is_(None),
                    ProtectedParameter.intent_id == intent.id,
                ),
            )
            .order_by(ProtectedParameter.parameter_path, ProtectedParameter.version.desc())
        ).all()
        latest_by_path: dict[str, ProtectedParameter] = {}
        for item in rows:
            latest_by_path.setdefault(item.parameter_path, item)
        constraints = [
            SqlAlchemyProjectRepository._constraint_contract(item)
            for item in latest_by_path.values()
        ]
        return project, context, intent, constraints

    @staticmethod
    def _policy_contracts(
        project: Project,
        context: ProjectContext,
        intent: ExperimentIntent,
    ) -> tuple[
        ProjectContextReference,
        ExperimentIntentReference,
        ProjectContextPayload,
        ExperimentIntentPayload,
    ]:
        if (
            context.confirmed_by is None
            or context.confirmed_at is None
            or context.effective_at is None
        ):
            raise ConflictError("正式 Context 版本缺少确认或生效信息")
        return (
            ProjectContextReference(
                context_id=context.id,
                version=context.version,
                confirmed_by=context.confirmed_by,
                confirmed_at=context.confirmed_at,
                effective_at=context.effective_at,
                change_reason=context.change_reason,
            ),
            ExperimentIntentReference(
                intent_id=intent.id,
                version=intent.version,
                context_id=context.id,
                context_version=context.version,
                status=intent.status,
                mode=intent.experiment_mode,
            ),
            ProjectContextPayload(
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
            ExperimentIntentPayload(
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
    def _resolve_policy_narrative(
        session: Session,
        *,
        context: ProjectContextReference,
        intent: ExperimentIntentReference,
        context_payload: ProjectContextPayload,
        intent_payload: ExperimentIntentPayload,
        constraints: list[ParameterConstraint],
    ) -> HumanReadablePolicy:
        source_hash: str | None
        try:
            source_hash = policy_narrative_source_hash(
                build_policy_narrative_source(
                    context=context,
                    intent=intent,
                    context_payload=context_payload,
                    intent_payload=intent_payload,
                    constraints=constraints,
                )
            )
        except Exception:
            source_hash = None
        record = session.scalar(
            select(PolicyNarrative).where(
                PolicyNarrative.context_id == context.context_id,
                PolicyNarrative.intent_id == intent.intent_id,
            )
        )
        common = {
            "format": POLICY_NARRATIVE_FORMAT,
            "generator": POLICY_NARRATIVE_GENERATOR,
            "generator_version": POLICY_NARRATIVE_VERSION,
            "context_id": context.context_id,
            "context_version": context.version,
            "intent_id": intent.intent_id,
            "intent_version": intent.version,
            "current_source_hash": source_hash,
            "governance_notice": POLICY_NARRATIVE_NOTICE,
        }
        if record is None:
            return HumanReadablePolicy(
                status="MISSING",
                error="该结构化版本尚未生成对应的人类可读说明",
                **common,
            )
        record_values = {
            "source_hash": record.source_hash,
            "generated_by": record.generated_by,
            "generated_at": record.generated_at,
        }
        if record.status == "FAILED":
            return HumanReadablePolicy(
                status="FAILED",
                error=record.error or "人类可读说明生成失败",
                **common,
                **record_values,
            )
        if (
            source_hash is None
            or record.source_hash != source_hash
            or record.context_version != context.version
            or record.intent_version != intent.version
            or record.generator != POLICY_NARRATIVE_GENERATOR
            or record.generator_version != POLICY_NARRATIVE_VERSION
        ):
            return HumanReadablePolicy(
                status="STALE",
                error="已保存说明与当前结构化来源或模板版本不一致，旧内容已隐藏",
                **common,
                **record_values,
            )
        return HumanReadablePolicy(
            status="READY",
            content=record.content,
            error=None,
            **common,
            **record_values,
        )

    def load_pending_constraints(
        self,
        session: Session,
        *,
        project_id: UUID,
        context_id: UUID,
        context_version: int,
        intent_id: UUID,
        intent_version: int,
    ) -> list[ParameterConstraint]:
        constraints = session.scalars(
            self._constraint_query(
                project_id=project_id,
                context_id=context_id,
                context_version=context_version,
                intent_id=intent_id,
                intent_version=intent_version,
                verification_status=VerificationStatus.PENDING,
            ).order_by(ProtectedParameter.parameter_path, ProtectedParameter.version)
        ).all()
        return [self._constraint_contract(item) for item in constraints]

    @staticmethod
    def _constraint_query(
        *,
        project_id: UUID,
        context_id: UUID,
        context_version: int,
        intent_id: UUID,
        intent_version: int,
        verification_status: VerificationStatus,
    ) -> Select[tuple[ProtectedParameter]]:
        return select(ProtectedParameter).where(
            ProtectedParameter.project_id == project_id,
            ProtectedParameter.context_id == context_id,
            ProtectedParameter.context_version == context_version,
            ProtectedParameter.active.is_(True),
            ProtectedParameter.verification_status == verification_status,
            or_(
                ProtectedParameter.intent_id.is_(None),
                (
                    (ProtectedParameter.intent_id == intent_id)
                    & (ProtectedParameter.intent_version == intent_version)
                ),
            ),
        )

    @staticmethod
    def _constraint_contract(item: ProtectedParameter) -> ParameterConstraint:
        if item.verification_status is VerificationStatus.CONFIRMED and (
            item.confirmed_by is None or item.confirmed_at is None
        ):
            raise ConflictError("已确认约束缺少确认人或确认时间")
        allowed_range = item.allowed_range or {}
        try:
            return ParameterConstraint(
                constraint_id=item.id,
                version=item.version,
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
        except ValidationError as exc:
            raise ConflictError("当前约束的来源或确认信息不完整") from exc
