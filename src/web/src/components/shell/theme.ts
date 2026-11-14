import { readStorage, writeStorage } from "./storage";
export type ThemeChoice = "system" | "light" | "dark";
const key = "telescreen.theme";
export function getTheme(): ThemeChoice {
  const value = readStorage(key);
  return value === "light" || value === "dark" ? value : "system";
}
export function applyTheme(choice: ThemeChoice = getTheme()): void {
  if (choice === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", choice);
}
export function setTheme(choice: ThemeChoice): void {
  writeStorage(key, choice);
  applyTheme(choice);
}
