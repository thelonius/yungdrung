// Очередь файлов до сохранения (SLICE2_SPEC.md §5.6/§5.7): чисто локальное
// состояние, без сети — добавление и удаление меняют `files` через `onChange`.
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { PendingFiles } from './PendingFiles';

describe('PendingFiles', () => {
  it('добавление файла через инпут отдаёт полный список наружу', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<PendingFiles files={[]} onChange={onChange} />);

    const file = new File(['x'], 'план.docx');
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);

    expect(onChange).toHaveBeenCalledWith([file]);
  });

  it('убрать файл вызывает onChange без него', async () => {
    const a = new File(['a'], 'a.png');
    const b = new File(['b'], 'b.png');
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<PendingFiles files={[a, b]} onChange={onChange} />);

    expect(screen.getByText('a.png')).toBeInTheDocument();
    const buttons = screen.getAllByTitle('Убрать файл');
    await user.click(buttons[0]);

    expect(onChange).toHaveBeenCalledWith([b]);
  });
});
