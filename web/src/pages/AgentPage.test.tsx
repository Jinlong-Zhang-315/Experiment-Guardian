import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentPage } from "./AgentPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/projects/p1/agent"]}>
        <Routes>
          <Route path="/projects/:projectId/agent" element={<AgentPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("external Agent task visibility", () => {
  it("shows MCP origin and the frozen policy version while allowing Web continuation", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return Response.json({ role: "OWNER", agent_enabled: true });
      }
      if (url.includes("/agent/threads/task-1")) {
        return Response.json({
          thread: {
            thread_id: "task-1", project_id: "p1", title: "外部消融任务",
            origin: "EXTERNAL_MCP", status: "ACTIVE",
            created_at: "2026-07-28T10:00:00Z", updated_at: "2026-07-28T10:00:00Z",
          },
          messages: [],
          external_task_context: {
            captured_at: "2026-07-28T10:00:00Z", source_hash: "a".repeat(64),
            authoritative_scope: "FORMAL_POLICY_ONLY", governance_notice: "结构化数据为准",
            context_freshness: "CURRENT",
            policy: { context: { version: 1 }, active_intent: { version: 2 } },
          },
        });
      }
      if (url.includes("/agent/threads?")) {
        return Response.json({ items: [{
          thread_id: "task-1", project_id: "p1", title: "外部消融任务",
          origin: "EXTERNAL_MCP", status: "ACTIVE",
          created_at: "2026-07-28T10:00:00Z", updated_at: "2026-07-28T10:00:00Z",
        }] });
      }
      return Response.json({});
    }));

    renderPage();
    expect(await screen.findByText("任务启动于 Context v1 / Intent v2。当前正式版本未变化。"))
      .toBeInTheDocument();
    expect(screen.getAllByText(/MCP 任务/).length).toBeGreaterThan(0);
    expect(screen.getByRole("textbox", { name: "发送给治理 Agent" })).toBeEnabled();
  });

  it("defaults a new Web thread to the evaluated analysis capability", async () => {
    let createBody: Record<string, unknown> | undefined;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return Response.json({ role: "OWNER", agent_enabled: true });
      if (url.includes("/agent/threads?") && (!init?.method || init.method === "GET")) {
        return Response.json({ items: [] });
      }
      if (url.endsWith("/agent/threads") && init?.method === "POST") {
        createBody = JSON.parse(String(init.body));
        return Response.json({
          thread_id: "analysis-1", project_id: "p1", title: "新对话",
          origin: "WEB", capability_domain: "ANALYSIS", status: "ACTIVE",
          created_at: "2026-07-30T10:00:00Z", updated_at: "2026-07-30T10:00:00Z",
        });
      }
      if (url.endsWith("/agent/threads/analysis-1")) {
        return Response.json({
          thread: {
            thread_id: "analysis-1", project_id: "p1", title: "新对话",
            origin: "WEB", capability_domain: "ANALYSIS", status: "ACTIVE",
            created_at: "2026-07-30T10:00:00Z", updated_at: "2026-07-30T10:00:00Z",
          },
          messages: [],
        });
      }
      return Response.json({});
    }));

    renderPage();
    expect(await screen.findByRole("button", { name: "实验分析" })).toHaveClass("active");
    fireEvent.click(screen.getByRole("button", { name: "新对话" }));
    await waitFor(() => expect(createBody).toEqual({ capability_domain: "ANALYSIS" }));
  });
});
