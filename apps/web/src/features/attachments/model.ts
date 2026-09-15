// Общие типы вложений — нужны и карточке (F2), и шаблонам (F3), поэтому
// решены здесь, а не в компоненте (SLICE2_SPEC.md §5.1, интерфейс `Owner`).
export type Owner =
  | { kind: 'task'; task_id: number; step_id?: number }
  | { kind: 'template'; name: string };

/** Байты человеку: `Б/КБ/МБ`, как в старой карточке (map-client.md §2.2.4). */
export function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}
