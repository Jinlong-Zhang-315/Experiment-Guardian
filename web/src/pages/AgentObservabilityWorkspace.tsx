import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, CircleDollarSign, Clock3, Gauge, X } from "lucide-react";
import { api, formatTime } from "../api";
import { Badge, Empty, ErrorNotice } from "../components";
import type { AgentModelObservability, AgentRun } from "../types";

function integer(value: number | undefined) {
  return value === undefined ? "-" : new Intl.NumberFormat("zh-CN").format(value);
}

function latency(value: number | undefined) {
  return value === undefined ? "-" : `${integer(value)} ms`;
}

function cost(value: string | undefined, currency: string | undefined) {
  return value === undefined ? "-" : `${currency ?? ""} ${value}`.trim();
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

export function AgentObservabilityWorkspace({
  projectId,
  onClose,
}: {
  projectId: string;
  onClose: () => void;
}) {
  const [windowDays, setWindowDays] = useState(7);
  const query = useQuery({
    queryKey: ["agent-model-observability", projectId, windowDays],
    queryFn: () => api<AgentModelObservability>(
      `/projects/${projectId}/agent/model-observability?window_days=${windowDays}`,
    ),
  });
  const totals = query.data?.totals;

  return <div className="observability-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="observability-workspace" role="dialog" aria-modal="true" aria-label="模型运行观测" onMouseDown={(event) => event.stopPropagation()}>
      <header className="observability-header">
        <div><Gauge /><span><strong>模型运行观测</strong><small>Owner 可见</small></span></div>
        <div className="observability-header-actions">
          <div className="segmented" aria-label="观测时间范围">{[7, 30, 90].map((days) => <button className={windowDays === days ? "active" : ""} key={days} onClick={() => setWindowDays(days)}>{days} 天</button>)}</div>
          <button className="icon-button" aria-label="关闭模型运行观测" title="关闭" onClick={onClose}><X /></button>
        </div>
      </header>
      <main className="observability-body">
        {query.error && <ErrorNotice error={query.error} />}
        {query.isPending && <div className="page-loading">正在加载模型运行数据</div>}
        {query.data && <>
          <div className="observability-current"><span>当前装配</span><strong>{query.data.current_provider}</strong><code>{query.data.current_model_id || "未启用"}</code></div>
          <section className="observability-metrics" aria-label="运行汇总">
            <Metric label="Runs" value={integer(totals?.run_count)} />
            <Metric label="模型调用" value={integer(totals?.model_call_count)} />
            <Metric label="成功 / 失败" value={`${integer(totals?.succeeded_call_count)} / ${integer(totals?.failed_call_count)}`} />
            <Metric label="输入 Token" value={integer(totals?.input_tokens)} />
            <Metric label="输出 Token" value={integer(totals?.output_tokens)} />
            <Metric label="平均 / 最大延迟" value={`${latency(totals?.average_latency_ms)} / ${latency(totals?.maximum_latency_ms)}`} />
            <Metric label="重试" value={integer(totals?.retry_count)} />
            <Metric label="缺失用量 / 未计价" value={`${integer(totals?.missing_usage_call_count)} / ${integer(totals?.unpriced_call_count)}`} />
          </section>
          <section className="observability-costs">
            <header><CircleDollarSign /><div><strong>估算费用</strong><small>基于调用时冻结的配置费率，不是云平台账单</small></div></header>
            {!query.data.costs.length ? <span>当前窗口没有可计价调用</span> : query.data.costs.map((item) => <strong key={item.currency}>{item.currency} {item.estimated_cost}</strong>)}
          </section>
          <section className="table-section observability-table">
            <div className="section-heading"><h2>提供商与调用用途</h2><span>{formatTime(query.data.window_from)} 至 {formatTime(query.data.window_to)}</span></div>
            {!query.data.groups.length ? <Empty>当前窗口没有模型调用</Empty> : <div className="table-wrap"><table><thead><tr><th>Provider / Model</th><th>用途</th><th>调用</th><th>成功 / 失败</th><th>Token In / Out</th><th>平均 / 最大延迟</th><th>重试</th></tr></thead><tbody>
              {query.data.groups.map((group) => <tr key={`${group.provider}-${group.model_id}-${group.purpose}`}>
                <td><strong>{group.provider}</strong><br /><code>{group.model_id}</code></td><td><Badge value={group.purpose} /></td><td>{integer(group.model_call_count)}</td><td>{integer(group.succeeded_call_count)} / {integer(group.failed_call_count)}</td><td>{integer(group.input_tokens)} / {integer(group.output_tokens)}</td><td>{latency(group.average_latency_ms)} / {latency(group.maximum_latency_ms)}</td><td>{integer(group.retry_count)}</td>
              </tr>)}</tbody></table></div>}
          </section>
          {Object.keys(query.data.failure_categories).length > 0 && <section className="observability-failures"><h2>失败分类</h2>{Object.entries(query.data.failure_categories).map(([name, count]) => <div key={name}><code>{name}</code><strong>{count}</strong></div>)}</section>}
        </>}
      </main>
    </section>
  </div>;
}

export function AgentRunDetailsDialog({
  projectId,
  runId,
  onClose,
}: {
  projectId: string;
  runId: string;
  onClose: () => void;
}) {
  const query = useQuery({
    queryKey: ["agent-run", projectId, runId],
    queryFn: () => api<AgentRun>(`/projects/${projectId}/agent/runs/${runId}`),
  });
  const totalCost = new Map<string, number>();
  for (const call of query.data?.model_calls ?? []) {
    if (call.estimated_cost && call.cost_currency) {
      totalCost.set(call.cost_currency, (totalCost.get(call.cost_currency) ?? 0) + Number(call.estimated_cost));
    }
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="modal agent-run-dialog" role="dialog" aria-modal="true" aria-label="Agent Run 详情" onMouseDown={(event) => event.stopPropagation()}>
      <header className="modal-header"><div><h2>Agent Run 详情</h2><code>{runId}</code></div><button className="icon-button" onClick={onClose} aria-label="关闭 Run 详情" title="关闭"><X /></button></header>
      <main className="modal-body">
        {query.error && <ErrorNotice error={query.error} />}
        {query.isPending && <div className="page-loading">正在加载 Run 详情</div>}
        {query.data && <>
          <section className="run-observability-summary">
            <div><Activity /><span><small>状态 / 能力域</small><Badge value={query.data.status} /><strong>{query.data.capability_domain}</strong></span></div>
            <div><Gauge /><span><small>Provider / Model</small><strong>{query.data.provider} · {query.data.model_id}</strong></span></div>
            <div><Clock3 /><span><small>尝试次数</small><strong>{query.data.attempt_count} / {query.data.max_attempts}</strong></span></div>
            <div><CircleDollarSign /><span><small>配置费率估算</small><strong>{totalCost.size ? [...totalCost].map(([currency, value]) => `${currency} ${value.toFixed(10)}`).join(" · ") : "-"}</strong></span></div>
          </section>
          <section className="run-profile"><code>{query.data.prompt_version}</code><code>{query.data.tool_catalog_version}</code></section>
          {!query.data.model_calls.length ? <Empty>该 Run 尚无模型调用记录</Empty> : <div className="table-wrap"><table><thead><tr><th>次序</th><th>用途</th><th>状态</th><th>Provider / Model</th><th>Token In / Out</th><th>延迟</th><th>估算费用</th><th>结束原因</th></tr></thead><tbody>
            {query.data.model_calls.map((call) => <tr key={call.call_id}><td>{call.generation}.{call.ordinal}</td><td><Badge value={call.purpose} /></td><td><Badge value={call.status} /></td><td>{call.provider}<br /><code>{call.model_id}</code></td><td>{integer(call.input_tokens)} / {integer(call.output_tokens)}</td><td>{latency(call.latency_ms)}</td><td>{cost(call.estimated_cost, call.cost_currency)}</td><td>{call.error_code ?? call.finish_reason ?? "-"}</td></tr>)}
          </tbody></table></div>}
        </>}
      </main>
    </section>
  </div>;
}
