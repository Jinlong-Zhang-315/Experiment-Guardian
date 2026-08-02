import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Database, Download, FileText, GitCommit, Search } from "lucide-react";
import { useParams } from "react-router-dom";
import { api, formatTime } from "../api";
import { Badge, Empty, ErrorNotice, JsonBlock } from "../components";
import type { Experiment, ExperimentDetail, Page } from "../types";

export function ExperimentPage() {
  const { projectId = "" } = useParams();
  const [selectedId, setSelectedId] = useState("");
  const [queryText, setQueryText] = useState("");
  const [protocol, setProtocol] = useState("");
  const [results, setResults] = useState<Array<Record<string, unknown>> | null>(null);
  const [searchError, setSearchError] = useState<unknown>();
  const query = useQuery({
    queryKey: ["experiments", projectId],
    queryFn: () => api<Page<Experiment>>(`/projects/${projectId}/experiments`),
  });
  const detail = useQuery({
    queryKey: ["experiment", projectId, selectedId],
    queryFn: () => api<ExperimentDetail>(`/projects/${projectId}/experiments/${selectedId}`),
    enabled: Boolean(selectedId),
  });

  useEffect(() => {
    if (!selectedId && query.data?.items[0]) setSelectedId(query.data.items[0].experiment_id);
  }, [query.data, selectedId]);

  async function search(event: React.FormEvent) {
    event.preventDefault();
    setSearchError(undefined);
    try {
      setResults(await api(`/projects/${projectId}/experiments/query`, {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, query: queryText, protocol, top_k: 10 }),
      }));
    } catch (error) {
      setSearchError(error);
    }
  }

  async function download(artifactId: string) {
    const result = await api<{ download_url: string }>(
      `/projects/${projectId}/artifacts/${artifactId}/download-url`,
      { method: "POST" },
    );
    location.assign(result.download_url);
  }

  if (query.error) return <ErrorNotice error={query.error} />;
  if (!query.data) return <div className="page-loading">正在加载正式实验</div>;

  const selected = detail.data;
  const provenance = selected?.material_provenance;
  return (
    <main className="page">
      <header className="page-header">
        <div>
          <span className="eyebrow">实验查询</span>
          <h1>正式实验记录</h1>
          <p>结构化条件先于向量候选</p>
        </div>
      </header>

      <form className="search-bar" onSubmit={search}>
        <label><span>语义查询</span><input required value={queryText} onChange={(event) => setQueryText(event.target.value)} placeholder="输入实验目标或结果" /></label>
        <label><span>协议</span><input required value={protocol} onChange={(event) => setProtocol(event.target.value)} placeholder="例如 40/20" /></label>
        <button className="button primary"><Search />查询</button>
      </form>
      {searchError ? <ErrorNotice error={searchError} /> : null}
      {results && (
        <section className="search-results">
          <div className="section-heading"><h2>候选证据</h2><span>{results.length} 条</span></div>
          <p className="query-scope-note">SUMMARY 候选不携带配置、命令或 Artifact。选择正式记录后加载 FULL 详情。</p>
          {results.length ? <JsonBlock value={results} /> : <Empty>没有匹配记录</Empty>}
        </section>
      )}

      {!query.data.items.length ? <Empty>暂无正式 Experiment</Empty> : (
        <div className="master-detail">
          <section className="record-list">
            {query.data.items.map((item) => (
              <button className={item.experiment_id === selectedId ? "record-row active" : "record-row"} key={item.experiment_id} onClick={() => setSelectedId(item.experiment_id)}>
                <Database />
                <span><strong>{item.name}</strong><small>{item.dataset} · {item.protocol} · seed {item.seed}</small></span>
                <Badge value={item.status} />
              </button>
            ))}
          </section>

          {detail.error ? <ErrorNotice error={detail.error} /> : null}
          {!selected && !detail.error ? <div className="page-loading">正在加载完整实验记录</div> : null}
          {selected && (
            <section className="detail-panel">
              <div className="detail-title">
                <div><Badge value={selected.status} /> <Badge value={selected.experiment_mode} /> <Badge value={selected.detail_level} /></div>
                <code>{selected.experiment_id}</code>
              </div>

              {provenance && (provenance.contains_non_current_material || provenance.contains_unspecified_material) && (
                <div className="submission-provenance-warning">
                  <AlertTriangle />
                  <strong>材料来源边界</strong>
                  <span>{provenance.disclaimer}</span>
                </div>
              )}

              <div className="fact-strip compact">
                <div><GitCommit /><span>Git commit</span><strong className="mono">{selected.git_commit}</strong></div>
                <div><Database /><span>Context / Intent</span><strong>v{selected.context_version} / v{selected.intent_version}</strong></div>
              </div>
              <dl className="facts">
                <dt>模型</dt><dd>{selected.model_name}</dd>
                <dt>数据集 / 协议</dt><dd>{selected.dataset} / {selected.protocol}</dd>
                <dt>Seed</dt><dd>{selected.seed}</dd>
                <dt>配置哈希</dt><dd className="mono">{selected.config_hash}</dd>
                <dt>确认时间</dt><dd>{formatTime(selected.confirmed_at)}</dd>
              </dl>

              <h2>指标</h2>
              <JsonBlock value={selected.metrics} />

              <h2>固定版本材料</h2>
              <div className="artifact-list">
                {selected.artifacts.map((item) => (
                  <div key={item.artifact_id}>
                    <FileText />
                    <span>
                      <strong>{item.filename}</strong>
                      <small>{item.artifact_type} · {item.material_origin} · {Math.ceil(item.size_bytes / 1024)} KiB</small>
                      <small>{item.provenance.note ?? "未提供来源说明"}</small>
                    </span>
                    <button className="icon-button" disabled={!item.cloud_hash_verified} onClick={() => download(item.artifact_id)} aria-label={`下载 ${item.filename}`} title="下载已验证固定版本">
                      <Download />
                    </button>
                  </div>
                ))}
              </div>

              <h2>摘要</h2>
              <JsonBlock value={selected.summary} />
              <h2>最终运行证据</h2>
              {selected.final_run_evidence ? <JsonBlock value={selected.final_run_evidence} /> : <Empty>没有最终运行证据</Empty>}
              <h2>追溯关系</h2>
              <JsonBlock value={{ submission_id: selected.submission_id, run_manifest_id: selected.run_manifest_id, context_id: selected.context_id, context_version: selected.context_version, intent_id: selected.intent_id, intent_version: selected.intent_version }} />
            </section>
          )}
        </div>
      )}
    </main>
  );
}
