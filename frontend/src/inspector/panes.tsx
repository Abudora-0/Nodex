import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { BackupInfo, BulkResult, Change, DiffResponse, HistoryEntry, Preset, VaultFolder } from "../api/types";
import { errorText, useStore } from "../state/store";
import { baseName, Confirm, display, formatBytes, Icon, timeAgo } from "../ui/bits";
import { Select } from "../ui/Select";

function useGameId() {
  const game = useStore((s) => s.game);
  return game ? game.root.toLowerCase() : "";
}

/* ---------------- Compare ---------------- */

export function DiffPane({ left, right }: { left: string; right: string }) {
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let alive = true;
    setDiff(null);
    setError(null);
    api
      .diff(left, right)
      .then((d) => alive && setDiff(d))
      .catch((e) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
  }, [left, right]);

  const changes = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const all = diff?.changes ?? [];
    return needle ? all.filter((c) => (c.label ?? c.name).toLowerCase().includes(needle)) : all;
  }, [diff, query]);

  return (
    <div className="dock-body">
      <div className="versus">
        <div className="side">
          <b>{baseName(left)}</b>
          <span>before</span>
        </div>
        <span className="vs">vs</span>
        <div className="side" style={{ textAlign: "right" }}>
          <b>{baseName(right)}</b>
          <span>after</span>
        </div>
      </div>
      <div className="pane-bar">
        <input className="field search" type="search" placeholder="Filter changes" value={query} onChange={(e) => setQuery(e.target.value)} />
        <span className="chip">{diff ? `${diff.changes.length} differences` : "comparing"}</span>
      </div>
      {error && <div className="notice bad">{error}</div>}
      <div className="pane-scroll">
        {!diff && !error ? (
          <div className="empty-note">
            <span className="spin" />
          </div>
        ) : changes.length === 0 && diff ? (
          <div className="empty-note">
            <h4>Identical</h4>
            {query ? "No differences match the filter." : "These two saves hold the same values."}
          </div>
        ) : (
          changes.map((c) => (
            <div className="diff-row" key={c.name}>
              <span className="n" title={c.name}>
                {c.label ?? c.name.replace(/^store\./, "")}
              </span>
              <span className={`b ${c.before_missing ? "missing" : ""}`}>{c.before_missing ? "absent" : display(c.before)}</span>
              <span className="arrow">{"→"}</span>
              <span className={`a ${c.after_missing ? "missing" : ""}`}>{c.after_missing ? "absent" : display(c.after)}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

/* ---------------- Change list editor (bulk + presets) ---------------- */

function ChangeEditor({ changes, onChange }: { changes: Change[]; onChange(next: Change[]): void }) {
  const variables = useStore((s) => s.variables);
  const names = useMemo(
    () => (variables?.variables ?? []).filter((v) => v.editable).map((v) => ({ value: v.name, label: v.short_name, hint: v.kind })),
    [variables],
  );

  const update = (index: number, patch: Partial<Change>) => onChange(changes.map((c, i) => (i === index ? { ...c, ...patch } : c)));

  return (
    <div className="editor-rows">
      {changes.map((change, index) => (
        <div className="editor-row" key={index}>
          {names.length ? (
            <Select
              ariaLabel="Variable"
              searchable
              placeholder="Pick a variable"
              value={change.name}
              options={names.some((n) => n.value === change.name) || !change.name ? names : [{ value: change.name, label: change.name }, ...names]}
              onChange={(name) => update(index, { name })}
            />
          ) : (
            <input className="field" placeholder="variable name" value={change.name} onChange={(e) => update(index, { name: e.target.value })} />
          )}
          <input className="field" placeholder="new value" value={String(change.value ?? "")} onChange={(e) => update(index, { value: e.target.value })} />
          <button className="btn ghost icon" aria-label="Remove change" onClick={() => onChange(changes.filter((_, i) => i !== index))}>
            <Icon name="close" />
          </button>
        </div>
      ))}
      <div>
        <button className="btn sm" onClick={() => onChange([...changes, { name: "", value: "" }])}>
          <Icon name="plus" /> Add change
        </button>
      </div>
    </div>
  );
}

function BulkReport({ result }: { result: BulkResult }) {
  return (
    <>
      <div className={`notice ${result.failed ? "warn" : "ok"}`}>
        {result.dry_run ? "Dry run: " : ""}
        {result.ok} ok, {result.failed} not changed.
        {!result.dry_run && result.failed > 0 && " Files that failed were left exactly as they were."}
      </div>
      <ul className="ledger">
        {result.results.map((r) => (
          <li key={r.path}>
            <span className={`status-dot ${r.status}`} />
            <div className="body">
              <div className="title">{baseName(r.path)}</div>
              <div className="detail">{r.status === "ok" ? r.detail : `${r.status}: ${r.detail}`}</div>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

/* ---------------- Bulk edit ---------------- */

export function BulkPane() {
  const selection = useStore((s) => s.selection);
  const toast = useStore((s) => s.toast);
  const bumpHistory = useStore((s) => s.bumpHistory);
  const refreshSaves = useStore((s) => s.refreshSaves);
  const undoLast = useStore((s) => s.undoLast);
  const [changes, setChanges] = useState<Change[]>([{ name: "", value: "" }]);
  const [result, setResult] = useState<BulkResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);

  const ready = changes.filter((c) => c.name.trim());

  async function run(dryRun: boolean) {
    setBusy(true);
    try {
      const r = await api.bulkEdit(selection, ready, dryRun);
      setResult(r);
      if (!dryRun) {
        bumpHistory();
        void refreshSaves();
        toast(r.failed ? "info" : "ok", `Bulk edit wrote ${r.ok} of ${selection.length} saves.`, r.ok ? { label: "Undo", run: () => void undoLast() } : undefined);
      }
    } catch (caught) {
      toast("bad", errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dock-body">
      <div className="pane-scroll pane-pad">
        <div className="section-label">{selection.length} saves gathered</div>
        <div className="change-list" style={{ marginBottom: 14 }}>
          {selection.map((p) => (
            <span className="chip" key={p}>
              {baseName(p)}
            </span>
          ))}
        </div>
        <div className="section-label">apply these changes to each</div>
        <ChangeEditor changes={changes} onChange={setChanges} />
        <p className="faint" style={{ fontSize: 12, marginTop: 14 }}>
          Every save is checked and written on its own, with its own backup. If one is refused, the others still go through.
        </p>
        {result && <BulkReport result={result} />}
      </div>
      <div className="pane-bar" style={{ borderTop: "1px solid var(--line)", borderBottom: 0 }}>
        <PresetSaver changes={ready} />
        <div className="grow" />
        <button className="btn sm" disabled={busy || !ready.length} onClick={() => void run(true)}>
          Dry run
        </button>
        <button className="btn accent sm" disabled={busy || !ready.length} onClick={() => setConfirm(true)}>
          Write to {selection.length} saves
        </button>
      </div>
      <Confirm
        open={confirm}
        onOpenChange={setConfirm}
        title={`Write to ${selection.length} saves?`}
        body={`${ready.length} change(s) will be applied to each selected save. Each file is backed up first and can be undone.`}
        confirmLabel="Write all"
        onConfirm={() => void run(false)}
      />
    </div>
  );
}

function PresetSaver({ changes }: { changes: Change[] }) {
  const gameId = useGameId();
  const toast = useStore((s) => s.toast);
  const [name, setName] = useState("");
  const [open, setOpen] = useState(false);
  if (!open) {
    return (
      <button className="btn ghost sm" disabled={!changes.length} onClick={() => setOpen(true)}>
        Save as preset
      </button>
    );
  }
  return (
    <form
      style={{ display: "flex", gap: 6 }}
      onSubmit={async (event) => {
        event.preventDefault();
        try {
          await api.savePreset({ game_id: gameId, name, changes });
          toast("ok", `Preset "${name}" saved.`);
          setOpen(false);
          setName("");
        } catch (caught) {
          toast("bad", errorText(caught));
        }
      }}
    >
      <input className="field" autoFocus placeholder="Preset name" value={name} onChange={(e) => setName(e.target.value)} style={{ height: 26 }} />
      <button className="btn sm" disabled={!name.trim()}>
        Save
      </button>
    </form>
  );
}

/* ---------------- Presets ---------------- */

export function PresetsPane() {
  const gameId = useGameId();
  const selection = useStore((s) => s.selection);
  const toast = useStore((s) => s.toast);
  const bumpHistory = useStore((s) => s.bumpHistory);
  const loadVariables = useStore((s) => s.loadVariables);
  const refreshSaves = useStore((s) => s.refreshSaves);
  const [presets, setPresets] = useState<Preset[] | null>(null);
  const [editing, setEditing] = useState<Preset | null>(null);
  const [result, setResult] = useState<BulkResult | null>(null);

  const reload = () =>
    api
      .presets(gameId)
      .then((r) => setPresets(r.presets))
      .catch((e) => toast("bad", errorText(e)));

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gameId]);

  async function apply(preset: Preset) {
    try {
      const r = await api.applyPreset(preset.id, selection, false);
      setResult(r);
      bumpHistory();
      void loadVariables();
      void refreshSaves();
      toast(r.failed ? "info" : "ok", `Applied "${preset.name}" to ${r.ok} save(s).`);
    } catch (caught) {
      toast("bad", errorText(caught));
    }
  }

  if (editing) {
    return (
      <div className="dock-body">
        <div className="pane-scroll pane-pad">
          <div className="section-label">preset name</div>
          <input className="field" style={{ width: "100%" }} value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
          <div className="section-label">changes</div>
          <ChangeEditor changes={editing.changes} onChange={(changes) => setEditing({ ...editing, changes })} />
        </div>
        <div className="pane-bar" style={{ borderTop: "1px solid var(--line)", borderBottom: 0 }}>
          <button className="btn ghost sm" onClick={() => setEditing(null)}>
            Cancel
          </button>
          <div className="grow" />
          <button
            className="btn solid sm"
            disabled={!editing.name.trim() || !editing.changes.some((c) => c.name)}
            onClick={async () => {
              try {
                await api.savePreset({ ...editing, id: editing.id || undefined, changes: editing.changes.filter((c) => c.name) });
                setEditing(null);
                void reload();
              } catch (caught) {
                toast("bad", errorText(caught));
              }
            }}
          >
            Save preset
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="dock-body">
      <div className="pane-bar">
        <span className="muted" style={{ fontSize: 13 }}>
          Named sets of changes for this game.
        </span>
        <div className="grow" />
        <button className="btn sm" onClick={() => setEditing({ id: "", game_id: gameId, name: "", changes: [{ name: "", value: "" }] })}>
          <Icon name="plus" /> New preset
        </button>
      </div>
      <div className="pane-scroll">
        {presets === null ? (
          <div className="empty-note">
            <span className="spin" />
          </div>
        ) : presets.length === 0 ? (
          <div className="empty-note">
            <h4>No presets yet</h4>A preset stores changes like "max affection" so you can apply them to any save of this game in one step.
          </div>
        ) : (
          <ul className="ledger">
            {presets.map((preset) => (
              <li key={preset.id}>
                <div className="body">
                  <div className="title" style={{ fontFamily: "var(--font-display)", fontStyle: "italic", fontSize: 16 }}>
                    {preset.name}
                  </div>
                  <div className="change-list">
                    {preset.changes.slice(0, 6).map((c) => (
                      <span className="chip effect" key={c.name}>
                        {c.name.replace(/^store\./, "")} = {String(c.value)}
                      </span>
                    ))}
                    {preset.changes.length > 6 && <span className="chip">+{preset.changes.length - 6}</span>}
                  </div>
                </div>
                <button className="btn ghost sm" onClick={() => setEditing(preset)}>
                  Edit
                </button>
                <button
                  className="btn ghost sm icon"
                  aria-label={`Delete ${preset.name}`}
                  onClick={async () => {
                    await api.deletePreset(preset.id);
                    void reload();
                  }}
                >
                  <Icon name="trash" />
                </button>
                <button className="btn accent sm" disabled={!selection.length} onClick={() => void apply(preset)}>
                  Apply{selection.length > 1 ? ` to ${selection.length}` : ""}
                </button>
              </li>
            ))}
          </ul>
        )}
        {result && <BulkReport result={result} />}
      </div>
    </div>
  );
}

/* ---------------- Backups (this save) ---------------- */

export function BackupsPane({ path }: { path: string }) {
  const toast = useStore((s) => s.toast);
  const bumpHistory = useStore((s) => s.bumpHistory);
  const historyVersion = useStore((s) => s.historyVersion);
  const loadVariables = useStore((s) => s.loadVariables);
  const refreshSaves = useStore((s) => s.refreshSaves);
  const select = useStore((s) => s.select);
  const [backups, setBackups] = useState<BackupInfo[] | null>(null);
  const [comparing, setComparing] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<BackupInfo | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .backups(path)
      .then((r) => alive && setBackups(r.backups))
      .catch((e) => alive && toast("bad", errorText(e)));
    return () => {
      alive = false;
    };
  }, [path, historyVersion, toast]);

  if (comparing) {
    return (
      <div className="dock-body">
        <div className="pane-bar">
          <button className="btn ghost sm" onClick={() => setComparing(null)}>
            <Icon name="back" /> Backups
          </button>
        </div>
        <DiffPane left={comparing} right={path} />
      </div>
    );
  }

  return (
    <div className="dock-body">
      <div className="pane-scroll">
        {backups === null ? (
          <div className="empty-note">
            <span className="spin" />
          </div>
        ) : backups.length === 0 ? (
          <div className="empty-note">
            <h4>No backups yet</h4>Nodex copies this save into the vault before every write.
          </div>
        ) : (
          <ul className="ledger">
            {backups.map((b) => {
              const date = new Date(b.taken * 1000);
              return (
                <li key={b.path}>
                  <div className="stamp">
                    <b>{date.getDate()}</b>
                    {date.toLocaleString(undefined, { month: "short" })}
                  </div>
                  <div className="body">
                    <div className="title">{date.toLocaleTimeString()}</div>
                    <div className="detail">
                      {timeAgo(b.taken)}, {formatBytes(b.size)}
                    </div>
                  </div>
                  <button className="btn ghost sm" onClick={() => setComparing(b.path)}>
                    Compare
                  </button>
                  <button className="btn sm" onClick={() => setRestoring(b)}>
                    Restore
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <Confirm
        open={!!restoring}
        onOpenChange={(open) => !open && setRestoring(null)}
        title="Restore this backup?"
        body={`The save will be replaced with the copy from ${restoring ? new Date(restoring.taken * 1000).toLocaleString() : ""}. The current file is backed up first.`}
        confirmLabel="Restore"
        onConfirm={async () => {
          if (!restoring) return;
          try {
            await api.restore(path, restoring.path);
            toast("ok", "Backup restored.");
            bumpHistory();
            void refreshSaves();
            select(path);
            void loadVariables();
          } catch (caught) {
            toast("bad", errorText(caught));
          }
        }}
      />
    </div>
  );
}

/* ---------------- Vault ---------------- */

export function VaultPane() {
  const toast = useStore((s) => s.toast);
  const [vault, setVault] = useState<{ root: string; folders: VaultFolder[] } | null>(null);
  const [folder, setFolder] = useState<string>("");

  useEffect(() => {
    api
      .vault()
      .then((v) => {
        setVault(v);
        setFolder(v.folders[0]?.folder ?? "");
      })
      .catch((e) => toast("bad", errorText(e)));
  }, [toast]);

  const current = vault?.folders.find((f) => f.folder === folder);
  const total = vault?.folders.reduce((sum, f) => sum + f.bytes, 0) ?? 0;

  return (
    <div className="dock-body">
      <div className="pane-bar">
        {vault && vault.folders.length > 0 && (
          <Select
            ariaLabel="Vault folder"
            searchable={vault.folders.length > 8}
            value={folder}
            onChange={setFolder}
            options={vault.folders.map((f) => ({ value: f.folder, label: f.folder, hint: String(f.count) }))}
          />
        )}
        <div className="grow" />
        {vault && <span className="chip">{formatBytes(total)} kept</span>}
      </div>
      <div className="pane-scroll">
        {!vault ? (
          <div className="empty-note">
            <span className="spin" />
          </div>
        ) : !current ? (
          <div className="empty-note">
            <h4>The vault is empty</h4>Backups appear here after the first write.
          </div>
        ) : (
          <ul className="ledger">
            {current.backups.map((b) => (
              <li key={b.path}>
                <div className="body">
                  <div className="title mono">{b.original_name}</div>
                  <div className="detail">
                    {new Date(b.taken * 1000).toLocaleString()}, {formatBytes(b.size)}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
        {vault && <p className="faint mono" style={{ padding: "8px 16px", fontSize: 11 }}>{vault.root}</p>}
      </div>
    </div>
  );
}

/* ---------------- History ---------------- */

export function HistoryPane({ path }: { path?: string }) {
  const toast = useStore((s) => s.toast);
  const historyVersion = useStore((s) => s.historyVersion);
  const bumpHistory = useStore((s) => s.bumpHistory);
  const loadVariables = useStore((s) => s.loadVariables);
  const refreshSaves = useStore((s) => s.refreshSaves);
  const [entries, setEntries] = useState<HistoryEntry[] | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .history(path)
      .then((r) => alive && setEntries(r.entries))
      .catch((e) => alive && toast("bad", errorText(e)));
    return () => {
      alive = false;
    };
  }, [path, historyVersion, toast]);

  async function undo(entry: HistoryEntry, force = false) {
    try {
      await api.undo({ batch_id: entry.batch_id, force });
      toast("ok", "Undone. The previous file was restored from its backup.");
    } catch (caught) {
      const status = (caught as { status?: number }).status;
      if (status === 409 && !force) {
        toast("bad", "The save changed after this edit.", { label: "Undo anyway", run: () => void undo(entry, true) });
      } else toast("bad", errorText(caught));
    }
    bumpHistory();
    void loadVariables();
    void refreshSaves();
  }

  return (
    <div className="pane-scroll">
      {entries === null ? (
        <div className="empty-note">
          <span className="spin" />
        </div>
      ) : entries.length === 0 ? (
        <div className="empty-note">
          <h4>No history</h4>Every write, bulk edit, restore and undo is recorded here.
        </div>
      ) : (
        <ul className="ledger">
          {entries.map((entry) => (
            <li key={entry.id} className={entry.undone ? "undone" : ""}>
              <div className="body">
                <div className="title">
                  <span className="chip" style={{ marginRight: 6 }}>
                    {entry.source}
                  </span>
                  {!path && baseName(entry.path)}
                  <span className="faint" style={{ fontSize: 12, marginLeft: 6 }}>
                    {timeAgo(entry.ts)}
                  </span>
                </div>
                <div className="change-list">
                  {entry.changes.slice(0, 5).map((c) => (
                    <span className="chip effect" key={c.name}>
                      {c.name.replace(/^store\./, "")}: {display(c.before)} {"→"} {display(c.after)}
                    </span>
                  ))}
                  {entry.changes.length > 5 && <span className="chip">+{entry.changes.length - 5}</span>}
                  {entry.restored_from && <span className="chip gilt">restored {timeAgo(Number(entry.ts))}</span>}
                </div>
              </div>
              {!entry.undone && entry.backup && (
                <button className="btn ghost sm" onClick={() => void undo(entry)}>
                  <Icon name="undo" /> Undo
                </button>
              )}
              {entry.undone && <span className="chip">undone</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
