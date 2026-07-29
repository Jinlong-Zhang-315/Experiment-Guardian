import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PlanPage } from "./PlanPage";
import { SubmissionPage } from "./SubmissionPage";

function renderPage(path: string, element: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/projects/:projectId/*" element={element} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("R17c invariant checkpoint pages", () => {
  it("shows the approved-plan checkpoint on Plan Check detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
      items: [{
        plan_check_id: "pc-1", requester_id: "u1", context_version: 2, intent_version: 3,
        check_result: "BLOCKED", approval_status: "NOT_REQUIRED", risk_level: "CRITICAL",
        planned_changes: [], report: {}, git_commit: "abc1234", command: "python train.py",
        experiment_plan_decision_id: "decision-1",
        invariant_check: { overall_status: "CRITICAL_DEVIATION", checks: [] },
        created_at: "2026-07-28T10:00:00Z", allowed_actions: [],
      }],
    })));

    renderPage("/projects/p1/plans", <PlanPage />);

    expect(await screen.findByRole("heading", { name: "批准计划与关键不变量" }))
      .toBeInTheDocument();
    expect(screen.getByText("CRITICAL DEVIATION")).toBeInTheDocument();
    expect(screen.getByText("decision-1")).toBeInTheDocument();
  });

  it("shows the final invariant checkpoint on Submission detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
      items: [{
        submission_id: "submission-1", run_manifest_id: "manifest-1", submitted_by: "u1",
        source_agent: "agent", status: "NEEDS_REVIEW", workflow_status: "COMPLETED",
        invariant_check: { overall_status: "CRITICAL_DEVIATION", checks: [] },
        risks: [], artifacts: [], created_at: "2026-07-28T10:00:00Z",
        updated_at: "2026-07-28T10:01:00Z", allowed_actions: ["REJECT"],
      }],
    })));

    renderPage("/projects/p1/submissions", <SubmissionPage />);

    expect(await screen.findByRole("heading", { name: "最终关键不变量核对" }))
      .toBeInTheDocument();
    expect(screen.getByText("CRITICAL DEVIATION")).toBeInTheDocument();
  });
});
