export const PROJECT_EXTENSION = ".rasplot.json";

export function confirmProjectSwitch(unsaved: boolean, confirmFn: (message: string) => boolean = window.confirm) {
  return !unsaved || confirmFn("This project has unsaved changes. Continue and discard them?");
}

export function projectStatusLabel(path: string | null, unsaved: boolean) {
  if (!path) return unsaved ? "Unsaved project changes" : "Not saved as a project";
  return unsaved ? `Unsaved changes — ${path}` : `Saved — ${path}`;
}
