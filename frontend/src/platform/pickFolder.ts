import { api } from "../api/client";
import { isTauri } from "./env";

export async function pickFolder(title = "Select a game folder"): Promise<string | null> {
  if (isTauri) {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const chosen = await open({ directory: true, multiple: false, title });
    return typeof chosen === "string" ? chosen : null;
  }
  return (await api.browse()).path;
}

/** Export the walkthrough document: a native Save As in the app, a download in the browser. */
export async function exportWalkthrough(gamePath: string, title: string): Promise<string | null> {
  const fileName = `${title || "walkthrough"}.html`;
  if (isTauri) {
    const { save } = await import("@tauri-apps/plugin-dialog");
    const destination = await save({ defaultPath: fileName, filters: [{ name: "HTML", extensions: ["html"] }] });
    if (!destination) return null;
    await api.exportWalkthroughTo(gamePath, destination);
    return destination;
  }
  const blob = await api.exportWalkthrough(gamePath);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  return fileName;
}
