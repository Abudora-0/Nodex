import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Diagnosis, GalleryReport, Walkthrough } from "../api/types";
import { exportWalkthrough } from "../platform/pickFolder";
import { errorText, useStore } from "../state/store";
import { Confirm, Icon } from "../ui/bits";

const MAX_ROUTES = 5;

const CONFIDENCE: Record<string, { text: string; tone: string }> = {
  exact: { text: "Nothing was lost", tone: "ok" },
  high: { text: "Container rebuilt, all data intact", tone: "ok" },
  partial: { text: "Variables recovered, rollback history lost", tone: "warn" },
  substituted: { text: "Rebuilt from a neighbouring save", tone: "warn" },
  none: { text: "Nothing could be recovered", tone: "bad" },
};

/* ---------------- Repair ---------------- */

export function RepairPane({ path }: { path: string }) {
  const toast = useStore((s) => s.toast);
  const refreshSaves = useStore((s) => s.refreshSaves);
  const loadVariables = useStore((s) => s.loadVariables);
  const bumpHistory = useStore((s) => s.bumpHistory);
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);

  useEffect(() => setDiagnosis(null), [path]);

  async function run(action: "diagnose" | "repair") {
    setBusy(true);
    try {
      const result = action === "diagnose" ? await api.diagnose(path) : await api.repair(path);
      setDiagnosis(result);
      if (action === "repair") {
        toast("ok", "Save repaired. The damaged original was backed up first.");
        bumpHistory();
        void refreshSaves();
        void loadVariables();
      }
    } catch (caught) {
      toast("bad", errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  const confidence = diagnosis ? CONFIDENCE[diagnosis.confidence] ?? { text: diagnosis.confidence, tone: "warn" } : null;

  return (
    <div className="dock-body">
      <div className="pane-bar">
        <button className="btn sm" onClick={() => void run("diagnose")} disabled={busy}>
          {busy ? <span className="spin" /> : null} Diagnose
        </button>
        <button
          className="btn accent sm"
          onClick={() => setConfirm(true)}
          disabled={busy || !diagnosis || diagnosis.healthy || !diagnosis.recoverable}
        >
          Repair and write
        </button>
        <div className="grow" />
        {confidence && <span className={`chip ${confidence.tone === "ok" ? "effect" : confidence.tone === "bad" ? "bad" : "gilt"}`}>{confidence.text}</span>}
      </div>
      <div className="pane-scroll pane-pad">
        {!diagnosis ? (
          <div className="empty-note">
            <h4>Check this save</h4>
            Diagnosis reads the zip container and the pickle stream without writing anything. Repair only happens when you ask.
          </div>
        ) : (
          <>
            <p style={{ marginTop: 0 }}>{diagnosis.summary}</p>
            {diagnosis.variables_recovered > 0 && <p className="muted">{diagnosis.variables_recovered} store variables readable.</p>}
            <div className="section-label">recovery ladder</div>
            {diagnosis.steps.map((step, i) => (
              <div className="step-line" key={i}>
                <span className={`mark ${step.ok ? "" : "no"}`}>{step.ok ? "✓" : "×"}</span>
                <div>
                  <div>{step.name}</div>
                  <div className="faint" style={{ fontSize: 12 }}>
                    {step.detail}
                  </div>
                </div>
              </div>
            ))}
          </>
        )}
      </div>
      <Confirm
        open={confirm}
        onOpenChange={setConfirm}
        title="Repair this save?"
        body={confidence ? `Expected result: ${confidence.text.toLowerCase()}. The current file is backed up first.` : ""}
        confirmLabel="Repair and write"
        onConfirm={() => void run("repair")}
      />
    </div>
  );
}

/* ---------------- Walkthrough ---------------- */

export function WalkthroughPane() {
  const game = useStore((s) => s.game);
  const toast = useStore((s) => s.toast);
  const setWorld = useStore((s) => s.setWorld);
  const focusMapNode = useStore((s) => s.focusMapNode);
  const loadGraph = useStore((s) => s.loadGraph);
  const [data, setData] = useState<Walkthrough | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [onlyMeaningful, setOnlyMeaningful] = useState(false);
  const [busy, setBusy] = useState(false);

  const root = game?.root ?? "";

  useEffect(() => {
    if (!root) return;
    let alive = true;
    setData(null);
    setError(null);
    api
      .walkthrough(root)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
  }, [root]);

  const menus = useMemo(() => {
    if (!data) return [];
    const needle = query.trim().toLowerCase();
    const pool = onlyMeaningful ? data.menus.filter((m) => m.meaningful) : data.menus;
    if (!needle) return pool;
    return pool.filter((m) =>
      [m.label ?? "", m.filename, ...m.choices.flatMap((c) => [c.caption, ...c.effects, ...c.routes])].join(" ").toLowerCase().includes(needle),
    );
  }, [data, query, onlyMeaningful]);

  async function toggleMod(install: boolean) {
    setBusy(true);
    try {
      if (install) {
        const r = await api.installMod(root, "#7fdc7f");
        toast("ok", `Mod installed: ${r.annotated_menus} menus annotated. Start the game to see it.`);
      } else {
        const r = await api.uninstallMod(root);
        toast("ok", r.removed.length ? `Removed ${r.removed.length} file(s). The game is back to normal.` : "Nothing to remove.");
      }
      setData(await api.walkthrough(root));
    } catch (caught) {
      toast("bad", errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dock-body">
      <div className="pane-bar">
        <input className="field search" type="search" placeholder="Filter by choice, label or variable" value={query} onChange={(e) => setQuery(e.target.value)} />
        <div className="seg" role="group" aria-label="Which choices">
          <button aria-pressed={!onlyMeaningful} onClick={() => setOnlyMeaningful(false)}>
            All
          </button>
          <button aria-pressed={onlyMeaningful} onClick={() => setOnlyMeaningful(true)}>
            Consequential
          </button>
        </div>
      </div>
      {data && (
        <div className="pane-bar">
          <span className="chip">
            {menus.length} of {data.total_menus} choice points
          </span>
          <span className="chip">{data.scripts_read} scripts</span>
          {data.scripts_failed.length > 0 && <span className="chip bad">{data.scripts_failed.length} unreadable</span>}
          <div className="grow" />
          <button
            className="btn ghost sm"
            onClick={async () => {
              try {
                const written = await exportWalkthrough(root, data.title);
                if (written) toast("ok", `Walkthrough exported to ${written}.`);
              } catch (caught) {
                toast("bad", errorText(caught));
              }
            }}
          >
            Export HTML
          </button>
          {data.mod_installed ? (
            <button className="btn danger sm" disabled={busy} onClick={() => void toggleMod(false)}>
              Remove mod
            </button>
          ) : (
            <button className="btn accent sm" disabled={busy} onClick={() => void toggleMod(true)}>
              Install mod
            </button>
          )}
        </div>
      )}
      {error && <div className="notice bad">{error}</div>}
      <div className="pane-scroll">
        {!data && !error ? (
          <div className="empty-note">
            <span className="spin" />
            <p>Reading every script in the game</p>
          </div>
        ) : data && menus.length === 0 ? (
          <div className="empty-note">
            <h4>{data.total_menus === 0 ? "No choices" : "No match"}</h4>
            {data.total_menus === 0 ? "This game has no menu statements; it may be a kinetic novel." : `Nothing matches "${query}".`}
          </div>
        ) : (
          menus.map((menu) => (
            <section className="menu-card" key={menu.key}>
              <header>
                <b>{menu.label || "(no label)"}</b>
                <span>
                  {menu.filename}:{menu.line}
                </span>
              </header>
              {menu.choices.map((choice) => (
                <div
                  className="opt"
                  key={choice.index}
                  title="Show on the choice map"
                  onClick={() => {
                    void loadGraph();
                    setWorld("map");
                    focusMapNode(`choice:${menu.key}:${choice.index}`, true);
                  }}
                >
                  <span className="cap">{choice.caption || "(blank)"}</span>
                  {choice.condition && <span className="mono" style={{ color: "var(--ending)", fontSize: 11 }}>if {choice.condition}</span>}
                  <div className="chips">
                    {choice.effects.map((e) => (
                      <span className="chip effect" key={e}>
                        {e}
                      </span>
                    ))}
                    {choice.routes.slice(0, MAX_ROUTES).map((r) => (
                      <span className="chip route" key={r}>
                        {"→"} {r}
                      </span>
                    ))}
                    {choice.routes.length > MAX_ROUTES && (
                      <span className="chip" title={choice.routes.slice(MAX_ROUTES).join(", ")}>
                        +{choice.routes.length - MAX_ROUTES} routes
                      </span>
                    )}
                    {choice.effects.length === 0 && choice.downstream.length > 0 && (
                      <span className="chip">later: {choice.downstream.slice(0, 3).join(", ")}</span>
                    )}
                    {choice.inert && choice.downstream.length === 0 && <span className="chip">no effect</span>}
                  </div>
                </div>
              ))}
            </section>
          ))
        )}
      </div>
    </div>
  );
}

/* ---------------- Gallery ---------------- */

export function GalleryPane() {
  const game = useStore((s) => s.game);
  const toast = useStore((s) => s.toast);
  const root = game?.root ?? "";
  const [data, setData] = useState<GalleryReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [markImages, setMarkImages] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);

  async function load() {
    setError(null);
    try {
      const report = await api.gallery(root);
      setData(report);
      setChosen(new Set(report.locked.length ? report.locked : report.suggested));
    } catch (caught) {
      setError(errorText(caught));
    }
  }

  useEffect(() => {
    if (!root) return;
    setData(null);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [root]);

  async function unlock() {
    setBusy(true);
    try {
      const result = await api.unlockGallery(root, [...chosen], markImages);
      toast(
        "ok",
        result.written
          ? `Unlocked ${result.flags_set.length} item(s)${result.images_marked ? `, marked ${result.images_marked} images seen` : ""}. The persistent file was backed up first.`
          : "Nothing needed changing; everything selected was already unlocked.",
      );
      await load();
    } catch (caught) {
      toast("bad", errorText(caught));
    } finally {
      setBusy(false);
    }
  }

  const visible = data ? (showAll ? data.flags : data.flags.filter((f) => f.suggested)) : [];

  return (
    <div className="dock-body">
      <div className="pane-bar">
        <div className="seg" role="group" aria-label="Which flags">
          <button aria-pressed={!showAll} onClick={() => setShowAll(false)}>
            Suggested
          </button>
          <button aria-pressed={showAll} onClick={() => setShowAll(true)}>
            All flags
          </button>
        </div>
        <div className="grow" />
        {data && data.image_count > 0 && (
          <label className="check" title="Records every declared image as seen. Needed by Ren'Py's built-in Gallery, but writes many entries.">
            <input type="checkbox" checked={markImages} onChange={(e) => setMarkImages(e.target.checked)} />
            mark {data.image_count} images seen
          </label>
        )}
        <button className="btn accent sm" disabled={busy || !data?.persistent || (chosen.size === 0 && !markImages)} onClick={() => setConfirm(true)}>
          Unlock {chosen.size || ""}
        </button>
      </div>
      {error && <div className="notice bad">{error}</div>}
      {data && !data.persistent && <div className="notice warn">No persistent file found. Launch the game once so Ren'Py creates one.</div>}
      <div className="pane-scroll">
        {!data && !error ? (
          <div className="empty-note">
            <span className="spin" />
            <p>Scanning scripts for gallery locks</p>
          </div>
        ) : data ? (
          <>
            {data.needs_manual.length > 0 && (
              <div className="notice warn" style={{ margin: 16 }}>
                <b>{data.needs_manual.length} gallery collection(s) need a manual decision.</b> They hold a set of unlocked entries whose names the
                game builds at runtime, so Nodex leaves them alone rather than erase them.
                {data.needs_manual.map((flag) => (
                  <div key={flag.name} className="mono" style={{ fontSize: 11, marginTop: 6 }}>
                    {flag.name} ({flag.value_kind}): {flag.existing_members.slice(0, 5).join(", ") || "empty"}
                  </div>
                ))}
              </div>
            )}
            {visible.length === 0 ? (
              <div className="empty-note">
                <h4>No gallery flags</h4>
                {data.image_count > 0 ? "You can still mark images as seen, which opens Ren'Py's built-in Gallery." : "None were found in this game's scripts."}
              </div>
            ) : (
              visible.map((flag) => (
                <label className="flag-row" key={flag.name}>
                  <span className="check">
                    <input
                      type="checkbox"
                      checked={chosen.has(flag.name)}
                      disabled={!flag.enumerable}
                      onChange={() =>
                        setChosen((prev) => {
                          const next = new Set(prev);
                          if (next.has(flag.name)) next.delete(flag.name);
                          else next.add(flag.name);
                          return next;
                        })
                      }
                    />
                  </span>
                  <span style={{ minWidth: 0 }}>
                    <div className="name" title={flag.name}>
                      {flag.name}
                    </div>
                    <div className="sub">
                      {flag.kind}, {flag.value_kind}
                      {!flag.enumerable && ", entries unknown"}
                    </div>
                  </span>
                  <span className={`chip ${flag.locked ? "route" : "effect"}`}>{flag.locked ? "locked" : "open"}</span>
                </label>
              ))
            )}
          </>
        ) : null}
      </div>
      <Confirm
        open={confirm}
        onOpenChange={setConfirm}
        title="Unlock the gallery?"
        body={`${chosen.size} flag(s)${markImages ? " plus every declared image" : ""} will be written to the persistent file, after a backup.`}
        confirmLabel="Unlock"
        onConfirm={() => void unlock()}
      />
    </div>
  );
}

/* ---------------- Choice map node ---------------- */

export function NodePane() {
  const graph = useStore((s) => s.graph);
  const id = useStore((s) => s.mapNode);
  const focusMapNode = useStore((s) => s.focusMapNode);
  const jumpToVariable = useStore((s) => s.jumpToVariable);
  const variables = useStore((s) => s.variables);

  const node = graph?.nodes.find((n) => n.id === id);
  const byId = useMemo(() => new Map(graph?.nodes.map((n) => [n.id, n]) ?? []), [graph]);
  if (!graph || !node) {
    return (
      <div className="empty-note">
        <h4>Pick a node</h4>Click anything on the choice map to see what it does and where it leads.
      </div>
    );
  }

  const outgoing = graph.edges.filter((e) => e.source === node.id).map((e) => ({ edge: e, node: byId.get(e.target)! }));
  const incoming = graph.edges.filter((e) => e.target === node.id).map((e) => ({ edge: e, node: byId.get(e.source)! }));
  const variableNames = new Set(variables?.variables.map((v) => v.name) ?? []);

  const effectTarget = (effect: string) => {
    const name = effect.replace(/^maybe /, "").match(/^[+-]?[\d.]*\s*([A-Za-z_][\w.]*)/)?.[1] ?? effect.split(/[ .=]/)[0];
    const qualified = `store.${name}`;
    return variableNames.has(qualified) ? qualified : variableNames.has(name) ? name : null;
  };

  return (
    <div className="pane-scroll pane-pad node-detail">
      <div className="section-label">{node.type === "menu" ? "choice point" : node.type}</div>
      <h2>{node.label || "(blank)"}</h2>
      {node.file && (
        <p className="mono faint" style={{ margin: "4px 0 0" }}>
          {node.file}
          {node.line ? `:${node.line}` : ""}
        </p>
      )}
      {node.condition && (
        <>
          <div className="section-label">only offered if</div>
          <code style={{ color: "var(--ending)" }}>{node.condition}</code>
        </>
      )}
      {node.effects && node.effects.length > 0 && (
        <>
          <div className="section-label">changes</div>
          <div className="change-list">
            {node.effects.map((effect) => {
              const target = effectTarget(effect);
              return target ? (
                <button key={effect} className="chip effect" style={{ cursor: "pointer" }} title="Show in the open save" onClick={() => jumpToVariable(target)}>
                  {effect}
                </button>
              ) : (
                <span key={effect} className="chip effect">
                  {effect}
                </span>
              );
            })}
          </div>
        </>
      )}
      {node.downstream && node.downstream.length > 0 && (
        <>
          <div className="section-label">further along</div>
          <div className="change-list">
            {node.downstream.map((effect) => (
              <span key={effect} className="chip">
                {effect}
              </span>
            ))}
          </div>
        </>
      )}
      {outgoing.length > 0 && (
        <>
          <div className="section-label">leads to</div>
          <div className="path-list">
            {outgoing.map(({ edge, node: target }) => (
              <button key={edge.id} onClick={() => focusMapNode(target.id, true)}>
                <span className="t">{edge.kind}</span>
                <span>{target.label || "(blank)"}</span>
              </button>
            ))}
          </div>
        </>
      )}
      {incoming.length > 0 && (
        <>
          <div className="section-label">reached from</div>
          <div className="path-list">
            {incoming.slice(0, 30).map(({ edge, node: source }) => (
              <button key={edge.id} onClick={() => focusMapNode(source.id, true)}>
                <Icon name="back" />
                <span>{source.label || "(blank)"}</span>
              </button>
            ))}
          </div>
        </>
      )}
      {graph.partial && (
        <p className="faint" style={{ fontSize: 12, marginTop: 20 }}>
          The map follows menus, jumps and calls. Plain fall-through from one label to the next is not drawn.
        </p>
      )}
    </div>
  );
}
