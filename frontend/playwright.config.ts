import { defineConfig, devices } from "@playwright/test";

/** Browser tests against a real backend (11.9). Run: npm run build && npm run e2e */
const port = Number(process.env.E2E_PORT ?? 8765);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
    timezoneId: "Asia/Kolkata", // a non-UTC zone, so time bugs show up
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "bash e2e/server.sh",
    url: `http://127.0.0.1:${port}/readyz`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
