import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExperimentPage } from "./ExperimentPage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/projects/p1/experiments"]}>
        <Routes><Route path="/projects/:projectId/experiments" element={<ExperimentPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("formal experiment detail", () => {
  it("loads FULL detail and displays material provenance", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/projects/p1/experiments/e1")) {
        return Promise.resolve(Response.json({
          experiment_id: "e1", submission_id: "s1", run_manifest_id: "m1",
          name: "TDSM replay", model_name: "TDSM", dataset: "NTU60", protocol: "40/20",
          seed: 315, experiment_mode: "FORMAL", status: "COMPLETED", context_id: "c1",
          context_version: 1, intent_id: "i1", intent_version: 1, config_hash: "a".repeat(64),
          git_commit: "fixture-commit", summary: {}, confirmed_at: "2026-08-01T10:00:00Z",
          created_at: "2026-08-01T10:00:00Z", detail_level: "FULL",
          metrics: [{ name: "zsl_test_acc", value: 0.446607, split: "REPORTED", aggregation_type: "SINGLE_RUN", is_primary: true }],
          artifacts: [{
            artifact_id: "a1", filename: "result.json", mime_type: "application/json",
            size_bytes: 128, sha256: "b".repeat(64), artifact_type: "RESULT",
            cloud_hash_verified: true, s3_version_id: "v1", material_origin: "DERIVED_FROM_LOG",
            provenance: { classification: "DERIVED_FROM_LOG", source_reference: "val_log.txt", source_sha256: "c".repeat(64), derivation_method: "parse metric", note: "派生结果，不是原始输出" },
          }],
          material_provenance: { facts: [], contains_non_current_material: true, contains_unspecified_material: false, historical_material_was_prevalidated: false, disclaimer: "历史材料不代表原始运行已通过运行前验证。" },
          final_run_evidence: { deviation_explanation: "num_epoch 与历史日志轮数不一致" },
        }));
      }
      return Promise.resolve(Response.json({ items: [{
        experiment_id: "e1", submission_id: "s1", run_manifest_id: "m1",
        name: "TDSM replay", model_name: "TDSM", dataset: "NTU60", protocol: "40/20",
        seed: 315, experiment_mode: "FORMAL", status: "COMPLETED", context_id: "c1",
        context_version: 1, intent_id: "i1", intent_version: 1, config_hash: "a".repeat(64),
        git_commit: "fixture-commit", summary: {}, confirmed_at: "2026-08-01T10:00:00Z",
        created_at: "2026-08-01T10:00:00Z", detail_level: "SUMMARY",
      }] }));
    }));

    renderPage();

    expect(await screen.findByText("材料来源边界")).toBeInTheDocument();
    expect(screen.getByText(/DERIVED_FROM_LOG/)).toBeInTheDocument();
    expect(screen.getByText("派生结果，不是原始输出")).toBeInTheDocument();
    expect(screen.getByText("FULL")).toBeInTheDocument();
    expect(screen.getByText(/zsl_test_acc/)).toBeInTheDocument();
    expect(screen.getByText(/num_epoch 与历史日志轮数不一致/)).toBeInTheDocument();
  });
});
