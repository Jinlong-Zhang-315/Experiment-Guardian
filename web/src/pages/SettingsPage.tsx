import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Clock3,
  FileJson,
  GitBranch,
  Pencil,
  RefreshCw,
  Shield,
} from "lucide-react";
import { useParams } from "react-router-dom";
import { api, formatTime, idempotencyKey } from "../api";
import { Badge, ErrorNotice, JsonBlock, Modal } from "../components";
import type { HumanReadablePolicy, Session, SettingsView } from "../types";

function createDraft(settings: SettingsView) {
  const payload = settings.current.context_payload;
  const context = {
    goal: payload.goal,
    non_goals: payload.non_goals,
    mainline_model: payload.mainline_model,
    baseline: payload.baseline,
    dataset: payload.dataset,
    protocol: payload.protocol,
    primary_metric: payload.primary_metric,
    default_seeds: payload.default_seeds,
    active_branch: payload.active_branch,
    active_config: payload.active_config,
    deprecated_items: payload.deprecated_items,
    key_decisions: payload.key_decisions,
    change_reason: "",
  };
  const intent = { ...settings.current.intent_payload };
  delete intent.intent_receipt;
  const constraints = settings.current.constraints.map((item) => {
    const draftConstraint = { ...item };
    for (const key of [
      "constraint_id",
      "version",
      "context_id",
      "context_version",
      "intent_id",
      "intent_version",
      "source_type",
      "verification_status",
      "confirmed_by",
      "confirmed_at",
    ]) {
      delete draftConstraint[key];
    }
    return draftConstraint;
  });
  return { context, intent, constraints };
}

function NarrativeContent({ content }: { content: string }) {
  return (
    <div className="narrative-content">
      {content.split("\n").map((line, index) => {
        const key = `${index}-${line}`;
        if (line.startsWith("### ")) return <h4 key={key}>{line.slice(4)}</h4>;
        if (line.startsWith("## ")) return <h3 key={key}>{line.slice(3)}</h3>;
        if (line.startsWith("# ")) return <h2 key={key}>{line.slice(2)}</h2>;
        if (line.startsWith("> ")) return <aside key={key}>{line.slice(2)}</aside>;
        if (line.startsWith("- ")) return <p className="narrative-bullet" key={key}>{line.slice(2)}</p>;
        if (!line) return <span className="narrative-space" key={key} aria-hidden />;
        return <p key={key}>{line}</p>;
      })}
    </div>
  );
}

function NarrativePanel({
  value,
  canRegenerate,
  pending,
  onRegenerate,
}: {
  value?: HumanReadablePolicy;
  canRegenerate: boolean;
  pending: boolean;
  onRegenerate: () => void;
}) {
  const status = value?.status ?? "MISSING";
  return (
    <section className="narrative-section">
      <div className="section-heading narrative-heading">
        <div>
          <h2>项目正式说明</h2>
          <span>
            Context v{value?.context_version ?? "-"} / Intent v{value?.intent_version ?? "-"}
          </span>
        </div>
        <Badge value={status} />
      </div>
      {status === "READY" && value?.content ? (
        <>
          <NarrativeContent content={value.content} />
          <footer className="narrative-meta">
            <span>模板 {value.generator_version}</span>
            <span>生成于 {formatTime(value.generated_at)}</span>
            <span className="mono">来源 {value.source_hash?.slice(0, 12)}…</span>
          </footer>
        </>
      ) : (
        <div className="narrative-unavailable">
          <p>{value?.error ?? "该结构化版本尚未生成对应的人类可读说明。"}</p>
          {canRegenerate && (
            <button className="button" disabled={pending} onClick={onRegenerate}>
              <RefreshCw aria-hidden />
              重新生成
            </button>
          )}
        </div>
      )}
    </section>
  );
}

export function SettingsPage({ session }: { session: Session }) {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["settings", projectId],
    queryFn: () => api<SettingsView>(`/projects/${projectId}/settings`),
  });
  const [editing, setEditing] = useState(false);
  const [contextJson, setContextJson] = useState("");
  const [intentJson, setIntentJson] = useState("");
  const [constraintsJson, setConstraintsJson] = useState("");
  const [formError, setFormError] = useState("");
  const draft = useMemo(() => query.data ? createDraft(query.data) : null, [query.data]);
  const publish = useMutation({
    mutationFn: (body: unknown) => api(`/projects/${projectId}/policy-versions`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey() },
      body: JSON.stringify(body),
    }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["settings", projectId] });
      setEditing(false);
    },
  });
  const regenerate = useMutation({
    mutationFn: (contextId: string) => api<HumanReadablePolicy>(
      `/projects/${projectId}/contexts/${contextId}/human-readable/regenerate`,
      { method: "POST" },
    ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["settings", projectId] });
    },
  });

  if (query.error) return <ErrorNotice error={query.error} />;
  if (!query.data || !draft) return <div className="page-loading">正在加载项目策略</div>;
  const { current, context_history: history, project } = query.data;

  function openEditor() {
    setContextJson(JSON.stringify(draft!.context, null, 2));
    setIntentJson(JSON.stringify(draft!.intent, null, 2));
    setConstraintsJson(JSON.stringify(draft!.constraints, null, 2));
    setFormError("");
    setEditing(true);
  }

  function submit() {
    try {
      publish.mutate({
        expected_context_version: current.context.version,
        context: JSON.parse(contextJson),
        intent: JSON.parse(intentJson),
        constraints: JSON.parse(constraintsJson),
      });
    } catch {
      setFormError("JSON 格式无效");
    }
  }

  return (
    <main className="page">
      <header className="page-header">
        <div>
          <span className="eyebrow">项目设置</span>
          <h1>{project.name}</h1>
          <p>{project.description || "未填写项目描述"}</p>
        </div>
        {session.role === "OWNER" && (
          <button className="button primary" onClick={openEditor}>
            <Pencil aria-hidden />
            发布新版本
          </button>
        )}
      </header>

      <section className="fact-strip">
        <div><Shield /><span>生效 Context</span><strong>v{current.context.version}</strong></div>
        <div><GitBranch /><span>Active Intent</span><strong>v{current.active_intent.version}</strong></div>
        <div><Check /><span>已确认约束</span><strong>{current.constraints.length}</strong></div>
      </section>

      <NarrativePanel
        value={current.human_readable}
        canRegenerate={session.role === "OWNER"}
        pending={regenerate.isPending}
        onRegenerate={() => regenerate.mutate(current.context.context_id)}
      />
      {regenerate.error && <ErrorNotice error={regenerate.error} />}

      <details className="structured-source">
        <summary><FileJson aria-hidden />结构化事实源与完整 JSON</summary>
        <p>Plan Check、审批、Manifest 和执行治理仅使用以下结构化数据。</p>
        <JsonBlock value={{
          context: current.context,
          active_intent: current.active_intent,
          context_payload: current.context_payload,
          intent_payload: current.intent_payload,
          constraints: current.constraints,
        }} />
      </details>

      <section className="table-section">
        <div className="section-heading">
          <h2>参数约束</h2>
          <span>{current.constraints.length} 条</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>参数路径</th><th>保护级别</th><th>正式值</th><th>原因</th></tr>
            </thead>
            <tbody>
              {current.constraints.map((item) => (
                <tr key={item.parameter_path}>
                  <td className="mono">{item.parameter_path}</td>
                  <td><Badge value={item.protection_level} /></td>
                  <td className="mono">{JSON.stringify(item.expected_value)}</td>
                  <td>{String(item.reason)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="history">
        <h2>Context 历史</h2>
        {history.map((item) => (
          <details className="history-entry" key={item.context_id}>
            <summary className="history-row">
              <Clock3 aria-hidden />
              <strong>v{item.version}</strong>
              <Badge value={item.status} />
              <span>{item.change_reason}</span>
              <time>{formatTime(item.effective_at)}</time>
            </summary>
            <NarrativePanel
              value={item.human_readable}
              canRegenerate={session.role === "OWNER"}
              pending={regenerate.isPending}
              onRegenerate={() => regenerate.mutate(item.context_id)}
            />
          </details>
        ))}
      </section>

      {editing && (
        <Modal title={`发布 Context v${current.context.version + 1}`} onClose={() => setEditing(false)}>
          <div className="modal-body form-stack">
            <label>正式上下文<textarea rows={12} value={contextJson} onChange={(event) => setContextJson(event.target.value)} /></label>
            <label>实验意图<textarea rows={9} value={intentJson} onChange={(event) => setIntentJson(event.target.value)} /></label>
            <label>参数约束<textarea rows={12} value={constraintsJson} onChange={(event) => setConstraintsJson(event.target.value)} /></label>
            {(formError || publish.error) && <ErrorNotice error={formError || publish.error} />}
          </div>
          <footer className="modal-actions">
            <button className="button" onClick={() => setEditing(false)}>取消</button>
            <button className="button primary" disabled={publish.isPending} onClick={submit}>确认并发布</button>
          </footer>
        </Modal>
      )}
    </main>
  );
}
