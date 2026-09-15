import { defineConfig } from '@playwright/test';

// Приёмка срезов 1c и 2 (REFACTOR.md, SLICE2_SPEC.md §5.8): «утро за десять
// нажатий» и «задача одной строкой + карточка». Сервер — настоящий FastAPI с
// собранным dist и общим сидом из e2e/serve.py на все спеки (десять шагов на
// сегодня и десять тысяч закрытых задач). Спеки бьют по одному и тому же
// стору — `workers: 1` не даёт им гоняться параллельно: `quick-card` заводит
// свою задачу через `/api/v1/tasks/quick`, и вперемешку с `morning` она
// молча меняла бы число строк ленты, которое та пиннит точно.
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:8797', headless: true },
  webServer: {
    command: 'python3 e2e/serve.py',
    url: 'http://127.0.0.1:8797/api/v1/reasons',
    timeout: 120_000,
    reuseExistingServer: false,
  },
});
