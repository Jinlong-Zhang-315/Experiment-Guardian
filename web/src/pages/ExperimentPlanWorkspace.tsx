import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Braces,
  CheckCircle2,
  ClipboardCheck,
  FilePenLine,
  History,
  RotateCcw,
  Save,
  X,
  XCircle,
} from "lucide-react";
import { api, formatTime, idempotencyKey } from "../api";
import { Badge, Empty, ErrorNotice, JsonBlock } from "../components";
import type {
  ExperimentPlanCandidateInvariant,
  ExperimentPlanEvidence,
  ExperimentPlanSummary,
  ExperimentPlanView,
  Page,
} from "../types";

type Tab = "review" | "plan" | "history" | "json";
type Decision = "APPROVED" | "CONDITIONALLY_APPROVED" | "REJECTED" | "CHANGES_REQUESTED";

function MarkdownText({ content }: { content: string }) {
  return <div className="draft-narrative">{content.split("\n").map((line, index) => {
    const key = `${index}-${line}`;
    if (line.startsWith("### ")) return <h4 key={key}>{line.slice(4)}</h4>;
    if (line.startsWith("## ")) return <h3 key={key}>{line.slice(3)}</h3>;
    if (line.startsWith("# ")) return <h2 key={key}>{line.slice(2)}</h2>;
    if (line.startsWith("- ")) return <p className="draft-bullet" key={key}>{line.slice(2)}</p>;
    if (!line) return <span className="draft-space" key={key} aria-hidden />;
    return <p key={key}>{line}</p>;
  })}</div>;
}

function CandidateChoice({
  value,
  choice,
  onChoice,
}: {
  value: ExperimentPlanCandidateInvariant;
  choice?: "CONFIRM" | "REJECT";
  onChoice: (choice: "CONFIRM" | "REJECT") => void;
}) {
  return <article className="plan-candidate">
    <header><Badge value={value.representation} /><strong>{value.statement}</strong></header>
    <p>{value.rationale}</p>
    <small>{value.verification_method}</small>
    {value.parameter_path && <div className="impact-row"><code>{value.parameter_path}</code><JsonBlock value={value.expected_value} /></div>}
    <div className="segmented" aria-label="候选不变量决定">
      <button className={choice === "CONFIRM" ? "active" : ""} onClick={() => onChoice("CONFIRM")}><CheckCircle2 />确认</button>
      <button className={choice === "REJECT" ? "active" : ""} onClick={() => onChoice("REJECT")}><XCircle />拒绝</button>
    </div>
  </article>;
}

export function ExperimentPlanWorkspace({
  projectId,
  initialPlanId,
  onClose,
}: {
  projectId: string;
  initialPlanId?: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState(initialPlanId ?? "");
  const [tab, setTab] = useState<Tab>("review");
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editPlan, setEditPlan] = useState("");
  const [editEvidence, setEditEvidence] = useState("{}");
  const [formError, setFormError] = useState("");
  const [decision, setDecision] = useState<Decision>();
  const [reason, setReason] = useState("");
  const [conditions, setConditions] = useState("");
  const [candidateChoices, setCandidateChoices] = useState<Record<string, "CONFIRM" | "REJECT">>({});

  const plans = useQuery({
    queryKey: ["experiment-plans", projectId],
    queryFn: () => api<Page<ExperimentPlanSummary>>(`/projects/${projectId}/agent/experiment-plans?limit=50`),
  });
  const detail = useQuery({
    queryKey: ["experiment-plan", projectId, selectedId],
    queryFn: () => api<ExperimentPlanView>(`/projects/${projectId}/agent/experiment-plans/${selectedId}`),
    enabled: Boolean(selectedId),
    refetchInterval: (query) => {
      const status = query.state.data?.summary.status;
      return status === "REVIEW_QUEUED" || status === "REVIEWING" ? 1500 : false;
    },
  });

  useEffect(() => {
    if (!selectedId && plans.data?.items[0]) setSelectedId(plans.data.items[0].plan_id);
  }, [plans.data, selectedId]);
  useEffect(() => {
    setEditing(false);
    setDecision(undefined);
    setReason("");
    setConditions("");
    setCandidateChoices({});
  }, [selectedId, detail.data?.current.revision_id]);

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["experiment-plans", projectId] });
    await queryClient.invalidateQueries({ queryKey: ["experiment-plan", projectId, selectedId] });
  }

  const revise = useMutation({
    mutationFn: (evidence: ExperimentPlanEvidence) => api(
      `/projects/${projectId}/agent/experiment-plans/${selectedId}/revisions`,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey() },
        body: JSON.stringify({
          expected_revision: detail.data?.summary.current_revision,
          title: editTitle,
          plan_markdown: editPlan,
          evidence,
        }),
      },
    ),
    onSuccess: async () => { setEditing(false); await refresh(); },
  });
  const retry = useMutation({
    mutationFn: () => api(`/projects/${projectId}/agent/experiment-plans/${selectedId}/review/retry`, {
      method: "POST", headers: { "Idempotency-Key": idempotencyKey() }, body: JSON.stringify({}),
    }),
    onSuccess: refresh,
  });
  const decide = useMutation({
    mutationFn: (value: Decision) => api<ExperimentPlanView>(
      `/projects/${projectId}/agent/experiment-plans/${selectedId}/decisions`,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey() },
        body: JSON.stringify({
          expected_revision: detail.data?.summary.current_revision,
          review_hash: detail.data?.review?.review_hash,
          approval_digest: detail.data?.review?.approval_digest,
          decision: value,
          reason,
          conditions: value === "CONDITIONALLY_APPROVED" ? conditions.split("\n").map((item) => item.trim()).filter(Boolean) : [],
          confirmed_candidate_ids: value === "APPROVED" || value === "CONDITIONALLY_APPROVED"
            ? Object.entries(candidateChoices).filter(([, choice]) => choice === "CONFIRM").map(([id]) => id)
            : [],
          rejected_candidate_ids: value === "APPROVED" || value === "CONDITIONALLY_APPROVED"
            ? Object.entries(candidateChoices).filter(([, choice]) => choice === "REJECT").map(([id]) => id)
            : [],
        }),
      },
    ),
    onSuccess: async () => { setDecision(undefined); await refresh(); },
  });

  function openEditor() {
    if (!detail.data) return;
    setEditTitle(detail.data.current.title);
    setEditPlan(detail.data.current.plan_markdown);
    setEditEvidence(JSON.stringify(detail.data.current.evidence, null, 2));
    setFormError("");
    setEditing(true);
  }
  function saveRevision() {
    try {
      revise.mutate(JSON.parse(editEvidence) as ExperimentPlanEvidence);
      setFormError("");
    } catch {
      setFormError("证据 JSON 格式无效");
    }
  }

  const value = detail.data;
  const candidatesComplete = (value?.review?.candidate_invariants.length ?? 0) === Object.keys(candidateChoices).length;
  const approvalDecision = decision === "APPROVED" || decision === "CONDITIONALLY_APPROVED";
  const canSubmitDecision = Boolean(
    decision && reason.trim() && (!approvalDecision || candidatesComplete) &&
    (decision !== "CONDITIONALLY_APPROVED" || conditions.trim()),
  );

  return <div className="draft-workspace-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="draft-workspace" role="dialog" aria-modal="true" aria-label="实验计划" onMouseDown={(event) => event.stopPropagation()}>
      <header className="draft-workspace-header">
        <div><ClipboardCheck /><span><strong>实验计划</strong><small>自然语言审核不能替代正式 Plan Check</small></span></div>
        <button className="icon-button" onClick={onClose} title="关闭" aria-label="关闭实验计划"><X /></button>
      </header>
      <div className="draft-workspace-body">
        <aside className="draft-list">
          {plans.error && <ErrorNotice error={plans.error} />}
          {!plans.isPending && !plans.data?.items.length && <Empty>外部 Coding Agent 尚未提交计划</Empty>}
          {plans.data?.items.map((item) => <button className={selectedId === item.plan_id ? "active" : ""} key={item.plan_id} onClick={() => setSelectedId(item.plan_id)}>
            <span><strong>{item.title}</strong><small>revision {item.current_revision} · {formatTime(item.updated_at)}</small></span>
            <span><Badge value={item.status} /><Badge value={item.freshness} /></span>
          </button>)}
        </aside>
        <main className="draft-detail plan-detail">
          {detail.error && <ErrorNotice error={detail.error} />}
          {!selectedId && <Empty>选择一份实验计划</Empty>}
          {selectedId && detail.isPending && <div className="page-loading">正在加载实验计划</div>}
          {value && !editing && <>
            <header className="draft-detail-header"><div><strong>{value.current.title}</strong><span>Context v{value.current.context_version} / Intent v{value.current.intent_version ?? "无"} / revision {value.current.revision}</span></div><div><Badge value={value.summary.status} /><Badge value={value.summary.freshness} /></div></header>
            {value.summary.freshness === "STALE" && <div className="draft-warning"><AlertTriangle />正式策略已变化，旧审核不能批准；请创建新 revision。</div>}
            <nav className="draft-tabs" aria-label="实验计划视图">
              <button className={tab === "review" ? "active" : ""} onClick={() => setTab("review")}><ClipboardCheck />审核</button>
              <button className={tab === "plan" ? "active" : ""} onClick={() => setTab("plan")}><FilePenLine />计划</button>
              <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}><History />历史</button>
              <button className={tab === "json" ? "active" : ""} onClick={() => setTab("json")}><Braces />JSON</button>
            </nav>
            <div className="draft-tab-content">
              {tab === "review" && !value.review && <Empty>{value.summary.status === "REVIEW_FAILED" ? "审核失败，可安全重试" : "内部 Agent 正在审核"}</Empty>}
              {tab === "review" && value.review && <div className="plan-review">
                <div className="fact-strip compact"><div><span>硬检查</span><strong>{value.review.hard_check.status}</strong></div><div><span>Agent 建议</span><strong>{value.review.semantic_review.recommendation}</strong></div><div><span>自动修订</span><strong>{value.current.automatic_revision_round}/2</strong></div></div>
                {value.review.hard_check.issues.map((issue) => <details open={issue.severity === "HIGH" || issue.severity === "CRITICAL"} key={issue.code}><summary><Badge value={issue.severity} />{issue.code}</summary><p>{issue.message}</p><JsonBlock value={issue} /></details>)}
                <MarkdownText content={value.review.semantic_review.review_markdown} />
                {value.review.semantic_review.findings.map((finding, index) => <details open={finding.severity === "HIGH" || finding.severity === "CRITICAL"} key={`${finding.kind}-${index}`}><summary><Badge value={finding.severity} />{finding.kind}</summary><p>{finding.statement}</p><small>{finding.rationale}</small></details>)}
                <h3>候选关键不变量</h3>
                {value.review.candidate_invariants.length ? value.review.candidate_invariants.map((item) => <CandidateChoice key={item.candidate_id} value={item} choice={candidateChoices[item.candidate_id]} onChoice={(choice) => setCandidateChoices((current) => ({ ...current, [item.candidate_id]: choice }))} />) : <p className="muted">没有新增候选关键不变量。</p>}
                <h3>自由探索范围</h3>{value.review.semantic_review.free_exploration.map((item) => <p className="draft-bullet" key={item}>{item}</p>)}
                {value.review.semantic_review.user_decisions.length > 0 && <div className="draft-warning"><AlertTriangle /><span>{value.review.semantic_review.user_decisions.join("；")}</span></div>}
              </div>}
              {tab === "plan" && <><MarkdownText content={value.current.plan_markdown} /><details><summary>计划证据</summary><JsonBlock value={value.current.evidence} /></details></>}
              {tab === "history" && <div className="draft-history">{value.revisions.map((item) => <article key={item.revision_id}><strong>revision {item.revision}</strong><span><Badge value={item.author_type} /> 自动轮次 {item.automatic_revision_round}/2</span><small>{formatTime(item.created_at)} · <code>{item.content_hash.slice(0, 12)}</code></small></article>)}</div>}
              {tab === "json" && <><p className="draft-source-note">结构化正式策略仍是唯一治理事实源。</p><JsonBlock value={value} /></>}
            </div>
            {value.decision && <div className="proposal-success"><CheckCircle2 /><span>已记录决定：{value.decision.decision}。{value.decision.reason}</span></div>}
            <footer className="draft-actions">
              {value.allowed_actions.includes("RETRY_REVIEW") && <button className="button" onClick={() => retry.mutate()} disabled={retry.isPending}><RotateCcw />重试审核</button>}
              {value.allowed_actions.includes("REVISE") && <button className="button" onClick={openEditor}><FilePenLine />编辑新 revision</button>}
              {value.allowed_actions.includes("REQUEST_CHANGES") && <button className="button" onClick={() => setDecision("CHANGES_REQUESTED")}>要求修改</button>}
              {value.allowed_actions.includes("REJECT") && <button className="button danger" onClick={() => setDecision("REJECTED")}>拒绝</button>}
              {value.allowed_actions.includes("CONDITIONAL_APPROVE") && <button className="button" onClick={() => setDecision("CONDITIONALLY_APPROVED")}>有条件批准</button>}
              {value.allowed_actions.includes("APPROVE") && <button className="button primary" onClick={() => setDecision("APPROVED")}><CheckCircle2 />批准</button>}
            </footer>
            {decision && <section className="plan-decision-form"><h3>{decision}</h3><label>决定理由<textarea rows={3} value={reason} onChange={(event) => setReason(event.target.value)} /></label>{decision === "CONDITIONALLY_APPROVED" && <label>批准条件（每行一项）<textarea rows={4} value={conditions} onChange={(event) => setConditions(event.target.value)} /></label>}<p>决定绑定当前 revision、审核哈希和全部候选不变量；正式 LOCKED 规则不能被覆盖。</p><div><button className="button" onClick={() => setDecision(undefined)}>取消</button><button className="button primary" disabled={!canSubmitDecision || decide.isPending} onClick={() => decide.mutate(decision)}><Save />确认决定</button></div></section>}
            {(retry.error || decide.error) && <ErrorNotice error={retry.error || decide.error} />}
          </>}
          {value && editing && <><header className="draft-detail-header"><div><strong>编辑 revision {value.summary.current_revision + 1}</strong><span>保存后重新绑定当前正式策略并由内部 Agent 审核</span></div></header><div className="draft-editor-scroll"><div className="raw-editor"><label>标题<input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} /></label><label>完整自然语言计划<textarea rows={24} value={editPlan} onChange={(event) => setEditPlan(event.target.value)} /></label><label>完整证据 JSON<textarea rows={12} value={editEvidence} onChange={(event) => setEditEvidence(event.target.value)} /></label></div></div>{(formError || revise.error) && <ErrorNotice error={formError || revise.error} />}<footer className="draft-actions"><button className="button" onClick={() => setEditing(false)}>取消</button><button className="button primary" disabled={!editTitle.trim() || !editPlan.trim() || revise.isPending} onClick={saveRevision}><Save />提交并审核</button></footer></>}
        </main>
      </div>
    </section>
  </div>;
}
