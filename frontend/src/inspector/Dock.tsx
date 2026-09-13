import { useRef, useState, type PointerEvent } from "react";
import { useStore, type DockTab } from "../state/store";
import { baseName, Icon, Kbd } from "../ui/bits";
import { GalleryPane, NodePane, RepairPane, WalkthroughPane } from "./gamePanes";
import { BackupsPane, BulkPane, DiffPane, HistoryPane, PresetsPane, VaultPane } from "./panes";
import { VariableGrid } from "./VariableGrid";

const LABEL: Record<DockTab, string> = {
  variables: "Variables",
  compare: "Compare",
  bulk: "Bulk edit",
  history: "History",
  backups: "Backups",
  presets: "Presets",
  repair: "Repair",
  walkthrough: "Walkthrough",
  gallery: "Gallery",
  vault: "Vault",
  node: "Node",
};

export function Dock() {
  const open = useStore((s) => s.dockOpen);
  const tab = useStore((s) => s.dockTab);
  const width = useStore((s) => s.dockWidth);
  const focusMode = useStore((s) => s.focusMode);
  const setDock = useStore((s) => s.setDock);
  const selection = useStore((s) => s.selection);
  const saves = useStore((s) => s.saves);
  const variables = useStore((s) => s.variables);
  const edits = useStore((s) => s.edits);
  const game = useStore((s) => s.game);
  const world = useStore((s) => s.world);
  const [dragging, setDragging] = useState(false);
  const startRef = useRef<{ x: number; width: number } | null>(null);

  if (!open) return null;

  const primary = selection[0];
  const save = saves.find((s) => s.path === primary);
  const isRenpySave = save?.engine === "renpy";

  let tabs: DockTab[];
  let title = "";
  let sub = "";
  if (tab === "walkthrough" || tab === "gallery" || tab === "vault") {
    tabs = game?.engine === "renpy" && game.game_dir ? ["walkthrough", "gallery", "vault"] : ["vault"];
    title = game?.title ?? "Nodex";
    sub = game?.root ?? "";
  } else if (world === "map" && tab === "node") {
    tabs = ["node"];
    title = "Choice map";
    sub = game?.title ?? "";
  } else if (selection.length > 1) {
    tabs = selection.length === 2 ? ["compare", "bulk", "presets"] : ["bulk", "presets"];
    title = `${selection.length} saves`;
    sub = selection.map(baseName).join(", ");
  } else {
    tabs = ["variables", "history", "backups", "presets", ...(isRenpySave ? (["repair"] as DockTab[]) : [])];
    title = save?.save_name && isRenpySave ? save.save_name : save ? baseName(save.name) : "Save";
    sub = primary ?? "";
  }
  if (!tabs.includes(tab)) tabs = [tab, ...tabs];

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    startRef.current = { x: event.clientX, width };
    setDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!startRef.current) return;
    const max = window.innerWidth * 0.72;
    const next = Math.min(max, Math.max(380, startRef.current.width + (startRef.current.x - event.clientX)));
    setDock({ dockWidth: Math.round(next) });
  }
  function onPointerUp() {
    startRef.current = null;
    setDragging(false);
  }

  const editCount = Object.keys(edits).length;

  return (
    <aside className="dock" style={{ width }} aria-label="Inspector">
      {!focusMode && (
        <div
          className={`dock-resize ${dragging ? "dragging" : ""}`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize inspector"
        />
      )}
      <div className="dock-head">
        <div className="titlebar">
          <div className="grow">
            <h3 title={title}>{title}</h3>
            <div className="sub" title={sub}>
              {sub}
            </div>
          </div>
          {(tab === "variables" || tab === "compare") && (
            <button
              className="btn ghost icon"
              title={focusMode ? "Back to canvas (F)" : "Focus mode (F)"}
              aria-label={focusMode ? "Exit focus mode" : "Focus mode"}
              onClick={() => setDock({ focusMode: !focusMode })}
            >
              <Icon name={focusMode ? "collapse" : "expand"} />
            </button>
          )}
          <button className="btn ghost icon" aria-label="Close inspector" title="Close (Esc)" onClick={() => setDock({ dockOpen: false, focusMode: false })}>
            <Icon name="close" />
          </button>
        </div>
        <div className="dock-tabs" role="tablist">
          {tabs.map((t) => (
            <button key={t} role="tab" aria-selected={t === tab} onClick={() => setDock({ dockTab: t })}>
              {LABEL[t]}
              {t === "variables" && variables && <span className="count">{variables.variables.length}</span>}
              {t === "variables" && editCount > 0 && <span className="count" style={{ color: "var(--ending)" }}>*{editCount}</span>}
            </button>
          ))}
        </div>
      </div>

      {tab === "variables" && primary && <VariableGrid key={primary} />}
      {tab === "compare" && selection.length === 2 && <DiffPane left={selection[0]} right={selection[1]} />}
      {tab === "bulk" && <BulkPane />}
      {tab === "presets" && <PresetsPane />}
      {tab === "backups" && primary && <BackupsPane path={primary} />}
      {tab === "history" && <HistoryPane path={primary} />}
      {tab === "repair" && primary && <RepairPane path={primary} />}
      {tab === "walkthrough" && <WalkthroughPane />}
      {tab === "gallery" && <GalleryPane />}
      {tab === "vault" && <VaultPane />}
      {tab === "node" && <NodePane />}
      {tab === "variables" && !primary && (
        <div className="empty-note">
          Pick a save on the canvas. <Kbd>Ctrl</Kbd> <Kbd>K</Kbd> finds anything.
        </div>
      )}
    </aside>
  );
}
