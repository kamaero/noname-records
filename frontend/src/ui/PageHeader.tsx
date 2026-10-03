import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Icon } from "../components/Icon";

type PageHeaderProps = {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  back?: { to: string; label: string };
  className?: string;
};

export function PageHeader({ title, subtitle, actions, back, className }: PageHeaderProps) {
  return (
    <header className={["ui-pagehead", className || ""].filter(Boolean).join(" ")}>
      <div className="ui-pagehead-main">
        {back ? (
          <Link className="ui-pagehead-back" to={back.to}>
            <Icon name="back" />
            {back.label}
          </Link>
        ) : null}
        <h1>{title}</h1>
        {subtitle ? <p className="ui-pagehead-sub">{subtitle}</p> : null}
      </div>
      {actions ? <div className="ui-pagehead-actions">{actions}</div> : null}
    </header>
  );
}
