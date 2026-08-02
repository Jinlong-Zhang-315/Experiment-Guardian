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

  it("prominently shows historical and derived material provenance", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
      items: [{
        submission_id: "submission-2", run_manifest_id: "manifest-2", submitted_by: "u1",
        source_agent: "agent", status: "NEEDS_REVIEW", workflow_status: "COMPLETED",
        material_provenance: {
          facts: [], contains_non_current_material: true,
          contains_unspecified_material: false, historical_material_was_prevalidated: false,
          disclaimer: "包含历史材料，不代表原始运行已经过运行前验证。",
        },
        risks: [],
        artifacts: [{
          artifact_id: "artifact-1", filename: "result.json", artifact_type: "RESULT",
          size_bytes: 128, cloud_hash_verified: true, material_origin: "DERIVED_FROM_LOG",
          provenance: {
            classification: "DERIVED_FROM_LOG", source_reference: "val_log.txt",
            source_sha256: "a".repeat(64), derivation_method: "parse best metric",
            note: "派生 JSON，不是原始输出",
          },
        }],
        created_at: "2026-08-01T10:00:00Z", updated_at: "2026-08-01T10:01:00Z",
        allowed_actions: ["REJECT"],
      }],
    })));

    renderPage("/projects/p1/submissions", <SubmissionPage />);

    expect(await screen.findByText("材料来源边界")).toBeInTheDocument();
    expect(screen.getByText("包含历史材料，不代表原始运行已经过运行前验证。"))
      .toBeInTheDocument();
    expect(screen.getByText(/DERIVED_FROM_LOG/)).toBeInTheDocument();
    expect(screen.getByText("派生 JSON，不是原始输出")).toBeInTheDocument();
  });

  it("shows the structured model failure category", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
      items: [{
        submission_id: "submission-3", run_manifest_id: "manifest-3", submitted_by: "u1",
        source_agent: "agent", status: "FAILED", workflow_status: "TERMINAL_FAILURE",
        processing_error: {
          code: "SUMMARY_GENERATION_AUTHENTICATION_FAILED", provider: "bailian",
          http_status: 401, retryable: false,
        },
        risks: [], artifacts: [], created_at: "2026-08-02T10:00:00Z",
        updated_at: "2026-08-02T10:01:00Z", allowed_actions: [],
      }],
    })));

    renderPage("/projects/p1/submissions", <SubmissionPage />);

    expect(await screen.findByText("后台处理失败")).toBeInTheDocument();
    expect(screen.getByText(/SUMMARY_GENERATION_AUTHENTICATION_FAILED/)).toBeInTheDocument();
  });
});
