import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";

export function Badge({ value }: { value: string }) {
  const normalized = value.toLowerCase().replaceAll("_", "-");
  return <span className={`badge badge-${normalized}`}>{value.replaceAll("_", " ")}</span>;
}

export function StateIcon({ severity }: { severity: string }) {
  const key = severity.toUpperCase();
  if (["CRITICAL", "BLOCKED", "REJECTED", "FAILED"].includes(key)) return <XCircle aria-hidden />;
  if (["HIGH", "NEEDS_APPROVAL", "PENDING"].includes(key)) return <AlertTriangle aria-hidden />;
  if (["PASS", "APPROVED", "COMPLETED"].includes(key)) return <CheckCircle2 aria-hidden />;
  return <Info aria-hidden />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>;
}

export function ErrorNotice({ error }: { error: unknown }) {
  return (
    <div className="error-notice" role="alert">
      <AlertTriangle aria-hidden />
      <span>{error instanceof Error ? error.message : "请求失败"}</span>
    </div>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json-block">{JSON.stringify(value, null, 2)}</pre>;
}

export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="modal" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => event.stopPropagation()}>
        <header className="modal-header"><h2>{title}</h2><button className="icon-button" onClick={onClose} aria-label="关闭" title="关闭"><X aria-hidden /></button></header>
        {children}
      </section>
    </div>
  );
}
