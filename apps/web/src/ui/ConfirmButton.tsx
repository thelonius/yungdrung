// Двухшаговая кнопка вместо `confirm()` (Р15 SLICE2_SPEC.md, правило
// целевого UX №6: никаких блокирующих диалогов). Первый клик вооружает
// кнопку и показывает «Точно? Enter / Esc», второй клик или Enter
// подтверждают; Esc, клик мимо или уход фокуса снимают взвод без действия.
import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, MouseEvent } from 'react';

type Props = {
  label: string;
  confirmLabel?: string;
  onConfirm: () => void;
  className?: string;
  disabled?: boolean;
};

export function ConfirmButton({ label, confirmLabel = 'Точно? Enter / Esc', onConfirm, className, disabled }: Props) {
  const [armed, setArmed] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (armed) ref.current?.focus();
  }, [armed]);

  // Клик в любое другое место страницы снимает взвод — иначе кнопка навсегда
  // остаётся «на боевом», если человек передумал и ушёл мышью, а не с клавиатуры.
  useEffect(() => {
    if (!armed) return;
    const onDocPointerDown = (e: globalThis.MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setArmed(false);
    };
    document.addEventListener('mousedown', onDocPointerDown);
    return () => document.removeEventListener('mousedown', onDocPointerDown);
  }, [armed]);

  function onClick(e: MouseEvent<HTMLButtonElement>) {
    e.preventDefault();
    if (!armed) { setArmed(true); return; }
    setArmed(false);
    onConfirm();
  }

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>) {
    if (e.key === 'Escape' && armed) { e.preventDefault(); setArmed(false); }
  }

  return (
    <button
      type="button"
      ref={ref}
      className={`${armed ? 'primary' : ''} ${className ?? ''}`}
      disabled={disabled}
      aria-pressed={armed}
      onClick={onClick}
      onKeyDown={onKeyDown}
      onBlur={() => setArmed(false)}
    >
      {armed ? confirmLabel : label}
    </button>
  );
}
