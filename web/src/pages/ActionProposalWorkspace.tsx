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
import type { ActionProposal, Page } from "../types";

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
        <div><ShieldCheck /><span><strong>正式操作提案</strong><small>Agent 只能准备，Owner 明确确认后才会执行</small></span></div>
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
            <span><strong>发布 Policy Bundle</strong><small>Context v{item.base_context_version} → v{item.base_context_version + 1}</small></span>
            <span><Badge value={item.status} /><Badge value={item.confirmability} /></span>
          </button>)}
        </aside>
        <main className="draft-detail proposal-detail">
          {detail.error && <ErrorNotice error={detail.error} />}
          {!selectedId && <Empty>选择一份提案查看冻结内容</Empty>}
          {selectedId && detail.isPending && <div className="page-loading">正在加载提案</div>}
          {value && <>
            <header className="draft-detail-header">
              <div><strong>Policy Bundle 正式发布</strong><span>草稿 revision {value.source_draft_revision} · 创建于 {formatTime(value.created_at)}</span></div>
              <div><Badge value={value.status} /><Badge value={value.confirmability} /></div>
            </header>

            <div className="proposal-facts">
              <div><Clock3 /><span>有效期</span><strong>{formatTime(value.expires_at)}</strong></div>
              <div><FileDiff /><span>结构化差异</span><strong>{value.diff_snapshot.length} 项</strong></div>
              <div><AlertTriangle /><span>最高关注</span><strong>{value.impact_snapshot.attention_level}</strong></div>
            </div>

            {value.confirmability_reasons.length > 0 && <div className="draft-warning">
              <AlertTriangle /><span>{value.confirmability_reasons.join("；")}</span>
            </div>}
            {value.status === "EXECUTED" && <div className="proposal-success">
              <CheckCircle2 /><span>已发布为正式 Context v{value.executed_context_version}</span>
            </div>}

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

            <details className="proposal-raw">
              <summary><Braces />查看冻结结构化发布请求</summary>
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
                  <span>我已核对冻结差异、影响和结构化发布请求</span>
                </label> : <span className="proposal-owner-note">等待 Owner 审阅并确认</span>}
                {value.allowed_actions.includes("CONFIRM") && <button
                  className="button primary"
                  disabled={!confirmedReview || confirm.isPending}
                  onClick={() => confirm.mutate(value)}
                ><ShieldCheck />确认并发布正式版本</button>}
              </>}
            </footer>}
            {(confirm.error || cancel.error) && <ErrorNotice error={confirm.error || cancel.error} />}
          </>}
        </main>
      </div>
    </section>
  </div>;
}
