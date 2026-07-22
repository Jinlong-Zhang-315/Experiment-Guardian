import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, GitCommit, ShieldAlert, X } from "lucide-react";
import { useParams } from "react-router-dom";
import { api, formatTime, idempotencyKey } from "../api";
import { Badge, Empty, ErrorNotice, JsonBlock, StateIcon } from "../components";
import type { Page, PlanCheck } from "../types";

export function PlanPage() {
  const { projectId = "" } = useParams(); const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["plans", projectId], queryFn: () => api<Page<PlanCheck>>(`/projects/${projectId}/plan-checks`) });
  const [selectedId, setSelectedId] = useState(""); const [reason, setReason] = useState("");
  useEffect(() => { if (!selectedId && query.data?.items[0]) setSelectedId(query.data.items[0].plan_check_id); }, [query.data, selectedId]);
  const decide = useMutation({ mutationFn: ({ id, decision }: { id: string; decision: string }) => api(`/projects/${projectId}/plan-checks/${id}/decision`, { method: "POST", headers: { "Idempotency-Key": idempotencyKey() }, body: JSON.stringify({ decision, decision_reason: reason || null }) }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["plans", projectId] }) });
  if (query.error) return <ErrorNotice error={query.error} />; if (!query.data) return <div className="page-loading">正在加载检查记录</div>;
  const selected = query.data.items.find((item) => item.plan_check_id === selectedId);
  return <main className="page"><header className="page-header"><div><span className="eyebrow">计划审批</span><h1>训练前配置检查</h1><p>配置一致性结果与审批状态</p></div></header>
    {!query.data.items.length ? <Empty>暂无 Plan Check</Empty> : <div className="master-detail"><section className="record-list" aria-label="Plan Check 列表">{query.data.items.map((item) => <button className={item.plan_check_id === selectedId ? "record-row active" : "record-row"} key={item.plan_check_id} onClick={() => setSelectedId(item.plan_check_id)}><StateIcon severity={item.check_result} /><span><strong>{item.check_result}</strong><small>{formatTime(item.created_at)}</small></span><Badge value={item.approval_status} /></button>)}</section>
      {selected && <section className="detail-panel"><div className="detail-title"><div><Badge value={selected.check_result} /> <Badge value={selected.risk_level} /></div><code>{selected.plan_check_id}</code></div><div className="fact-strip compact"><div><GitCommit /><span>Git commit</span><strong className="mono">{selected.git_commit}</strong></div><div><ShieldAlert /><span>Context / Intent</span><strong>v{selected.context_version} / v{selected.intent_version}</strong></div></div><h2>参数变化</h2>{selected.planned_changes.length ? <JsonBlock value={selected.planned_changes} /> : <Empty>没有参数变化</Empty>}<h2>检查报告</h2><JsonBlock value={selected.report} /><h2>计划命令</h2><pre className="command-block">{selected.command}</pre>{selected.allowed_actions.length > 0 && <div className="decision-bar"><label>决定说明<input value={reason} maxLength={2000} onChange={(event) => setReason(event.target.value)} /></label><button className="button danger" onClick={() => decide.mutate({ id: selected.plan_check_id, decision: "REJECTED" })}><X />拒绝</button><button className="button primary" onClick={() => decide.mutate({ id: selected.plan_check_id, decision: "APPROVED" })}><Check />批准</button></div>}{decide.error && <ErrorNotice error={decide.error} />}</section>}</div>}
  </main>;
}
