// Регрессия на находку ревью среза 2: строка шага обязана нести литеральные
// классы `is-closed`/`is-overdue`/`is-active`/`is-group`/`is-substep`
// (SLICE2_SPEC.md §5.5 п.5, `map-client.md` §1, перенос из `task.js:238-245`)
// поверх хешированных классов CSS-module.
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { newLeaf } from './model';
import type { DraftStep } from './model';
import { StepChecklist } from './StepChecklist';

const noop = () => {};

function baseProps(nodes: DraftStep[]) {
  return {
    nodes, taskId: 1, focusedPath: null, openPaths: new Set<string>(), errors: [],
    onFocus: noop, onToggleOpen: noop, onMarkDone: noop, onReopen: noop, onOpenDialog: noop,
    onPatch: noop, onAddSubstep: noop, onSplit: noop, onMove: noop, onAttachmentsChanged: noop,
  };
}

describe('StepChecklist — классы строки', () => {
  it('is-closed на закрытом шаге', () => {
    const closed: DraftStep = { ...newLeaf(), id: 1, title: 'Готово', status: 'done', closed: true };
    render(<StepChecklist {...baseProps([closed])} />);
    expect(screen.getByTestId('card-step')).toHaveClass('is-closed');
  });

  it('is-overdue и is-active на открытом просроченном шаге', () => {
    const overdue: DraftStep = { ...newLeaf(), id: 1, title: 'Опаздывает', state: 'overdue', active: true };
    render(<StepChecklist {...baseProps([overdue])} />);
    const row = screen.getByTestId('card-step');
    expect(row).toHaveClass('is-overdue');
    expect(row).toHaveClass('is-active');
  });

  it('is-group на группе, is-substep на подшагах', () => {
    const group: DraftStep = { ...newLeaf(), id: 1, title: 'Группа', mode: 'par', steps: [
      { ...newLeaf(), id: 2, title: 'Подшаг' },
    ] };
    render(<StepChecklist {...baseProps([group])} />);
    const rows = screen.getAllByTestId('card-step');
    expect(rows[0]).toHaveClass('is-group');
    expect(rows[1]).toHaveClass('is-substep');
  });
});
