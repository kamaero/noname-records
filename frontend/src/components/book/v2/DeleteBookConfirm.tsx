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
    <div className="hub-confirm" role="group" aria-label="Confirm deletion">
      <p>
        Delete book «{title}» with its chapters, cast and annotations? This cannot be undone.
      </p>
      <div className="hub-confirm-actions">
        <Button variant="danger" size="sm" loading={pending} onClick={onConfirm}>
          Delete book
        </Button>
        <Button ref={cancelRef} variant="ghost" size="sm" disabled={pending} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
