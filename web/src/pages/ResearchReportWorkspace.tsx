import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Braces, FileText, RotateCcw, Search, X } from "lucide-react";
import { api, formatTime, idempotencyKey } from "../api";
import { Badge, Empty, ErrorNotice, JsonBlock } from "../components";
import type {
  Page,
  ResearchMemorySearchResponse,
  ResearchReportSummary,
  ResearchReportView,
  Session,
} from "../types";

export function ResearchReportWorkspace({
  projectId,
  initialReportId,
  onClose,
}: {
  projectId: string;
  initialReportId?: string;
  onClose: () => void;
}) {
  const [selectedId, setSelectedId] = useState(initialReportId ?? "");
  const [raw, setRaw] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [includeStale, setIncludeStale] = useState(false);
  const queryClient = useQueryClient();
  const session = useQuery({ queryKey: ["session"], queryFn: () => api<Session>("/auth/me") });
  const reports = useQuery({
    queryKey: ["research-reports", projectId],
    queryFn: () => api<Page<ResearchReportSummary>>(
      `/projects/${projectId}/agent/research-reports?limit=100`,
    ),
  });
  const detail = useQuery({
    queryKey: ["research-report", projectId, selectedId],
    queryFn: () => api<ResearchReportView>(
      `/projects/${projectId}/agent/research-reports/${selectedId}`,
    ),
    enabled: Boolean(selectedId),
  });
  const search = useMutation({
    mutationFn: () => api<ResearchMemorySearchResponse>(
      `/projects/${projectId}/agent/research-memories/search`,
      { method: "POST", body: JSON.stringify({ query: searchText, include_stale: includeStale, top_k: 10 }) },
    ),
  });
  const retry = useMutation({
    mutationFn: (memoryId: string) => api(
      `/projects/${projectId}/agent/research-memories/${memoryId}/embedding/retry`,
      { method: "POST", headers: { "Idempotency-Key": idempotencyKey() } },
    ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["research-report", projectId, selectedId] });
    },
  });

  useEffect(() => {
    if (!selectedId && reports.data?.items[0]) setSelectedId(reports.data.items[0].report_id);
  }, [reports.data, selectedId]);

  const evidence = useMemo(() => new Map(
    (detail.data?.source_snapshot.evidence ?? []).map((item) => [item.evidence_id, item]),
  ), [detail.data]);
  const memories = useMemo(() => new Map(
    (detail.data?.research_memories ?? []).map((item) => [item.finding_id, item]),
  ), [detail.data]);

  return <div className="research-workspace-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="research-workspace" role="dialog" aria-modal="true" aria-label="候选研究报告" onMouseDown={(event) => event.stopPropagation()}>
      <header className="research-workspace-header">
        <div><strong>候选研究报告</strong><span>项目成员共享 · 不属于正式事实</span></div>
        <button className="icon-button" aria-label="关闭研究报告" title="关闭" onClick={onClose}><X /></button>
      </header>
      <div className="research-workspace-body">
        <aside className="research-list">
          {reports.error && <ErrorNotice error={reports.error} />}
          {!reports.isPending && !reports.data?.items.length && <Empty>通过治理 Agent 显式选择实验并生成第一份报告</Empty>}
          {reports.data?.items.map((item) => <button className={selectedId === item.report_id ? "active" : ""} key={item.report_id} onClick={() => { setSelectedId(item.report_id); setRaw(false); }}>
            <FileText /><span><strong>{item.title}</strong><small>{item.experiment_ids.length} 个实验 · {formatTime(item.created_at)}</small></span>
          </button>)}
        </aside>
        <main className="research-detail">
          {detail.error && <ErrorNotice error={detail.error} />}
          {detail.isPending && selectedId && <div className="page-loading">正在加载研究报告</div>}
          {detail.data && <>
            <header className="research-detail-header">
              <div><span className="eyebrow">ANALYSIS · 非正式事实</span><h2>{detail.data.title}</h2><p>{detail.data.objective}</p></div>
              <div className="segmented"><button className={!raw ? "active" : ""} onClick={() => setRaw(false)}><FileText />报告</button><button className={raw ? "active" : ""} onClick={() => setRaw(true)}><Braces />原始数据</button></div>
            </header>
            {detail.data.source_warnings.map((warning) => <div className="research-warning" key={`${warning.code}-${warning.experiment_id}`}><AlertTriangle /><span>{warning.message}</span></div>)}
            <form className="research-memory-search" onSubmit={(event) => { event.preventDefault(); search.mutate(); }}>
              <label><span>候选研究记忆</span><input required maxLength={1000} value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="检索阶段结论、冲突或开放问题" /></label>
              <label className="research-stale-toggle"><input type="checkbox" checked={includeStale} onChange={(event) => setIncludeStale(event.target.checked)} />包含来源已变化记录</label>
              <button className="button" disabled={search.isPending}><Search />检索</button>
            </form>
            {search.error && <ErrorNotice error={search.error} />}
            {search.data && <section className="research-memory-results">
              <header><strong>候选证据</strong><span>{search.data.items.length} / {search.data.candidate_count}</span></header>
              {search.data.candidate_truncated && <small>候选集合已截断为最近 200 条。</small>}
              {!search.data.items.length ? <Empty>没有匹配的当前候选记忆</Empty> : search.data.items.map((item) => <article key={item.memory_id}>
                <header><Badge value={item.memory_type} /><Badge value={item.source_freshness} /><strong>{Math.round(item.similarity * 100)}%</strong></header>
                <p>{item.statement}</p><small>CANDIDATE_EVIDENCE · {item.experiment_ids.length} 个来源实验</small>
                {item.source_warnings.map((warning) => <span className="research-memory-warning" key={warning}>{warning}</span>)}
              </article>)}
            </section>}
            {raw ? <><p className="research-source-note">结构化实验记录仍是唯一正式事实源；以下内容是生成时的不可变分析快照。</p><JsonBlock value={detail.data} /></> : <div className="research-report-body">
              <section className="research-summary"><h3>阶段摘要</h3><p>{detail.data.report.executive_summary}</p><small>引用：{detail.data.report.executive_summary_citation_ids.join("、")}</small></section>
              {detail.data.memory_materialization_pending && <div className="research-warning"><AlertTriangle /><span>旧报告的候选记忆正在补建，报告正文不受影响。</span></div>}
              <div className="research-findings">{detail.data.report.findings.map((finding) => {
                const memory = memories.get(finding.finding_id);
                const retryable = memory && ["FAILED", "DEAD_LETTER", "RETRYABLE_FAILURE"].includes(memory.embedding_status);
                return <article key={finding.finding_id}>
                <header><Badge value={finding.kind} /><code>{finding.finding_id}</code>{memory && <><Badge value={memory.embedding_status} /><Badge value={memory.source_freshness} /></>}{retryable && session.data?.role === "OWNER" && <button className="icon-button compact" aria-label={`重试 ${finding.finding_id} 索引`} title="重试索引" onClick={() => retry.mutate(memory.memory_id)}><RotateCcw /></button>}</header>
                <h3>{finding.statement}</h3><p>{finding.rationale}</p>
                {memory?.last_error?.message && <p className="research-memory-warning">{memory.last_error.message}</p>}
                {finding.limitations.length > 0 && <ul>{finding.limitations.map((item) => <li key={item}>{item}</li>)}</ul>}
                <div className="research-evidence">{finding.citation_ids.map((citationId) => {
                  const citation = evidence.get(citationId);
                  return <div key={citationId}><code>{citationId}</code><strong>{citation?.label ?? "来源"}</strong><span>{citation?.excerpt ?? "来源快照中未找到展示摘要"}</span></div>;
                })}</div>
              </article>;})}</div>
              {detail.data.report.limitations.length > 0 && <section className="research-limitations"><h3>限制</h3>{detail.data.report.limitations.map((item, index) => <p key={`${index}-${item.statement}`}>{item.statement} <small>{item.citation_ids.join("、")}</small></p>)}</section>}
              <footer className="research-provenance"><span>{detail.data.provider} · {detail.data.model_id}</span><code>{detail.data.source_hash}</code></footer>
            </div>}
          </>}
        </main>
      </div>
    </section>
  </div>;
}
