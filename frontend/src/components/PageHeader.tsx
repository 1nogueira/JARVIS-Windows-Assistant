import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function PageHeader({ icon: Icon, eyebrow, title, description, actions }: { icon: LucideIcon; eyebrow?: string; title: string; description: string; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <span className="page-icon"><Icon size={19} /></span>
      <div>{eyebrow && <small>{eyebrow}</small>}<h1>{title}</h1><p>{description}</p></div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

