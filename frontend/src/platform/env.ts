export const isTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

interface ApiTarget {
  url: string;
  token: string | null;
}

let target: Promise<ApiTarget> | null = null;

async function resolveTarget(): Promise<ApiTarget> {
  if (!isTauri) return { url: "", token: null };
  const { invoke } = await import("@tauri-apps/api/core");
  for (let attempt = 0; attempt < 150; attempt++) {
    const found = await invoke<ApiTarget | null>("api_base");
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("The Nodex engine did not start.");
}

export function apiTarget(): Promise<ApiTarget> {
  target ??= resolveTarget();
  return target;
}
