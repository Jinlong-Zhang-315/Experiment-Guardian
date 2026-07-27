import { expect, test } from "@playwright/test";

const settings = {
  project: { project_id: "p1", name: "NTU60 Governance", description: "动作识别实验治理", active: true },
  current: {
    context: { context_id: "context-1", version: 3, confirmed_by: "owner-1", effective_at: "2026-07-22T10:00:00Z" },
    active_intent: { intent_id: "intent-1", version: 5, mode: "FORMAL" },
    constraints: [
      { parameter_path: "dataset.protocol", protection_level: "LOCKED", expected_value: "40/20", reason: "正式评测协议" },
      { parameter_path: "model.backbone", protection_level: "APPROVAL_REQUIRED", expected_value: "shift-gcn", reason: "主线结构" },
      { parameter_path: "model.fusion", protection_level: "EXPERIMENT_VARIABLE", expected_value: 0.2, reason: "当前实验变量" },
    ],
    context_payload: { goal: "验证多模态融合策略", dataset: "NTU60", protocol: "40/20", mainline_model: "shift-gcn", baseline: { checkpoint: "baseline.pt" }, active_config: { dataset: { protocol: "40/20" }, model: { backbone: "shift-gcn", fusion: 0.2 } } },
    intent_payload: { name: "fusion sweep", objective: "评估融合系数", hypothesis: "局部调整可提升 top1", intent_receipt: "Owner 已确认该正式实验意图。" },
    human_readable: {
      status: "READY", format: "MARKDOWN", generator: "DETERMINISTIC_TEMPLATE",
      generator_version: "policy-narrative-v1",
      content: "# NTU60 Governance：正式实验策略\n\n## 项目目标\n验证多模态融合策略\n\n## 锁定参数\n- `dataset.protocol` = `\"40/20\"`：不得在实验配置中修改",
      context_id: "context-1", context_version: 3, intent_id: "intent-1", intent_version: 5,
      source_hash: "a".repeat(64), current_source_hash: "a".repeat(64),
      generated_by: "owner-1", generated_at: "2026-07-22T10:00:00Z",
      authoritative: false, governance_notice: "结构化数据为准",
    },
  },
  context_history: [
    { context_id: "context-1", version: 3, status: "ACTIVE", change_reason: "确认主线", effective_at: "2026-07-22T10:00:00Z" },
    { context_id: "context-0", version: 2, status: "SUPERSEDED", change_reason: "更新协议", effective_at: "2026-07-20T10:00:00Z" },
  ],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) {
      return route.fulfill({ json: { user_id: "owner-1", team_id: "team-1", session_id: "session-1", name: "Zhang Owner", email: "owner@example.com", role: "OWNER", csrf_token: "csrf", recent_authentication: true, absolute_expires_at: "2026-07-29T00:00:00Z", agent_enabled: true } });
    }
    if (path === "/api/v1/projects") {
      return route.fulfill({ json: { items: [settings.project] } });
    }
    if (path.endsWith("/settings")) return route.fulfill({ json: settings });
    if (path.endsWith("/agent/model-observability")) return route.fulfill({ json: {
      project_id: "p1", window_from: "2026-07-20T00:00:00Z", window_to: "2026-07-27T00:00:00Z",
      current_provider: "bailian", current_model_id: "qwen-agent", pricing_configured: true,
      totals: { run_count: 2, model_call_count: 3, succeeded_call_count: 3, failed_call_count: 0, abandoned_call_count: 0, retry_count: 0, input_tokens: 1500, output_tokens: 400, missing_usage_call_count: 0, unpriced_call_count: 0, average_latency_ms: 380, maximum_latency_ms: 610 },
      groups: [{ provider: "bailian", model_id: "qwen-agent", purpose: "AGENT_TURN", run_count: 2, model_call_count: 3, succeeded_call_count: 3, failed_call_count: 0, abandoned_call_count: 0, retry_count: 0, input_tokens: 1500, output_tokens: 400, missing_usage_call_count: 0, unpriced_call_count: 0, average_latency_ms: 380, maximum_latency_ms: 610 }],
      costs: [{ currency: "CNY", estimated_cost: "0.0042000000" }], failure_categories: {},
    } });
    return route.fulfill({ json: { items: [] } });
  });
});

test("workspace and research reports remain readable without horizontal overflow", async ({ page }, testInfo) => {
  await page.goto("/projects/p1/settings");
  await expect(page.getByRole("heading", { name: "NTU60 Governance", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "项目正式说明" })).toBeVisible();
  await expect(
    page.locator(".narrative-content").getByText("验证多模态融合策略", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("cell", { name: "dataset.protocol" })).toBeVisible();
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("settings.png"), fullPage: true });

  for (const [navigation, heading] of [
    ["计划审批", "训练前配置检查"],
    ["实验审核", "Submission 回执"],
    ["实验查询", "正式实验记录"],
  ]) {
    await page.getByRole("link", { name: navigation }).click();
    await expect(page.getByRole("heading", { name: heading })).toBeVisible();
    await expectNoPageOverflow(page);
  }

  await page.getByRole("link", { name: "治理 Agent" }).click();
  await expect(page.getByRole("heading", { name: "项目实验助手" })).toBeVisible();
  await page.getByRole("button", { name: "研究报告" }).click();
  await expect(page.getByRole("dialog", { name: "候选研究报告" })).toBeVisible();
  await expect(page.getByText("通过治理 Agent 显式选择实验并生成第一份报告")).toBeVisible();
  await expectNoPageOverflow(page);
  await page.getByRole("button", { name: "关闭研究报告" }).click();
  await expect(page.getByRole("dialog", { name: "候选研究报告" })).not.toBeVisible();
  await page.getByRole("button", { name: "模型观测" }).click();
  await expect(page.getByRole("dialog", { name: "模型运行观测" })).toBeVisible();
  await expect(page.getByText("CNY 0.0042000000")).toBeVisible();
  await expectNoPageOverflow(page);
  await page.getByRole("button", { name: "关闭模型运行观测" }).click();
});

async function expectNoPageOverflow(page: import("@playwright/test").Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}
