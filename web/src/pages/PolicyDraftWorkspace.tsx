import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Archive,
  Braces,
  FileDiff,
  FilePenLine,
  History,
  ListChecks,
  Plus,
  Save,
  Trash2,
  X,
} from "lucide-react";
import { api, formatTime, idempotencyKey } from "../api";
import { Badge, Empty, ErrorNotice, JsonBlock } from "../components";
import type {
  Page,
  PolicyConstraintCandidate,
  PolicyDraftAmbiguity,
  PolicyDraftCandidate,
  PolicyDraftRevision,
  PolicyDraftSummary,
  PolicyDraftView,
} from "../types";

type DraftTab = "receipt" | "diff" | "impact" | "json" | "history";

function lines(value: string[]): string {
  return value.join("\n");
}

function splitLines(value: string): string[] {
  return value.split("\n").map((item) => item.trim()).filter(Boolean);
}

function MarkdownText({ content }: { content: string }) {
  return <div className="draft-narrative">{content.split("\n").map((line, index) => {
    const key = `${index}-${line}`;
    if (line.startsWith("### ")) return <h4 key={key}>{line.slice(4)}</h4>;
    if (line.startsWith("## ")) return <h3 key={key}>{line.slice(3)}</h3>;
    if (line.startsWith("# ")) return <h2 key={key}>{line.slice(2)}</h2>;
    if (line.startsWith("> ")) return <aside key={key}>{line.slice(2)}</aside>;
    if (line.startsWith("- ")) return <p className="draft-bullet" key={key}>{line.slice(2)}</p>;
    if (!line) return <span className="draft-space" key={key} aria-hidden />;
    return <p key={key}>{line}</p>;
  })}</div>;
}

function JsonValueInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  const [error, setError] = useState("");
  useEffect(() => setText(JSON.stringify(value, null, 2)), [value]);
  function apply() {
    try {
      onChange(JSON.parse(text));
      setError("");
    } catch {
      setError("JSON 格式无效");
    }
  }
  return <label>{label}
    <textarea rows={4} value={text} onChange={(event) => setText(event.target.value)} onBlur={apply} />
    {error && <small className="field-error">{error}</small>}
  </label>;
}

function CandidateEditor({
  value,
  ambiguities,
  changeSummary,
  onCandidate,
  onAmbiguities,
  onChangeSummary,
}: {
  value: PolicyDraftCandidate;
  ambiguities: PolicyDraftAmbiguity[];
  changeSummary: string;
  onCandidate: (value: PolicyDraftCandidate) => void;
  onAmbiguities: (value: PolicyDraftAmbiguity[]) => void;
  onChangeSummary: (value: string) => void;
}) {
  function context<K extends keyof PolicyDraftCandidate["context"]>(
    key: K,
    next: PolicyDraftCandidate["context"][K],
  ) {
    onCandidate({ ...value, context: { ...value.context, [key]: next } });
  }
  function intent<K extends keyof PolicyDraftCandidate["intent"]>(
    key: K,
    next: PolicyDraftCandidate["intent"][K],
  ) {
    onCandidate({ ...value, intent: { ...value.intent, [key]: next } });
  }
  function constraint(index: number, next: PolicyConstraintCandidate) {
    const constraints = [...value.constraints];
    constraints[index] = next;
    onCandidate({ ...value, constraints });
  }
  return <div className="draft-editor">
    <section>
      <h3>变更说明</h3>
      <label>本次 revision 说明
        <textarea rows={3} value={changeSummary} onChange={(event) => onChangeSummary(event.target.value)} />
      </label>
    </section>
    <section>
      <h3>项目 Context</h3>
      <div className="draft-form-grid">
        <label className="wide">项目目标<textarea rows={3} value={value.context.goal} onChange={(event) => context("goal", event.target.value)} /></label>
        <label>主线模型<input value={value.context.mainline_model} onChange={(event) => context("mainline_model", event.target.value)} /></label>
        <label>数据集<input value={value.context.dataset} onChange={(event) => context("dataset", event.target.value)} /></label>
        <label>实验协议<input value={value.context.protocol} onChange={(event) => context("protocol", event.target.value)} /></label>
        <label>正式分支<input value={value.context.active_branch} onChange={(event) => context("active_branch", event.target.value)} /></label>
        <label className="wide">非目标（每行一项）<textarea rows={3} value={lines(value.context.non_goals)} onChange={(event) => context("non_goals", splitLines(event.target.value))} /></label>
        <label className="wide">Context 变更原因<textarea rows={2} value={value.context.change_reason} onChange={(event) => context("change_reason", event.target.value)} /></label>
        <JsonValueInput label="Baseline JSON" value={value.context.baseline} onChange={(next) => context("baseline", next as Record<string, unknown>)} />
        <JsonValueInput label="主指标 JSON" value={value.context.primary_metric} onChange={(next) => context("primary_metric", next as Record<string, unknown>)} />
        <div className="wide"><JsonValueInput label="Active Config JSON" value={value.context.active_config} onChange={(next) => context("active_config", next as Record<string, unknown>)} /></div>
      </div>
    </section>
    <section>
      <h3>Experiment Intent</h3>
      <div className="draft-form-grid">
        <label>名称<input value={value.intent.name} onChange={(event) => intent("name", event.target.value)} /></label>
        <label className="wide">目标<textarea rows={2} value={value.intent.objective} onChange={(event) => intent("objective", event.target.value)} /></label>
        <label className="wide">假设<textarea rows={2} value={value.intent.hypothesis} onChange={(event) => intent("hypothesis", event.target.value)} /></label>
        <label>允许变量（每行一项）<textarea rows={4} value={lines(value.intent.allowed_variables)} onChange={(event) => intent("allowed_variables", splitLines(event.target.value))} /></label>
        <label>受控变量（每行一项）<textarea rows={4} value={lines(value.intent.controlled_variables)} onChange={(event) => intent("controlled_variables", splitLines(event.target.value))} /></label>
        <label>预期输出（每行一项）<textarea rows={3} value={lines(value.intent.expected_outputs)} onChange={(event) => intent("expected_outputs", splitLines(event.target.value))} /></label>
        <label>接受标准（每行一项）<textarea rows={3} value={lines(value.intent.acceptance_criteria)} onChange={(event) => intent("acceptance_criteria", splitLines(event.target.value))} /></label>
      </div>
    </section>
    <section>
      <div className="section-heading"><h3>参数约束</h3><button className="button" onClick={() => onCandidate({
        ...value,
        constraints: [...value.constraints, {
          parameter_path: "",
          protection_level: "APPROVAL_REQUIRED",
          expected_value: null,
          reason: "",
          original_message: "用户在 Web 草稿编辑器中新增",
        }],
      })}><Plus />新增约束</button></div>
      <div className="draft-constraint-list">{value.constraints.map((item, index) => <article key={`${index}-${item.parameter_path}`}>
        <div className="draft-constraint-heading">
          <strong>约束 {index + 1}</strong>
          <button className="icon-button" title="删除约束" aria-label={`删除约束 ${index + 1}`} onClick={() => onCandidate({ ...value, constraints: value.constraints.filter((_, position) => position !== index) })}><Trash2 /></button>
        </div>
        <div className="draft-form-grid">
          <label>参数路径<input value={item.parameter_path} onChange={(event) => constraint(index, { ...item, parameter_path: event.target.value })} /></label>
          <label>保护级别<select value={item.protection_level} onChange={(event) => constraint(index, { ...item, protection_level: event.target.value as PolicyConstraintCandidate["protection_level"] })}>
            <option value="LOCKED">LOCKED</option><option value="APPROVAL_REQUIRED">APPROVAL_REQUIRED</option><option value="EXPERIMENT_VARIABLE">EXPERIMENT_VARIABLE</option>
          </select></label>
          <JsonValueInput label="正式期望值 JSON" value={item.expected_value} onChange={(next) => constraint(index, { ...item, expected_value: next })} />
          <label>原因<textarea rows={3} value={item.reason} onChange={(event) => constraint(index, { ...item, reason: event.target.value })} /></label>
        </div>
      </article>)}</div>
    </section>
    <section>
      <div className="section-heading"><h3>未解决歧义</h3><button className="button" onClick={() => onAmbiguities([...ambiguities, { field_path: "context", question: "", source_text: "Web 编辑" }])}><Plus />新增歧义</button></div>
      {ambiguities.map((item, index) => <div className="draft-ambiguity-row" key={`${index}-${item.field_path}`}>
        <input aria-label={`歧义字段 ${index + 1}`} value={item.field_path} onChange={(event) => onAmbiguities(ambiguities.map((value, position) => position === index ? { ...value, field_path: event.target.value } : value))} />
        <input aria-label={`歧义问题 ${index + 1}`} value={item.question} onChange={(event) => onAmbiguities(ambiguities.map((value, position) => position === index ? { ...value, question: event.target.value } : value))} />
        <button className="icon-button" title="删除歧义" aria-label={`删除歧义 ${index + 1}`} onClick={() => onAmbiguities(ambiguities.filter((_, position) => position !== index))}><Trash2 /></button>
      </div>)}
    </section>
  </div>;
}

export function PolicyDraftWorkspace({
  projectId,
  initialDraftId,
  onClose,
}: {
  projectId: string;
  initialDraftId?: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [showAbandoned, setShowAbandoned] = useState(false);
  const [selectedId, setSelectedId] = useState(initialDraftId ?? "");
  const [selectedRevision, setSelectedRevision] = useState<number>();
  const [tab, setTab] = useState<DraftTab>("receipt");
  const [editing, setEditing] = useState(false);
  const [editCandidate, setEditCandidate] = useState<PolicyDraftCandidate>();
  const [editAmbiguities, setEditAmbiguities] = useState<PolicyDraftAmbiguity[]>([]);
  const [editSummary, setEditSummary] = useState("");
  const [rawJson, setRawJson] = useState("");
  const [editorMode, setEditorMode] = useState<"structured" | "raw">("structured");
  const [formError, setFormError] = useState("");
  const [abandoning, setAbandoning] = useState(false);
  const [abandonReason, setAbandonReason] = useState("");

  const drafts = useQuery({
    queryKey: ["policy-drafts", projectId, showAbandoned],
    queryFn: () => api<Page<PolicyDraftSummary>>(`/projects/${projectId}/agent/policy-drafts?status=${showAbandoned ? "ABANDONED" : "ACTIVE"}`),
  });
  const detail = useQuery({
    queryKey: ["policy-draft", projectId, selectedId],
    queryFn: () => api<PolicyDraftView>(`/projects/${projectId}/agent/policy-drafts/${selectedId}`),
    enabled: Boolean(selectedId),
  });
  const historyRevision = useQuery({
    queryKey: ["policy-draft-revision", projectId, selectedId, selectedRevision],
    queryFn: () => api<PolicyDraftRevision>(`/projects/${projectId}/agent/policy-drafts/${selectedId}/revisions/${selectedRevision}`),
    enabled: Boolean(selectedId && selectedRevision),
  });
  const display = selectedRevision ? historyRevision.data : detail.data?.current;

  useEffect(() => {
    if (!selectedId && drafts.data?.items[0]) setSelectedId(drafts.data.items[0].draft_id);
  }, [drafts.data, selectedId]);
  useEffect(() => {
    setSelectedRevision(undefined);
    setEditing(false);
    setAbandoning(false);
  }, [selectedId]);

  const save = useMutation({
    mutationFn: (body: unknown) => api<PolicyDraftRevision>(
      `/projects/${projectId}/agent/policy-drafts/${selectedId}/revisions`,
      { method: "POST", headers: { "Idempotency-Key": idempotencyKey() }, body: JSON.stringify(body) },
    ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["policy-draft", projectId, selectedId] });
      await queryClient.invalidateQueries({ queryKey: ["policy-drafts", projectId] });
      setEditing(false);
    },
  });
  const abandon = useMutation({
    mutationFn: () => api(`/projects/${projectId}/agent/policy-drafts/${selectedId}/abandon`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey() },
      body: JSON.stringify({ expected_revision: detail.data?.summary.current_revision, reason: abandonReason }),
    }),
    onSuccess: async () => {
      setSelectedId("");
      setAbandoning(false);
      await queryClient.invalidateQueries({ queryKey: ["policy-drafts", projectId] });
    },
  });

  function openEditor() {
    if (!detail.data) return;
    const current = structuredClone(detail.data.current.candidate);
    setEditCandidate(current);
    setEditAmbiguities(structuredClone(detail.data.current.unresolved_ambiguities));
    setEditSummary(detail.data.current.change_summary);
    setRawJson(JSON.stringify(current, null, 2));
    setEditorMode("structured");
    setFormError("");
    setEditing(true);
  }

  function submitEdit() {
    if (!detail.data || !editCandidate) return;
    try {
      const candidate = editorMode === "raw" ? JSON.parse(rawJson) : editCandidate;
      save.mutate({
        expected_revision: detail.data.summary.current_revision,
        candidate,
        change_summary: editSummary,
        unresolved_ambiguities: editAmbiguities,
      });
      setFormError("");
    } catch {
      setFormError("候选 Bundle JSON 格式无效");
    }
  }

  const tabs = useMemo<Array<[DraftTab, string, typeof ListChecks]>>(() => [
    ["receipt", "说明", ListChecks],
    ["diff", "差异", FileDiff],
    ["impact", "影响", AlertTriangle],
    ["json", "JSON", Braces],
    ["history", "历史", History],
  ], []);

  return <div className="draft-workspace-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="draft-workspace" role="dialog" aria-modal="true" aria-label="治理草稿" onMouseDown={(event) => event.stopPropagation()}>
      <header className="draft-workspace-header">
        <div><FilePenLine /><span><strong>治理草稿</strong><small>候选内容不会自动生效</small></span></div>
        <div><button className="button" onClick={() => { setShowAbandoned((value) => !value); setSelectedId(""); }}><Archive />{showAbandoned ? "活动草稿" : "已取消"}</button><button className="icon-button" onClick={onClose} title="关闭" aria-label="关闭治理草稿"><X /></button></div>
      </header>
      <div className="draft-workspace-body">
        <aside className="draft-list">
          {drafts.error && <ErrorNotice error={drafts.error} />}
          {!drafts.isPending && !drafts.data?.items.length && <Empty>{showAbandoned ? "没有已取消草稿" : "通过 Agent 对话创建第一份治理草稿"}</Empty>}
          {drafts.data?.items.map((item) => <button className={selectedId === item.draft_id ? "active" : ""} key={item.draft_id} onClick={() => setSelectedId(item.draft_id)}>
            <span><strong>{item.change_summary}</strong><small>Context v{item.base_context_version} / revision {item.current_revision}</small></span>
            <span><Badge value={item.readiness} /><Badge value={item.freshness} /></span>
          </button>)}
        </aside>
        <main className="draft-detail">
          {detail.error && <ErrorNotice error={detail.error} />}
          {!selectedId && <Empty>选择一份治理草稿查看候选内容</Empty>}
          {selectedId && detail.isPending && <div className="page-loading">正在加载草稿</div>}
          {detail.data && !editing && <>
            <header className="draft-detail-header">
              <div><strong>{detail.data.summary.change_summary}</strong><span>Context v{detail.data.summary.base_context_version} / Intent v{detail.data.summary.base_intent_version} / revision {display?.revision ?? detail.data.summary.current_revision}</span></div>
              <div><Badge value={detail.data.summary.status} /><Badge value={detail.data.summary.freshness} /><Badge value={display?.validation.readiness ?? detail.data.summary.readiness} /></div>
            </header>
            {detail.data.summary.freshness === "STALE" && <div className="draft-warning"><AlertTriangle />正式 Policy Bundle 已变化；该草稿只读，不能静默 rebase。</div>}
            <nav className="draft-tabs" aria-label="治理草稿视图">{tabs.map(([key, label, Icon]) => <button className={tab === key ? "active" : ""} key={key} onClick={() => setTab(key)}><Icon />{label}</button>)}</nav>
            <div className="draft-tab-content">
              {display && tab === "receipt" && (display.narrative.status === "READY" && display.narrative.content ? <MarkdownText content={display.narrative.content} /> : <ErrorNotice error={display.narrative.error} />)}
              {display && tab === "diff" && <div className="draft-diff-list">{display.diff.length ? display.diff.map((item) => <details open={item.attention_level === "HIGH"} key={item.field_path}>
                <summary><Badge value={item.attention_level} /><code>{item.field_path}</code><Badge value={item.change_type} /></summary>
                <p>{item.impact}</p><div className="draft-value-pair"><div><small>原值</small><JsonBlock value={item.previous_value} /></div><div><small>候选值</small><JsonBlock value={item.candidate_value} /></div></div>
              </details>) : <Empty>该 revision 没有结构化变更</Empty>}</div>}
              {display && tab === "impact" && <div className="draft-impact">
                <div className="fact-strip compact"><div><AlertTriangle /><span>关注等级</span><strong>{display.current_impact.attention_level}</strong></div><div><ListChecks /><span>Plan 模拟</span><strong>{display.current_impact.plan_simulations.length}</strong></div></div>
                {display.impact_changed_since_revision && <div className="draft-warning"><AlertTriangle />待办状态已变化，当前影响与 revision 创建时不同。</div>}
                {display.current_impact.warnings.map((warning) => <p className="impact-warning" key={warning}>{warning}</p>)}
                <h3>待审批 Plan 模拟</h3>
                {display.current_impact.plan_simulations.map((item) => <div className="impact-row" key={item.plan_check_id}><code>{item.plan_check_id}</code><span><Badge value={item.original_check_result} /> → <Badge value={item.simulated_check_result ?? item.status} /></span><small>{item.governance_notice}</small></div>)}
                <h3>进行中 Submission</h3>
                {display.current_impact.submission_impacts.map((item) => <div className="impact-row" key={item.submission_id}><code>{item.submission_id}</code><Badge value={item.status} /><small>{item.message}</small></div>)}
              </div>}
              {display && tab === "json" && <><p className="draft-source-note">该 JSON 是候选数据，不是正式事实源。</p><JsonBlock value={display.candidate} /></>}
              {tab === "history" && <div className="draft-history">{detail.data.revisions.map((item) => <button className={display?.revision === item.revision ? "active" : ""} key={item.revision_id} onClick={() => setSelectedRevision(item.revision === detail.data!.summary.current_revision ? undefined : item.revision)}>
                <strong>revision {item.revision}</strong><span><Badge value={item.readiness} /><Badge value={item.source} /></span><small>{item.change_summary} · {formatTime(item.created_at)}</small>
              </button>)}</div>}
            </div>
            {detail.data.summary.status === "ACTIVE" && !selectedRevision && <footer className="draft-actions">
              {abandoning ? <><label>取消原因<input value={abandonReason} onChange={(event) => setAbandonReason(event.target.value)} /></label><button className="button" onClick={() => setAbandoning(false)}>返回</button><button className="button danger" disabled={!abandonReason.trim() || abandon.isPending} onClick={() => abandon.mutate()}>确认取消</button></> : <>
                <button className="button danger" onClick={() => setAbandoning(true)}><Archive />取消草稿</button>
                <button className="button primary" disabled={detail.data.summary.freshness === "STALE"} onClick={openEditor}><FilePenLine />编辑新 revision</button>
              </>}
            </footer>}
            {(abandon.error || historyRevision.error) && <ErrorNotice error={abandon.error || historyRevision.error} />}
          </>}
          {detail.data && editing && editCandidate && <>
            <header className="draft-detail-header"><div><strong>编辑 revision {detail.data.summary.current_revision + 1}</strong><span>保存后不会覆盖旧 revision，也不会发布正式策略</span></div><div className="segmented"><button className={editorMode === "structured" ? "active" : ""} onClick={() => setEditorMode("structured")}>结构化</button><button className={editorMode === "raw" ? "active" : ""} onClick={() => { setRawJson(JSON.stringify(editCandidate, null, 2)); setEditorMode("raw"); }}>原始 JSON</button></div></header>
            <div className="draft-editor-scroll">{editorMode === "structured" ? <CandidateEditor value={editCandidate} ambiguities={editAmbiguities} changeSummary={editSummary} onCandidate={setEditCandidate} onAmbiguities={setEditAmbiguities} onChangeSummary={setEditSummary} /> : <div className="raw-editor"><label>完整候选 Bundle<textarea rows={34} value={rawJson} onChange={(event) => setRawJson(event.target.value)} /></label><label>本次 revision 说明<textarea rows={3} value={editSummary} onChange={(event) => setEditSummary(event.target.value)} /></label></div>}</div>
            {(formError || save.error) && <ErrorNotice error={formError || save.error} />}
            <footer className="draft-actions"><button className="button" onClick={() => setEditing(false)}>取消编辑</button><button className="button primary" disabled={!editSummary.trim() || save.isPending} onClick={submitEdit}><Save />保存 revision</button></footer>
          </>}
        </main>
      </div>
    </section>
  </div>;
}
