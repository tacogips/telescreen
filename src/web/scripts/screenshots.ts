import { access, mkdir } from "node:fs/promises";
import { spawn, type ChildProcess } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { chromium } from "playwright-core";
import { resolve } from "node:path";
const root = resolve(import.meta.dirname, "../../..");
const output = "/tmp/telescreen-screenshots-web";
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
        const pages = ["/issues", "/search?q=TELE", "/workflows", "/runs", "/police", "/collectors", "/escalation", "/audit", "/assistant", "/settings"];
        const discoverFirst = async (list: string, prefix: string): Promise<string | null> => { await page.goto(`http://127.0.0.1:4319${list}`, { waitUntil: "networkidle" }); return page.locator(`a[href^="${prefix}/"]`).first().getAttribute("href"); };
        const firstIssue = await discoverFirst("/issues", "/issues"); if (firstIssue !== null) pages.splice(1, 0, firstIssue);
        const firstWorkflow = await discoverFirst("/workflows", "/workflows"); if (firstWorkflow !== null) pages.push(firstWorkflow);
        const firstRun = await discoverFirst("/runs", "/runs"); if (firstRun !== null) pages.push(firstRun);
        const destination = resolve(root, "tmp/ui-screenshots"); await mkdir(destination, { recursive: true });
        for (const theme of ["light", "dark"] as const) { await page.emulateMedia({ colorScheme: theme }); for (const path of pages) { await page.goto(`http://127.0.0.1:4319${path}`, { waitUntil: "networkidle" }); const name = path.slice(1).replaceAll("/", "-") || "issues"; await page.screenshot({ path: resolve(destination, `${name}-${theme}.png`), fullPage: true }); } }
        await page.setViewportSize({ width: 390, height: 844 }); await page.goto("http://127.0.0.1:4319/issues", { waitUntil: "networkidle" }); await page.screenshot({ path: resolve(destination, "issues-mobile-light.png"), fullPage: true });
        if (errors > 0) { console.error(`Browser reported ${errors} errors.`); process.exitCode = 1; }
      } finally { await browser.close(); }
    } catch (error) { console.error(error); process.exitCode = 1; }
    finally { if (preview.exitCode === null) { preview.kill("SIGTERM"); await new Promise<void>((resolveExit) => preview.once("exit", () => resolveExit())); } }
  }
}
