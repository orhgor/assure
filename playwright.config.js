// @ts-check
const { defineConfig, devices } = require("@playwright/test");

const PORT = process.env.PLAYWRIGHT_PORT || "8801";
const BASE_URL = process.env.ASSURE_BASE_URL || `http://127.0.0.1:${PORT}`;
const LOCAL_HOST =
  /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/?$/i.test(BASE_URL);

/** @type {import('@playwright/test').PlaywrightTestConfig} */
const config = {
  testDir: "./tests/e2e",
  testMatch: "**/*.spec.js",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  use: {
    baseURL: BASE_URL,
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
};

if (LOCAL_HOST) {
  config.webServer = {
    command: `.venv/bin/python scripts/playwright_dev_server.py --port ${PORT}`,
    url: `${BASE_URL}/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  };
}

module.exports = defineConfig(config);
