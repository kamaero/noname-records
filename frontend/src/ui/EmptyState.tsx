import type { ReactNode } from "react";
import { Icon, type IconName } from "../components/Icon";

type EmptyStateProps = {
  /** one sentence: what this is and what to do */
  text: ReactNode;
  /** one action (a Button / LinkButton) */
  action?: ReactNode;
  icon?: IconName;
  className?: string;
};

export function EmptyState({ text, action, icon, className }: EmptyStateProps) {
  return (
    <div className={["ui-empty", className || ""].filter(Boolean).join(" ")}>
      {icon ? <Icon name={icon} /> : null}
      <p>{text}</p>
      {action}
    </div>
  );
}
