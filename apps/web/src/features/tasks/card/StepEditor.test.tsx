// Регрессия на находку ревью среза 2: `errors[].(start_date|control_date)`
// узла-группы считаются (`errorAt`), но не показывались — ядро отдаёт такую
// ошибку, когда у группы остались даты листа («Даты ставятся подшагам, не
// группе», domain/steps_plan.py).
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { FieldError } from '@/api/client';
import { newLeaf } from './model';
import type { DraftStep } from './model';
import { StepEditor } from './StepEditor';

function groupNode(): DraftStep {
  return { ...newLeaf(), mode: 'par', steps: [newLeaf()] };
}

describe('StepEditor — ошибки группы', () => {
  it('показывает ошибку start_date и control_date узла-группы', () => {
    const errors: FieldError[] = [
      { field: 'steps[0].start_date', error: 'Даты ставятся подшагам, не группе' },
      { field: 'steps[0].control_date', error: 'Даты ставятся подшагам, не группе' },
    ];
    render(
      <StepEditor
        node={groupNode()}
        indices={[0]}
        taskId={1}
        errors={errors}
        onPatch={() => {}}
        onAddSubstep={() => {}}
        onSplit={() => {}}
        onAttachmentsChanged={() => {}}
      />,
    );
    expect(screen.getAllByText('Даты ставятся подшагам, не группе')).toHaveLength(2);
  });
});
