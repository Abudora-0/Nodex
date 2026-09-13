import { useVirtualizer } from "@tanstack/react-virtual";
import fuzzysort from "fuzzysort";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { Variable } from "../api/types";
import { useStore } from "../state/store";
import { Kbd } from "../ui/bits";
import { Select } from "../ui/Select";

type Row = { kind: "group"; label: string } | { kind: "var"; variable: Variable; match?: ReactNode };

const ROW_H = 32;
const GROUP_H = 28;

function isNumeric(kind: string) {
  return kind === "int" || kind === "float" || kind === "number";
}

function isBool(variable: Variable) {
  return variable.kind === "bool" || typeof variable.value === "boolean";
}

type FuzzyResult = NonNullable<ReturnType<typeof fuzzysort.single>>;
type Scope = "editable" | "all" | "edited";

function highlight(result: FuzzyResult): ReactNode {
  return result.highlight((m: string, i: number) => <mark key={i}>{m}</mark>);
}

export function VariableGrid() {
  const data = useStore((s) => s.variables);
  const busy = useStore((s) => s.variablesBusy);
  const error = useStore((s) => s.variablesError);
  const edits = useStore((s) => s.edits);
  const stageEdit = useStore((s) => s.stageEdit);
  const writeEdits = useStore((s) => s.writeEdits);
  const discardEdits = useStore((s) => s.discardEdits);
  const includeInternal = useStore((s) => s.includeInternal);
  const setIncludeInternal = useStore((s) => s.setIncludeInternal);
  const gridJump = useStore((s) => s.gridJump);

  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<Scope>("editable");
  const [cursor, setCursor] = useState(0);
  const [flash, setFlash] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const engine = data?.engine ?? "renpy";
  const hasGroups = !!data?.variables.some((v) => v.group);

  const rows = useMemo<Row[]>(() => {
    if (!data) return [];
    let pool = data.variables;
    if (scope === "editable") pool = pool.filter((v) => v.editable);
    if (scope === "edited") pool = pool.filter((v) => v.name in edits);

    let matched: { variable: Variable; match?: ReactNode }[];
    const needle = query.trim();
    if (needle) {
      const results = fuzzysort.go(needle, pool, { keys: ["short_name", "name", "preview"], threshold: 0.35, limit: 2000 });
      matched = results.map((r) => ({ variable: r.obj, match: r[0] && r[0].score > 0 ? highlight(r[0]) : undefined }));
    } else {
      matched = pool.map((variable) => ({ variable }));
    }

    if (!hasGroups || needle) return matched.map((m) => ({ kind: "var" as const, ...m }));
    const out: Row[] = [];
    let group = "";
    for (const m of matched) {
      const g = m.variable.group ?? "";
      if (g !== group) {
        group = g;
        out.push({ kind: "group", label: g || "other" });
      }
      out.push({ kind: "var", ...m });
    }
    return out;
  }, [data, scope, query, edits, hasGroups]);

  const virtual = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => (rows[i]?.kind === "group" ? GROUP_H : ROW_H),
    overscan: 14,
  });

  const [pendingJump, setPendingJump] = useState<string | null>(null);

  useEffect(() => {
    if (!gridJump) return;
    setQuery("");
    setScope("all");
    setPendingJump(gridJump);
    useStore.setState({ gridJump: null });
  }, [gridJump]);

  useEffect(() => {
    if (!pendingJump || !data) return;
    const index = rows.findIndex((r) => r.kind === "var" && r.variable.name === pendingJump);
    if (index < 0) return;
    setPendingJump(null);
    setCursor(index);
    requestAnimationFrame(() => virtual.scrollToIndex(index, { align: "center" }));
    setFlash(pendingJump);
    setTimeout(() => setFlash(null), 1500);
  }, [pendingJump, rows, data, virtual]);

  useEffect(() => {
    setCursor((c) => Math.min(c, Math.max(0, rows.length - 1)));
  }, [rows.length]);

  function moveCursor(delta: number) {
    let next = cursor;
    do {
      next = Math.max(0, Math.min(rows.length - 1, next + delta));
    } while (rows[next]?.kind === "group" && next > 0 && next < rows.length - 1);
    setCursor(next);
    virtual.scrollToIndex(next, { align: "auto" });
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;
    const inField = target.tagName === "INPUT";
    if (event.key === "/" && !inField) {
      event.preventDefault();
      searchRef.current?.focus();
      return;
    }
    if (inField) {
      if (event.key === "Escape") {
        target.blur();
        scrollRef.current?.focus();
      }
      if (event.key === "Enter" && target !== searchRef.current) {
        target.blur();
        scrollRef.current?.focus();
        moveCursor(1);
      }
      return;
    }
    if (event.key === "j" || event.key === "ArrowDown") {
      event.preventDefault();
      moveCursor(1);
    } else if (event.key === "k" || event.key === "ArrowUp") {
      event.preventDefault();
      moveCursor(-1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      scrollRef.current?.querySelector<HTMLElement>(`[data-row="${cursor}"] input, [data-row="${cursor}"] button`)?.focus();
    } else if (event.key === "Escape") {
      const row = rows[cursor];
      if (row?.kind === "var") stageEdit(row.variable.name, null);
    }
  }

  const editCount = Object.keys(edits).length;
  const scopeOptions: { value: Scope; label: string; hint?: string }[] = [
    { value: "editable", label: "Editable" },
    { value: "all", label: "Everything" },
    { value: "edited", label: "Edited", hint: String(editCount) },
  ];

  if (error) {
    return (
      <div className="notice bad">
        This save could not be read: {error}
        {engine === "renpy" && " Open the Repair tab to diagnose it."}
      </div>
    );
  }

  return (
    <div className="dock-body" onKeyDown={onKeyDown}>
      <div className="pane-bar">
        <input
          ref={searchRef}
          className="field search"
          type="search"
          placeholder="Fuzzy find a variable  ( / )"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setCursor(0);
          }}
        />
        <Select<Scope> ariaLabel="Which variables" value={scope} options={scopeOptions} onChange={setScope} />
        {engine === "renpy" && (
          <label className="check" title="Include Ren'Py internals such as store._ and config">
            <input type="checkbox" checked={includeInternal} onChange={(e) => setIncludeInternal(e.target.checked)} />
            internals
          </label>
        )}
      </div>
      {data?.engine === "rpgmaker" && data.named === false && (
        <div className="notice warn">No game folder next to this save, so switches and variables show numeric IDs.</div>
      )}
      <div className="pane-scroll grid" ref={scrollRef} tabIndex={0} aria-label="Variables" role="grid">
        <div className="grid-head" role="row">
          <span>name</span>
          <span>type</span>
          <span>value</span>
        </div>
        {busy && !data ? (
          <div className="empty-note">
            <span className="spin" />
          </div>
        ) : rows.length === 0 ? (
          <div className="empty-note">
            <h4>{query ? "No match" : "Nothing to show"}</h4>
            {query ? `Nothing resembles "${query}".` : "Switch the filter to see more."}
          </div>
        ) : (
          <div style={{ height: virtual.getTotalSize(), position: "relative" }}>
            {virtual.getVirtualItems().map((item) => {
              const row = rows[item.index];
              const style = { position: "absolute" as const, top: 0, left: 0, right: 0, transform: `translateY(${item.start}px)` };
              if (row.kind === "group") {
                return (
                  <div key={item.key} className="grid-group" style={{ ...style, position: "absolute" }}>
                    {row.label}
                  </div>
                );
              }
              const v = row.variable;
              const staged = edits[v.name];
              const dirty = staged !== undefined;
              return (
                <div
                  key={item.key}
                  data-row={item.index}
                  role="row"
                  className={`grid-row ${dirty ? "dirty" : ""} ${item.index === cursor ? "cursor" : ""} ${flash === v.name ? "flash" : ""}`}
                  style={style}
                  onMouseDown={() => setCursor(item.index)}
                >
                  <span className="name" title={v.name}>
                    {row.match ?? v.short_name}
                  </span>
                  <span className="kind">{v.kind}</span>
                  <span className="value">
                    <ValueEditor variable={v} staged={staged} onStage={(value) => stageEdit(v.name, value)} />
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
      <div className="pane-bar" style={{ borderTop: "1px solid var(--line)", borderBottom: 0 }}>
        <span className="muted" style={{ fontSize: 12 }}>
          {data ? `${rows.filter((r) => r.kind === "var").length} of ${data.variables.length} shown` : ""}
        </span>
        <span className="faint" style={{ fontSize: 12 }}>
          <Kbd>j</Kbd> <Kbd>k</Kbd> move, <Kbd>Enter</Kbd> edit, <Kbd>Ctrl</Kbd>+<Kbd>S</Kbd> write
        </span>
        <div className="grow" />
        {editCount > 0 && (
          <>
            <button className="btn ghost sm" onClick={discardEdits}>
              Discard
            </button>
            <button className="btn accent sm" onClick={() => void writeEdits()}>
              Write {editCount} change{editCount === 1 ? "" : "s"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function ValueEditor({ variable, staged, onStage }: { variable: Variable; staged?: string; onStage(value: string | null): void }) {
  const original = variable.value === null ? "" : String(variable.value);
  const current = staged ?? original;

  if (!variable.editable) {
    return (
      <span className="readonly" title={variable.preview}>
        {variable.preview}
      </span>
    );
  }

  const stage = (value: string) => onStage(value === original ? null : value);

  if (isBool(variable)) {
    const normal = /^(true|1)$/i.test(current) ? "True" : "False";
    return (
      <Select
        compact
        ariaLabel={`${variable.short_name} value`}
        value={normal}
        options={[
          { value: "True", label: "True" },
          { value: "False", label: "False" },
        ]}
        onChange={(value) => stage(/^(true|1)$/i.test(original) === (value === "True") ? original : value)}
      />
    );
  }

  if (isNumeric(variable.kind) || (typeof variable.value === "number" && variable.kind !== "str")) {
    const step = (delta: number) => {
      const n = Number(current);
      if (Number.isFinite(n)) stage(String(variable.kind === "float" ? +(n + delta).toFixed(6) : Math.round(n + delta)));
    };
    return (
      <span className="stepper">
        <input className="field" inputMode="decimal" value={current} onChange={(e) => stage(e.target.value)} aria-label={`${variable.short_name} value`} />
        <button tabIndex={-1} onClick={() => step(-1)} aria-label="Decrease">
          -
        </button>
        <button tabIndex={-1} onClick={() => step(1)} aria-label="Increase">
          +
        </button>
      </span>
    );
  }

  return <input className="field" value={current} onChange={(e) => stage(e.target.value)} aria-label={`${variable.short_name} value`} />;
}
