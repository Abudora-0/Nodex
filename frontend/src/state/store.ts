import { create } from "zustand";
import { api, ApiError } from "../api/client";
import type { ChoiceGraph, GameInfo, Library, SaveEntry, SaveLocation, VariablesResponse } from "../api/types";

export type World = "library" | "game" | "map";
export type Theme = "ink" | "paper";
export type DockTab =
  | "variables"
  | "compare"
  | "bulk"
  | "history"
  | "backups"
  | "presets"
  | "repair"
  | "walkthrough"
  | "gallery"
  | "vault"
  | "node";

export interface Toast {
  id: number;
  tone: "ok" | "bad" | "info";
  text: string;
  action?: { label: string; run: () => void };
}

const THEME_KEY = "nodex.theme";
const DOCK_KEY = "nodex.dockWidth";

function readStored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

function writeStored(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable */
  }
}

interface State {
  theme: Theme;
  world: World;
  engineOnline: boolean | null;

  library: Library | null;
  libraryBusy: boolean;

  game: GameInfo | null;
  gameBusy: boolean;
  locations: SaveLocation[];
  activeLocation: string | null;
  saves: SaveEntry[];

  selection: string[];
  dockTab: DockTab;
  dockOpen: boolean;
  dockWidth: number;
  focusMode: boolean;

  variables: VariablesResponse | null;
  variablesBusy: boolean;
  variablesError: string | null;
  includeInternal: boolean;
  edits: Record<string, string>;
  gridJump: string | null;

  graph: ChoiceGraph | null;
  graphBusy: boolean;
  graphError: string | null;
  mapNode: string | null;
  flyTo: string | null;

  paletteOpen: boolean;
  toasts: Toast[];
  historyVersion: number;

  setTheme(theme: Theme): void;
  setWorld(world: World): void;
  setDock(patch: Partial<Pick<State, "dockTab" | "dockOpen" | "dockWidth" | "focusMode">>): void;
  setPalette(open: boolean): void;
  toast(tone: Toast["tone"], text: string, action?: Toast["action"]): void;
  dismissToast(id: number): void;
  bumpHistory(): void;

  checkEngine(): Promise<void>;
  loadLibrary(scan?: boolean): Promise<void>;
  openPath(path: string): Promise<void>;
  closeGame(): void;
  openLocation(path: string): Promise<void>;
  refreshSaves(): Promise<void>;
  select(path: string, mode?: "replace" | "toggle" | "pair"): void;
  clearSelection(): void;
  loadVariables(): Promise<void>;
  setIncludeInternal(value: boolean): void;
  stageEdit(name: string, value: string | null): void;
  discardEdits(): void;
  writeEdits(): Promise<void>;
  undoLast(): Promise<void>;
  loadGraph(): Promise<void>;
  focusMapNode(id: string | null, fly?: boolean): void;
  jumpToVariable(name: string): void;
}

let toastSeq = 0;

export const errorText = (caught: unknown) => (caught instanceof Error ? caught.message : String(caught));

export const useStore = create<State>((set, get) => ({
  theme: readStored<Theme>(THEME_KEY, window.matchMedia?.("(prefers-color-scheme: light)").matches ? "paper" : "ink"),
  world: "library",
  engineOnline: null,

  library: null,
  libraryBusy: false,

  game: null,
  gameBusy: false,
  locations: [],
  activeLocation: null,
  saves: [],

  selection: [],
  dockTab: "variables",
  dockOpen: false,
  dockWidth: readStored<number>(DOCK_KEY, 520),
  focusMode: false,

  variables: null,
  variablesBusy: false,
  variablesError: null,
  includeInternal: false,
  edits: {},
  gridJump: null,

  graph: null,
  graphBusy: false,
  graphError: null,
  mapNode: null,
  flyTo: null,

  paletteOpen: false,
  toasts: [],
  historyVersion: 0,

  setTheme(theme) {
    writeStored(THEME_KEY, theme);
    set({ theme });
  },
  setWorld(world) {
    set({ world, focusMode: false });
  },
  setDock(patch) {
    if (patch.dockWidth !== undefined) writeStored(DOCK_KEY, patch.dockWidth);
    set(patch);
  },
  setPalette(open) {
    set({ paletteOpen: open });
  },
  toast(tone, text, action) {
    const id = ++toastSeq;
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, tone, text, action }] }));
    setTimeout(() => get().dismissToast(id), action ? 9000 : 5000);
  },
  dismissToast(id) {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
  },
  bumpHistory() {
    set((s) => ({ historyVersion: s.historyVersion + 1 }));
  },

  async checkEngine() {
    try {
      await api.health();
      set({ engineOnline: true });
    } catch {
      set({ engineOnline: false });
    }
  },

  async loadLibrary(scan = false) {
    set({ libraryBusy: true });
    try {
      const library = scan ? await api.scanLibrary() : await api.library();
      set({ library });
      if (!scan && library.discovered.length === 0) {
        set({ library: await api.scanLibrary() });
      }
    } catch (caught) {
      get().toast("bad", errorText(caught));
    } finally {
      set({ libraryBusy: false });
    }
  },

  async openPath(path) {
    const trimmed = path.trim().replace(/^"|"$/g, "");
    if (!trimmed) return;
    set({ gameBusy: true });
    try {
      let game: GameInfo | null = null;
      try {
        game = await api.detectGame(trimmed);
      } catch (caught) {
        if (!(caught instanceof ApiError) || caught.status === 404) throw caught;
      }

      let locations: SaveLocation[] = game?.save_locations ?? [];
      if (game && locations.length === 0) {
        locations = game.save_dirs.map((dir) => ({ path: dir, source: "detected", save_count: 0 }));
      }
      if (!game) {
        const listing = await api.listSaves(trimmed);
        const title = trimmed.split(/[\\/]/).filter(Boolean).pop() ?? trimmed;
        game = {
          root: trimmed,
          engine: listing.saves[0]?.engine ?? "renpy",
          version: null,
          version_name: null,
          python_major: 3,
          title,
          game_dir: null,
          save_dirs: [trimmed],
          save_locations: [],
        };
        locations = [{ path: trimmed, source: "save folder", save_count: listing.saves.length }];
      }

      set({
        game,
        locations,
        activeLocation: null,
        saves: [],
        selection: [],
        variables: null,
        edits: {},
        graph: null,
        mapNode: null,
        world: "game",
        dockOpen: false,
        focusMode: false,
      });
      if (locations[0]) await get().openLocation(locations[0].path);
      void get().loadLibrary();
    } catch (caught) {
      get().toast("bad", errorText(caught));
    } finally {
      set({ gameBusy: false });
    }
  },

  closeGame() {
    set({ game: null, saves: [], selection: [], variables: null, edits: {}, graph: null, world: "library", dockOpen: false });
  },

  async openLocation(path) {
    set({ activeLocation: path, selection: [], variables: null, edits: {} });
    await get().refreshSaves();
  },

  async refreshSaves() {
    const location = get().activeLocation;
    if (!location) return;
    try {
      const listing = await api.listSaves(location);
      if (get().activeLocation !== location) return;
      set({ saves: listing.saves });
      // A bare folder of Ren'Py .save files also matches Unity's extension list.
      const game = get().game;
      const engine = listing.saves[0]?.engine;
      if (game && !game.game_dir && engine && engine !== game.engine) set({ game: { ...game, engine } });
    } catch (caught) {
      set({ saves: [] });
      get().toast("bad", errorText(caught));
    }
  },

  select(path, mode = "replace") {
    const { selection, edits } = get();
    let next: string[];
    if (mode === "toggle") {
      next = selection.includes(path) ? selection.filter((p) => p !== path) : [...selection, path];
    } else if (mode === "pair") {
      next = selection.length && selection[0] !== path ? [selection[0], path] : [path];
    } else {
      next = [path];
    }
    const primaryChanged = next[0] !== selection[0];
    if (primaryChanged && Object.keys(edits).length) {
      get().toast("info", "Unsaved edits were discarded when you switched saves.");
    }

    let dockTab = get().dockTab;
    if (next.length === 2 && mode === "pair") dockTab = "compare";
    else if (next.length > 1) dockTab = "bulk";
    else if (["compare", "bulk", "walkthrough", "gallery", "node", "vault"].includes(dockTab)) dockTab = "variables";

    set({
      selection: next,
      dockOpen: next.length > 0,
      dockTab,
      ...(primaryChanged ? { edits: {}, variables: null } : {}),
    });
    if (primaryChanged && next[0]) void get().loadVariables();
  },

  clearSelection() {
    set({ selection: [], dockOpen: false, variables: null, edits: {}, focusMode: false });
  },

  async loadVariables() {
    const path = get().selection[0];
    if (!path) return;
    set({ variablesBusy: true, variablesError: null });
    try {
      const variables = await api.variables(path, get().includeInternal);
      if (get().selection[0] === path) set({ variables });
    } catch (caught) {
      if (get().selection[0] === path) {
        set({ variables: null, variablesError: errorText(caught) });
        const save = get().saves.find((s) => s.path === path);
        if (save?.engine === "renpy") set({ dockTab: "repair" });
      }
    } finally {
      set({ variablesBusy: false });
    }
  },

  setIncludeInternal(value) {
    set({ includeInternal: value });
    void get().loadVariables();
  },

  stageEdit(name, value) {
    set((s) => {
      const edits = { ...s.edits };
      if (value === null) delete edits[name];
      else edits[name] = value;
      return { edits };
    });
  },

  discardEdits() {
    set({ edits: {} });
  },

  async writeEdits() {
    const path = get().selection[0];
    const edits = get().edits;
    const names = Object.keys(edits);
    if (!path || names.length === 0) return;
    try {
      const result = await api.edit(
        path,
        names.map((name) => ({ name, value: edits[name] })),
      );
      set({ edits: {} });
      get().toast("ok", `Wrote ${result.applied.length} change(s). Verified by round trip and backed up.`, {
        label: "Undo",
        run: () => void get().undoLast(),
      });
      get().bumpHistory();
      await Promise.all([get().loadVariables(), get().refreshSaves()]);
    } catch (caught) {
      get().toast("bad", errorText(caught));
    }
  },

  async undoLast() {
    try {
      const result = await api.undo();
      get().toast("ok", `Restored ${result.restored.length} file(s) from backup.`);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        get().toast("bad", "That save changed after the edit (the game may have rewritten it).", {
          label: "Undo anyway",
          run: () =>
            void api
              .undo({ force: true })
              .then(() => get().toast("ok", "Restored from backup."))
              .catch((e) => get().toast("bad", errorText(e))),
        });
      } else {
        get().toast("bad", errorText(caught));
      }
    }
    get().bumpHistory();
    await Promise.all([get().loadVariables(), get().refreshSaves()]);
  },

  async loadGraph() {
    const game = get().game;
    if (!game || game.engine !== "renpy") return;
    if (get().graph?.game_dir && get().graph!.title === game.title) return;
    set({ graphBusy: true, graphError: null });
    try {
      set({ graph: await api.graph(game.root) });
    } catch (caught) {
      set({ graphError: errorText(caught) });
    } finally {
      set({ graphBusy: false });
    }
  },

  focusMapNode(id, fly = false) {
    set({ mapNode: id, flyTo: fly ? id : null, ...(id ? { dockOpen: true, dockTab: "node" as DockTab } : {}) });
  },

  jumpToVariable(name) {
    set({ dockOpen: true, dockTab: "variables", gridJump: name });
  },
}));
