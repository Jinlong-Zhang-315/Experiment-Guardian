import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Archive, ArchiveRestore, Bot, BookOpenText, ClipboardCheck, FilePenLine, Gauge, Info, MessageSquarePlus, RotateCcw, Send, ShieldCheck, TriangleAlert } from "lucide-react";
import { useParams } from "react-router-dom";
import { api, formatTime, idempotencyKey, streamServerEvents } from "../api";
import { Badge, Empty, ErrorNotice } from "../components";
import type {
  AgentMessage,
  AgentCapabilityDomain,
  AgentRun,
  AgentRunReceipt,
  AgentThread,
  AgentThreadView,
  Page,
  Session,
} from "../types";
import { PolicyDraftWorkspace } from "./PolicyDraftWorkspace";
import { ActionProposalWorkspace } from "./ActionProposalWorkspace";
import { ResearchReportWorkspace } from "./ResearchReportWorkspace";
import { AgentObservabilityWorkspace, AgentRunDetailsDialog } from "./AgentObservabilityWorkspace";
import { ExperimentPlanWorkspace } from "./ExperimentPlanWorkspace";

const ACTIVE_RUNS = new Set(["PENDING", "RUNNING", "RETRYABLE_FAILURE"]);
const CAPABILITY_OPTIONS: Array<{ value: AgentCapabilityDomain; label: string }> = [
  { value: "GENERAL", label: "通用" },
  { value: "ANALYSIS", label: "实验分析" },
  { value: "POLICY", label: "策略草稿" },
  { value: "RESEARCH", label: "研究综合" },
  { value: "PROPOSAL", label: "操作提案" },
];

function AnswerContent({ content }: { content: string }) {
  return <div className="agent-answer">{content.split("\n").map((line, index) => {
    const key = `${index}-${line}`;
    if (line.startsWith("### ")) return <h4 key={key}>{line.slice(4)}</h4>;
    if (line.startsWith("## ")) return <h3 key={key}>{line.slice(3)}</h3>;
    if (line.startsWith("# ")) return <h2 key={key}>{line.slice(2)}</h2>;
    if (line.startsWith("- ")) return <p className="agent-bullet" key={key}>{line.slice(2)}</p>;
    if (!line) return <span className="agent-space" key={key} aria-hidden />;
    return <p key={key}>{line}</p>;
  })}</div>;
}

function Message({
  value,
  onOpenDraft,
  onOpenProposal,
  onOpenReport,
  onOpenRun,
}: {
  value: AgentMessage;
  onOpenDraft: (draftId: string) => void;
  onOpenProposal: (proposalId: string) => void;
  onOpenReport: (reportId: string) => void;
  onOpenRun: (runId: string) => void;
}) {
  return <article className={`agent-message ${value.role.toLowerCase()}`}>
    <header><strong>{value.role === "USER" ? "你" : "治理 Agent"}</strong><time>{formatTime(value.created_at)}</time></header>
    {value.role === "ASSISTANT" && (value.sections?.length ?? 0) > 0
      ? <div className="agent-sections">{(value.sections ?? []).map((section, index) => <section className={`agent-section ${section.evidence_kind.toLowerCase()}`} key={`${section.evidence_kind}-${index}`}>
        <header><Badge value={section.evidence_kind} /><strong>{section.title}</strong></header>
        <AnswerContent content={section.content} />
        {section.citation_ids.length > 0 && <small>引用：{section.citation_ids.join("、")}</small>}
      </section>)}</div>
      : value.role === "ASSISTANT" ? <AnswerContent content={value.content} /> : <p>{value.content}</p>}
    {value.citations.length > 0 && <details className="agent-citations"><summary>查看 {value.citations.length} 条证据引用</summary>
      <div>{value.citations.map((citation) => {
        const content = <><span><Badge value={citation.evidence_kind} /> <code>{citation.evidence_id}</code></span>
          <strong>{citation.label}</strong><p>{citation.excerpt}</p>
          <small>{citation.entity_type}{citation.entity_version ? ` · ${citation.entity_version}` : ""}</small></>;
        return citation.entity_type === "POLICY_DRAFT" && citation.entity_id
          ? <button className="agent-citation-link" key={citation.evidence_id} onClick={() => onOpenDraft(citation.entity_id!)}>{content}</button>
          : citation.entity_type === "ACTION_PROPOSAL" && citation.entity_id
            ? <button className="agent-citation-link" key={citation.evidence_id} onClick={() => onOpenProposal(citation.entity_id!)}>{content}</button>
          : <section key={citation.evidence_id}>{content}</section>;
      })}</div>
    </details>}
    {value.role === "USER" && value.run_id && <button className="button agent-run-link" onClick={() => onOpenRun(value.run_id!)}><Activity />运行详情</button>}
    {value.research_report_id && <button className="button agent-report-link" onClick={() => onOpenReport(value.research_report_id!)}><BookOpenText />查看研究报告</button>}
  </article>;
}

export function AgentPage() {
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const [showArchived, setShowArchived] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState("");
  const [activeRun, setActiveRun] = useState<AgentRunReceipt | null>(null);
  const [runStatus, setRunStatus] = useState("");
  const [activity, setActivity] = useState("");
  const [streamedAnswer, setStreamedAnswer] = useState("");
  const [streamError, setStreamError] = useState<unknown>();
  const [draftWorkspace, setDraftWorkspace] = useState<{ open: boolean; draftId?: string }>({ open: false });
  const [proposalWorkspace, setProposalWorkspace] = useState<{ open: boolean; proposalId?: string }>({ open: false });
  const [reportWorkspace, setReportWorkspace] = useState<{ open: boolean; reportId?: string }>({ open: false });
  const [planWorkspace, setPlanWorkspace] = useState<{ open: boolean; planId?: string }>({ open: false });
  const [observabilityOpen, setObservabilityOpen] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [newThreadCapability, setNewThreadCapability] = useState<AgentCapabilityDomain>("ANALYSIS");
  const lastEventId = useRef(0);

  const threads = useQuery({
    queryKey: ["agent-threads", projectId, showArchived],
    queryFn: () => api<Page<AgentThread>>(`/projects/${projectId}/agent/threads?archived=${showArchived}`),
  });
  const session = useQuery({ queryKey: ["session"], queryFn: () => api<Session>("/auth/me") });
  const thread = useQuery({
    queryKey: ["agent-thread", projectId, selectedId],
    queryFn: () => api<AgentThreadView>(`/projects/${projectId}/agent/threads/${selectedId}`),
    enabled: Boolean(selectedId),
  });

  useEffect(() => {
    setSelectedId("");
    setActiveRun(null);
    setStreamedAnswer("");
  }, [projectId, showArchived]);
  useEffect(() => {
    if (!selectedId && threads.data?.items[0]) setSelectedId(threads.data.items[0].thread_id);
  }, [threads.data, selectedId]);

  const createThread = useMutation({
    mutationFn: () => api<AgentThread>(`/projects/${projectId}/agent/threads`, {
      method: "POST", body: JSON.stringify({ capability_domain: newThreadCapability }),
    }),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ["agent-threads", projectId] });
      setShowArchived(false);
      setSelectedId(created.thread_id);
    },
  });
  const updateThread = useMutation({
    mutationFn: ({ id, archived }: { id: string; archived: boolean }) => api<AgentThread>(
      `/projects/${projectId}/agent/threads/${id}`,
      { method: "PATCH", body: JSON.stringify({ archived }) },
    ),
    onSuccess: async () => {
      setSelectedId("");
      await queryClient.invalidateQueries({ queryKey: ["agent-threads", projectId] });
    },
  });
  const send = useMutation({
    mutationFn: (content: string) => api<AgentRunReceipt>(
      `/projects/${projectId}/agent/threads/${selectedId}/messages`,
      { method: "POST", headers: { "Idempotency-Key": idempotencyKey() }, body: JSON.stringify({ content }) },
    ),
    onSuccess: (run) => {
      setDraft("");
      setStreamedAnswer("");
      setStreamError(undefined);
      setRunStatus("PENDING");
      setActivity("等待 Worker");
      lastEventId.current = 0;
      setActiveRun(run);
      queryClient.invalidateQueries({ queryKey: ["agent-thread", projectId, selectedId] });
    },
  });
  const retry = useMutation({
    mutationFn: (runId: string) => api<AgentRunReceipt>(
      `/projects/${projectId}/agent/runs/${runId}/retry`,
      { method: "POST", headers: { "Idempotency-Key": idempotencyKey() } },
    ),
    onSuccess: (run) => {
      setStreamedAnswer("");
      setStreamError(undefined);
      setRunStatus("PENDING");
      setActivity("等待 Worker");
      lastEventId.current = 0;
      setActiveRun(run);
    },
  });

  useEffect(() => {
    if (activeRun || !thread.data?.messages.length) return;
    const last = thread.data.messages.at(-1);
    if (last?.role !== "USER" || !last.run_id) return;
    api<AgentRun>(`/projects/${projectId}/agent/runs/${last.run_id}`).then((run) => {
      if (ACTIVE_RUNS.has(run.status)) {
        setActiveRun(run);
        setRunStatus(run.status);
      } else if (run.status === "FAILED" || run.status === "DEAD_LETTER") {
        setRunStatus(run.status);
        setActiveRun(run);
      }
    }).catch(setStreamError);
  }, [activeRun, projectId, thread.data]);

  const activeRunId = activeRun?.run_id;
  const activeThreadId = activeRun?.thread_id;
  const activeEventsUrl = activeRun?.events_url;
  const shouldFollow = Boolean(activeRun && (ACTIVE_RUNS.has(activeRun.status) || ACTIVE_RUNS.has(runStatus)));
  useEffect(() => {
    if (!activeRunId || !activeThreadId || !activeEventsUrl || !shouldFollow) return;
    const runId = activeRunId;
    const threadId = activeThreadId;
    const eventsUrl = activeEventsUrl;
    const controller = new AbortController();
    let disposed = false;
    async function follow() {
      try {
        while (!disposed) {
          lastEventId.current = await streamServerEvents(
            eventsUrl,
            lastEventId.current,
            (event) => {
              if (event.event === "answer.delta" && typeof event.data.text === "string") {
                setStreamedAnswer((current) => current + event.data.text);
              }
              if (event.event === "run.started") setActivity("正在分析问题");
              if (event.event === "summary.started") setActivity("正在压缩较早对话");
              if (event.event === "summary.completed") setActivity("对话摘要已更新");
              if (event.event === "summary.failed") setActivity("摘要更新失败，使用安全降级上下文");
              if (event.event === "tool.started" && typeof event.data.tool === "string") {
                setActivity(`正在读取 ${event.data.tool}`);
              }
              if (event.event === "tool.completed") setActivity("正在整理正式记录");
              if (event.event === "run.completed") setActivity("");
              if (typeof event.data.status === "string") setRunStatus(event.data.status);
            },
            controller.signal,
          );
          const run = await api<AgentRun>(`/projects/${projectId}/agent/runs/${runId}`);
          setRunStatus(run.status);
          if (!ACTIVE_RUNS.has(run.status)) {
            await queryClient.invalidateQueries({ queryKey: ["agent-thread", projectId, threadId] });
            await queryClient.invalidateQueries({ queryKey: ["agent-threads", projectId] });
            if (!disposed) setActiveRun(run);
            return;
          }
          await new Promise((resolve) => setTimeout(resolve, 600));
        }
      } catch (error) {
        if (!controller.signal.aborted) setStreamError(error);
      }
    }
    void follow();
    return () => { disposed = true; controller.abort(); };
  }, [activeEventsUrl, activeRunId, activeThreadId, projectId, queryClient, shouldFollow]);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (draft.trim() && selectedId) send.mutate(draft.trim());
  }

  if (threads.error) return <ErrorNotice error={threads.error} />;
  if (!threads.data) return <div className="page-loading">正在加载 Agent 会话</div>;
  const terminalFailure = activeRun && (runStatus === "FAILED" || runStatus === "DEAD_LETTER");
  const externalContext = thread.data?.external_task_context;
  const externalPolicy = externalContext?.policy as {
    context?: { version?: number };
    active_intent?: { version?: number };
  } | undefined;
  return <main className="page agent-page">
    <header className="page-header"><div><span className="eyebrow">治理 Agent</span><h1>项目实验助手</h1><p>回答仅基于当前身份可见的正式记录</p></div>
      <div className="agent-header-actions">
        {session.data?.role === "OWNER" && <button className="button" onClick={() => setObservabilityOpen(true)}><Gauge />模型观测</button>}
        <button className="button" onClick={() => setDraftWorkspace({ open: true })}><FilePenLine />治理草稿</button>
        <button className="button" onClick={() => setProposalWorkspace({ open: true })}><ShieldCheck />发布提案</button>
        <button className="button" onClick={() => setReportWorkspace({ open: true })}><BookOpenText />研究报告</button>
        <button className="button" onClick={() => setPlanWorkspace({ open: true })}><ClipboardCheck />实验计划</button>
        <button className="button" onClick={() => setShowArchived((value) => !value)}>{showArchived ? <ArchiveRestore /> : <Archive />}{showArchived ? "当前会话" : "已归档"}</button>
        <div className="segmented agent-capability-selector" aria-label="新对话能力域">{CAPABILITY_OPTIONS.map((option) => <button type="button" title={option.label} className={newThreadCapability === option.value ? "active" : ""} key={option.value} onClick={() => setNewThreadCapability(option.value)}>{option.label}</button>)}</div>
        <button className="button primary" onClick={() => createThread.mutate()} disabled={createThread.isPending}><MessageSquarePlus />新对话</button>
      </div>
    </header>
    {createThread.error ? <ErrorNotice error={createThread.error} /> : null}
    {!threads.data.items.length ? <Empty>{showArchived ? "没有已归档会话" : "创建一个会话后开始查询项目正式记录"}</Empty> :
      <div className="agent-workspace">
        <aside className="agent-thread-list">{threads.data.items.map((item) => <button className={selectedId === item.thread_id ? "active" : ""} key={item.thread_id} onClick={() => setSelectedId(item.thread_id)}>
          <Bot /><span><strong>{item.title}</strong><small>{item.origin === "EXTERNAL_MCP" ? "MCP 任务 · " : `${item.capability_domain ?? "GENERAL"} · `}{formatTime(item.updated_at)}</small></span>
        </button>)}</aside>
        <section className="agent-conversation">
          <div className="agent-conversation-toolbar">
            <div><strong>{thread.data?.thread.title ?? "会话"}</strong>{thread.data?.thread.origin === "EXTERNAL_MCP" ? <Badge value="MCP 任务" /> : thread.data && <Badge value={thread.data.thread.capability_domain ?? "GENERAL"} />}{runStatus && <Badge value={runStatus} />}</div>
            {thread.data && <button className="icon-button" title={showArchived ? "恢复会话" : "归档会话"} aria-label={showArchived ? "恢复会话" : "归档会话"} disabled={Boolean(activeRun && ACTIVE_RUNS.has(runStatus))} onClick={() => updateThread.mutate({ id: thread.data!.thread.thread_id, archived: !showArchived })}>{showArchived ? <ArchiveRestore /> : <Archive />}</button>}
          </div>
          <div className="agent-message-list">
            {externalContext && <div className={externalContext.context_freshness === "STALE" ? "agent-summary-warning" : "agent-task-context"}>{externalContext.context_freshness === "STALE" ? <TriangleAlert /> : <Info />}<span>任务启动于 Context v{externalPolicy?.context?.version ?? "?"} / Intent v{externalPolicy?.active_intent?.version ?? "无"}。{externalContext.warning ?? "当前正式版本未变化。"}</span></div>}
            {thread.data?.context_summary?.degraded && <div className="agent-summary-warning"><TriangleAlert /> <span>{thread.data.context_summary.warning}</span></div>}
            {thread.isPending ? <div className="page-loading">正在加载消息</div> : thread.data?.messages.map((message) => <Message key={message.message_id} value={message} onOpenDraft={(draftId) => setDraftWorkspace({ open: true, draftId })} onOpenProposal={(proposalId) => setProposalWorkspace({ open: true, proposalId })} onOpenReport={(reportId) => setReportWorkspace({ open: true, reportId })} onOpenRun={setSelectedRunId} />)}
            {(streamedAnswer || activity) && ACTIVE_RUNS.has(runStatus) && <article className="agent-message assistant streaming"><header><strong>治理 Agent</strong><Badge value={runStatus || "RUNNING"} /></header>{streamedAnswer ? <AnswerContent content={streamedAnswer} /> : <p>{activity}</p>}</article>}
          </div>
          {streamError ? <ErrorNotice error={streamError} /> : null}
          {terminalFailure && activeRun ? <div className="agent-retry"><span>本次运行失败，审计记录已保留。</span><button className="button" onClick={() => retry.mutate(activeRun.run_id)}><RotateCcw />重试</button></div> : null}
          {!showArchived && <form className="agent-composer" onSubmit={submit}><textarea aria-label="发送给治理 Agent" rows={3} maxLength={8192} value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="询问项目目标、当前约束、最近实验或待处理事项" /><button className="icon-button" aria-label="发送" title="发送" disabled={!draft.trim() || send.isPending || Boolean(activeRun && ACTIVE_RUNS.has(runStatus))}><Send /></button></form>}
          {send.error ? <ErrorNotice error={send.error} /> : null}
        </section>
      </div>}
    {draftWorkspace.open && <PolicyDraftWorkspace projectId={projectId} initialDraftId={draftWorkspace.draftId} onClose={() => setDraftWorkspace({ open: false })} />}
    {proposalWorkspace.open && <ActionProposalWorkspace projectId={projectId} initialProposalId={proposalWorkspace.proposalId} onClose={() => setProposalWorkspace({ open: false })} />}
    {reportWorkspace.open && <ResearchReportWorkspace projectId={projectId} initialReportId={reportWorkspace.reportId} onClose={() => setReportWorkspace({ open: false })} />}
    {planWorkspace.open && <ExperimentPlanWorkspace projectId={projectId} initialPlanId={planWorkspace.planId} onClose={() => setPlanWorkspace({ open: false })} />}
    {observabilityOpen && <AgentObservabilityWorkspace projectId={projectId} onClose={() => setObservabilityOpen(false)} />}
    {selectedRunId && <AgentRunDetailsDialog projectId={projectId} runId={selectedRunId} onClose={() => setSelectedRunId("")} />}
  </main>;
}
