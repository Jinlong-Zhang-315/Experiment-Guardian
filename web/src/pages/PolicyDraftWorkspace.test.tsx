import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setActiveSession } from "../api";
import type { PolicyDraftView } from "../types";
import { PolicyDraftWorkspace } from "./PolicyDraftWorkspace";

const impact = {
  status: "COMPLETE" as const,
  generated_at: "2026-07-24T08:00:00Z",
  pending_state_hash: "a".repeat(64),
  attention_level: "HIGH" as const,
  future_policy_effects: ["context.goal：可能改变项目目标"],
  plan_simulations: [],
  plan_simulations_truncated: false,
  submission_impacts: [],
  submission_impacts_truncated: false,
  warnings: [],
};

const view: PolicyDraftView = {
  summary: {
    draft_id: "d1",
    project_id: "p1",
    created_by: "u1",
    status: "ACTIVE",
    freshness: "CURRENT",
    base_context_id: "c1",
    base_context_version: 1,
    base_intent_id: "i1",
    base_intent_version: 1,
    current_revision: 1,
    readiness: "READY",
    ambiguity_count: 0,
    change_summary: "调整项目目标",
    created_at: "2026-07-24T08:00:00Z",
    updated_at: "2026-07-24T08:00:00Z",
  },
  current: {
    revision_id: "r1",
    draft_id: "d1",
    revision: 1,
    author_id: "u1",
    source: "AGENT",
    candidate: {
      context: {
        goal: "新目标",
        non_goals: ["不自动训练"],
        mainline_model: "shift-gcn",
        baseline: {},
        dataset: "NTU60",
        protocol: "40/20",
        primary_metric: { name: "top1", higher_is_better: true },
        default_seeds: [1],
        active_branch: "main",
        active_config: { model: { fusion: 0.2 } },
        deprecated_items: [],
        key_decisions: [],
        change_reason: "候选变更",
      },
      intent: {
        name: "fusion",
        objective: "验证融合",
        hypothesis: "可能提升",
        allowed_variables: ["model.fusion"],
        controlled_variables: [],
        expected_outputs: ["top1"],
        acceptance_criteria: [],
        original_message: "修改 fusion",
      },
      constraints: [{
        parameter_path: "model.fusion",
        protection_level: "EXPERIMENT_VARIABLE",
        expected_value: 0.2,
        reason: "实验变量",
        original_message: "允许修改",
      }],
    },
    candidate_hash: "b".repeat(64),
    change_summary: "调整项目目标",
    unresolved_ambiguities: [],
    validation: { readiness: "READY", issues: [], unresolved_ambiguities: [] },
    diff: [{
      field_path: "context.goal",
      change_type: "MODIFIED",
      previous_value: "旧目标",
      candidate_value: "新目标",
      attention_level: "HIGH",
      impact: "可能改变项目目标。",
    }],
    narrative: {
      status: "READY",
      generator_version: "policy-draft-template-v1",
      source_hash: "b".repeat(64),
      content: "# Policy Bundle 治理候选草稿\n\n> 该内容尚未生效",
      authoritative: false,
      governance_notice: "候选内容",
    },
    stored_impact: impact,
    current_impact: impact,
    impact_changed_since_revision: false,
    created_at: "2026-07-24T08:00:00Z",
  },
  revisions: [{
    revision_id: "r1",
    revision: 1,
    author_id: "u1",
    source: "AGENT",
    readiness: "READY",
    candidate_hash: "b".repeat(64),
    change_summary: "调整项目目标",
    ambiguity_count: 0,
    created_at: "2026-07-24T08:00:00Z",
  }],
};

const queryClients: QueryClient[] = [];

function renderWorkspace() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <PolicyDraftWorkspace projectId="p1" onClose={vi.fn()} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  for (const client of queryClients) client.clear();
  queryClients.length = 0;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setActiveSession(null);
});

describe("PolicyDraftWorkspace", () => {
  it("shows candidate receipt and deterministic diff without a publish action", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/policy-drafts?")) return Response.json({ items: [view.summary] });
      return Response.json(view);
    }));
    renderWorkspace();
    expect(await screen.findByText("调整项目目标")).toBeInTheDocument();
    expect(await screen.findByText("该内容尚未生效")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /发布/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "差异" }));
    expect(await screen.findByText("context.goal")).toBeInTheDocument();
    expect(screen.getByText("可能改变项目目标。")).toBeInTheDocument();
  });

  it("saves a new full revision with optimistic concurrency", async () => {
    let posted: Record<string, unknown> | undefined;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST" && url.endsWith("/revisions")) {
        posted = JSON.parse(String(init.body));
        return Response.json({ ...view.current, revision: 2 });
      }
      if (url.includes("/policy-drafts?")) return Response.json({ items: [view.summary] });
      return Response.json(view);
    }));
    setActiveSession({
      user_id: "u1",
      team_id: "t1",
      session_id: "s1",
      name: "Owner",
      email: "owner@example.com",
      role: "OWNER",
      csrf_token: "csrf",
      recent_authentication: true,
      absolute_expires_at: "2026-07-25T00:00:00Z",
      agent_enabled: true,
    });
    renderWorkspace();
    await screen.findByRole("button", { name: "编辑新 revision" });
    fireEvent.click(screen.getByRole("button", { name: "编辑新 revision" }));
    fireEvent.change(screen.getByLabelText("项目目标"), { target: { value: "再次调整目标" } });
    fireEvent.change(screen.getByLabelText("本次 revision 说明"), { target: { value: "Owner 修订" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 revision" }));
    await waitFor(() => expect(posted).toBeDefined());
    await screen.findByRole("button", { name: "编辑新 revision" });
    expect(posted?.expected_revision).toBe(1);
    expect((posted?.candidate as { context: { goal: string } }).context.goal).toBe("再次调整目标");
  });
});
