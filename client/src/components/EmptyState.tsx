import type { ReactNode } from "react";

export function EmptyState({
  title,
  children,
  action
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-state__mark">卷</div>
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}

