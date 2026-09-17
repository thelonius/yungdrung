import { expect, test } from '@playwright/test';

// Критерий готовности среза 1c из REFACTOR.md: утренний разбор десяти шагов —
// десять нажатий, без мыши, ни одного ожидания дольше 100 мс на отметку.
test('утро: десять шагов за десять нажатий d, без мыши', async ({ page }) => {
  const durations: number[] = [];
  page.on('requestfinished', async (req) => {
    if (req.url().includes('/mark')) {
      const t = req.timing();
      durations.push(t.responseEnd - t.requestStart);
    }
  });

  await page.goto('/');
  await expect(page.getByTestId('feed-row')).toHaveCount(10);
  await expect(page.getByTestId('feed-row').first()).toHaveAttribute('aria-selected', 'true');

  for (let left = 10; left > 0; left--) {
    await page.keyboard.press('d');
    await expect(page.getByTestId('feed-row')).toHaveCount(left - 1);
  }

  await expect(page.getByTestId('empty')).toContainText('На сегодня всё');
  await expect.poll(() => durations.length).toBe(10);
  const slowest = Math.max(...durations);
  console.log(`отметки, мс: ${durations.map((d) => d.toFixed(0)).join(' ')} (max ${slowest.toFixed(0)})`);
  expect(slowest).toBeLessThan(100);

  // Промах отменяется клавишей, пока виден тост.
  await page.keyboard.press('u');
  await expect(page.getByTestId('feed-row')).toHaveCount(1);
});
