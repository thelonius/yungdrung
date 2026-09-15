import { expect, test } from '@playwright/test';

// Приёмка среза 2 (SLICE2_SPEC.md §5.8): задача одной строкой → карточка →
// отметка из карточки, ни одного `page.click`/мыши — тот же стиль, что и
// `morning.spec.ts`, только на другом пути (быстрый ввод, `/задача/:id`).
test('задача одной строкой + карточка + отметка из карточки', async ({ page }) => {
  let taskId: number | null = null;
  let quickPosts = 0;
  let taskGetsAfterMark = 0;
  let countTaskGets = false;
  const markDurations: number[] = [];

  page.on('request', (req) => {
    const url = req.url();
    if (req.method() === 'POST' && url.includes('/api/v1/tasks/quick')) quickPosts += 1;
    if (req.method() === 'GET' && taskId !== null && url.endsWith(`/api/v1/tasks/${taskId}`) && countTaskGets) {
      taskGetsAfterMark += 1;
    }
  });
  page.on('requestfinished', async (req) => {
    if (req.url().includes('/mark')) {
      const t = req.timing();
      markDurations.push(t.responseEnd - t.requestStart);
    }
  });

  await page.goto('/');
  await page.keyboard.press('a');
  await page.getByTestId('quick-input').fill('позвонить Василию сегодня');
  await expect(page.getByTestId('quick-label')).toContainText('сегодня');

  await page.keyboard.press('Enter');
  await expect(page.getByTestId('quick-done')).toContainText('Создана «позвонить Василию»');

  // Второй Enter на опустевшем поле — открыть только что созданную карточку.
  // `page.url()` отдаёт путь percent-encoded (кириллица не ASCII), поэтому
  // сверяем декодированный `pathname`, а не сырую строку через `toHaveURL`.
  await page.keyboard.press('Enter');
  await expect.poll(() => decodeURIComponent(new URL(page.url()).pathname)).toMatch(/^\/задача\/\d+$/);
  taskId = Number(decodeURIComponent(new URL(page.url()).pathname).match(/\/задача\/(\d+)$/)![1]);

  await expect(page.locator('#task-title')).toHaveValue('позвонить Василию');
  await expect(page.getByTestId('card-step')).toHaveCount(1);
  await expect(page.getByTestId('card-step')).toHaveAttribute('aria-selected', 'true');

  countTaskGets = true;
  await page.keyboard.press('d');
  await expect(page.locator('[data-status="done"]')).toContainText('закрыта');

  await expect(page.getByText('1 из 1')).toBeVisible();
  // Заголовок тоста — `getByText` цепляет ещё и дублирующий live-region
  // анонс Radix Toast (aria-live), поэтому берём его по `data-testid`.
  await expect(page.getByTestId('toast')).toContainText('закрыта целиком');
  await expect.poll(() => markDurations.length).toBe(1);
  console.log(`отметка из карточки, мс: ${markDurations[0].toFixed(0)}`);
  expect(markDurations[0]).toBeLessThan(100); // тот же порог `/mark`, что в morning.spec.ts

  await expect.poll(() => taskGetsAfterMark).toBeGreaterThanOrEqual(1);
  expect(taskGetsAfterMark).toBeLessThanOrEqual(2);

  await page.keyboard.press('u');
  await expect(page.getByText('0 из 1')).toBeVisible();

  expect(quickPosts).toBe(1);

  // Уборка: сервер общий на весь прогон (`workers: 1`, один процесс
  // `e2e/serve.py`), заведённая здесь задача не должна остаться в ленте
  // для гипотетических тестов после этого — независимо от порядка спеков.
  await page.request.delete(`/api/v1/tasks/${taskId}`);
});
