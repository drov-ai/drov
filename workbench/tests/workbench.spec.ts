import {
  test,
  expect,
  type Page,
  type APIRequestContext,
} from "@playwright/test";
import { readFile } from "node:fs/promises";

const auth = { Authorization: "Bearer workbench-test" };
async function controls(request: APIRequestContext, data?: object) {
  const response = data
    ? await request.post("/v1/test/controls", { headers: auth, data })
    : await request.get("/v1/test/controls", { headers: auth });
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function login(page: Page, token = "workbench-test", navigate = true) {
  if (navigate) await page.goto("/workbench/");
  await page.getByLabel("访问令牌").fill(token);
  await page.getByRole("button", { name: "连接工作台" }).click();
  await expect(page.getByRole("heading", { name: "运行工作区" })).toBeVisible();
}
async function start(
  page: Page,
  name: string,
  value = "synthetic",
  body = true,
) {
  await page
    .getByLabel("Agent", { exact: true })
    .selectOption(JSON.stringify([name, "1"]));
  await page.getByLabel("会话名称").fill(`${name}-${crypto.randomUUID()}`);
  await page.getByLabel("任务", { exact: true }).fill(value);
  if (body) await page.getByLabel("显示正文", { exact: true }).check();
  await page.getByRole("button", { name: "开始运行" }).click();
}
async function done(page: Page, text: string) {
  await expect(page.locator(".answer")).toContainText(text);
}

test.beforeEach(async ({ request }) => {
  await controls(request, { denied: [] });
});

test("login, authentication, memory-only credential and no external traffic", async ({
  page,
}) => {
  const external: string[] = [];
  page.on("request", (req) => {
    if (!req.url().startsWith("http://127.0.0.1:8766/"))
      external.push(req.url());
  });
  await page.goto("/workbench/");
  await page.getByLabel("访问令牌").fill("invalid");
  await page.getByRole("button", { name: "连接工作台" }).click();
  await expect(page.getByRole("alert")).toContainText("连接失败");
  await page.getByLabel("访问令牌").fill("workbench-test");
  await page.getByRole("button", { name: "连接工作台" }).click();
  await expect(page.getByRole("heading", { name: "运行工作区" })).toBeVisible();
  expect(
    await page.evaluate(() => [
      localStorage.length,
      sessionStorage.length,
      document.cookie,
    ]),
  ).toEqual([0, 0, ""]);
  expect(await page.content()).not.toContain("workbench-test");
  expect(page.url()).not.toContain("token");
  expect(external).toEqual([]);
  await page.reload();
  await expect(page.getByRole("button", { name: "连接工作台" })).toBeVisible();
});

test("research, context sources, event de-duplication and session navigation", async ({
  page,
  request,
}) => {
  await login(page);
  await start(page, "research");
  await done(page, "cache-a vs cache-b");
  await page.getByRole("tab", { name: "上下文" }).click();
  await page.getByRole("button", { name: "读取来源清单" }).first().click();
  await expect(page.locator(".source").first()).toBeVisible();
  await expect(page.locator(".context-card").first()).toContainText("预算");
  await page.getByRole("tab", { name: "事件", exact: true }).click();
  const count = await page.locator(".event").count(),
    before = await controls(request);
  await page.getByRole("button", { name: "重连事件" }).click();
  await expect(page.locator(".connection")).toHaveText("已同步");
  expect(await page.locator(".event").count()).toBe(count);
  expect((await controls(request)).models).toEqual(before.models);
  await page.getByRole("button", { name: "＋  新任务" }).click();
  await page.locator(".session").first().click();
  await page.getByRole("tab", { name: "概览" }).click();
  await done(page, "cache-a vs cache-b");
});

test("assistant persists actual memory tool result", async ({ page }) => {
  await login(page);
  await start(page, "assistant");
  await done(page, "偏好已保存");
  await expect(page.locator(".operation")).toContainText("memory.remember");
  await page.getByRole("tab", { name: "事件", exact: true }).click();
  await expect(
    page.locator(".event").filter({ hasText: "memory.changed" }),
  ).toBeVisible();
});

test("operations input and approve original plan writes exactly once", async ({
  page,
  request,
}) => {
  const before = await controls(request);
  await login(page);
  await start(page, "operations");
  await page.getByLabel("补充输入").fill("item-01");
  await page.getByRole("button", { name: "提交输入" }).click();
  await expect(page.getByRole("button", { name: "批准原计划" })).toBeVisible();
  expect((await controls(request)).writes.length).toBe(before.writes.length);
  await page.getByRole("button", { name: "批准原计划" }).click();
  await done(page, "事项处理完成");
  const after = await controls(request);
  expect(after.writes.length).toBe(before.writes.length + 1);
  await page.getByRole("button", { name: "重连事件" }).click();
  await expect(page.locator(".connection")).toHaveText("已同步");
  expect((await controls(request)).writes).toEqual(after.writes);
});

test("refusal does not execute the write", async ({ page, request }) => {
  const before = await controls(request);
  await login(page);
  await start(page, "operations");
  await page.getByLabel("补充输入").fill("item-01");
  await page.getByRole("button", { name: "提交输入" }).click();
  await page.getByRole("button", { name: "拒绝", exact: true }).click();
  await done(page, "已拒绝，未执行写入");
  expect((await controls(request)).writes).toEqual(before.writes);
});

test("unknown write, verifier, and manual reconciliation do not repeat effect", async ({
  page,
  request,
}) => {
  const before = await controls(request);
  await login(page);
  await start(page, "operations", "unknown");
  await page.getByLabel("补充输入").fill("item-01");
  await page.getByRole("button", { name: "提交输入" }).click();
  await page.getByRole("button", { name: "批准原计划" }).click();
  await expect(
    page.getByRole("button", { name: "调用原核查器" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "调用原核查器" }).click();
  await expect(page.getByRole("status")).toContainText(
    "synthetic local write log",
  );
  await page.getByLabel("显示正文", { exact: true }).uncheck();
  await expect(page.locator('[role="status"]')).toHaveText("");
  await page.getByLabel("显示正文", { exact: true }).check();
  await page.getByLabel("核查结论").selectOption("applied");
  await page.getByLabel("核查依据").fill("local counted write");
  await page.getByLabel("核查结果 JSON").fill('"written"');
  await page.getByRole("button", { name: "提交核查" }).click();
  await done(page, "事项处理完成");
  expect((await controls(request)).writes.length).toBe(
    before.writes.length + 1,
  );
});

test("failed run and explicit saved-response recovery", async ({
  page,
  request,
}) => {
  await login(page);
  await start(page, "research", "fail");
  await expect(page.locator(".status.failed")).toBeVisible();
  await page.getByRole("button", { name: "＋  新任务" }).click();
  await start(page, "recovery");
  await expect(
    page.getByRole("button", { name: "显式恢复原运行" }),
  ).toBeVisible();
  const before = await controls(request);
  await page.getByRole("button", { name: "显式恢复原运行" }).click();
  await done(page, "saved final");
  expect((await controls(request)).models.recovery).toBe(
    before.models.recovery,
  );
});

test("automatic SSE reconnect and explicit cancellation", async ({
  page,
  request,
}) => {
  let interrupted = false;
  const cursors: string[] = [];
  await page.route("**/v1/runs/*/events?*", async (route) => {
    cursors.push(
      new URL(route.request().url()).searchParams.get("after") || "",
    );
    if (!interrupted) {
      interrupted = true;
      await route.abort();
    } else await route.continue();
  });
  await login(page);
  await start(page, "research", "slow");
  await expect(page.locator(".connection")).toHaveText("连接中断，等待重连");
  await expect.poll(() => cursors.length).toBeGreaterThan(1);
  await page.getByRole("button", { name: "取消运行", exact: true }).click();
  await expect(page.locator(".status.cancelled")).toBeVisible();
  expect(interrupted).toBe(true);
  expect(cursors.every((x) => x.includes(":"))).toBe(true);
  const after = await controls(request);
  await page.getByRole("button", { name: "重连事件" }).click();
  await expect(page.locator(".connection")).toHaveText("已同步");
  expect((await controls(request)).models).toEqual(after.models);
});

test("lost start response retries same request key instead of running twice", async ({
  page,
  request,
}) => {
  const before = await controls(request);
  let lost = false;
  const bodies: string[] = [];
  await page.route("**/v1/runs", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    bodies.push(route.request().postData()!);
    if (!lost) {
      lost = true;
      await route.fetch();
      await route.abort();
    } else await route.continue();
  });
  await login(page);
  await start(page, "research");
  await page.getByRole("button", { name: "重试同一提交" }).click();
  await done(page, "cache-a vs cache-b");
  expect(bodies[0]).toBe(bodies[1]);
  expect((await controls(request)).models.research).toBe(
    before.models.research + 2,
  );
});

test("revocation purges existing content and preview names; other scope sees no session", async ({
  page,
  request,
}) => {
  await login(page);
  await start(page, "research");
  await done(page, "cache-a vs cache-b");
  const priorSession = await page
    .locator(".session.selected .session-name")
    .innerText();
  await controls(request, { denied: ["view_content"] });
  await page.getByRole("button", { name: "重连事件" }).click();
  await expect(page.getByRole("status")).toContainText("已清除当前正文");
  expect(await page.content()).not.toContain("cache-a vs cache-b");
  await expect(page.getByLabel("显示正文", { exact: true })).not.toBeChecked();
  await controls(request, { denied: [] });
  await page.getByRole("button", { name: "断开连接" }).click();
  await login(page, "other", false);
  await expect(page.locator(".session")).toHaveCount(0);
  await expect(page.getByLabel("会话名称")).not.toHaveValue(priorSession);
});

test("untrusted text and HTML/SVG files are escaped and downloaded, never executed", async ({
  page,
}) => {
  await login(page);
  await page
    .getByLabel("Agent", { exact: true })
    .selectOption(JSON.stringify(["research", "1"]));
  await page.getByLabel("会话名称").fill(crypto.randomUUID());
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" onload="window.pwned=1"><script>window.pwned=1</script></svg>';
  await page.getByLabel("上传文件").setInputFiles({
    name: "unsafe.svg",
    mimeType: "image/svg+xml",
    buffer: Buffer.from(svg),
  });
  await page.getByRole("button", { name: "暂存文件" }).click();
  await expect(page.getByText("已暂存：", { exact: false })).toBeVisible();
  await page
    .getByLabel("任务", { exact: true })
    .fill('<img src=x onerror="window.pwned=1">');
  await page.getByLabel("显示正文", { exact: true }).check();
  await page.getByRole("button", { name: "开始运行" }).click();
  await done(page, "cache-a vs cache-b");
  await page.getByRole("tab", { name: "文件", exact: true }).click();
  await page.getByRole("button", { name: "读取输入清单" }).click();
  await page.getByRole("button", { name: "读取文件信息" }).click();
  await expect(page.getByRole("heading", { name: "unsafe.svg" })).toBeVisible();
  await expect(page.getByRole("button", { name: "预览 PNG" })).toHaveCount(0);
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载附件" }).click();
  expect((await download).suggestedFilename()).toBe("unsafe.svg");
  expect(await page.evaluate(() => (window as any).pwned)).toBeUndefined();
  await expect(page.locator("iframe, object, embed")).toHaveCount(0);
  await page.getByLabel("显示正文", { exact: true }).uncheck();
  await expect(page.getByRole("heading", { name: "unsafe.svg" })).toHaveCount(
    0,
  );
});

test("real Docker upload, original approval, CSV/PNG after destroy and no repeat on preview", async ({
  page,
  request,
}) => {
  const config = await controls(request);
  if (process.env.DROV_REQUIRE_DOCKER === "1") expect(config.docker).toBe(true);
  test.skip(!config.docker, "Explicit local Docker config required");
  await login(page);
  await page
    .getByLabel("Agent", { exact: true })
    .selectOption(JSON.stringify(["csv-sandbox", "1"]));
  await page.getByLabel("会话名称").fill(crypto.randomUUID());
  await page.getByLabel("任务", { exact: true }).fill("calculate");
  await page.getByLabel("上传文件").setInputFiles({
    name: "numbers.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("value\n12\n18\n30\n"),
  });
  await page.getByRole("button", { name: "暂存文件" }).click();
  await expect(page.getByText("已暂存：", { exact: false })).toBeVisible();
  await page.getByLabel("显示正文", { exact: true }).check();
  await page.getByRole("button", { name: "开始运行" }).click();
  await expect(page.getByRole("button", { name: "批准原计划" })).toBeVisible();
  await page.getByRole("button", { name: "批准原计划" }).click();
  await done(page, "Actual sum is 60");
  await expect(page.locator(".execution-fact")).toContainText(
    "清理：已确认销毁",
  );
  const before = await controls(request);
  await page.getByRole("tab", { name: "文件", exact: true }).click();
  for (const card of await page.locator(".file-card").all()) {
    await card.getByRole("button", { name: "读取文件信息" }).click();
    await expect(card.getByRole("heading")).toBeVisible();
  }
  const chart = page
    .locator(".file-card")
    .filter({ has: page.getByRole("heading", { name: "chart.png" }) });
  await chart.getByRole("button", { name: "预览 PNG" }).click();
  await expect(chart.locator("img")).toBeVisible();
  expect(
    await chart
      .locator("img")
      .evaluate((img: HTMLImageElement) => [
        img.naturalWidth,
        img.naturalHeight,
      ]),
  ).toEqual([120, 80]);
  const csv = page
    .locator(".file-card")
    .filter({ has: page.getByRole("heading", { name: "summary.csv" }) });
  const downloaded = page.waitForEvent("download");
  await csv.getByRole("button", { name: "下载附件" }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe("summary.csv");
  expect(await readFile((await download.path())!, "utf8")).toBe(
    "count,sum,mean\n3,60,20.0\n",
  );
  expect((await controls(request)).active_leases).toBe(0);
  expect((await controls(request)).models).toEqual(before.models);
  await page.screenshot({
    path: "test-results/docker-workbench.png",
    fullPage: true,
  });
  await controls(request, { denied: ["view_content"] });
  await chart.getByRole("button", { name: "下载附件" }).click();
  await expect(page.getByRole("status")).toContainText("已清除当前正文");
  await expect(page.getByRole("heading", { name: "chart.png" })).toHaveCount(0);
  await expect(page.locator(".preview")).toHaveCount(0);
});

test("responsive workspace fits a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/mobile-workbench.png",
    fullPage: true,
  });
});

test("lost interaction receipt retries original response without a second write", async ({
  page,
  request,
}) => {
  const before = await controls(request);
  await login(page);
  await start(page, "operations");
  await page.getByLabel("补充输入").fill("item-01");
  await page.getByRole("button", { name: "提交输入" }).click();
  let first = true;
  const bodies: string[] = [];
  await page.route("**/v1/runs/*/responses", async (route) => {
    bodies.push(route.request().postData()!);
    if (first) {
      first = false;
      await route.fetch();
      await route.abort();
    } else await route.continue();
  });
  await page.getByRole("button", { name: "批准原计划" }).click();
  await page.getByRole("button", { name: "重试原响应" }).click();
  await done(page, "事项处理完成");
  expect(bodies[0]).toBe(bodies[1]);
  expect((await controls(request)).writes.length).toBe(
    before.writes.length + 1,
  );
});

test("5xx after acceptance preserves the start key for explicit retry", async ({
  page,
  request,
}) => {
  const before = await controls(request);
  let first = true;
  const bodies: string[] = [];
  await page.route("**/v1/runs", async (route) => {
    bodies.push(route.request().postData()!);
    if (first) {
      first = false;
      await route.fetch();
      await route.fulfill({
        status: 503,
        json: { error: { code: "service_unavailable" } },
      });
    } else await route.continue();
  });
  await login(page);
  await start(page, "research");
  await page.getByRole("button", { name: "重试同一提交" }).click();
  await done(page, "cache-a vs cache-b");
  expect(bodies[0]).toBe(bodies[1]);
  expect((await controls(request)).models.research).toBe(
    before.models.research + 2,
  );
});

test("metadata-only user cannot obtain output and pagehide removes old DOM", async ({
  page,
}) => {
  await login(page, "metadata");
  await start(page, "research", "synthetic", false);
  await expect(page.locator(".status.completed")).toBeVisible();
  await expect(page.locator(".answer")).toHaveCount(0);
  await page.getByLabel("显示正文", { exact: true }).check();
  await expect(page.getByRole("status")).toContainText("已清除当前正文");
  expect(await page.content()).not.toContain("cache-a vs cache-b");
  await page.evaluate(() =>
    window.dispatchEvent(new PageTransitionEvent("pagehide")),
  );
  await expect(page.getByLabel("访问令牌")).toBeVisible();
  await expect(page.locator(".workspace")).toHaveCount(0);
});

test("forged PNG is rejected before browser image presentation", async ({
  page,
}) => {
  await login(page);
  await page.getByLabel("会话名称").fill(crypto.randomUUID());
  await page.getByLabel("上传文件").setInputFiles({
    name: "forged.png",
    mimeType: "image/png",
    buffer: Buffer.from("<svg onload='window.pwned=1'></svg>"),
  });
  await page.getByRole("button", { name: "暂存文件" }).click();
  await expect(page.getByText("已暂存：", { exact: false })).toBeVisible();
  await page.getByLabel("任务", { exact: true }).fill("synthetic");
  await page.getByLabel("显示正文", { exact: true }).check();
  await page.getByRole("button", { name: "开始运行" }).click();
  await done(page, "cache-a vs cache-b");
  await page.getByRole("tab", { name: "文件", exact: true }).click();
  await page.getByRole("button", { name: "读取输入清单" }).click();
  await page.getByRole("button", { name: "读取文件信息" }).click();
  await page.getByRole("button", { name: "预览 PNG" }).click();
  await expect(page.getByRole("status")).toContainText("非有效 PNG");
  await expect(page.locator(".preview")).toHaveCount(0);
});

test("input draft and focus survive durable event catch-up", async ({
  page,
}) => {
  await login(page);
  await start(page, "operations");
  await expect(page.locator(".connection")).toHaveText("已同步");
  const value = page.getByLabel("补充输入");
  await value.fill("preserved draft");
  // Trigger catch-up while the input owns focus (as with an automatic reconnect).
  await page.evaluate(() => {
    Array.from(document.querySelectorAll("button"))
      .find((node) => node.textContent === "重连事件")!
      .click();
  });
  await expect(page.locator(".connection")).toHaveText("已同步");
  await expect(value).toHaveValue("preserved draft");
  await expect(value).toBeFocused();
  await page.getByRole("button", { name: "取消运行", exact: true }).click();
  await expect(page.locator(".status.cancelled")).toBeVisible();
});

test("opaque session names round-trip through the query lookup", async ({
  page,
}) => {
  await login(page);
  const name = "会话/../name?part#" + crypto.randomUUID();
  await page.getByLabel("会话名称").fill(name);
  await page.getByLabel("任务", { exact: true }).fill("synthetic");
  await page.getByLabel("显示正文", { exact: true }).check();
  await page.getByRole("button", { name: "开始运行" }).click();
  await done(page, "cache-a vs cache-b");
  await page.getByRole("button", { name: "＋  新任务" }).click();
  await page.locator(".session").filter({ hasText: name }).click();
  await done(page, "cache-a vs cache-b");
});

test("late verifier response cannot restore private evidence after an identity switch", async ({
  page,
  request,
}) => {
  await login(page);
  await start(page, "operations", "unknown");
  await page.getByLabel("补充输入").fill("item-01");
  await page.getByRole("button", { name: "提交输入" }).click();
  await page.getByRole("button", { name: "批准原计划" }).click();
  let release!: () => void, arrived!: () => void;
  const hold = new Promise<void>((resolve) => {
    release = resolve;
  });
  const started = new Promise<void>((resolve) => {
    arrived = resolve;
  });
  await page.route("**/v1/runs/*/verify", async (route) => {
    const response = await route.fetch();
    arrived();
    await hold;
    await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "调用原核查器" }).click();
  await started;
  await page.getByRole("button", { name: "断开连接" }).click();
  await login(page, "other", false);
  await page.evaluate(() => {
    (window as any).seen = [];
    new MutationObserver(() =>
      (window as any).seen.push(
        document.querySelector('[role="status"]')?.textContent,
      ),
    ).observe(document.querySelector('[role="status"]')!, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  });
  const returned = page.waitForResponse((response) =>
    response.url().endsWith("/verify"),
  );
  release();
  await (await returned).finished();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  expect(
    (await page.evaluate(() => (window as any).seen)).join(" "),
  ).not.toContain("synthetic local write log");
  await expect(page.getByRole("heading", { name: "运行工作区" })).toBeVisible();
  await expect(page.locator(".session")).toHaveCount(0);
  await controls(request, { denied: [] });
});
