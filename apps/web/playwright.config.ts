import { defineConfig } from '@playwright/test';

// Приёмка среза 1c (REFACTOR.md): «утро за десять нажатий». Сервер — настоящий
// FastAPI с собранным dist и сидом из e2e/serve.py: десять шагов на сегодня и
// десять тысяч закрытых задач, чтобы время ответа мерилось на целевом объёме.
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: 0,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:8797', headless: true },
  webServer: {
    command: 'python3 e2e/serve.py',
    url: 'http://127.0.0.1:8797/api/v1/reasons',
    timeout: 120_000,
    reuseExistingServer: false,
  },
});
