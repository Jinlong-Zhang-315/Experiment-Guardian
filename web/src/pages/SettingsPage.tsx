import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Clock3, GitBranch, Pencil, Shield } from "lucide-react";
import { useParams } from "react-router-dom";
import { api, formatTime, idempotencyKey } from "../api";
import { Badge, ErrorNotice, JsonBlock, Modal } from "../components";
import type { Session, SettingsView } from "../types";

function createDraft(settings: SettingsView) {
  const payload = settings.current.context_payload;
  const context = {
    goal: payload.goal, non_goals: payload.non_goals, mainline_model: payload.mainline_model,
    baseline: payload.baseline, dataset: payload.dataset, protocol: payload.protocol,
    primary_metric: payload.primary_metric, default_seeds: payload.default_seeds,
    active_branch: payload.active_branch, active_config: payload.active_config,
    deprecated_items: payload.deprecated_items, key_decisions: payload.key_decisions,
    change_reason: "",
  };
  const intent = { ...settings.current.intent_payload };
  delete intent.intent_receipt;
  const constraints = settings.current.constraints.map((item) => {
    const draftConstraint = { ...item };
    for (const key of ["context_id", "context_version", "intent_id", "intent_version", "source_type", "verification_status", "confirmed_by", "confirmed_at"]) {
      delete draftConstraint[key];
    }
    return draftConstraint;
  });
  return { context, intent, constraints };
}

export function SettingsPage({ session }: { session: Session }) {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["settings", projectId], queryFn: () => api<SettingsView>(`/projects/${projectId}/settings`) });
  const [editing, setEditing] = useState(false);
  const [contextJson, setContextJson] = useState("");
  const [intentJson, setIntentJson] = useState("");
  const [constraintsJson, setConstraintsJson] = useState("");
  const [formError, setFormError] = useState("");
  const draft = useMemo(() => query.data ? createDraft(query.data) : null, [query.data]);
  const publish = useMutation({
    mutationFn: (body: unknown) => api(`/projects/${projectId}/policy-versions`, { method: "POST", headers: { "Idempotency-Key": idempotencyKey() }, body: JSON.stringify(body) }),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["settings", projectId] }); setEditing(false); },
  });
  if (query.error) return <ErrorNotice error={query.error} />;
  if (!query.data || !draft) return <div className="page-loading">正在加载项目策略</div>;
  const { current, context_history: history, project } = query.data;
  function openEditor() {
    setContextJson(JSON.stringify(draft!.context, null, 2));
    setIntentJson(JSON.stringify(draft!.intent, null, 2));
    setConstraintsJson(JSON.stringify(draft!.constraints, null, 2));
    setFormError(""); setEditing(true);
  }
  function submit() {
    try {
      publish.mutate({ expected_context_version: current.context.version, context: JSON.parse(contextJson), intent: JSON.parse(intentJson), constraints: JSON.parse(constraintsJson) });
    } catch { setFormError("JSON 格式无效"); }
  }
  return (
    <main className="page"><header className="page-header"><div><span className="eyebrow">项目设置</span><h1>{project.name}</h1><p>{project.description || "未填写项目描述"}</p></div>{session.role === "OWNER" && <button className="button primary" onClick={openEditor}><Pencil aria-hidden />发布新版本</button>}</header>
      <section className="fact-strip"><div><Shield /><span>生效 Context</span><strong>v{current.context.version}</strong></div><div><GitBranch /><span>Active Intent</span><strong>v{current.active_intent.version}</strong></div><div><Check /><span>已确认约束</span><strong>{current.constraints.length}</strong></div></section>
      <div className="settings-grid"><section><h2>正式上下文</h2><dl className="facts"><dt>目标</dt><dd>{String(current.context_payload.goal)}</dd><dt>数据集 / 协议</dt><dd>{String(current.context_payload.dataset)} / {String(current.context_payload.protocol)}</dd><dt>主线模型</dt><dd>{String(current.context_payload.mainline_model)}</dd><dt>生效时间</dt><dd>{formatTime(current.context.effective_at as string)}</dd><dt>确认人</dt><dd className="mono">{String(current.context.confirmed_by)}</dd></dl><details><summary>配置与基线</summary><JsonBlock value={{ baseline: current.context_payload.baseline, active_config: current.context_payload.active_config }} /></details></section>
        <section><h2>Active 实验意图</h2><dl className="facts"><dt>名称</dt><dd>{String(current.intent_payload.name)}</dd><dt>目标</dt><dd>{String(current.intent_payload.objective)}</dd><dt>假设</dt><dd>{String(current.intent_payload.hypothesis)}</dd><dt>模式</dt><dd><Badge value={String(current.active_intent.mode)} /></dd></dl><details><summary>完整意图回执</summary><JsonBlock value={current.intent_payload} /></details></section></div>
      <section className="table-section"><div className="section-heading"><h2>参数约束</h2><span>{current.constraints.length} 条</span></div><div className="table-wrap"><table><thead><tr><th>参数路径</th><th>保护级别</th><th>正式值</th><th>原因</th></tr></thead><tbody>{current.constraints.map((item) => <tr key={item.parameter_path}><td className="mono">{item.parameter_path}</td><td><Badge value={item.protection_level} /></td><td className="mono">{JSON.stringify(item.expected_value)}</td><td>{String(item.reason)}</td></tr>)}</tbody></table></div></section>
      <section className="history"><h2>Context 历史</h2>{history.map((item) => <div className="history-row" key={item.context_id}><Clock3 aria-hidden /><strong>v{item.version}</strong><Badge value={item.status} /><span>{item.change_reason}</span><time>{formatTime(item.effective_at)}</time></div>)}</section>
      {editing && <Modal title={`发布 Context v${current.context.version + 1}`} onClose={() => setEditing(false)}><div className="modal-body form-stack"><label>正式上下文<textarea rows={12} value={contextJson} onChange={(event) => setContextJson(event.target.value)} /></label><label>实验意图<textarea rows={9} value={intentJson} onChange={(event) => setIntentJson(event.target.value)} /></label><label>参数约束<textarea rows={12} value={constraintsJson} onChange={(event) => setConstraintsJson(event.target.value)} /></label>{(formError || publish.error) && <ErrorNotice error={formError || publish.error} />}</div><footer className="modal-actions"><button className="button" onClick={() => setEditing(false)}>取消</button><button className="button primary" disabled={publish.isPending} onClick={submit}>确认并发布</button></footer></Modal>}
    </main>
  );
}
