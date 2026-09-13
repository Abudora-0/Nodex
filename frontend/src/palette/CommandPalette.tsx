import { Command } from "cmdk";
import fuzzysort from "fuzzysort";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { pickFolder } from "../platform/pickFolder";
import { useStore } from "../state/store";
import { Kbd } from "../ui/bits";
import { ENGINE_NAME } from "../graph/nodes";

interface Item {
  id: string;
  group: string;
  glyph: string;
  label: string;
  hint?: string;
  keywords?: string;
  run(): void;
}

const LIMIT_PER_GROUP = 40;

export function CommandPalette() {
  const open = useStore((s) => s.paletteOpen);
  const setPalette = useStore((s) => s.setPalette);
  const [query, setQuery] = useState("");

  const state = useStore();

  const items = useMemo<Item[]>(() => {
    if (!open) return [];
    const s = useStore.getState();
    const close = () => setPalette(false);
    const act = (fn: () => void) => () => {
      close();
      fn();
    };
    const list: Item[] = [
      { id: "c:open", group: "Commands", glyph: ">", label: "Open a game folder", keywords: "browse load", run: act(async () => {
        const chosen = await pickFolder();
        if (chosen) void s.openPath(chosen);
      }) },
      { id: "c:library", group: "Commands", glyph: ">", label: "Go to library", run: act(() => s.setWorld("library")) },
      { id: "c:scan", group: "Commands", glyph: ">", label: "Rescan library", run: act(() => void s.loadLibrary(true)) },
      { id: "c:theme", group: "Commands", glyph: ">", label: `Switch to ${s.theme === "ink" ? "paper (light)" : "ink (dark)"} theme`, keywords: "dark light mode", run: act(() => s.setTheme(s.theme === "ink" ? "paper" : "ink")) },
      { id: "c:undo", group: "Commands", glyph: ">", label: "Undo the last write", hint: "restores from backup", run: act(() => void s.undoLast()) },
      { id: "c:history", group: "Commands", glyph: ">", label: "Show edit history", run: act(() => s.setDock({ dockOpen: true, dockTab: "history" })) },
      { id: "c:vault", group: "Commands", glyph: ">", label: "Open the backups vault", run: act(() => s.setDock({ dockOpen: true, dockTab: "vault" })) },
    ];
    if (s.game) {
      list.push({ id: "c:saves", group: "Commands", glyph: ">", label: "Show saves", hint: s.game.title, run: act(() => s.setWorld("game")) });
      if (s.game.engine === "renpy" && s.game.game_dir) {
        list.push(
          { id: "c:map", group: "Commands", glyph: ">", label: "Open the choice map", run: act(() => { void s.loadGraph(); s.setWorld("map"); }) },
          { id: "c:walk", group: "Commands", glyph: ">", label: "Walkthrough list and mod", run: act(() => s.setDock({ dockOpen: true, dockTab: "walkthrough" })) },
          { id: "c:gallery", group: "Commands", glyph: ">", label: "Unlock the gallery", run: act(() => s.setDock({ dockOpen: true, dockTab: "gallery" })) },
        );
      }
    }
    if (s.selection[0]) {
      list.push(
        { id: "c:focus", group: "Commands", glyph: ">", label: s.focusMode ? "Leave focus mode" : "Focus on the variable grid", hint: "F", run: act(() => s.setDock({ dockOpen: true, dockTab: "variables", focusMode: !s.focusMode })) },
        { id: "c:presets", group: "Commands", glyph: ">", label: "Apply a preset", run: act(() => s.setDock({ dockOpen: true, dockTab: "presets" })) },
      );
      if (Object.keys(s.edits).length) {
        list.push({ id: "c:write", group: "Commands", glyph: ">", label: `Write ${Object.keys(s.edits).length} staged change(s)`, hint: "Ctrl+S", run: act(() => void s.writeEdits()) });
      }
    }

    const seenGames = new Set<string>();
    for (const r of s.library?.recents ?? []) {
      seenGames.add(r.root.toLowerCase());
      list.push({ id: `g:${r.root}`, group: "Games", glyph: "▤", label: r.title, hint: ENGINE_NAME[r.engine], keywords: r.root, run: act(() => void s.openPath(r.root)) });
    }
    for (const d of s.library?.discovered ?? []) {
      if (seenGames.has(d.path.toLowerCase())) continue;
      list.push({ id: `g:${d.path}`, group: "Games", glyph: "▤", label: d.title, hint: `${ENGINE_NAME[d.engine]}${d.kind === "save_folder" ? ", saves" : ""}`, keywords: d.path, run: act(() => void s.openPath(d.path)) });
    }

    for (const save of s.saves) {
      list.push({ id: `s:${save.path}`, group: "Saves", glyph: "▣", label: save.save_name && save.engine === "renpy" ? save.save_name : save.name, hint: save.name, run: act(() => { s.setWorld("game"); s.select(save.path); }) });
    }

    for (const v of s.variables?.variables ?? []) {
      list.push({ id: `v:${v.name}`, group: "Variables", glyph: "=", label: v.short_name, hint: v.preview, keywords: v.name, run: act(() => s.jumpToVariable(v.name)) });
    }

    for (const n of s.graph?.nodes ?? []) {
      if (n.type === "choice") continue;
      list.push({
        id: `n:${n.id}`,
        group: n.type === "menu" ? "Choice points" : n.type === "ending" ? "Endings" : "Labels",
        glyph: n.type === "menu" ? "◇" : n.type === "ending" ? "◎" : "§",
        label: n.label,
        hint: n.file ? `${n.file}${n.line ? `:${n.line}` : ""}` : undefined,
        run: act(() => { s.setWorld("map"); s.focusMapNode(n.id, true); }),
      });
    }
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, state.library, state.game, state.saves, state.variables, state.graph, state.selection, state.edits, state.theme, state.focusMode]);

  const filtered = useMemo(() => {
    const needle = query.trim();
    const groups = new Map<string, { item: Item; label: ReactNode }[]>();
    if (!needle) {
      for (const item of items) {
        if (item.group !== "Commands" && item.group !== "Games") continue;
        const bucket = groups.get(item.group) ?? [];
        if (bucket.length < 8 || item.group === "Commands") bucket.push({ item, label: item.label });
        groups.set(item.group, bucket);
      }
      return groups;
    }
    if (/^([a-zA-Z]:[\\/]|\\\\|\/|~)/.test(needle)) {
      const path = needle.replace(/^"|"$/g, "");
      groups.set("Open", [
        {
          item: {
            id: "c:open-path",
            group: "Open",
            glyph: ">",
            label: `Open ${path}`,
            hint: "game or save folder",
            run: () => {
              setPalette(false);
              void useStore.getState().openPath(path);
            },
          },
          label: `Open ${path}`,
        },
      ]);
    }
    const results = fuzzysort.go(needle, items, { keys: ["label", "keywords"], threshold: 0.3, limit: 400 });
    for (const r of results) {
      const bucket = groups.get(r.obj.group) ?? [];
      if (bucket.length >= LIMIT_PER_GROUP) continue;
      const labelMatch = r[0];
      bucket.push({ item: r.obj, label: labelMatch && labelMatch.score > 0 ? labelMatch.highlight((m, i) => <mark key={i}>{m}</mark>) : r.obj.label });
      groups.set(r.obj.group, bucket);
    }
    return groups;
  }, [items, query]);

  const firstId = useMemo(() => [...filtered.values()][0]?.[0]?.item.id ?? "", [filtered]);
  const [selected, setSelected] = useState("");
  useEffect(() => setSelected(firstId), [firstId]);
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  if (!open) return null;

  return (
    <>
      <div className="overlay" onClick={() => setPalette(false)} />
      <Command
        className="palette"
        label="Command palette"
        shouldFilter={false}
        value={selected}
        onValueChange={setSelected}
        loop
        onKeyDown={(event) => {
          if (event.key === "Escape") setPalette(false);
        }}
      >
        <Command.Input autoFocus value={query} onValueChange={setQuery} placeholder="Find a game, save, variable, label, or command" />
        <Command.List>
          <Command.Empty>Nothing by that name.</Command.Empty>
          {[...filtered.entries()].map(([group, entries]) => (
            <Command.Group key={group} heading={group}>
              {entries.map(({ item, label }) => (
                <Command.Item key={item.id} value={item.id} onSelect={() => item.run()}>
                  <span className="glyph">{item.glyph}</span>
                  <span className="label">{label}</span>
                  {item.hint && <span className="hint">{item.hint}</span>}
                </Command.Item>
              ))}
            </Command.Group>
          ))}
        </Command.List>
        <div className="palette-foot">
          <span>
            <Kbd>{"↑"}</Kbd>
            <Kbd>{"↓"}</Kbd> move
          </span>
          <span>
            <Kbd>Enter</Kbd> open
          </span>
          <span>
            <Kbd>Esc</Kbd> close
          </span>
          <span style={{ marginLeft: "auto" }}>{query ? "" : `${items.length} things indexed`}</span>
        </div>
      </Command>
    </>
  );
}
