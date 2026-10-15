import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  retries: 0,
  timeout: 30000,
  reporter: [["list"], ["json", { outputFile: "test-results/results.json" }]],
  use: {
    baseURL: "http://127.0.0.1:8766",
    browserName: "chromium",
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
    trace: "off",
  },
  webServer: {
    command: `${process.env.DROV_TEST_PYTHON || ".venv/bin/python"} -m tests.workbench_server --port 8766`,
    cwd: "..",
    url: "http://127.0.0.1:8766/workbench/",
    reuseExistingServer: false,
    timeout: 15000,
    stdout: "ignore",
    stderr: "pipe",
  },
});
