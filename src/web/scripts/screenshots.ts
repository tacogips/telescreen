import { access, mkdir } from "node:fs/promises";
import { spawn, type ChildProcess } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { chromium } from "playwright-core";
import { resolve } from "node:path";
const root = resolve(import.meta.dirname, "../../..");
const output = "/tmp/telescreen-screenshots-web";
type ScreenshotAction = Readonly<{ kind: "click"; role: "button" | "tab" | "link" | "checkbox"; name: string; exact?: boolean }> | Readonly<{ kind: "clickSelector"; selector: string }> | Readonly<{ kind: "select"; name: string; value: string }> | Readonly<{ kind: "press"; key: string }>;
type ScreenshotState = Readonly<{ name: string; path: string; actions: readonly ScreenshotAction[] }>;
const v13States: ScreenshotState[] = [
  ...["Unresolved", "For review", "Regressed", "Escalating", "Archived", "Resolved"].map((label) => ({ name: `stream-${label.toLowerCase().replaceAll(" ", "-")}`, path: "/issues", actions: [{ kind: "click" as const, role: "tab" as const, name: label, exact: false }] })),
  { name: "stream-period-7d", path: "/issues", actions: [{ kind: "select", name: "Period", value: "7d" }] },
  { name: "stream-trend-24h", path: "/issues", actions: [{ kind: "select", name: "Trend window", value: "24h" }] },
  { name: "stream-views", path: "/issues", actions: [] },
  { name: "stream-bulk-selection", path: "/issues", actions: [{ kind: "click", role: "checkbox", name: "Select TELE-3" }] },
  { name: "stream-archive-menu", path: "/issues", actions: [{ kind: "click", role: "checkbox", name: "Select TELE-3" }, { kind: "clickSelector", selector: "details.archive-menu > summary" }] },
  { name: "issue-merged-primary", path: "/issues/v13-iss-01", actions: [{ kind: "click", role: "button", name: "More" }] },
  { name: "issue-merged-child", path: "/issues/v13-iss-02", actions: [{ kind: "click", role: "button", name: "More" }] },
  { name: "alerts-list", path: "/alerts", actions: [] },
  { name: "alerts-new", path: "/alerts/new", actions: [] },
  { name: "alerts-detail-history", path: "/alerts/alert-01", actions: [{ kind: "click", role: "tab", name: "History" }] },
  { name: "dashboard", path: "/dashboard", actions: [] },
  { name: "profile", path: "/settings/profile", actions: [] },
  { name: "integrations", path: "/settings/integrations", actions: [] },
  { name: "tokens", path: "/settings/tokens", actions: [] },
  { name: "ownership-rules", path: "/settings/ownership", actions: [] },
  { name: "member-edit", path: "/settings/users", actions: [{ kind: "click", role: "button", name: "Edit Alex Morgan" }] },
  { name: "workflows-list", path: "/workflows", actions: [] },
  { name: "palette-open", path: "/issues", actions: [{ kind: "press", key: "Control+k" }] },
  { name: "shortcut-help", path: "/issues", actions: [{ kind: "press", key: "?" }] },
  { name: "onboarding", path: "/dashboard", actions: [{ kind: "click", role: "button", name: "Getting started" }] },
];
const v14States: ScreenshotState[] = [
  { name: "data-list", path: "/data", actions: [] },
  { name: "data-list-overdue", path: "/data", actions: [{ kind: "click", role: "tab", name: "Overdue", exact: false }] },
  { name: "data-list-incomplete", path: "/data", actions: [{ kind: "click", role: "tab", name: "Incomplete", exact: false }] },
  { name: "data-detail", path: "/data/data-asset-01", actions: [] },
  { name: "data-detail-values", path: "/data/data-asset-01", actions: [{ kind: "click", role: "tab", name: "Values" }] },
  { name: "data-detail-record-event", path: "/data/data-asset-01", actions: [{ kind: "click", role: "button", name: "Record event" }] },
  { name: "data-new", path: "/data/new", actions: [] },
  { name: "data-fields", path: "/settings/data-fields", actions: [] },
  { name: "data-fields-editor", path: "/settings/data-fields", actions: [{ kind: "click", role: "button", name: "New field" }] },
  { name: "issue-data-link", path: "/issues/v13-iss-01", actions: [] },
  { name: "collectors-inventory-mode", path: "/collectors", actions: [{ kind: "click", role: "button", name: "Connect source" }] },
];
const browsers = [process.env.TELESCREEN_SCREENSHOT_BROWSER, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"].filter((value): value is string => Boolean(value));
let executablePath: string | undefined;
for (const candidate of browsers) { try { await access(candidate); executablePath = candidate; break; } catch { /* try next known browser */ } }
if (executablePath === undefined) { console.error("No Chrome or Chromium browser found; set TELESCREEN_SCREENSHOT_BROWSER."); process.exitCode = 2; }
else {
  const build = spawn("bun", ["run", "build", "--outDir", output, "--emptyOutDir"], { cwd: resolve(root, "src/web"), env: { ...process.env, VITE_TELESCREEN_MOCK: "1" }, stdio: "inherit" });
  const buildCode = await new Promise<number>((resolveCode) => build.once("exit", (code) => resolveCode(code ?? 1)));
  if (buildCode !== 0) process.exitCode = buildCode;
  else {
    const preview: ChildProcess = spawn("bun", ["x", "vite", "preview", "--outDir", output, "--host", "127.0.0.1", "--port", "4319", "--strictPort"], { cwd: resolve(root, "src/web"), stdio: "ignore" });
    try {
      let ready = false;
      for (let attempt = 0; attempt < 40 && !ready; attempt += 1) {
        if (preview.exitCode !== null) throw new Error("Preview server exited before becoming ready.");
        try { const response = await fetch("http://127.0.0.1:4319/"); ready = response.ok; } catch { await delay(250); }
      }
      if (!ready) throw new Error("Preview server did not become ready.");
      const browser = await chromium.launch({ executablePath, headless: true });
      try {
        const page = await browser.newPage(); let errors = 0;
        page.on("pageerror", (error) => { errors += 1; console.error(`[pageerror ${page.url()}] ${error.message}`); });
        page.on("response", (response) => { if (response.status() >= 400) console.error(`[http ${response.status()}] ${response.url()}`); }); page.on("console", (message) => { if (message.type() === "error") { errors += 1; console.error(`[console ${page.url()}] ${message.text()}`); } });
        const states: ScreenshotState[] = [...v13States, ...v14States, ...["/my-tasks", "/search?q=TELE", "/runs", "/police", "/collectors", "/policies", "/escalation", "/audit", "/assistant", "/settings", "/login", "/settings/templates", "/settings/templates/system.workflow_notify"].map((path) => ({ name: path.slice(1).replaceAll("/", "-"), path, actions: [] }))];
        const discoverFirst = async (list: string, prefix: string): Promise<string | null> => { await page.goto(`http://127.0.0.1:4319${list}`, { waitUntil: "networkidle" }); return page.locator(`a[href^="${prefix}/"]`).first().getAttribute("href"); };
        for (const [list, prefix, suffix] of [["/issues", "/issues", ""], ["/workflows", "/workflows", ""], ["/runs", "/runs", ""], ["/policies", "/policies", ""], ["/collectors", "/collectors", "?tab=coverage"]] as const) { const found = await discoverFirst(list, prefix); if (found !== null) states.push({ name: `${prefix.slice(1)}-first${suffix ? "-coverage" : ""}`, path: `${found}${suffix}`, actions: [] }); }
        const destination = "/tmp/telescreen-v13-p03-screenshots"; await mkdir(destination, { recursive: true });
        const strict = process.env.TELESCREEN_SCREENSHOTS_STRICT === "1";
        for (const theme of ["light", "dark"] as const) { await page.emulateMedia({ colorScheme: theme }); for (const state of states) { await page.goto(`http://127.0.0.1:4319${state.path}`, { waitUntil: "networkidle" }); let skipped = false; for (const action of state.actions) { if (action.kind === "press") await page.keyboard.press(action.key); else if (action.kind === "select") { const target = page.getByRole("combobox", { name: action.name, exact: true }); if (await target.count() === 0) { skipped = true; if (strict) process.exitCode = 1; break; } await target.first().selectOption(action.value); } else if (action.kind === "clickSelector") { const target = page.locator(action.selector); if (await target.count() === 0) { skipped = true; if (strict) process.exitCode = 1; break; } await target.first().click(); } else { const target = page.getByRole(action.role, { name: action.name, exact: action.exact ?? true }); if (await target.count() === 0) { skipped = true; if (strict) process.exitCode = 1; break; } await target.first().click(); } } if (skipped) console.error(`state-skipped: ${state.name}`); await page.screenshot({ path: resolve(destination, `${state.name}-${theme}.png`), fullPage: true }); } }
        await page.setViewportSize({ width: 390, height: 844 }); await page.goto("http://127.0.0.1:4319/issues", { waitUntil: "networkidle" }); await page.screenshot({ path: resolve(destination, "issues-mobile-light.png"), fullPage: true });
        if (errors > 0) { console.error(`Browser reported ${errors} errors.`); process.exitCode = 1; }
      } finally { await browser.close(); }
    } catch (error) { console.error(error); process.exitCode = 1; }
    finally { if (preview.exitCode === null) { preview.kill("SIGTERM"); await new Promise<void>((resolveExit) => preview.once("exit", () => resolveExit())); } }
  }
}
