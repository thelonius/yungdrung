// Живой предпросмотр плана шагов (SLICE2_SPEC.md §5.4 п.4): перезапускается,
// когда `planSignature` меняется — то есть когда `DateField` разобрал новую
// дату (`onParsed`) или сменился режим/состав шагов. Не набор текста: это и
// значит «без своего таймера» — единственный дебаунс уже отработал внутри
// `DateField`.
import { useEffect, useState } from 'react';
import { api } from '@/api/client';
import type { PlanResult } from '@/api/client';
import { planSignature, toPlanIn } from './model';
import type { StepDraft } from './model';

export function usePlan(steps: StepDraft[]): PlanResult | null {
  const [result, setResult] = useState<PlanResult | null>(null);
  const signature = planSignature(steps);

  useEffect(() => {
    let cancelled = false;
    api.POST('/api/v1/tasks/plan', { body: toPlanIn(steps) }).then(({ data }) => {
      if (!cancelled && data) setResult(data);
    });
    return () => { cancelled = true; };
    // `signature` — производное от `steps`, но именно оно решает, когда
    // перезапускать запрос; `steps` в зависимостях дал бы вызов на каждое
    // нажатие клавиши в названии шага, чего быть не должно (п.4).
  }, [signature]);

  return result;
}
