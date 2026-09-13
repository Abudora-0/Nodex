import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { useStore } from "../state/store";

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  const dismiss = useStore((s) => s.dismissToast);
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast ${toast.tone}`}>
          <div className="msg">{toast.text}</div>
          {toast.action && (
            <button
              className="btn sm"
              onClick={() => {
                toast.action!.run();
                dismiss(toast.id);
              }}
            >
              {toast.action.label}
            </button>
          )}
          <button className="btn ghost sm icon" aria-label="Dismiss" onClick={() => dismiss(toast.id)}>
            <Icon name="close" />
          </button>
        </div>
      ))}
    </div>
  );
}

interface ConfirmProps {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  tone?: "accent" | "danger";
  onConfirm(): void;
  onOpenChange(open: boolean): void;
}

export function Confirm({ open, title, body, confirmLabel, tone = "accent", onConfirm, onOpenChange }: ConfirmProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="dialog">
          <Dialog.Title asChild>
            <h3>{title}</h3>
          </Dialog.Title>
          <Dialog.Description asChild>
            <p>{body}</p>
          </Dialog.Description>
          <div className="actions">
            <Dialog.Close asChild>
              <button className="btn">Cancel</button>
            </Dialog.Close>
            <button
              className={`btn ${tone}`}
              autoFocus
              onClick={() => {
                onConfirm();
                onOpenChange(false);
              }}
            >
              {confirmLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

const PATHS: Record<string, string> = {
  close: "M4 4l8 8M12 4l-8 8",
  folder: "M2 4.5h4l1.5 1.5H14v6.5H2z",
  search: "M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM11 11l3 3",
  sun: "M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6 13 13M3 13l1.4-1.4M11.6 4.4 13 3",
  moon: "M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z",
  undo: "M5 3 2 6l3 3M2.5 6H10a4 4 0 0 1 0 8H6",
  expand: "M2 6V2h4M14 6V2h-4M2 10v4h4M14 10v4h-4",
  collapse: "M6 2v4H2M10 2v4h4M6 14v-4H2M10 14v-4h4",
  refresh: "M13 3v3.5H9.5M3 13V9.5h3.5M12.4 6.5A5 5 0 0 0 3.5 5M3.6 9.5A5 5 0 0 0 12.5 11",
  plus: "M8 3v10M3 8h10",
  trash: "M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.7 9h5.6l.7-9",
  map: "M2 3.5 6 2l4 1.5L14 2v10.5L10 14l-4-1.5L2 14zM6 2v10.5M10 3.5V14",
  back: "M10 3 5 8l5 5",
};

export function Icon({ name, size = 14 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d={PATHS[name] ?? ""} />
    </svg>
  );
}

export function Glyph() {
  return (
    <svg className="glyph" viewBox="0 0 24 24" aria-hidden>
      <circle cx="5" cy="6" r="2.6" fill="var(--save)" />
      <circle cx="19" cy="6" r="2.6" fill="none" stroke="var(--text)" strokeWidth="1.6" />
      <rect x="15.6" y="15.6" width="6" height="6" transform="rotate(45 18.6 18.6)" fill="var(--choice)" />
      <path d="M5 8.6v4.4c0 3 2 5 5 5h4.2M7.4 6H16.4" fill="none" stroke="var(--text)" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

export function timeAgo(seconds: number): string {
  const diff = Date.now() / 1000 - seconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(seconds * 1000).toLocaleDateString();
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function baseName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}

export function display(value: unknown): string {
  if (value === null || value === undefined) return "none";
  if (typeof value === "string") return JSON.stringify(value);
  return String(value);
}
