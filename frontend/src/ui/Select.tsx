import * as Popover from "@radix-ui/react-popover";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

export interface SelectOption<T extends string> {
  value: T;
  label: string;
  hint?: string;
}

interface Props<T extends string> {
  value: T;
  options: SelectOption<T>[];
  onChange(value: T): void;
  compact?: boolean;
  searchable?: boolean;
  ariaLabel: string;
  placeholder?: string;
  className?: string;
}

/** A themed listbox dropdown. Native select popups are drawn by the OS and ignore the theme. */
export function Select<T extends string>({
  value,
  options,
  onChange,
  compact,
  searchable,
  ariaLabel,
  placeholder,
  className,
}: Props<T>) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matches = needle ? options.filter((o) => o.label.toLowerCase().includes(needle)) : options;
    return matches.slice(0, 300);
  }, [options, query]);

  const current = options.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(Math.max(0, options.findIndex((o) => o.value === value)));
  }, [open, options, value]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  function commit(index: number) {
    const option = filtered[index];
    if (!option) return;
    onChange(option.value);
    setOpen(false);
  }

  function onKey(event: KeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => Math.min(filtered.length - 1, i + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (event.key === "Home") {
      setActive(0);
    } else if (event.key === "End") {
      setActive(filtered.length - 1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      commit(active);
    }
  }

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          type="button"
          className={`select-trigger ${compact ? "compact" : ""} ${className ?? ""}`}
          aria-label={ariaLabel}
          aria-haspopup="listbox"
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" && !open) {
              event.preventDefault();
              setOpen(true);
            }
          }}
        >
          <span className="value">{current?.label ?? placeholder ?? "Select"}</span>
          <svg className="caret" viewBox="0 0 10 10" aria-hidden>
            <path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
          </svg>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className="popover" align="start" sideOffset={4} onKeyDown={onKey}>
          {searchable && (
            <input
              className="field search"
              autoFocus
              placeholder="Filter"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setActive(0);
              }}
            />
          )}
          <div
            className="listbox"
            role="listbox"
            aria-label={ariaLabel}
            ref={listRef}
            tabIndex={searchable ? -1 : 0}
            autoFocus={!searchable}
            aria-activedescendant={`opt-${active}`}
          >
            {filtered.map((option, index) => (
              <div
                key={option.value}
                id={`opt-${index}`}
                data-index={index}
                className="option"
                role="option"
                aria-selected={option.value === value}
                data-active={index === active}
                onMouseMove={() => setActive(index)}
                onClick={() => commit(index)}
              >
                <span>{option.label}</span>
                {option.hint && <span className="hint">{option.hint}</span>}
              </div>
            ))}
            {filtered.length === 0 && <div className="option faint">No matches</div>}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
