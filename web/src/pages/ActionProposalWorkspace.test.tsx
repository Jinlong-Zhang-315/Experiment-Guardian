import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setActiveSession } from "../api";
import type { ActionProposal } from "../types";
import { ActionProposalWorkspace } from "./ActionProposalWorkspace";

const proposal: ActionProposal = {
  proposal_id: "ap1",
  project_id: "p1",
  created_by: "u1",
  operation: "POLICY_PUBLISH",
  status: "PROPOSED",
  confirmability: "READY",
  confirmability_reasons: [],
  allowed_actions: ["CONFIRM", "CANCEL"],
  source_thread_id: "th1",
  source_run_id: "run1",
  source_tool_call_id: "call1",
  source_draft_id: "d1",
  source_draft_revision_id: "dr1",
  source_draft_revision: 2,
  source_candidate_hash: "a".repeat(64),
  payload: {
    expected_context_version: 1,
    context: {
      goal: "新目标",
      non_goals: [],
      mainline_model: "shift-gcn",
      baseline: {},
      dataset: "NTU60",
      protocol: "40/20",
      primary_metric: { name: "top1" },
      default_seeds: [1],
      active_branch: "main",
      active_config: { fusion: 0.3 },
      deprecated_items: [],
      key_decisions: [],
      change_reason: "治理提案",
    },
    intent: {
      name: "fusion",
      objective: "验证融合",
      hypothesis: "提升指标",
      allowed_variables: ["fusion"],
      controlled_variables: [],
      expected_outputs: ["top1"],
      acceptance_criteria: [],
      original_message: "调整融合",
    },
    constraints: [{
      parameter_path: "fusion",
      protection_level: "EXPERIMENT_VARIABLE",
      expected_value: 0.2,
      reason: "实验变量",
      original_message: "允许调整",
    }],
  },
  payload_hash: "b".repeat(64),
  base_context_id: "c1",
  base_context_version: 1,
  base_intent_id: "i1",
  base_intent_version: 1,
  base_policy_hash: "c".repeat(64),
  diff_snapshot: [{
    field_path: "context.goal",
    change_type: "MODIFIED",
    previous_value: "旧目标",
    candidate_value: "新目标",
    attention_level: "HIGH",
    impact: "改变项目正式目标。",
  }],
  impact_snapshot: {
    status: "COMPLETE",
    generated_at: "2026-07-24T08:00:00Z",
    pending_state_hash: "d".repeat(64),
    attention_level: "HIGH",
    future_policy_effects: ["后续 Plan 使用新目标。"],
    plan_simulations: [],
    plan_simulations_truncated: false,
    submission_impacts: [],
    submission_impacts_truncated: false,
    warnings: [],
  },
  pending_state_hash: "d".repeat(64),
  proposal_digest: "e".repeat(64),
  expires_at: "2026-07-25T08:00:00Z",
  created_at: "2026-07-24T08:00:00Z",
  updated_at: "2026-07-24T08:00:00Z",
};

const clients: QueryClient[] = [];

function renderWorkspace() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <ActionProposalWorkspace projectId="p1" onClose={vi.fn()} />
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

describe("ActionProposalWorkspace", () => {
  it("requires explicit review before owner confirmation", async () => {
    let confirmed: Record<string, unknown> | undefined;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST" && url.endsWith("/confirm")) {
        confirmed = JSON.parse(String(init.body));
        return Response.json({
          ...proposal,
          status: "EXECUTED",
          confirmability: "TERMINAL",
          allowed_actions: [],
          executed_context_version: 2,
        });
      }
      if (url.includes("action-proposals?")) return Response.json({ items: [proposal] });
      return Response.json(proposal);
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
    const button = await screen.findByRole("button", { name: "确认并发布正式版本" });
    expect(button).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() => expect(confirmed).toEqual({
      proposal_digest: proposal.proposal_digest,
    }));
  });

  it("shows researcher the owner waiting state without confirm action", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("action-proposals?")) {
        return Response.json({
          items: [{ ...proposal, allowed_actions: ["CANCEL"] }],
        });
      }
      return Response.json({ ...proposal, allowed_actions: ["CANCEL"] });
    }));
    renderWorkspace();
    expect(await screen.findByText("等待 Owner 审阅并确认")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认并发布正式版本" })).not.toBeInTheDocument();
  });
});
