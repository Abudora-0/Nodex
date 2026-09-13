import { apiTarget } from "../platform/env";
import type {
  BackupInfo,
  BulkResult,
  Change,
  ChoiceGraph,
  Diagnosis,
  DiffResponse,
  EditResponse,
  GalleryReport,
  GameInfo,
  HistoryEntry,
  Library,
  Preset,
  SaveEntry,
  VaultFolder,
  VariablesResponse,
  Walkthrough,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(message);
  }
}

async function send(method: string, path: string, body?: unknown): Promise<Response> {
  const { url, token } = await apiTarget();
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["X-Nodex-Token"] = token;
  const response = await fetch(url + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    const message =
      typeof detail === "string"
        ? detail
        : typeof detail === "object" && detail && "message" in detail
          ? String((detail as { message: unknown }).message)
          : `Request failed (${response.status})`;
    throw new ApiError(message, response.status, detail);
  }
  return response;
}

async function json<T>(method: string, path: string, body?: unknown): Promise<T> {
  return (await send(method, path, body)).json() as Promise<T>;
}

const q = (params: Record<string, string | number | boolean>) =>
  "?" + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString();

export const api = {
  health: () => json<{ status: string }>("GET", "/api/health"),
  browse: () => json<{ path: string | null }>("POST", "/api/browse", {}),

  detectGame: (path: string) => json<GameInfo>("POST", "/api/game/detect", { path }),
  listSaves: (path: string) => json<{ directory: string; saves: SaveEntry[] }>("GET", "/api/saves" + q({ path })),
  variables: (path: string, includeInternal = false) =>
    json<VariablesResponse>("GET", "/api/save/variables" + q({ path, include_internal: includeInternal })),
  edit: (path: string, changes: Change[]) => json<EditResponse>("POST", "/api/save/edit", { path, changes }),
  diff: (left: string, right: string) => json<DiffResponse>("POST", "/api/save/diff", { left, right }),
  bulkEdit: (paths: string[], changes: Change[], dryRun: boolean) =>
    json<BulkResult>("POST", "/api/save/bulk-edit", { paths, changes, dry_run: dryRun }),
  diagnose: (path: string) => json<Diagnosis>("POST", "/api/save/diagnose", { path }),
  repair: (path: string) => json<Diagnosis>("POST", "/api/save/repair", { path }),

  async thumbnailUrl(path: string): Promise<string> {
    const { url, token } = await apiTarget();
    return url + "/api/save/thumbnail" + q(token ? { path, token } : { path });
  },

  library: () => json<Library>("GET", "/api/library"),
  scanLibrary: () => json<Library>("POST", "/api/library/scan"),
  addRoot: (path: string) => json<Library>("POST", "/api/library/roots", { path }),
  removeRoot: (path: string) => json<Library>("DELETE", "/api/library/roots", { path }),
  clearRecents: () => json<Library>("DELETE", "/api/library/recents"),

  backups: (path: string) => json<{ path: string; backups: BackupInfo[] }>("GET", "/api/backups" + q({ path })),
  vault: () => json<{ root: string; folders: VaultFolder[] }>("GET", "/api/backups/vault"),
  restore: (path: string, backup: string) =>
    json<{ restored: string; from: string; previous: string | null }>("POST", "/api/backups/restore", { path, backup }),

  history: (path?: string) => json<{ entries: HistoryEntry[] }>("GET", "/api/history" + (path ? q({ path }) : "")),
  undo: (options: { entry_id?: string; batch_id?: string; force?: boolean } = {}) =>
    json<{ restored: { path: string; from: string }[] }>("POST", "/api/history/undo", options),

  presets: (gameId?: string) => json<{ presets: Preset[] }>("GET", "/api/presets" + (gameId ? q({ game_id: gameId }) : "")),
  savePreset: (preset: Omit<Preset, "id"> & { id?: string }) => json<Preset>("PUT", "/api/presets", preset),
  deletePreset: (id: string) => json<{ deleted: string }>("DELETE", `/api/presets/${encodeURIComponent(id)}`),
  applyPreset: (presetId: string, paths: string[], dryRun: boolean) =>
    json<BulkResult>("POST", "/api/presets/apply", { preset_id: presetId, paths, dry_run: dryRun }),

  walkthrough: (path: string) => json<Walkthrough>("POST", "/api/walkthrough/analyze", { path }),
  graph: (path: string) => json<ChoiceGraph>("POST", "/api/walkthrough/graph", { path }),
  installMod: (path: string, colour: string) =>
    json<{ written: string; annotated_menus: number }>("POST", "/api/walkthrough/install", { path, colour }),
  uninstallMod: (path: string) => json<{ removed: string[] }>("POST", "/api/walkthrough/uninstall", { path }),
  exportWalkthrough: async (path: string) => (await send("POST", "/api/walkthrough/export", { path })).blob(),
  exportWalkthroughTo: (path: string, dest: string) =>
    json<{ written: string }>("POST", "/api/walkthrough/export", { path, dest }),

  gallery: (path: string) => json<GalleryReport>("POST", "/api/gallery/scan", { path }),
  unlockGallery: (path: string, flags: string[], markImages: boolean) =>
    json<{ flags_set: string[]; images_marked: number; written: string | null }>("POST", "/api/gallery/unlock", {
      path,
      flags,
      mark_images: markImages,
    }),
};
