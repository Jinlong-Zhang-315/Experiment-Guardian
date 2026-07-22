import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

function renderApp(path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("Experiment Guardian Web shell", () => {
  it("redirects unauthenticated users to Cognito Managed Login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ error: { code: "AUTHENTICATION_FAILED", message: "未登录" } }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    renderApp("/projects/example/settings");
    expect(await screen.findByRole("heading", { name: "实验治理工作台" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "使用团队身份登录" })).toHaveAttribute(
      "href",
      expect.stringContaining("/api/v1/auth/login"),
    );
  });

  it("renders the four operational navigation entries for a signed-in user", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return Response.json({
            user_id: "u1", team_id: "t1", session_id: "s1", name: "Owner",
            email: "owner@example.com", role: "OWNER", csrf_token: "csrf",
            recent_authentication: true, absolute_expires_at: "2026-07-23T00:00:00Z",
          });
        }
        if (url.endsWith("/projects")) {
          return Response.json({ items: [{ project_id: "p1", name: "NTU60", description: "Demo", active: true }] });
        }
        return Response.json({
          project: { project_id: "p1", name: "NTU60", description: "Demo", active: true },
          current: {
            context: { context_id: "c1", version: 1, effective_at: "2026-07-22T00:00:00Z", confirmed_by: "u1" },
            active_intent: { intent_id: "i1", version: 1, mode: "FORMAL" },
            constraints: [],
            context_payload: { goal: "复现实验", dataset: "NTU60", protocol: "40/20", mainline_model: "shift-gcn", baseline: {}, active_config: {} },
            intent_payload: { name: "baseline", objective: "复现", hypothesis: "一致", intent_receipt: "confirmed" },
          },
          context_history: [],
        });
      }),
    );
    renderApp("/projects/p1/settings");
    expect(await screen.findByRole("heading", { name: "NTU60" })).toBeInTheDocument();
    for (const name of ["项目设置", "计划审批", "实验审核", "实验查询"]) {
      expect(screen.getByRole("link", { name })).toBeInTheDocument();
    }
  });
});
