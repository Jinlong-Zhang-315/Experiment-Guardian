import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  Braces,
  CheckCircle2,
  Clock3,
  FileDiff,
  ShieldCheck,
  X,
} from "lucide-react";
import { api, formatTime, idempotencyKey } from "../api";
import { Badge, Empty, ErrorNotice, JsonBlock } from "../components";
import type {
  ActionProposal,
  Page,
  PlanDecisionActionProposal,
  PolicyPublishActionProposal,
  SubmissionDecisionActionProposal,
} from "../types";

function proposalTitle(value: ActionProposal) {
  if (value.operation === "POLICY_PUBLISH") return "发布 Policy Bundle";
  const decision = value.payload.decision === "APPROVED" ? "批准" : "拒绝";
  return value.operation === "PLAN_CHECK_DECISION"
    ? `Plan ${decision}`
    : `Submission ${decision}`;
}

function proposalTarget(value: ActionProposal) {
  if (value.operation === "POLICY_PUBLISH") {
    return `Context v${value.base_context_version} → v${value.base_context_version + 1}`;
  }
  return value.operation === "PLAN_CHECK_DECISION"
    ? `Plan ${value.target_plan_check_id}`
    : `Submission ${value.target_submission_id}`;
}

function proposalRisk(value: ActionProposal) {
  if (value.operation === "POLICY_PUBLISH") return value.impact_snapshot.attention_level;
  if (value.operation === "PLAN_CHECK_DECISION") return value.impact_snapshot.risk_level;
  return value.impact_snapshot.highest_risk ?? value.impact_snapshot.review_eligibility;
}

export function ActionProposalWorkspace({
  projectId,
  initialProposalId,
  onClose,
}: {
  projectId: string;
  initialProposalId?: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState(initialProposalId ?? "");
  const [confirmedReview, setConfirmedReview] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const [cancelReason, setCancelReason] = useState("");

  const proposals = useQuery({
    queryKey: ["action-proposals", projectId],
    queryFn: () => api<Page<ActionProposal>>(
      `/projects/${projectId}/agent/action-proposals?limit=50`,
    ),
  });
  const detail = useQuery({
    queryKey: ["action-proposal", projectId, selectedId],
    queryFn: () => api<ActionProposal>(
      `/projects/${projectId}/agent/action-proposals/${selectedId}`,
    ),
    enabled: Boolean(selectedId),
  });

  useEffect(() => {
    if (!selectedId && proposals.data?.items[0]) {
      setSelectedId(proposals.data.items[0].proposal_id);
    }
  }, [proposals.data, selectedId]);
  useEffect(() => {
    setConfirmedReview(false);
    setCanceling(false);
    setCancelReason("");
  }, [selectedId]);

  const refresh = async (proposalId: string) => {
    await queryClient.invalidateQueries({ queryKey: ["action-proposals", projectId] });
    await queryClient.invalidateQueries({
      queryKey: ["action-proposal", projectId, proposalId],
    });
  };
  const confirm = useMutation({
    mutationFn: (proposal: ActionProposal) => api<ActionProposal>(
      `/projects/${projectId}/agent/action-proposals/${proposal.proposal_id}/confirm`,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey() },
        body: JSON.stringify({ proposal_digest: proposal.proposal_digest }),
      },
    ),
    onSuccess: (proposal) => refresh(proposal.proposal_id),
  });
  const cancel = useMutation({
    mutationFn: (proposal: ActionProposal) => api<ActionProposal>(
      `/projects/${projectId}/agent/action-proposals/${proposal.proposal_id}/cancel`,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey() },
        body: JSON.stringify({
          proposal_digest: proposal.proposal_digest,
          reason: cancelReason.trim(),
        }),
      },
    ),
    onSuccess: async (proposal) => {
      setCanceling(false);
      await refresh(proposal.proposal_id);
    },
  });

  const value = detail.data;
  return <div className="draft-workspace-backdrop" role="presentation" onMouseDown={onClose}>
    <section
      className="draft-workspace"
      role="dialog"
      aria-modal="true"
      aria-label="正式操作提案"
      onMouseDown={(event) => event.stopPropagation()}
    >
      <header className="draft-workspace-header">
        <div><ShieldCheck /><span><strong>正式操作提案</strong><small>Agent 只能准备，有权审核者明确确认后才会执行</small></span></div>
        <button className="icon-button" onClick={onClose} title="关闭" aria-label="关闭操作提案"><X /></button>
      </header>
      <div className="draft-workspace-body">
        <aside className="draft-list">
          {proposals.error && <ErrorNotice error={proposals.error} />}
          {!proposals.isPending && !proposals.data?.items.length && <Empty>尚无待确认或历史提案</Empty>}
          {proposals.data?.items.map((item) => <button
            className={selectedId === item.proposal_id ? "active" : ""}
            key={item.proposal_id}
            onClick={() => setSelectedId(item.proposal_id)}
          >
            <span>
              <strong>{proposalTitle(item)}</strong>
              <small>{proposalTarget(item)}</small>
            </span>
            <span><Badge value={item.status} /><Badge value={item.confirmability} /></span>
          </button>)}
        </aside>
        <main className="draft-detail proposal-detail">
          {detail.error && <ErrorNotice error={detail.error} />}
          {!selectedId && <Empty>选择一份提案查看冻结内容</Empty>}
          {selectedId && detail.isPending && <div className="page-loading">正在加载提案</div>}
          {value && <>
            <header className="draft-detail-header">
              <div>
                <strong>{value.operation === "POLICY_PUBLISH"
                  ? "Policy Bundle 正式发布"
                  : proposalTitle(value)}</strong>
                <span>{value.operation === "POLICY_PUBLISH"
                  ? `草稿 revision ${value.source_draft_revision}`
                  : proposalTarget(value)} · 创建于 {formatTime(value.created_at)}</span>
              </div>
              <div><Badge value={value.status} /><Badge value={value.confirmability} /></div>
            </header>

            <div className="proposal-facts">
              <div><Clock3 /><span>有效期</span><strong>{formatTime(value.expires_at)}</strong></div>
              <div><FileDiff /><span>结构化差异</span><strong>{value.diff_snapshot.length} 项</strong></div>
              <div><AlertTriangle /><span>{value.operation === "POLICY_PUBLISH" ? "最高关注" : "风险等级"}</span>
                <strong>{proposalRisk(value)}</strong>
              </div>
            </div>

            {value.confirmability_reasons.length > 0 && <div className="draft-warning">
              <AlertTriangle /><span>{value.confirmability_reasons.join("；")}</span>
            </div>}
            {value.status === "EXECUTED" && <div className="proposal-success">
              <CheckCircle2 /><span>{value.operation === "POLICY_PUBLISH"
                ? `已发布为正式 Context v${value.executed_context_version}`
                : value.operation === "PLAN_CHECK_DECISION"
                  ? `Plan 已${value.payload.decision === "APPROVED" ? "批准" : "拒绝"}`
                  : value.payload.decision === "APPROVED"
                    ? `已确认为正式 Experiment ${value.executed_experiment_id ?? ""}`
                    : "Submission 已拒绝"}</span>
            </div>}

            {value.operation === "POLICY_PUBLISH"
              ? <PolicyProposalContent value={value} />
              : value.operation === "PLAN_CHECK_DECISION"
                ? <PlanProposalContent value={value} />
                : <SubmissionProposalContent value={value} />}

            <details className="proposal-raw">
              <summary><Braces />查看冻结结构化{value.operation === "POLICY_PUBLISH" ? "发布请求" : "决定"}</summary>
              <p>正式执行只使用下列结构化数据；Agent 说明不参与约束判断。</p>
              <JsonBlock value={value.payload} />
              <small>摘要：<code>{value.proposal_digest}</code></small>
            </details>

            {value.status === "PROPOSED" && <footer className="draft-actions">
              {canceling ? <>
                <label>取消原因<input value={cancelReason} onChange={(event) => setCancelReason(event.target.value)} /></label>
                <button className="button" onClick={() => setCanceling(false)}>返回</button>
                <button className="button danger" disabled={!cancelReason.trim() || cancel.isPending} onClick={() => cancel.mutate(value)}><Ban />确认取消</button>
              </> : <>
                {value.allowed_actions.includes("CANCEL") && <button className="button danger" onClick={() => setCanceling(true)}><Ban />取消提案</button>}
                {value.allowed_actions.includes("CONFIRM") ? <label className="proposal-confirm">
                  <input type="checkbox" checked={confirmedReview} onChange={(event) => setConfirmedReview(event.target.checked)} />
                  <span>{value.operation === "POLICY_PUBLISH"
                    ? "我已核对冻结差异、影响和结构化发布请求"
                    : value.operation === "PLAN_CHECK_DECISION"
                      ? "我已核对 Plan 正式依据、最终决定和理由"
                      : "我已核对 Submission 回执、风险、追溯、材料和最终决定"}</span>
                </label> : <span className="proposal-owner-note">等待有权审核者审阅并确认</span>}
                {value.allowed_actions.includes("CONFIRM") && <button
                  className={`button ${value.operation !== "POLICY_PUBLISH" && value.payload.decision === "REJECTED" ? "danger" : "primary"}`}
                  disabled={!confirmedReview || confirm.isPending}
                  onClick={() => confirm.mutate(value)}
                ><ShieldCheck />{value.operation === "POLICY_PUBLISH"
                    ? "确认并发布正式版本"
                    : `确认并${value.payload.decision === "APPROVED" ? "批准" : "拒绝"} ${value.operation === "PLAN_CHECK_DECISION" ? "Plan" : "Submission"}`}</button>}
              </>}
            </footer>}
            {(confirm.error || cancel.error) && <ErrorNotice error={confirm.error || cancel.error} />}
          </>}
        </main>
      </div>
    </section>
  </div>;
}

function PolicyProposalContent({ value }: { value: PolicyPublishActionProposal }) {
  return <>
    <section className="proposal-section">
      <h3>冻结差异</h3>
      <div className="draft-diff-list">{value.diff_snapshot.map((item) => <details
        open={item.attention_level === "HIGH"}
        key={item.field_path}
      >
        <summary><Badge value={item.attention_level} /><code>{item.field_path}</code><Badge value={item.change_type} /></summary>
        <p>{item.impact}</p>
        <div className="draft-value-pair">
          <div><small>当前正式值</small><JsonBlock value={item.previous_value} /></div>
          <div><small>拟发布值</small><JsonBlock value={item.candidate_value} /></div>
        </div>
      </details>)}</div>
    </section>
    <section className="proposal-section">
      <h3>冻结影响</h3>
      {value.impact_snapshot.future_policy_effects.map((item) => <p key={item}>{item}</p>)}
      {value.impact_snapshot.warnings.map((item) => <p className="impact-warning" key={item}>{item}</p>)}
      <h4>待审批 Plan</h4>
      {value.impact_snapshot.plan_simulations.length
        ? value.impact_snapshot.plan_simulations.map((item) => <div className="impact-row" key={item.plan_check_id}>
          <code>{item.plan_check_id}</code>
          <span><Badge value={item.original_check_result} /> → <Badge value={item.simulated_check_result ?? item.status} /></span>
        </div>)
        : <p className="muted">没有待审批 Plan。</p>}
      <h4>进行中 Submission</h4>
      {value.impact_snapshot.submission_impacts.length
        ? value.impact_snapshot.submission_impacts.map((item) => <div className="impact-row" key={item.submission_id}>
          <code>{item.submission_id}</code><Badge value={item.status} /><small>{item.message}</small>
        </div>)
        : <p className="muted">没有进行中的 Submission。</p>}
    </section>
  </>;
}

function PlanProposalContent({ value }: { value: PlanDecisionActionProposal }) {
  return <>
    <section className="proposal-section">
      <h3>最终决定</h3>
      <div className="impact-row">
        <code>{value.target_plan_check_id}</code>
        <Badge value={value.payload.decision} />
        <small>{value.payload.decision_reason}</small>
      </div>
      <p className={value.payload.decision === "REJECTED" ? "impact-warning" : ""}>
        {value.impact_snapshot.decision_effect}
      </p>
      <div className="proposal-facts">
        <div><span>Context</span><strong>v{value.base_context_version}</strong></div>
        <div><span>Intent</span><strong>v{value.base_intent_version}</strong></div>
        <div><span>当前状态</span><strong>{value.impact_snapshot.check_result} / {value.impact_snapshot.approval_status}</strong></div>
      </div>
    </section>
    <section className="proposal-section">
      <h3>计划变化</h3>
      {value.diff_snapshot.length
        ? value.diff_snapshot.map((item, index) => <details
          open={value.impact_snapshot.risk_level === "HIGH" || value.impact_snapshot.risk_level === "CRITICAL"}
          key={`${String(item.parameter_path ?? item.field_path ?? "change")}-${index}`}
        >
          <summary>
            <Badge value={String(item.protection_level ?? item.risk_level ?? "CHANGE")} />
            <code>{String(item.parameter_path ?? item.field_path ?? `change-${index + 1}`)}</code>
          </summary>
          <JsonBlock value={item} />
        </details>)
        : <p className="muted">没有可展示的结构化变化。</p>}
    </section>
    <section className="proposal-section">
      <h3>风险依据</h3>
      {value.impact_snapshot.risks.length
        ? value.impact_snapshot.risks.map((risk, index) => <details
          open={String(risk.severity ?? risk.risk_level) === "HIGH" || String(risk.severity ?? risk.risk_level) === "CRITICAL"}
          key={`${String(risk.code ?? "risk")}-${index}`}
        >
          <summary><Badge value={String(risk.severity ?? risk.risk_level ?? value.impact_snapshot.risk_level)} />{String(risk.code ?? `风险 ${index + 1}`)}</summary>
          <JsonBlock value={risk} />
        </details>)
        : <p className="muted">正式检查报告没有额外风险条目。</p>}
    </section>
  </>;
}

function SubmissionProposalContent({ value }: { value: SubmissionDecisionActionProposal }) {
  const highRisks = value.impact_snapshot.risks.filter((risk) =>
    risk.severity === "HIGH" || risk.severity === "CRITICAL" || risk.blocking,
  );
  const otherRisks = value.impact_snapshot.risks.filter((risk) => !highRisks.includes(risk));
  return <>
    <section className="proposal-section">
      <h3>最终决定</h3>
      <div className="impact-row">
        <code>{value.target_submission_id}</code>
        <Badge value={value.payload.decision} />
        <small>{value.payload.decision_reason}</small>
      </div>
      <p className={value.payload.decision === "REJECTED" ? "impact-warning" : ""}>
        {value.impact_snapshot.decision_effect}
      </p>
      <div className="proposal-facts">
        <div><span>审核资格</span><strong>{value.impact_snapshot.review_eligibility}</strong></div>
        <div><span>Context</span><strong>v{value.base_context_version}</strong></div>
        <div><span>Intent</span><strong>v{value.base_intent_version}</strong></div>
      </div>
      <h4>实验目标</h4>
      <p>{value.impact_snapshot.objective}</p>
      {!value.impact_snapshot.approval_material_complete && <div className="draft-warning">
        <AlertTriangle /><span>{value.impact_snapshot.approval_material_issues.join("；")}</span>
      </div>}
    </section>
    <section className="proposal-section">
      <h3>强制展开风险</h3>
      {highRisks.length
        ? highRisks.map((risk) => <details open key={risk.id}>
          <summary><Badge value={risk.severity} /><code>{risk.field_path ?? risk.risk_type}</code></summary>
          <p>{risk.message}</p><p className="impact-warning">{risk.impact}</p>
          <JsonBlock value={risk} />
        </details>)
        : <p className="muted">无 HIGH、CRITICAL 或 blocking 风险。</p>}
      {otherRisks.length > 0 && <details>
        <summary>查看其余 {otherRisks.length} 项风险</summary>
        {otherRisks.map((risk) => <div className="impact-row" key={risk.id}>
          <Badge value={risk.severity} /><code>{risk.field_path ?? risk.risk_type}</code><small>{risk.message}</small>
        </div>)}
      </details>}
    </section>
    <section className="proposal-section">
      <h3>固定版本材料</h3>
      {value.impact_snapshot.artifacts.map((artifact) => <details key={artifact.id}>
        <summary><Badge value={artifact.artifact_type} /><span>{artifact.filename}</span></summary>
        <div className="impact-row"><span>VersionId</span><code>{artifact.s3_version_id ?? "缺失"}</code></div>
        <div className="impact-row"><span>SHA-256</span><code>{artifact.sha256}</code></div>
        <div className="impact-row"><span>云端哈希验证</span><Badge value={artifact.cloud_hash_verified ? "VERIFIED" : "MISSING"} /></div>
      </details>)}
    </section>
    <section className="proposal-section">
      <h3>追溯与审核回执</h3>
      <details><summary>查看 Manifest / Plan / Context / Intent 追溯</summary><JsonBlock value={value.impact_snapshot.trace} /></details>
      <details><summary>查看冻结审核回执</summary><JsonBlock value={value.impact_snapshot.review_receipt} /></details>
      <small>来源哈希：<code>{value.impact_snapshot.source_hash}</code></small>
    </section>
  </>;
}
