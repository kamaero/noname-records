import { useEffect, useRef } from "react";
import { Button } from "../../../ui";

type DeleteBookConfirmProps = {
  title: string;
  pending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

/** Inline confirmation for «Удалить книгу» — words and two buttons, no window.confirm. */
export function DeleteBookConfirm({ title, pending, onConfirm, onCancel }: DeleteBookConfirmProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);
  return (
    <div className="hub-confirm" role="group" aria-label="Подтверждение удаления">
      <p>
        Удалить книгу «{title}» вместе с главами, кастом и разметкой? Отменить это нельзя.
      </p>
      <div className="hub-confirm-actions">
        <Button variant="danger" size="sm" loading={pending} onClick={onConfirm}>
          Удалить книгу
        </Button>
        <Button ref={cancelRef} variant="ghost" size="sm" disabled={pending} onClick={onCancel}>
          Отмена
        </Button>
      </div>
    </div>
  );
}
