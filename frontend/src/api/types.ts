export type Engine = "renpy" | "rpgmaker" | "unity";

export interface SaveLocation {
  path: string;
  source: string;
  save_count: number;
}

export interface GameInfo {
  root: string;
  engine: Engine;
  version: string | null;
  version_name: string | null;
  python_major: number;
  title: string;
  game_dir: string | null;
  save_dirs: string[];
  save_locations: SaveLocation[];
}

export interface SaveEntry {
  path: string;
  name: string;
  size: number;
  modified: number;
  engine: Engine;
  save_name: string | null;
  renpy_version: string | null;
  readable: boolean;
}

export interface Variable {
  name: string;
  short_name: string;
  kind: string;
  value: string | number | boolean | null;
  preview: string;
  editable: boolean;
  internal: boolean;
  group?: string;
}

export interface VariablesResponse {
  path: string;
  engine?: Engine;
  save_name: string;
  renpy_version: string | null;
  python_major: number | null;
  named?: boolean;
  total: number;
  variables: Variable[];
}

export interface EditResponse {
  written: string;
  applied: { name: string; value: unknown }[];
  verified: boolean;
  batch_id: string;
  backup: string | null;
}

export interface DiffEntry {
  name: string;
  label?: string;
  group?: string;
  before: unknown;
  after: unknown;
  before_missing: boolean;
  after_missing: boolean;
}

export interface DiffResponse {
  left: string;
  right: string;
  engine: Engine;
  changes: DiffEntry[];
}

export interface Diagnosis {
  path: string;
  healthy: boolean;
  recoverable: boolean;
  confidence: string;
  summary: string;
  problems: string[];
  steps: { name: string; ok: boolean; detail: string }[];
  variables_recovered: number;
  written?: string;
}

export interface WTChoice {
  index: number;
  caption: string;
  condition: string | null;
  effects: string[];
  routes: string[];
  downstream: string[];
  inert: boolean;
}

export interface WTMenu {
  key: string;
  label: string | null;
  filename: string;
  line: number;
  meaningful: boolean;
  choices: WTChoice[];
}

export interface Walkthrough {
  game_dir: string;
  title: string;
  scripts_read: number;
  scripts_failed: string[];
  total_menus: number;
  meaningful_menus: number;
  variables: { name: string; count: number }[];
  menus: WTMenu[];
  mod_installed: boolean;
}

export type GraphNodeType = "label" | "menu" | "choice" | "ending";

export interface GraphNode {
  id: string;
  type: GraphNodeType;
  label: string;
  file: string | null;
  line?: number;
  menu?: string;
  meaningful?: boolean;
  condition?: string | null;
  effects?: string[];
  downstream?: string[];
  inert?: boolean;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: "contains" | "option" | "jump" | "call";
}

export interface ChoiceGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  stats: { menus: number; choices: number; labels: number; endings: number };
  partial: boolean;
  game_dir: string;
  title: string;
}

export interface GalleryFlag {
  name: string;
  kind: string;
  value_kind: string;
  references: number;
  present: boolean;
  locked: boolean;
  suggested: boolean;
  enumerable: boolean;
  is_collection: boolean;
  members: string[];
  existing_members: string[];
  current: unknown;
}

export interface GalleryReport {
  persistent: string | null;
  scripts_read: number;
  uses_builtin_gallery: boolean;
  image_count: number;
  seen_images_recorded: number;
  flags: GalleryFlag[];
  suggested: string[];
  locked: string[];
  needs_manual: GalleryFlag[];
}

export interface LibraryItem {
  kind: "game" | "save_folder";
  engine: Engine;
  title: string;
  path: string;
  game_id?: string;
  version?: string | null;
  save_count?: number | null;
  modified: number;
  source: "library" | "appdata" | "locallow";
}

export interface RecentGame {
  root: string;
  title: string;
  engine: Engine;
  game_id: string;
  opened: number;
}

export interface Library {
  roots: string[];
  recents: RecentGame[];
  discovered: LibraryItem[];
}

export interface BackupInfo {
  path: string;
  original_name: string;
  size: number;
  taken: number;
}

export interface VaultFolder {
  folder: string;
  count: number;
  bytes: number;
  backups: BackupInfo[];
}

export interface HistoryEntry {
  id: string;
  batch_id: string;
  ts: number;
  source: string;
  path: string;
  backup: string | null;
  sha256_after: string | null;
  changes: { name: string; before: unknown; after: unknown }[];
  undone: boolean;
  restored_from?: string;
}

export type BulkStatus = "ok" | "missing" | "invalid" | "rejected" | "error";

export interface BulkResult {
  dry_run: boolean;
  batch_id: string | null;
  ok: number;
  failed: number;
  results: { path: string; status: BulkStatus; detail: string; applied?: { name: string; before: unknown; value: unknown }[] }[];
}

export interface Change {
  name: string;
  value: unknown;
}

export interface Preset {
  id: string;
  game_id: string;
  name: string;
  changes: Change[];
  updated?: number;
}
