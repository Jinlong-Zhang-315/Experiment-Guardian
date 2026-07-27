import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentModelObservability, AgentRun } from "../types";
import { AgentObservabilityWorkspace, AgentRunDetailsDialog } from "./AgentObservabilityWorkspace";

const observability: AgentModelObservability = {
  project_id: "project-1",
  window_from: "2026-07-20T08:00:00Z",
  window_to: "2026-07-27T08:00:00Z",
  current_provider: "bedrock",
  current_model_id: "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
  pricing_configured: true,
  totals: {
    run_count: 4, model_call_count: 5, succeeded_call_count: 4,
    failed_call_count: 1, abandoned_call_count: 0, retry_count: 1,
    input_tokens: 1200, output_tokens: 300, missing_usage_call_count: 0,
    unpriced_call_count: 0, average_latency_ms: 420, maximum_latency_ms: 900,
  },
  groups: [{
    provider: "bedrock", model_id: "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
    purpose: "AGENT_TURN", run_count: 4, model_call_count: 5,
    succeeded_call_count: 4, failed_call_count: 1, abandoned_call_count: 0,
    retry_count: 1, input_tokens: 1200, output_tokens: 300,
    missing_usage_call_count: 0, unpriced_call_count: 0,
    average_latency_ms: 420, maximum_latency_ms: 900,
  }],
  costs: [{ currency: "USD", estimated_cost: "0.0123400000" }],
  failure_categories: { SERVICE_UNAVAILABLE: 1 },
};

const run: AgentRun = {
  run_id: "run-1", thread_id: "thread-1", trigger_message_id: "message-1",
  status: "SUCCEEDED", events_url: "/events", attempt_count: 1,
  max_attempts: 3, provider: "bedrock", model_id: observability.current_model_id,
  usage: { input_tokens: 1200, output_tokens: 300 }, final_message_id: "message-2",
  created_at: "2026-07-27T08:00:00Z", completed_at: "2026-07-27T08:00:01Z",
  model_calls: [{
    call_id: "call-1", generation: 1, ordinal: 1, purpose: "AGENT_TURN",
    status: "SUCCEEDED", provider: "bedrock", model_id: observability.current_model_id,
    input_tokens: 1200, output_tokens: 300, latency_ms: 420,
    estimated_cost: "0.0123400000", cost_currency: "USD", finish_reason: "end_turn",
    started_at: "2026-07-27T08:00:00Z", completed_at: "2026-07-27T08:00:01Z",
  }],
};

const clients: QueryClient[] = [];

function renderWithClient(element: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  return render(<QueryClientProvider client={client}>{element}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
  vi.unstubAllGlobals();
});

describe("AgentObservabilityWorkspace", () => {
  it("shows owner metrics and identifies configured-rate estimates", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      void input;
      return Response.json(observability);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithClient(<AgentObservabilityWorkspace projectId="project-1" onClose={vi.fn()} />);

    expect((await screen.findAllByText(observability.current_model_id)).length).toBeGreaterThan(0);
    expect(screen.getByText(/不是云平台账单/)).toBeInTheDocument();
    expect(screen.getAllByText("USD 0.0123400000").length).toBeGreaterThan(0);
    expect(screen.getByText("SERVICE_UNAVAILABLE")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "30 天" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) =>
      String(input).includes("window_days=30"),
    )).toBe(true));
  });

  it("shows a bounded model-call view for one run", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(run)));
    renderWithClient(<AgentRunDetailsDialog projectId="project-1" runId="run-1" onClose={vi.fn()} />);

    expect(await screen.findByText("AGENT TURN")).toBeInTheDocument();
    expect(screen.getByText("1,200 / 300")).toBeInTheDocument();
    expect(screen.getByText("420 ms")).toBeInTheDocument();
    expect(screen.getAllByText("USD 0.0123400000").length).toBeGreaterThan(0);
    expect(screen.queryByText(/request_snapshot/)).not.toBeInTheDocument();
  });
});
