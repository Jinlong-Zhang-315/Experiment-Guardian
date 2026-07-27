"""治理 Agent 候选研究报告契约。

报告只描述对正式实验快照的候选分析，不是 Context、Intent、Constraint 或 Experiment
事实源。所有强制治理判断仍由既有结构化记录和确定性规则完成。
"""

import hashlib
import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from experiment_guardian.domain.contracts import SHA256_PATTERN, ContractModel
from experiment_guardian.domain.research_memory import ResearchMemoryIndexView

ResearchFindingKind = Literal[
    "SUPPORTED_CONCLUSION",
    "CONFLICT",
    "OPEN_QUESTION",
    "RECOMMENDATION",
]


class ResearchReportPrepareInput(ContractModel):
    experiment_ids: list[UUID] = Field(min_length=2, max_length=8)
    objective: str = Field(min_length=1, max_length=1000)
    metric_name: str | None = Field(default=None, min_length=1, max_length=200)
    include_historical: bool = False

    @model_validator(mode="after")
    def validate_selection(self) -> "ResearchReportPrepareInput":
        if len(set(self.experiment_ids)) != len(self.experiment_ids):
            raise ValueError("研究报告的 Experiment ID 不能重复")
        self.objective = self.objective.strip()
        if self.metric_name is not None:
            self.metric_name = self.metric_name.strip()
        return self


class ResearchReportLookupInput(ContractModel):
    report_id: UUID


class ResearchReportListInput(ContractModel):
    limit: int = Field(default=10, ge=1, le=20)


class ResearchReportFinding(ContractModel):
    finding_id: str = Field(pattern=r"^F[0-9]{3}$")
    kind: ResearchFindingKind
    statement: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=4000)
    citation_ids: list[str] = Field(min_length=1, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_citations(self) -> "ResearchReportFinding":
        if len(set(self.citation_ids)) != len(self.citation_ids):
            raise ValueError("研究结论不能包含重复引用")
        return self


class ResearchReportLimitation(ContractModel):
    statement: str = Field(min_length=1, max_length=2000)
    citation_ids: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_citations(self) -> "ResearchReportLimitation":
        if len(set(self.citation_ids)) != len(self.citation_ids):
            raise ValueError("研究限制不能包含重复引用")
        return self


class AgentResearchReportPayload(ContractModel):
    schema_version: Literal[1] = 1
    source_hash: str = Field(pattern=SHA256_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    executive_summary: str = Field(min_length=1, max_length=4000)
    executive_summary_citation_ids: list[str] = Field(min_length=1, max_length=30)
    findings: list[ResearchReportFinding] = Field(min_length=1, max_length=20)
    limitations: list[ResearchReportLimitation] = Field(default_factory=list, max_length=20)
    selected_experiment_ids: list[UUID] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def validate_report_shape(self) -> "AgentResearchReportPayload":
        if len(set(self.selected_experiment_ids)) != len(self.selected_experiment_ids):
            raise ValueError("研究报告的 Experiment ID 不能重复")
        if len({item.finding_id for item in self.findings}) != len(self.findings):
            raise ValueError("研究报告的 finding_id 不能重复")
        if len(set(self.executive_summary_citation_ids)) != len(
            self.executive_summary_citation_ids
        ):
            raise ValueError("研究报告摘要不能包含重复引用")
        return self


class ResearchReportReference(ContractModel):
    report_id: UUID
    title: str = Field(min_length=1, max_length=200)
    source_hash: str = Field(pattern=SHA256_PATTERN)
    experiment_ids: list[UUID] = Field(min_length=2, max_length=8)


class ResearchReportSummary(ContractModel):
    report_id: UUID
    project_id: UUID
    created_by: UUID
    title: str
    objective: str
    experiment_ids: list[UUID]
    metric_name: str | None = None
    include_historical: bool
    source_hash: str
    provider: str
    model_id: str
    prompt_version: str
    created_at: datetime


class ResearchReportPage(ContractModel):
    items: list[ResearchReportSummary]
    next_cursor: str | None = None


class ResearchReportSourceWarning(ContractModel):
    code: Literal["SOURCE_STATUS_CHANGED", "SOURCE_MISSING"]
    experiment_id: UUID
    snapshot_status: str
    current_status: str | None = None
    message: str


class ResearchReportView(ResearchReportSummary):
    schema_version: int
    source_snapshot: dict[str, Any]
    report: AgentResearchReportPayload
    payload_hash: str
    source_thread_id: UUID
    source_run_id: UUID
    final_message_id: UUID
    source_warnings: list[ResearchReportSourceWarning] = Field(default_factory=list)
    research_memories: list[ResearchMemoryIndexView] = Field(default_factory=list)
    memory_materialization_pending: bool = False
    authoritative: Literal[False] = False
    evidence_classification: Literal["ANALYSIS"] = "ANALYSIS"


def research_report_source_hash(content: dict[str, Any]) -> str:
    """计算不受本次 ToolCall evidence_id 影响的确定性来源哈希。"""

    experiments = content.get("experiments")
    comparisons = content.get("comparisons")
    repeated_group = content.get("repeated_group")
    if (
        not isinstance(experiments, list)
        or not isinstance(comparisons, list)
        or not isinstance(repeated_group, dict)
        or not isinstance(repeated_group.get("analysis"), dict)
    ):
        raise ValueError("研究报告来源内容格式无效")
    if not all(isinstance(item, dict) for item in experiments + comparisons):
        raise ValueError("研究报告来源实验或比较格式无效")
    canonical = {
        key: value
        for key, value in content.items()
        if key not in {"source_hash", "experiments", "comparisons", "repeated_group"}
    }
    canonical["experiments"] = [
        {key: value for key, value in item.items() if key != "evidence_id"}
        for item in experiments
    ]
    canonical["comparisons"] = [
        {key: value for key, value in item.items() if key != "evidence_id"}
        for item in comparisons
    ]
    canonical["repeated_group"] = {"analysis": repeated_group["analysis"]}
    return hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def validate_report_against_source(
    report: AgentResearchReportPayload,
    source_snapshot: dict[str, Any],
) -> None:
    """验证模型报告只能引用本次确定性证据，且覆盖全部显式实验。"""

    content = source_snapshot.get("content")
    evidence_items = source_snapshot.get("evidence")
    if not isinstance(content, dict) or not isinstance(evidence_items, list):
        raise ValueError("研究报告来源快照不完整")
    source_hash = content.get("source_hash")
    source_ids = content.get("experiment_ids")
    if report.source_hash != source_hash:
        raise ValueError("研究报告 source_hash 与确定性证据不一致")
    if research_report_source_hash(content) != source_hash:
        raise ValueError("研究报告来源内容与 source_hash 不一致")
    report_ids = [str(item) for item in report.selected_experiment_ids]
    if not isinstance(source_ids, list) or report_ids != [str(item) for item in source_ids]:
        raise ValueError("研究报告 Experiment 集合或顺序与确定性证据不一致")

    evidence: dict[str, dict[str, Any]] = {}
    for item in evidence_items:
        if not isinstance(item, dict) or not isinstance(item.get("evidence_id"), str):
            raise ValueError("研究报告来源证据格式无效")
        evidence[item["evidence_id"]] = item
    used = set(report.executive_summary_citation_ids)
    for finding in report.findings:
        used.update(finding.citation_ids)
    for limitation in report.limitations:
        used.update(limitation.citation_ids)
    if not used.issubset(evidence):
        raise ValueError("研究报告引用了本次来源之外的证据")

    covered_experiments: set[str] = set()
    for citation_id in used:
        item = evidence[citation_id]
        if (
            item.get("entity_type") == "EXPERIMENT"
            and item.get("evidence_kind") == "CONFIRMED_FACT"
            and item.get("entity_id")
        ):
            covered_experiments.add(str(item["entity_id"]))
    for finding in report.findings:
        cited_items = [evidence[item] for item in finding.citation_ids]
        experiment_ids = {
            str(item["entity_id"])
            for item in cited_items
            if item.get("entity_type") == "EXPERIMENT"
            and item.get("evidence_kind") == "CONFIRMED_FACT"
            and item.get("entity_id")
        }
        analysis_count = sum(
            item.get("evidence_kind") == "ANALYSIS" for item in cited_items
        )
        if finding.kind in {"SUPPORTED_CONCLUSION", "CONFLICT"}:
            if len(experiment_ids) < 2 or analysis_count < 1:
                raise ValueError("结论或冲突必须引用至少两个实验事实和一个确定性分析")
        elif not cited_items:
            raise ValueError("开放问题或建议必须包含来源引用")
    missing = set(str(item) for item in source_ids) - covered_experiments
    if missing:
        raise ValueError("研究报告未覆盖全部显式选择的 Experiment")
