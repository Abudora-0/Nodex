import { ReactFlowProvider } from "@xyflow/react";
import { useEffect } from "react";
import { ChoiceMap } from "./graph/ChoiceMap";
import { GameWorld } from "./graph/GameWorld";
import { LibraryWorld } from "./graph/LibraryWorld";
import { Dock } from "./inspector/Dock";
import { CommandPalette } from "./palette/CommandPalette";
import { useStore } from "./state/store";
import { baseName, Glyph, Icon, Kbd, Toasts } from "./ui/bits";

function Rail() {
  const world = useStore((s) => s.world);
  const game = useStore((s) => s.game);
  const selection = useStore((s) => s.selection);
  const saves = useStore((s) => s.saves);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  const setWorld = useStore((s) => s.setWorld);
  const setPalette = useStore((s) => s.setPalette);

  const save = saves.find((s) => s.path === selection[0]);
  const steps: { key: string; label: string; here: boolean; color: string; go?: () => void }[] = [
    { key: "library", label: "Library", here: world === "library", color: "var(--muted)", go: () => setWorld("library") },
  ];
  if (game) {
    steps.push({ key: "game", label: game.title, here: world === "game" && !save, color: `var(--eng-${game.engine})`, go: () => setWorld("game") });
    if (world === "map") steps.push({ key: "map", label: "Choice map", here: true, color: "var(--choice)" });
    else if (selection.length > 1) steps.push({ key: "sel", label: `${selection.length} saves`, here: true, color: "var(--save)" });
    else if (save) steps.push({ key: "save", label: save.save_name && save.engine === "renpy" ? save.save_name : baseName(save.name), here: true, color: "var(--save)" });
  }

  return (
    <header className="rail">
      <button className="wordmark" onClick={() => setWorld("library")} aria-label="Nodex library">
        <Glyph />
        Nodex
      </button>
      <nav className="trail" aria-label="Where you are">
        {steps.map((step, i) => (
          <span key={step.key} style={{ display: "contents" }}>
            {i > 0 && <span className="trail-link" />}
            <button
              className={`trail-step ${step.here ? "here" : ""}`}
              style={{ color: step.here ? undefined : undefined }}
              onClick={step.go}
              disabled={!step.go}
              aria-current={step.here ? "location" : undefined}
            >
              <span className="dot" style={{ color: step.color }} />
              <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{step.label}</span>
            </button>
          </span>
        ))}
      </nav>
      <div className="rail-tools">
        <button className="palette-hint" onClick={() => setPalette(true)}>
          <Icon name="search" />
          <span className="grow">Find anything</span>
          <Kbd>Ctrl</Kbd>
          <Kbd>K</Kbd>
        </button>
        <button
          className="btn ghost icon"
          aria-label={`Switch to ${theme === "ink" ? "light" : "dark"} theme`}
          title="Theme"
          onClick={() => setTheme(theme === "ink" ? "paper" : "ink")}
        >
          <Icon name={theme === "ink" ? "sun" : "moon"} />
        </button>
      </div>
    </header>
  );
}

function StatusRail() {
  const online = useStore((s) => s.engineOnline);
  const edits = useStore((s) => s.edits);
  const game = useStore((s) => s.game);
  const saves = useStore((s) => s.saves);
  const undoLast = useStore((s) => s.undoLast);
  const writeEdits = useStore((s) => s.writeEdits);
  const count = Object.keys(edits).length;

  return (
    <footer className="status">
      <span>
        <span className={`led ${online === null ? "" : online ? "on" : "off"}`} />
        {online === null ? "starting engine" : online ? "engine ready" : "engine offline"}
      </span>
      {game && <span>{saves.length} saves</span>}
      <span className="grow" />
      {count > 0 && (
        <button className="staged" onClick={() => void writeEdits()} title="Write staged changes (Ctrl+S)">
          {count} staged, write
        </button>
      )}
      <button onClick={() => void undoLast()} title="Undo the last write">
        undo last write
      </button>
      <span>every write is verified and backed up</span>
    </footer>
  );
}

export function App() {
  const theme = useStore((s) => s.theme);
  const world = useStore((s) => s.world);
  const focusMode = useStore((s) => s.focusMode);
  const dockOpen = useStore((s) => s.dockOpen);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    const s = useStore.getState();
    void s.checkEngine().then(() => {
      if (useStore.getState().engineOnline) void s.loadLibrary();
    });
    const timer = setInterval(() => void useStore.getState().checkEngine(), 15000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const s = useStore.getState();
      const target = event.target as HTMLElement;
      const typing = target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable;
      const mod = event.ctrlKey || event.metaKey;

      if (mod && event.key.toLowerCase() === "k") {
        event.preventDefault();
        s.setPalette(!s.paletteOpen);
      } else if (mod && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void s.writeEdits();
      } else if (mod && event.key.toLowerCase() === "z" && !typing) {
        event.preventDefault();
        void s.undoLast();
      } else if (event.key === "Escape" && !typing && !s.paletteOpen) {
        if (s.focusMode) s.setDock({ focusMode: false });
        else if (s.dockOpen) s.setDock({ dockOpen: false });
      } else if (!typing && !mod && event.key.toLowerCase() === "f" && s.selection[0]) {
        s.setDock({ dockOpen: true, dockTab: s.dockTab === "compare" ? "compare" : "variables", focusMode: !s.focusMode });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="shell">
      <Rail />
      <main className={`stage ${focusMode && dockOpen ? "focus" : ""}`}>
        <section
          className="canvas"
          aria-label="Canvas"
          onClick={focusMode ? () => useStore.getState().setDock({ focusMode: false }) : undefined}
        >
          <ReactFlowProvider key={world}>
            {world === "library" && <LibraryWorld />}
            {world === "game" && <GameWorld />}
            {world === "map" && <ChoiceMap />}
          </ReactFlowProvider>
        </section>
        <Dock />
      </main>
      <StatusRail />
      <CommandPalette />
      <Toasts />
    </div>
  );
}
