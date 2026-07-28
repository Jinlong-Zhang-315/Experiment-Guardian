import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setActiveSession } from "../api";
import type { ExperimentPlanView } from "../types";
import { ExperimentPlanWorkspace } from "./ExperimentPlanWorkspace";

const hash = "a".repeat(64);
const view: ExperimentPlanView = {
  summary: {
    plan_id: "plan-1", project_id: "p1", task_id: "task-1", created_by: "u1",
    title: "融合系数消融", status: "READY_FOR_APPROVAL", current_revision: 2,
    freshness: "CURRENT", created_at: "2026-07-28T08:00:00Z",
    updated_at: "2026-07-28T09:00:00Z",
  },
  current: {
    revision_id: "revision-2", plan_id: "plan-1", revision: 2,
    author_type: "INTERNAL_AGENT", automatic_revision_round: 1,
    title: "融合系数消融", plan_markdown: "## 目标\n仅调整融合系数。",
    evidence: { config_summary: {}, related_experiment_ids: [] },
    context_id: "context-1", context_version: 1, intent_id: "intent-1", intent_version: 1,
    policy_snapshot: {}, policy_hash: hash, content_hash: hash, evidence_hash: hash,
    created_at: "2026-07-28T09:00:00Z",
  },
  review: {
    review_id: "review-2", revision_id: "revision-2", source_run_id: "run-2",
    hard_check: { status: "PASS", issues: [] },
    semantic_review: {
      recommendation: "READY", review_markdown: "计划与正式主线一致。", findings: [],
      free_exploration: ["融合模块内部实现"], user_decisions: [], citations: ["ev_1_1"],
    },
    candidate_invariants: [{
      candidate_id: "ci-1", statement: "保持协议不变", rationale: "确保结果可比",
      verification_method: "正式 Plan Check", representation: "STRUCTURED_PARAMETER",
      parameter_path: "dataset.protocol", expected_value: "40/20", citation_ids: ["ev_1_1"],
    }],
    approval_receipt: {}, review_hash: hash, approval_digest: "b".repeat(64),
    provider: "bailian", model_id: "qwen", prompt_version: "r17b-plan-review-v1",
    created_at: "2026-07-28T09:00:00Z",
  },
  revisions: [],
  allowed_actions: ["REJECT", "REQUEST_CHANGES", "APPROVE", "CONDITIONAL_APPROVE"],
};
view.revisions = [view.current];

const clients: QueryClient[] = [];

function renderWorkspace() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <ExperimentPlanWorkspace projectId="p1" onClose={vi.fn()} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setActiveSession(null);
});

describe("ExperimentPlanWorkspace", () => {
  it("requires a decision for every candidate invariant before approval", async () => {
    let decisionBody: Record<string, unknown> | undefined;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST" && url.endsWith("/decisions")) {
        decisionBody = JSON.parse(String(init.body));
        return Response.json({ ...view, summary: { ...view.summary, status: "APPROVED" } });
      }
      if (url.includes("/experiment-plans?")) return Response.json({ items: [view.summary] });
      return Response.json(view);
    }));
    setActiveSession({
      user_id: "u1", team_id: "t1", session_id: "s1", name: "Owner",
      email: "owner@example.com", role: "OWNER", csrf_token: "csrf",
      recent_authentication: true, absolute_expires_at: "2026-07-29T00:00:00Z",
      agent_enabled: true,
    });

    renderWorkspace();
    expect(await screen.findByText("计划与正式主线一致。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "批准" }));
    fireEvent.change(screen.getByLabelText("决定理由"), {
      target: { value: "已核对正式协议" },
    });
    expect(screen.getByRole("button", { name: "确认决定" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    fireEvent.click(screen.getByRole("button", { name: "确认决定" }));

    await waitFor(() => expect(decisionBody).toBeDefined());
    expect(decisionBody?.expected_revision).toBe(2);
    expect(decisionBody?.confirmed_candidate_ids).toEqual(["ci-1"]);
    expect(decisionBody?.rejected_candidate_ids).toEqual([]);
    expect(decisionBody?.review_hash).toBe(hash);
  });
});

