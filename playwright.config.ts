import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./apps/web/e2e",
  fullyParallel: false,
  timeout: 30_000,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3014",
    headless: true,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure"
  },
  webServer: {
    command: "cmd /c npm.cmd run dev -- --hostname 127.0.0.1 --port 3014",
    cwd: "C:\\projects\\sites\\blueprint-rec-2\\apps\\web",
    url: "http://127.0.0.1:3014",
    reuseExistingServer: true,
    timeout: 120_000,
    env: {
      NEXT_TELEMETRY_DISABLED: "1"
    }
  }
});
