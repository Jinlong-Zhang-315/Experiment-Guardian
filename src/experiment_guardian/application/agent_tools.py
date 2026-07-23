"""治理 Agent 的最小只读工具目录。

工具参数不包含身份和项目 ID。执行器始终使用 Thread 绑定的项目与 Worker 实时恢复的
RequestIdentity，模型无法通过参数越权。
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from experiment_guardian.application.action_proposals import ActionProposalService
from experiment_guardian.application.errors import (
    AuthorizationError,
    InputValidationError,
    ResourceNotFoundError,
)
from experiment_guardian.application.identity import RequestIdentity
from experiment_guardian.application.policy_drafts import PolicyDraftService
from experiment_guardian.domain.action_proposal import (
    ActionProposalPrepareInput,
)
from experiment_guardian.domain.agent import (
    AgentEvidence,
    AgentToolResult,
    AgentToolSpec,
)
from experiment_guardian.domain.agent_analysis import (
    ExperimentAnalysisRecord,
    MetricRecord,
    compare_experiments,
    repeated_experiment_statistics,
    require_finite_metrics,
)
from experiment_guardian.domain.contracts import ContractModel
from experiment_guardian.domain.enums import (
    AgentEvidenceKind,
    ApprovalStatus,
    ApprovalTargetType,
    CheckResult,
    ExperimentStatus,
    RiskSeverity,
    SubmissionStatus,
    TeamRole,
    WorkflowJobStatus,
)
from experiment_guardian.domain.policy_draft import (
    PolicyDraftCreateInput,
    PolicyDraftRevisionInput,
)
from experiment_guardian.infrastructure.models import (
    ApprovalRecord,
    Artifact,
    Experiment,
    ExperimentMetric,
    ExperimentSubmission,
    PlanCheck,
    Project,
    ProjectContext,
    RunManifest,
    SubmissionEmbedding,
    SubmissionRisk,
    WorkflowJob,
)
from experiment_guardian.infrastructure.repositories import SqlAlchemyProjectRepository

R15A_TOOL_CATALOG_VERSION = "r15a-v1"
R15B_TOOL_CATALOG_VERSION = "r15b-v1"
R15C_TOOL_CATALOG_VERSION = "r15c-v1"
TOOL_CATALOG_VERSION = "r15d-v1"
AgentToolDefinition = tuple[
    type[ContractModel],
    str,
    Callable[..., AgentToolResult],
]
AgentToolDefinitions = dict[str, AgentToolDefinition]


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


class _ExperimentCompareArgs(ContractModel):
    left_experiment_id: UUID
    right_experiment_id: UUID
    metric_name: str | None = Field(default=None, min_length=1, max_length=200)


class _ExperimentGroupStatsArgs(ContractModel):
    experiment_ids: list[UUID] = Field(min_length=2, max_length=20)
    metric_name: str | None = Field(default=None, min_length=1, max_length=200)


class _PlanCheckExplainArgs(ContractModel):
    plan_check_id: UUID


class _SubmissionDiagnoseArgs(ContractModel):
    submission_id: UUID


class _PolicyDraftUpdateArgs(PolicyDraftRevisionInput):
    draft_id: UUID


class _PolicyDraftLookupArgs(ContractModel):
    draft_id: UUID
    revision: int | None = Field(default=None, ge=1)


class AgentToolRegistry:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        projects: SqlAlchemyProjectRepository,
        policy_drafts: PolicyDraftService | None = None,
        action_proposals: ActionProposalService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._projects = projects
        self._policy_drafts = policy_drafts
        self._action_proposals = action_proposals
        self._r15a_definitions: AgentToolDefinitions = {
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
        self._r15b_definitions: AgentToolDefinitions = {
            **self._r15a_definitions,
            "experiments_compare_v1": (
                _ExperimentCompareArgs,
                "比较两个显式指定的正式实验，先检查可比性，再计算指标差和配置差异。",
                self._experiments_compare,
            ),
            "experiment_group_stats_v1": (
                _ExperimentGroupStatsArgs,
                "对 2 至 20 个显式指定且条件一致的重复实验计算基础描述统计。",
                self._experiment_group_stats,
            ),
            "plan_check_explain_v1": (
                _PlanCheckExplainArgs,
                "读取一个 Plan Check 的冻结依据、当前审批状态和 Manifest 资格。",
                self._plan_check_explain,
            ),
            "submission_diagnose_v1": (
                _SubmissionDiagnoseArgs,
                "读取一个 Submission 的追溯、材料、风险和后台任务状态并生成确定性诊断。",
                self._submission_diagnose,
            ),
        }
        self._r15c_definitions: AgentToolDefinitions = {
            **self._r15b_definitions,
            "policy_draft_create_v1": (
                PolicyDraftCreateInput,
                "基于当前正式版本创建完整 Policy Bundle 候选草稿；不会发布正式策略。",
                self._policy_draft_create,
            ),
            "policy_draft_update_v1": (
                _PolicyDraftUpdateArgs,
                "为现有治理草稿追加完整且不可变的 revision；不会覆盖历史 revision。",
                self._policy_draft_update,
            ),
            "policy_draft_validate_v1": (
                _PolicyDraftLookupArgs,
                "读取一个治理草稿 revision 的确定性校验、歧义和正式版本新鲜度。",
                self._policy_draft_validate,
            ),
            "policy_draft_impact_get_v1": (
                _PolicyDraftLookupArgs,
                "读取候选策略对待审批 Plan 的纯模拟和对进行中 Submission 的版本影响。",
                self._policy_draft_impact,
            ),
        }
        self._r15d_definitions: AgentToolDefinitions = {
            **self._r15c_definitions,
            "action_proposal_prepare_v1": (
                ActionProposalPrepareInput,
                (
                    "从当前、无歧义且校验通过的治理草稿冻结正式策略发布提案；"
                    "该工具不会发布策略，只有 Owner 能在 Web 工作台确认。"
                ),
                self._action_proposal_prepare,
            ),
        }

    @property
    def specs(self) -> list[AgentToolSpec]:
        return self.specs_for_version(TOOL_CATALOG_VERSION)

    def specs_for_version(self, catalog_version: str) -> list[AgentToolSpec]:
        definitions = self._definitions_for_version(catalog_version)
        return [
            AgentToolSpec(
                name=name,
                version="1",
                description=description,
                input_schema=model.model_json_schema(),
            )
            for name, (model, description, _) in definitions.items()
        ]

    def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
        catalog_version: str = TOOL_CATALOG_VERSION,
        run_id: UUID | None = None,
        tool_call_id: UUID | None = None,
    ) -> AgentToolResult:
        definition = self._definitions_for_version(catalog_version).get(tool_name)
        if definition is None:
            raise InputValidationError(f"Agent 请求了未注册工具: {tool_name}")
        model, _, handler = definition
        try:
            validated = model.model_validate(arguments)
        except ValidationError as exc:
            raise InputValidationError(f"{tool_name} 参数无效") from exc
        common = {
            "validated": validated,
            "project_id": project_id,
            "identity": identity,
            "evidence_prefix": evidence_prefix,
        }
        if tool_name.startswith(("policy_draft_", "action_proposal_")):
            return handler(
                **common,
                run_id=run_id,
                tool_call_id=tool_call_id,
            )
        return handler(**common)

    def _definitions_for_version(self, catalog_version: str) -> AgentToolDefinitions:
        if catalog_version == R15A_TOOL_CATALOG_VERSION:
            return self._r15a_definitions
        if catalog_version == R15B_TOOL_CATALOG_VERSION:
            return self._r15b_definitions
        if catalog_version == R15C_TOOL_CATALOG_VERSION:
            return self._r15c_definitions
        if catalog_version == TOOL_CATALOG_VERSION:
            return self._r15d_definitions
        raise InputValidationError(f"不支持的 Agent 工具目录版本: {catalog_version}")

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
                    statement.order_by(Experiment.confirmed_at.desc(), Experiment.id.desc()).limit(
                        validated.limit
                    )
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
                plans_statement = plans_statement.where(PlanCheck.requester_id == identity.user_id)
                submissions_statement = submissions_statement.where(
                    ExperimentSubmission.submitted_by == identity.user_id
                )
            plans = list(
                session.scalars(
                    plans_statement.order_by(PlanCheck.created_at.desc()).limit(validated.limit)
                ).all()
            )
            submissions = list(
                session.scalars(
                    submissions_statement.order_by(ExperimentSubmission.created_at.desc()).limit(
                        validated.limit
                    )
                ).all()
            )
            evidence: list[AgentEvidence] = []
            plan_items: list[dict[str, Any]] = []
            submission_items: list[dict[str, Any]] = []
            evidence_index = 1
            for row in plans:
                evidence_id = f"{evidence_prefix}_{evidence_index}"
                evidence_index += 1
                plan_item: dict[str, Any] = {
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
                plan_items.append(plan_item)
                evidence.append(
                    AgentEvidence(
                        evidence_id=evidence_id,
                        evidence_kind=AgentEvidenceKind.CONFIRMED_FACT,
                        entity_type="PLAN_CHECK",
                        entity_id=row.id,
                        entity_version=f"context:{row.context_version}/intent:{row.intent_version}",
                        label=f"Plan Check {row.id}",
                        excerpt=f"{row.check_result.value} / {row.approval_status.value}",
                        payload=plan_item,
                    )
                )
            for submission in submissions:
                evidence_id = f"{evidence_prefix}_{evidence_index}"
                evidence_index += 1
                submission_item: dict[str, Any] = {
                    "submission_id": str(submission.id),
                    "status": submission.status.value,
                    "workflow_status": submission.workflow_status.value,
                    "processing_step": (
                        submission.processing_step.value if submission.processing_step else None
                    ),
                    "review_receipt": submission.review_receipt,
                    "created_at": submission.created_at.isoformat(),
                    "evidence_id": evidence_id,
                }
                submission_items.append(submission_item)
                evidence.append(
                    AgentEvidence(
                        evidence_id=evidence_id,
                        evidence_kind=AgentEvidenceKind.CONFIRMED_FACT,
                        entity_type="SUBMISSION",
                        entity_id=submission.id,
                        label=f"Submission {submission.id}",
                        excerpt=(f"{submission.status.value} / {submission.workflow_status.value}"),
                        payload=submission_item,
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

    def _experiments_compare(
        self,
        *,
        validated: _ExperimentCompareArgs,
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
    ) -> AgentToolResult:
        self._require_scope(identity, "experiment:read")
        if validated.left_experiment_id == validated.right_experiment_id:
            raise InputValidationError("比较需要两个不同的 Experiment ID")
        with self._session_factory() as session:
            self._require_project(session, project_id, identity)
            records = [
                self._analysis_record(session, project_id, experiment_id)
                for experiment_id in (
                    validated.left_experiment_id,
                    validated.right_experiment_id,
                )
            ]
            analysis = compare_experiments(
                records[0],
                records[1],
                metric_name=validated.metric_name,
            )
            evidence = [
                self._analysis_experiment_evidence(f"{evidence_prefix}_{index}", record)
                for index, record in enumerate(records, start=1)
            ]
            analysis_evidence_id = f"{evidence_prefix}_3"
            evidence.append(
                AgentEvidence(
                    evidence_id=analysis_evidence_id,
                    evidence_kind=AgentEvidenceKind.ANALYSIS,
                    entity_type="EXPERIMENT_COMPARISON",
                    entity_version="r15b-v1",
                    label=f"{records[0].name} 与 {records[1].name} 的确定性比较",
                    excerpt=(
                        f"{analysis['comparability']}；"
                        f"{len(analysis['hard_blockers'])} 个阻断原因；"
                        f"{len(analysis['caveats'])} 个注意事项"
                    ),
                    payload=analysis,
                )
            )
            return AgentToolResult(
                content={
                    "left_experiment_id": str(records[0].experiment_id),
                    "right_experiment_id": str(records[1].experiment_id),
                    "analysis": analysis,
                    "fact_evidence_ids": [item.evidence_id for item in evidence[:2]],
                    "analysis_evidence_id": analysis_evidence_id,
                },
                evidence=evidence,
            )

    def _experiment_group_stats(
        self,
        *,
        validated: _ExperimentGroupStatsArgs,
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
    ) -> AgentToolResult:
        self._require_scope(identity, "experiment:read")
        if len(set(validated.experiment_ids)) != len(validated.experiment_ids):
            raise InputValidationError("Experiment ID 不能重复")
        with self._session_factory() as session:
            self._require_project(session, project_id, identity)
            records = [
                self._analysis_record(session, project_id, experiment_id)
                for experiment_id in validated.experiment_ids
            ]
            try:
                analysis = repeated_experiment_statistics(
                    records, metric_name=validated.metric_name
                )
            except ValueError as exc:
                raise InputValidationError(str(exc)) from exc
            evidence = [
                self._analysis_experiment_evidence(f"{evidence_prefix}_{index}", record)
                for index, record in enumerate(records, start=1)
            ]
            analysis_evidence_id = f"{evidence_prefix}_{len(records) + 1}"
            evidence.append(
                AgentEvidence(
                    evidence_id=analysis_evidence_id,
                    evidence_kind=AgentEvidenceKind.ANALYSIS,
                    entity_type="EXPERIMENT_GROUP_STATISTICS",
                    entity_version="r15b-v1",
                    label=f"{len(records)} 个显式实验的重复统计",
                    excerpt=(
                        "严格重复组，已计算描述统计"
                        if analysis["accepted"]
                        else "条件不一致，未计算聚合统计"
                    ),
                    payload=analysis,
                )
            )
            return AgentToolResult(
                content={
                    "experiment_ids": [str(item.experiment_id) for item in records],
                    "analysis": analysis,
                    "fact_evidence_ids": [item.evidence_id for item in evidence[:-1]],
                    "analysis_evidence_id": analysis_evidence_id,
                },
                evidence=evidence,
            )

    def _plan_check_explain(
        self,
        *,
        validated: _PlanCheckExplainArgs,
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
    ) -> AgentToolResult:
        self._require_scope(identity, "plan:read")
        with self._session_factory() as session:
            project = self._require_project(session, project_id, identity)
            role = self._projects.require_member(
                session, user_id=identity.user_id, team_id=project.team_id
            )
            plan = session.get(PlanCheck, validated.plan_check_id)
            if plan is None or plan.project_id != project_id:
                raise ResourceNotFoundError("项目中不存在该 Plan Check")
            if role is TeamRole.RESEARCHER and plan.requester_id != identity.user_id:
                raise AuthorizationError("Researcher 只能查看自己提交的 Plan Check")
            approval = session.scalar(
                select(ApprovalRecord).where(
                    ApprovalRecord.target_id == plan.id,
                    ApprovalRecord.target_type == ApprovalTargetType.PLAN_CHECK,
                )
            )
            manifest = session.scalar(
                select(RunManifest).where(RunManifest.plan_check_id == plan.id)
            )
            governance_allows_manifest = (
                plan.check_result is CheckResult.PASS
                and plan.approval_status is ApprovalStatus.NOT_REQUIRED
            ) or (
                plan.check_result is CheckResult.NEEDS_APPROVAL
                and plan.approval_status is ApprovalStatus.APPROVED
            )
            payload = {
                "plan_check_id": str(plan.id),
                "requester_id": str(plan.requester_id),
                "check_result": plan.check_result.value,
                # 当前列是审批事实源；不读取 report 中可能过期的派生字段。
                "approval_status": plan.approval_status.value,
                "risk_level": plan.risk_level.value,
                "context": {
                    "id": str(plan.context_id),
                    "version": plan.context_version,
                    "snapshot": plan.context_snapshot,
                },
                "intent": {
                    "id": str(plan.intent_id),
                    "version": plan.intent_version,
                    "snapshot": plan.intent_snapshot,
                },
                "constraint_snapshot": plan.constraint_snapshot,
                "configuration": plan.parsed_config,
                "planned_changes": plan.planned_changes,
                "git_commit": plan.git_commit,
                "command": plan.command,
                "local_attestation": plan.local_attestation,
                "approval_record": (
                    {
                        "id": str(approval.id),
                        "status": approval.status.value,
                        "decided_by": str(approval.decided_by),
                        "decision_reason": approval.decision_reason,
                        "decided_at": approval.decided_at.isoformat(),
                    }
                    if approval is not None
                    else None
                ),
                "manifest": (
                    {
                        "id": str(manifest.id),
                        "manifest_hash": manifest.manifest_hash,
                        "created_at": manifest.created_at.isoformat(),
                    }
                    if manifest is not None
                    else None
                ),
                "governance_allows_manifest": governance_allows_manifest,
                "can_create_manifest_now": (governance_allows_manifest and manifest is None),
            }
            evidence_id = f"{evidence_prefix}_1"
            return AgentToolResult(
                content={**payload, "evidence_id": evidence_id},
                evidence=[
                    AgentEvidence(
                        evidence_id=evidence_id,
                        evidence_kind=AgentEvidenceKind.CONFIRMED_FACT,
                        entity_type="PLAN_CHECK",
                        entity_id=plan.id,
                        entity_version=(
                            f"context:{plan.context_version}/intent:{plan.intent_version}"
                        ),
                        label=f"Plan Check {plan.id}",
                        excerpt=(
                            f"{plan.check_result.value} / "
                            f"{plan.approval_status.value} / {plan.risk_level.value}"
                        ),
                        payload=payload,
                    )
                ],
            )

    def _submission_diagnose(
        self,
        *,
        validated: _SubmissionDiagnoseArgs,
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
    ) -> AgentToolResult:
        self._require_scope(identity, "submission:read")
        with self._session_factory() as session:
            project = self._require_project(session, project_id, identity)
            role = self._projects.require_member(
                session, user_id=identity.user_id, team_id=project.team_id
            )
            submission = session.get(ExperimentSubmission, validated.submission_id)
            if submission is None or submission.project_id != project_id:
                raise ResourceNotFoundError("项目中不存在该 Submission")
            if role is TeamRole.RESEARCHER and submission.submitted_by != identity.user_id:
                raise AuthorizationError("Researcher 只能诊断自己提交的 Submission")
            manifest = session.get(RunManifest, submission.run_manifest_id)
            plan = session.get(PlanCheck, manifest.plan_check_id) if manifest is not None else None
            artifacts = list(
                session.scalars(
                    select(Artifact)
                    .where(Artifact.submission_id == submission.id)
                    .order_by(Artifact.artifact_type, Artifact.filename)
                ).all()
            )
            risks = list(
                session.scalars(
                    select(SubmissionRisk)
                    .where(SubmissionRisk.submission_id == submission.id)
                    .order_by(SubmissionRisk.severity.desc(), SubmissionRisk.created_at)
                ).all()
            )
            jobs = list(
                session.scalars(
                    select(WorkflowJob)
                    .where(WorkflowJob.submission_id == submission.id)
                    .order_by(WorkflowJob.created_at)
                ).all()
            )
            embedding = session.scalar(
                select(SubmissionEmbedding).where(
                    SubmissionEmbedding.submission_id == submission.id
                )
            )
            experiment = session.scalar(
                select(Experiment).where(Experiment.submission_id == submission.id)
            )
            findings = self._submission_findings(
                submission=submission,
                manifest=manifest,
                plan=plan,
                artifacts=artifacts,
                risks=risks,
                jobs=jobs,
                embedding=embedding,
            )
            processing_step = (
                submission.processing_step.value if submission.processing_step else None
            )
            payload = {
                "submission_id": str(submission.id),
                "submitted_by": str(submission.submitted_by),
                "status": submission.status.value,
                "workflow_status": submission.workflow_status.value,
                "processing_step": processing_step,
                "processing_error": submission.processing_error,
                "trace": {
                    "run_manifest_id": str(submission.run_manifest_id),
                    "manifest_found": manifest is not None,
                    "plan_check_id": (
                        str(manifest.plan_check_id) if manifest is not None else None
                    ),
                    "plan_found": plan is not None,
                    "formal_experiment_id": (
                        str(experiment.id) if experiment is not None else None
                    ),
                },
                "artifacts": [
                    {
                        "id": str(item.id),
                        "type": item.artifact_type.value,
                        "filename": item.filename,
                        "size_bytes": item.size_bytes,
                        "cloud_hash_verified": item.cloud_hash_verified,
                        "fixed_version_present": bool(item.s3_version_id),
                    }
                    for item in artifacts
                ],
                "risks": [
                    {
                        "id": str(item.id),
                        "severity": item.severity.value,
                        "type": item.risk_type,
                        "message": item.message,
                        "blocking": item.blocking,
                        "resolved": item.resolved,
                        "evidence_type": (item.evidence_type.value if item.evidence_type else None),
                    }
                    for item in risks
                ],
                "jobs": [
                    {
                        "id": str(item.id),
                        "type": item.job_type.value,
                        "status": item.status.value,
                        "generation": item.generation,
                        "attempt_count": item.attempt_count,
                        "max_attempts": item.max_attempts,
                        "last_error": item.last_error,
                    }
                    for item in jobs
                ],
                "outputs": {
                    "summary_available": submission.generated_summary is not None,
                    "embedding_available": embedding is not None,
                    "review_receipt_available": submission.review_receipt is not None,
                },
                "findings": findings,
                "diagnosed_at": datetime.now(UTC).isoformat(),
                "notice": "诊断只读取元数据和正式快照，不下载或解释原始日志内容。",
            }
            fact_evidence_id = f"{evidence_prefix}_1"
            analysis_evidence_id = f"{evidence_prefix}_2"
            return AgentToolResult(
                content={
                    **payload,
                    "fact_evidence_id": fact_evidence_id,
                    "analysis_evidence_id": analysis_evidence_id,
                },
                evidence=[
                    AgentEvidence(
                        evidence_id=fact_evidence_id,
                        evidence_kind=AgentEvidenceKind.CONFIRMED_FACT,
                        entity_type="SUBMISSION",
                        entity_id=submission.id,
                        entity_version=(
                            f"manifest:{submission.run_manifest_id}/"
                            f"workflow:{processing_step or 'none'}"
                        ),
                        label=f"Submission {submission.id}",
                        excerpt=(
                            f"{submission.status.value} / "
                            f"{submission.workflow_status.value} / "
                            f"{len(artifacts)} 个材料"
                        ),
                        payload=payload,
                    ),
                    AgentEvidence(
                        evidence_id=analysis_evidence_id,
                        evidence_kind=AgentEvidenceKind.ANALYSIS,
                        entity_type="SUBMISSION_DIAGNOSIS",
                        entity_id=submission.id,
                        entity_version="r15b-v1",
                        label=f"Submission {submission.id} 确定性诊断",
                        excerpt=f"发现 {len(findings)} 个需要关注的状态",
                        payload={"findings": findings},
                    ),
                ],
            )

    def _policy_draft_create(
        self,
        *,
        validated: PolicyDraftCreateInput,
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
        run_id: UUID | None,
        tool_call_id: UUID | None,
    ) -> AgentToolResult:
        service = self._require_policy_draft_service()
        if run_id is None or tool_call_id is None:
            raise InputValidationError("治理草稿创建缺少当前 Agent Run/ToolCall 来源")
        view = service.create_from_agent(
            project_id=project_id,
            identity=identity,
            run_id=run_id,
            tool_call_id=tool_call_id,
            request=validated,
        )
        return self._draft_candidate_result(
            evidence_prefix=evidence_prefix,
            summary=view.summary.model_dump(mode="json"),
            revision=view.current.model_dump(mode="json"),
            action="CREATED",
        )

    def _policy_draft_update(
        self,
        *,
        validated: _PolicyDraftUpdateArgs,
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
        run_id: UUID | None,
        tool_call_id: UUID | None,
    ) -> AgentToolResult:
        service = self._require_policy_draft_service()
        if run_id is None or tool_call_id is None:
            raise InputValidationError("治理草稿更新缺少当前 Agent Run/ToolCall 来源")
        revision_request = PolicyDraftRevisionInput.model_validate(
            validated.model_dump(mode="json", exclude={"draft_id"})
        )
        revision = service.revise_from_agent(
            project_id=project_id,
            draft_id=validated.draft_id,
            identity=identity,
            run_id=run_id,
            tool_call_id=tool_call_id,
            request=revision_request,
        )
        summary, _ = service.validate_for_agent(
            project_id=project_id,
            draft_id=validated.draft_id,
            revision=revision.revision,
            identity=identity,
        )
        return self._draft_candidate_result(
            evidence_prefix=evidence_prefix,
            summary=summary.model_dump(mode="json"),
            revision=revision.model_dump(mode="json"),
            action="UPDATED",
        )

    def _policy_draft_validate(
        self,
        *,
        validated: _PolicyDraftLookupArgs,
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
        run_id: UUID | None,
        tool_call_id: UUID | None,
    ) -> AgentToolResult:
        del run_id, tool_call_id
        summary, validation = self._require_policy_draft_service().validate_for_agent(
            project_id=project_id,
            draft_id=validated.draft_id,
            revision=validated.revision,
            identity=identity,
        )
        evidence_id = f"{evidence_prefix}_1"
        payload = {
            "draft": summary.model_dump(mode="json"),
            "validation": validation.model_dump(mode="json"),
        }
        return AgentToolResult(
            content={**payload, "evidence_id": evidence_id},
            evidence=[
                AgentEvidence(
                    evidence_id=evidence_id,
                    evidence_kind=AgentEvidenceKind.CANDIDATE_DRAFT,
                    entity_type="POLICY_DRAFT",
                    entity_id=summary.draft_id,
                    entity_version=f"revision:{validated.revision or summary.current_revision}",
                    label=f"治理候选草稿 {summary.draft_id}",
                    excerpt=(
                        f"{summary.readiness.value} / {summary.freshness.value} / "
                        f"{summary.ambiguity_count} 项未解决歧义"
                    ),
                    payload=payload,
                )
            ],
        )

    def _policy_draft_impact(
        self,
        *,
        validated: _PolicyDraftLookupArgs,
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
        run_id: UUID | None,
        tool_call_id: UUID | None,
    ) -> AgentToolResult:
        del run_id, tool_call_id
        summary, impact, changed = self._require_policy_draft_service().impact_for_agent(
            project_id=project_id,
            draft_id=validated.draft_id,
            revision=validated.revision,
            identity=identity,
        )
        evidence_id = f"{evidence_prefix}_1"
        payload = {
            "draft_id": str(summary.draft_id),
            "revision": validated.revision or summary.current_revision,
            "freshness": summary.freshness.value,
            "impact": impact.model_dump(mode="json"),
            "impact_changed_since_revision": changed,
            "governance_notice": (
                "Plan 结论是候选规则模拟，Submission 仅展示不可变版本追溯；"
                "该工具不会修改任何正式状态。"
            ),
        }
        return AgentToolResult(
            content={**payload, "evidence_id": evidence_id},
            evidence=[
                AgentEvidence(
                    evidence_id=evidence_id,
                    evidence_kind=AgentEvidenceKind.ANALYSIS,
                    entity_type="POLICY_DRAFT_IMPACT",
                    entity_id=summary.draft_id,
                    entity_version=f"revision:{payload['revision']}/r15c-v1",
                    label=f"治理草稿 {summary.draft_id} 确定性影响分析",
                    excerpt=(
                        f"{impact.attention_level} 关注 / "
                        f"{len(impact.plan_simulations)} 个 Plan 模拟 / "
                        f"{len(impact.submission_impacts)} 个 Submission 版本影响"
                    ),
                    payload=payload,
                )
            ],
        )

    def _require_policy_draft_service(self) -> PolicyDraftService:
        if self._policy_drafts is None:
            raise InputValidationError("当前 Agent 工具目录未装配治理草稿服务")
        return self._policy_drafts

    def _action_proposal_prepare(
        self,
        *,
        validated: ActionProposalPrepareInput,
        project_id: UUID,
        identity: RequestIdentity,
        evidence_prefix: str,
        run_id: UUID | None,
        tool_call_id: UUID | None,
    ) -> AgentToolResult:
        if self._action_proposals is None:
            raise InputValidationError("当前 Agent 工具目录未装配操作提案服务")
        if run_id is None or tool_call_id is None:
            raise InputValidationError("操作提案准备缺少当前 Agent Run/ToolCall 来源")
        proposal = self._action_proposals.prepare_from_agent(
            project_id=project_id,
            identity=identity,
            run_id=run_id,
            tool_call_id=tool_call_id,
            request=validated,
        )
        evidence_id = f"{evidence_prefix}_1"
        payload = proposal.model_dump(mode="json")
        return AgentToolResult(
            content={
                "proposal": payload,
                "evidence_id": evidence_id,
                "governance_notice": (
                    "提案尚未执行。必须由 Owner 在 Web 工作台查看完整差异和影响，"
                    "完成近期认证并明确确认。"
                ),
            },
            evidence=[
                AgentEvidence(
                    evidence_id=evidence_id,
                    evidence_kind=AgentEvidenceKind.ACTION_PROPOSAL,
                    entity_type="ACTION_PROPOSAL",
                    entity_id=proposal.proposal_id,
                    entity_version=(
                        f"policy-draft:{proposal.source_draft_revision}/digest:"
                        f"{proposal.proposal_digest[:12]}"
                    ),
                    label=f"正式策略发布提案 {proposal.proposal_id}",
                    excerpt=(
                        f"{proposal.status.value} / {proposal.confirmability.value} / "
                        f"Context v{proposal.base_context_version} -> "
                        f"v{proposal.base_context_version + 1}"
                    ),
                    payload=payload,
                )
            ],
        )

    @staticmethod
    def _draft_candidate_result(
        *,
        evidence_prefix: str,
        summary: dict[str, Any],
        revision: dict[str, Any],
        action: str,
    ) -> AgentToolResult:
        evidence_id = f"{evidence_prefix}_1"
        compact_revision = {
            "revision": revision["revision"],
            "candidate_hash": revision["candidate_hash"],
            "change_summary": revision["change_summary"],
            "unresolved_ambiguities": revision["unresolved_ambiguities"],
            "validation": revision["validation"],
            "diff": revision["diff"][:50],
            "narrative": revision["narrative"],
            "current_impact": revision["current_impact"],
            "impact_changed_since_revision": revision["impact_changed_since_revision"],
        }
        payload = {
            "action": action,
            "draft": summary,
            "revision": compact_revision,
            "evidence_id": evidence_id,
            "governance_notice": (
                "该对象是未生效候选草稿；执行与治理决策仍以当前正式 Policy Bundle 为准。"
            ),
        }
        return AgentToolResult(
            content=payload,
            evidence=[
                AgentEvidence(
                    evidence_id=evidence_id,
                    evidence_kind=AgentEvidenceKind.CANDIDATE_DRAFT,
                    entity_type="POLICY_DRAFT",
                    entity_id=UUID(str(summary["draft_id"])),
                    entity_version=f"revision:{compact_revision['revision']}",
                    label=f"治理候选草稿 {summary['draft_id']}",
                    excerpt=(
                        f"{summary['readiness']} / {summary['freshness']} / "
                        f"{summary['ambiguity_count']} 项未解决歧义"
                    ),
                    payload={
                        "draft": summary,
                        "revision": compact_revision,
                    },
                )
            ],
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

    def _analysis_record(
        self, session: Session, project_id: UUID, experiment_id: UUID
    ) -> ExperimentAnalysisRecord:
        experiment = session.get(Experiment, experiment_id)
        if experiment is None or experiment.project_id != project_id:
            raise ResourceNotFoundError(f"项目中不存在正式 Experiment {experiment_id}")
        manifest = session.get(RunManifest, experiment.run_manifest_id)
        context = session.get(ProjectContext, experiment.project_context_id)
        submission = session.get(ExperimentSubmission, experiment.submission_id)
        plan = session.get(PlanCheck, manifest.plan_check_id) if manifest is not None else None
        metric_rows = list(
            session.scalars(
                select(ExperimentMetric)
                .where(ExperimentMetric.experiment_id == experiment.id)
                .order_by(ExperimentMetric.is_primary.desc(), ExperimentMetric.name)
            ).all()
        )
        parsed_config: dict[str, Any] = {}
        parsed_config_present = False
        if manifest is not None and isinstance(manifest.config_snapshot, dict):
            candidate = manifest.config_snapshot.get("parsed")
            if isinstance(candidate, dict):
                parsed_config = candidate
                parsed_config_present = True
        primary_metric_name: str | None = None
        higher_is_better: bool | None = None
        if context is not None and isinstance(context.primary_metric, dict):
            raw_name = context.primary_metric.get("name")
            raw_direction = context.primary_metric.get("higher_is_better")
            if isinstance(raw_name, str):
                primary_metric_name = raw_name
            if isinstance(raw_direction, bool):
                higher_is_better = raw_direction
        record = ExperimentAnalysisRecord(
            experiment_id=experiment.id,
            name=experiment.name,
            status=experiment.status.value,
            dataset=experiment.dataset,
            protocol=experiment.protocol,
            model_name=experiment.model_name,
            seed=experiment.seed,
            experiment_mode=experiment.experiment_mode.value,
            context_id=experiment.project_context_id,
            context_version=experiment.project_context_version,
            intent_id=experiment.intent_id,
            intent_version=experiment.intent_version,
            git_commit=experiment.git_commit,
            checkpoint=experiment.checkpoint,
            command=experiment.command,
            config=parsed_config,
            metrics=tuple(
                MetricRecord(
                    name=item.name,
                    value=item.value,
                    split=item.split,
                    aggregation_type=item.aggregation_type,
                    epoch=item.epoch,
                    is_primary=item.is_primary,
                )
                for item in metric_rows
            ),
            primary_metric_name=primary_metric_name,
            higher_is_better=higher_is_better,
            trace_complete=bool(
                manifest is not None
                and context is not None
                and manifest.project_id == project_id
                and manifest.context_id == experiment.project_context_id
                and manifest.context_version == experiment.project_context_version
                and manifest.intent_id == experiment.intent_id
                and manifest.intent_version == experiment.intent_version
                and submission is not None
                and submission.project_id == project_id
                and submission.run_manifest_id == manifest.id
                and plan is not None
                and plan.project_id == project_id
                and plan.context_id == experiment.project_context_id
                and plan.context_version == experiment.project_context_version
                and plan.intent_id == experiment.intent_id
                and plan.intent_version == experiment.intent_version
                and parsed_config_present
            ),
        )
        try:
            require_finite_metrics(record)
        except ValueError as exc:
            raise InputValidationError(str(exc)) from exc
        return record

    @staticmethod
    def _analysis_experiment_evidence(
        evidence_id: str, record: ExperimentAnalysisRecord
    ) -> AgentEvidence:
        primary = next((item for item in record.metrics if item.is_primary), None)
        payload = {
            "experiment_id": str(record.experiment_id),
            "name": record.name,
            "status": record.status,
            "dataset": record.dataset,
            "protocol": record.protocol,
            "model_name": record.model_name,
            "seed": record.seed,
            "experiment_mode": record.experiment_mode,
            "context_id": str(record.context_id),
            "context_version": record.context_version,
            "intent_id": str(record.intent_id),
            "intent_version": record.intent_version,
            "git_commit": record.git_commit,
            "checkpoint": record.checkpoint,
            "command": record.command,
            "metrics": [
                {
                    "name": item.name,
                    "value": item.value,
                    "split": item.split,
                    "aggregation_type": item.aggregation_type,
                    "epoch": item.epoch,
                    "is_primary": item.is_primary,
                }
                for item in record.metrics
            ],
            "trace_complete": record.trace_complete,
        }
        return AgentEvidence(
            evidence_id=evidence_id,
            evidence_kind=AgentEvidenceKind.CONFIRMED_FACT,
            entity_type="EXPERIMENT",
            entity_id=record.experiment_id,
            entity_version=(f"context:{record.context_version}/intent:{record.intent_version}"),
            label=record.name,
            excerpt=(
                f"{record.dataset}/{record.protocol}，seed={record.seed}，"
                + (f"{primary.name}={primary.value}" if primary is not None else "无主指标")
            ),
            payload=payload,
        )

    @staticmethod
    def _submission_findings(
        *,
        submission: ExperimentSubmission,
        manifest: RunManifest | None,
        plan: PlanCheck | None,
        artifacts: list[Artifact],
        risks: list[SubmissionRisk],
        jobs: list[WorkflowJob],
        embedding: SubmissionEmbedding | None,
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []

        def add(code: str, severity: str, message: str) -> None:
            findings.append({"code": code, "severity": severity, "message": message})

        if manifest is None or plan is None:
            add("TRACE_INCOMPLETE", "CRITICAL", "Manifest 或 Plan Check 追溯缺失")
        if not artifacts:
            add("ARTIFACTS_MISSING", "HIGH", "Submission 尚无 Artifact")
        else:
            artifact_types = {item.artifact_type.value for item in artifacts}
            missing_required = {"CONFIG", "RESULT"} - artifact_types
            if missing_required:
                add(
                    "REQUIRED_ARTIFACT_MISSING",
                    "HIGH",
                    "缺少必需材料: " + ", ".join(sorted(missing_required)),
                )
            if any(not item.cloud_hash_verified or not item.s3_version_id for item in artifacts):
                add(
                    "ARTIFACT_EVIDENCE_INCOMPLETE",
                    "HIGH",
                    "至少一个 Artifact 缺少云端哈希或固定 VersionId",
                )
        for risk in risks:
            if not risk.resolved and (
                risk.blocking or risk.severity in {RiskSeverity.HIGH, RiskSeverity.CRITICAL}
            ):
                add(
                    f"UNRESOLVED_{risk.severity.value}_RISK",
                    risk.severity.value,
                    risk.message,
                )
        for job in jobs:
            if job.status in {WorkflowJobStatus.DEAD_LETTER, WorkflowJobStatus.FAILED}:
                add(
                    "BACKGROUND_JOB_FAILED",
                    "HIGH",
                    f"{job.job_type.value} 已进入 {job.status.value}",
                )
        if submission.processing_error:
            add("PROCESSING_ERROR", "MEDIUM", "Submission 保存了处理错误")
        if (
            submission.status
            in {
                SubmissionStatus.NEEDS_REVIEW,
                SubmissionStatus.APPROVED,
                SubmissionStatus.REJECTED,
            }
            and submission.generated_summary is None
        ):
            add("SUMMARY_MISSING", "MEDIUM", "审核阶段缺少生成摘要")
        if (
            submission.status
            in {
                SubmissionStatus.NEEDS_REVIEW,
                SubmissionStatus.APPROVED,
                SubmissionStatus.REJECTED,
            }
            and embedding is None
        ):
            add("EMBEDDING_MISSING", "MEDIUM", "审核阶段缺少检索向量")
        if (
            submission.status
            in {
                SubmissionStatus.NEEDS_REVIEW,
                SubmissionStatus.APPROVED,
                SubmissionStatus.REJECTED,
            }
            and submission.review_receipt is None
        ):
            add("REVIEW_RECEIPT_MISSING", "HIGH", "审核阶段缺少审核回执")
        return findings

    @staticmethod
    def _experiment_payload(row: Experiment, metrics: list[dict[str, Any]]) -> dict[str, Any]:
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
            entity_version=(f"context:{row.project_context_version}/intent:{row.intent_version}"),
            label=row.name,
            excerpt=(f"{row.dataset}/{row.protocol}，seed={row.seed}，{primary_text}"),
            payload=payload,
        )
