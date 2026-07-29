import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, FileText, X } from "lucide-react";
import { useParams } from "react-router-dom";
import { api, formatTime, idempotencyKey } from "../api";
import { Badge, Empty, ErrorNotice, JsonBlock, StateIcon } from "../components";
import type { Page, Submission } from "../types";

export function SubmissionPage() {
  const { projectId = "" } = useParams(); const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["submissions", projectId], queryFn: () => api<Page<Submission>>(`/projects/${projectId}/submissions`) });
  const [selectedId, setSelectedId] = useState(""); const [reason, setReason] = useState("");
  useEffect(() => { if (!selectedId && query.data?.items[0]) setSelectedId(query.data.items[0].submission_id); }, [query.data, selectedId]);
  const decide = useMutation({ mutationFn: ({ id, decision }: { id: string; decision: string }) => api(`/projects/${projectId}/submissions/${id}/decision`, { method: "POST", headers: { "Idempotency-Key": idempotencyKey() }, body: JSON.stringify({ decision, decision_reason: reason || null }) }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["submissions", projectId] }) });
  async function download(artifactId: string) { const result = await api<{ download_url: string }>(`/projects/${projectId}/artifacts/${artifactId}/download-url`, { method: "POST" }); location.assign(result.download_url); }
  if (query.error) return <ErrorNotice error={query.error} />; if (!query.data) return <div className="page-loading">正在加载实验草稿</div>;
  const selected = query.data.items.find((item) => item.submission_id === selectedId);
  return <main className="page"><header className="page-header"><div><span className="eyebrow">实验审核</span><h1>Submission 回执</h1><p>云端分析、风险和原始文件</p></div></header>
    {!query.data.items.length ? <Empty>暂无 Submission</Empty> : <div className="master-detail"><section className="record-list">{query.data.items.map((item) => <button className={item.submission_id === selectedId ? "record-row active" : "record-row"} key={item.submission_id} onClick={() => setSelectedId(item.submission_id)}><StateIcon severity={item.status} /><span><strong>{item.status}</strong><small>{formatTime(item.created_at)}</small></span><Badge value={item.processing_step ?? item.workflow_status} /></button>)}</section>
      {selected && <section className="detail-panel"><div className="detail-title"><div><Badge value={selected.status} /> <Badge value={selected.workflow_status} /></div><code>{selected.submission_id}</code></div>{selected.invariant_check && <><h2>最终关键不变量核对</h2><Badge value={selected.invariant_check.overall_status ?? "UNKNOWN"} /><JsonBlock value={selected.invariant_check} /></>}<h2>审核回执</h2>{selected.review_receipt ? <JsonBlock value={selected.review_receipt} /> : <Empty>审核回执尚未生成</Empty>}<h2>风险</h2>{selected.risks.length ? <div className="risk-list">{selected.risks.map((risk, index) => <details key={String(risk.risk_id ?? index)} open={["HIGH", "CRITICAL"].includes(risk.severity)}><summary><Badge value={risk.severity} /><strong>{risk.message}</strong></summary><JsonBlock value={risk} /></details>)}</div> : <Empty>没有已记录风险</Empty>}<h2>原始文件</h2><div className="artifact-list">{selected.artifacts.map((item) => <div key={item.artifact_id}><FileText /><span><strong>{item.filename}</strong><small>{item.artifact_type} · {Math.ceil(item.size_bytes / 1024)} KiB</small></span><button className="icon-button" disabled={!item.cloud_hash_verified} onClick={() => download(item.artifact_id)} aria-label={`下载 ${item.filename}`} title="下载已验证版本"><Download /></button></div>)}</div>{selected.allowed_actions.length > 0 && <div className="decision-bar"><label>决定说明<input value={reason} maxLength={2000} onChange={(event) => setReason(event.target.value)} /></label><button className="button danger" onClick={() => decide.mutate({ id: selected.submission_id, decision: "REJECTED" })}><X />拒绝</button>{selected.allowed_actions.includes("APPROVE") && <button className="button primary" onClick={() => decide.mutate({ id: selected.submission_id, decision: "APPROVED" })}><Check />确认入库</button>}</div>}{decide.error && <ErrorNotice error={decide.error} />}</section>}</div>}
  </main>;
}
