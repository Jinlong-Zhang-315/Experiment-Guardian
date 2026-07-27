import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ResearchReportView } from "../types";
import { ResearchReportWorkspace } from "./ResearchReportWorkspace";

const report: ResearchReportView = {
  report_id: "report-1",
  project_id: "project-1",
  created_by: "user-1",
  title: "融合实验阶段总结",
  objective: "比较两个显式实验",
  experiment_ids: ["experiment-1", "experiment-2"],
  include_historical: false,
  source_hash: "a".repeat(64),
  provider: "bailian",
  model_id: "qwen-agent",
  prompt_version: "r15e-a-v1",
  created_at: "2026-07-27T08:00:00Z",
  schema_version: 1,
  payload_hash: "b".repeat(64),
  source_thread_id: "thread-1",
  source_run_id: "run-1",
  final_message_id: "message-1",
  authoritative: false,
  evidence_classification: "ANALYSIS",
  memory_materialization_pending: false,
  research_memories: [{
    memory_id: "memory-1",
    finding_id: "F001",
    memory_type: "RESEARCH_SYNTHESIS",
    status: "CANDIDATE",
    source_freshness: "CURRENT",
    embedding_status: "DEAD_LETTER",
    provider: "bailian",
    model_id: "text-embedding-v4",
    document_version: "agent-research-memory-v1",
    last_error: { code: "PROVIDER_ERROR", message: "索引服务暂时不可用" },
  }],
  source_warnings: [{
    code: "SOURCE_STATUS_CHANGED",
    experiment_id: "experiment-2",
    snapshot_status: "COMPLETED",
    current_status: "SUPERSEDED",
    message: "实验状态已从 COMPLETED 变为 SUPERSEDED；报告内容未被追溯修改。",
  }],
  source_snapshot: {
    evidence: [
      { evidence_id: "ev_1_1", evidence_kind: "CONFIRMED_FACT", entity_type: "EXPERIMENT", entity_id: "experiment-1", label: "实验一", excerpt: "top1=0.8" },
      { evidence_id: "ev_1_2", evidence_kind: "CONFIRMED_FACT", entity_type: "EXPERIMENT", entity_id: "experiment-2", label: "实验二", excerpt: "top1=0.82" },
      { evidence_id: "ev_1_3", evidence_kind: "ANALYSIS", entity_type: "EXPERIMENT_COMPARISON", label: "确定性比较", excerpt: "COMPARABLE" },
    ],
  },
  report: {
    schema_version: 1,
    source_hash: "a".repeat(64),
    title: "融合实验阶段总结",
    executive_summary: "两个实验满足确定性比较条件。",
    executive_summary_citation_ids: ["ev_1_1", "ev_1_2", "ev_1_3"],
    findings: [{
      finding_id: "F001",
      kind: "SUPPORTED_CONCLUSION",
      statement: "第二次实验指标更高。",
      rationale: "只陈述确定性指标差，不推断因果。",
      citation_ids: ["ev_1_1", "ev_1_2", "ev_1_3"],
      limitations: ["不构成统计显著性证明"],
    }],
    limitations: [],
    selected_experiment_ids: ["experiment-1", "experiment-2"],
  },
};

const clients: QueryClient[] = [];

afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
  vi.unstubAllGlobals();
});

describe("ResearchReportWorkspace", () => {
  it("shows candidate status, citations, source warning and raw snapshot", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/auth/me")) return Response.json({ role: "OWNER" });
      return String(input).includes("research-reports?")
        ? Response.json({ items: [report] })
        : Response.json(report);
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    render(<QueryClientProvider client={client}>
      <ResearchReportWorkspace projectId="project-1" onClose={vi.fn()} />
    </QueryClientProvider>);

    expect(await screen.findByText("第二次实验指标更高。")).toBeInTheDocument();
    expect(screen.getByText(/不属于正式事实/)).toBeInTheDocument();
    expect(screen.getByText(/状态已从 COMPLETED/)).toBeInTheDocument();
    expect(screen.getByText("确定性比较")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /原始数据/ }));
    expect(screen.getByText(/唯一正式事实源/)).toBeInTheDocument();
    expect(screen.getByText(/"authoritative": false/)).toBeInTheDocument();
  });

  it("searches candidate memory and lets only the owner retry failed indexing", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return Response.json({ role: "OWNER" });
      if (url.includes("research-memories/search")) return Response.json({
        items: [{
          memory_id: "memory-1", report_id: "report-1", finding_id: "F001",
          memory_type: "RESEARCH_SYNTHESIS", statement: "第二次实验指标更高。",
          rationale: "确定性指标差", limitations: [], citation_ids: ["ev_1_1"],
          experiment_ids: ["experiment-1", "experiment-2"], protocols: ["40/20"],
          source_freshness: "CURRENT", source_warnings: [], similarity: 0.91,
          provider: "bailian", model_id: "text-embedding-v4",
          document_version: "agent-research-memory-v1", content_hash: "c".repeat(64),
          authoritative: false, evidence_classification: "ANALYSIS",
          retrieval_role: "CANDIDATE_EVIDENCE",
        }],
        candidate_count: 1, candidate_truncated: false,
        authoritative: false, retrieval_role: "CANDIDATE_EVIDENCE",
      });
      if (url.includes("embedding/retry") && init?.method === "POST") return Response.json({});
      return url.includes("research-reports?")
        ? Response.json({ items: [report] })
        : Response.json(report);
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    clients.push(client);
    render(<QueryClientProvider client={client}>
      <ResearchReportWorkspace projectId="project-1" onClose={vi.fn()} />
    </QueryClientProvider>);

    expect(await screen.findByText("索引服务暂时不可用")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/检索阶段结论/), { target: { value: "融合系数" } });
    fireEvent.click(screen.getByRole("button", { name: "检索" }));
    expect(await screen.findByText(/CANDIDATE_EVIDENCE/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重试 F001 索引/ }));
    await waitFor(() => expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes("embedding/retry")),
    ).toBe(true));
  });
});
