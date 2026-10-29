#!/usr/bin/env bun
/**
 * copy-notes — global clipboard → macOS Notes capture agent
 * ---------------------------------------------------------------
 * Watches the system clipboard. Whenever you copy text ANYWHERE, the text is
 * appended to a single Notes note (titled "mylife" by default; see NOTE_NAME).
 *
 * macOS Notes has no plugin API, so this is the supported equivalent: a tiny
 * bun/TypeScript LaunchAgent that polls the clipboard and writes the note via
 * AppleScript.
 *
 * Design (all verified live on macOS 26.x before shipping):
 *   - TRUE APPEND: each capture reads the note's CURRENT body and adds the new
 *     entry, so anything you delete in the note STAYS deleted (it is never
 *     resurrected from history). The live note is the source of truth.
 *   - The title is a real Notes "Title" (Headline) style via <h1>, re-asserted
 *     on every write so it stays a headline despite Notes' lossy read-back.
 *   - Notes ignores CSS px but honors pt; read-back flattens every size, so each
 *     write normalizes surviving content back to one pt size (older entries do
 *     not shrink). Timestamps use bold (which survives read-back) to stand out.
 *   - Never writes the clipboard => no feedback loop.
 *   - Skips concealed clipboards (password managers) + a configurable denylist
 *     so secrets don't get logged into a syncing note.
 *   - Single-writer pidfile lock guards against two instances racing the body.
 *   - captures.jsonl is a local backup only; it never drives the note.
 *
 * Commands:
 *   bun copy-notes.ts run        # run the watcher in the foreground (what launchd runs)
 *   bun copy-notes.ts test       # append one test entry now (also a permission probe)
 *   bun copy-notes.ts doctor     # diagnose permissions / paths / install state
 *   bun copy-notes.ts install    # install + start the LaunchAgent (runs at login)
 *   bun copy-notes.ts uninstall  # stop + remove the LaunchAgent
 *   bun copy-notes.ts status     # show running state + capture count
 *   bun copy-notes.ts logs       # tail the agent logs
 */

import { existsSync, mkdirSync, appendFileSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { rejectionReason } from "./privacy.mjs";

// ─────────────────────────────── Config ───────────────────────────────

const NOTE_NAME = "mylife";       // the Notes note to capture into (change to taste)
const LABEL = "com.copy-notes.agent";

const POLL_MS = 400;              // clipboard poll interval (human copy cadence)
const MAX_BYTES = 1_000_000;      // skip clipboards larger than ~1MB
const OSA_TIMEOUT_MS = 8_000;     // kill a wedged osascript/pbpaste after this

const BODY_PT = 14;               // captured-text font size (pt — Notes ignores px)
const NEWEST_FIRST = true;        // place the newest capture right under the title
// The note title is a real Notes "Title" (Headline) style => <h1>. Re-asserted on
// every write so it stays a headline even though Notes flattens it on read-back.
const TITLE_HTML = `<div><h1>${NOTE_NAME}</h1></div>`;

/**
 * Built-in high-confidence secret shapes that are NEVER logged.
 * A clipboard logger inevitably sees whatever you copy; these patterns plus the
 * concealed-flavor check below are the guardrails. Extend as you like.
 */
const SECRET_PATTERNS: RegExp[] = [
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,     // PEM private keys
  /\bsk-[A-Za-z0-9]{20,}\b/,                 // OpenAI-style secret keys
  /\bgh[pousr]_[A-Za-z0-9]{30,}\b/,          // GitHub tokens
  /\bAKIA[0-9A-Z]{16}\b/,                    // AWS access key id
  /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/,        // Slack tokens
];

// ─────────────────────────── Paths (no hardcoding) ───────────────────────────

const HOME = process.env.HOME ?? "";
const SCRIPT_PATH = import.meta.path;                       // absolute path of this file
const PROJECT_DIR = dirname(SCRIPT_PATH);
const APPLESCRIPT = join(PROJECT_DIR, "notes-io.applescript");
const BUN_PATH = process.execPath;                          // the bun binary running us
const BUN_DIR = dirname(BUN_PATH);

const DATA_DIR = join(HOME, "Library", "Application Support", "copy-notes");
const CAPTURES = join(DATA_DIR, "captures.jsonl");          // local backup log (not used to render)
const STATE_FILE = join(DATA_DIR, "state.json");            // { noteId }
const LOCK_FILE = join(DATA_DIR, "copy-notes.lock");        // pidfile

const LOG_DIR = join(HOME, "Library", "Logs");
const OUT_LOG = join(LOG_DIR, "copy-notes.out.log");
const ERR_LOG = join(LOG_DIR, "copy-notes.err.log");

const PLIST_PATH = join(HOME, "Library", "LaunchAgents", `${LABEL}.plist`);

/**
 * Force a UTF-8 locale for child processes. launchd starts us with NO locale
 * (effectively C/POSIX), which makes `pbpaste` transcode non-ASCII to "?" —
 * e.g. Chinese text becomes "??????". LC_CTYPE pins the character encoding to
 * UTF-8 and overrides any inherited LANG=C. (Verified on macOS 26.5.1.)
 */
const CHILD_ENV = { ...process.env, LANG: "en_US.UTF-8", LC_CTYPE: "en_US.UTF-8" };

type Entry = { ts: number; text: string };

// ─────────────────────────────── Utilities ───────────────────────────────

function ensureDirs() {
  for (const d of [DATA_DIR, LOG_DIR, dirname(PLIST_PATH)]) {
    if (!existsSync(d)) mkdirSync(d, { recursive: true });
  }
}

function log(msg: string) {
  // launchd captures stdout to OUT_LOG; in the foreground it just prints.
  console.log(`${new Date().toISOString()}  ${msg}`);
}

/** Spawn a process with a hard timeout. Returns code/out/err (code 124 => timed out). */
async function sh(args: string[], timeoutMs = OSA_TIMEOUT_MS): Promise<{ code: number; out: string; err: string }> {
  const proc = Bun.spawn(args, { stdout: "pipe", stderr: "pipe", env: CHILD_ENV });
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; proc.kill(9); }, timeoutMs);
  try {
    const [out, err] = await Promise.all([
      new Response(proc.stdout).text(),
      new Response(proc.stderr).text(),
    ]);
    const code = await proc.exited;
    return { code: timedOut ? 124 : code, out, err };
  } finally {
    clearTimeout(timer);
  }
}

// ─────────────────────────────── Clipboard ───────────────────────────────

async function clipboardInfo(): Promise<string> {
  const { out } = await sh(["osascript", "-e", "clipboard info"]);
  return out;
}

/** True if the pasteboard carries a plain-text flavor we can read. */
function infoHasText(info: string): boolean {
  return /«class utf8»|«class ut16»|Unicode text|public\.utf8-plain-text|public\.utf16/.test(info);
}

/** True if the pasteboard is marked concealed/transient (password managers). Best-effort. */
function infoIsConcealed(info: string): boolean {
  return /concealed|org\.nspasteboard\.(Concealed|Transient|AutoGenerated)/i.test(info);
}

async function readClipboardText(): Promise<string> {
  const { out } = await sh(["pbpaste"], 4000);
  return out;
}

function looksSecret(text: string): boolean {
  return SECRET_PATTERNS.some((re) => re.test(text)) || rejectionReason(text) !== null;
}

/** Extra privacy veto. Frontmost app is context, not guaranteed clipboard origin.
 * Use AppKit directly: no Accessibility permission or window-title harvesting. */
async function privacyContext(): Promise<{ source: string; concealed: boolean; generation: number } | null> {
  const script = `ObjC.import('AppKit');
    const app = $.NSWorkspace.sharedWorkspace.frontmostApplication;
    const types = ObjC.deepUnwrap($.NSPasteboard.generalPasteboard.types) || [];
    JSON.stringify({source: ObjC.unwrap(app.localizedName) || '', generation: Number($.NSPasteboard.generalPasteboard.changeCount),
      concealed: types.some(t => /concealed|transient|autogenerated/i.test(t))});`;
  const { code, out } = await sh(["osascript", "-l", "JavaScript", "-e", script]);
  if (code !== 0) return null;
  try { return JSON.parse(out.trim()); } catch { return null; }
}

// ─────────────────────────────── HTML render ───────────────────────────────

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function fmtTs(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** Wrap arbitrary text as readable Notes HTML (pt sizing; one <div> per line). */
function textToDivs(text: string): string {
  const lines = text.replace(/\r\n?/g, "\n").replace(/\n+$/, "").split("\n");
  return lines
    .map((line) =>
      line.length === 0
        ? `<div style="font-size: ${BODY_PT}pt"><br></div>`
        : `<div style="font-size: ${BODY_PT}pt">${esc(line)}</div>`,
    )
    .join("");
}

/** One capture: a blank spacer, a BOLD timestamp (bold survives Notes read-back so
 *  entries stay visually separated even after font-size normalization), then text. */
function entryHtml(e: Entry): string {
  return (
    `<div style="font-size: ${BODY_PT}pt"><br></div>` +
    `<div style="font-size: ${BODY_PT}pt"><b>${fmtTs(e.ts)}</b></div>` +
    textToDivs(e.text)
  );
}

/** Notes flattens every font-size to a tiny px value on read-back, which would
 *  shrink older entries over time. Re-assert our size on whatever comes back. */
function normalizeFonts(html: string): string {
  return html.replace(/font-size:\s*[\d.]+px/gi, `font-size: ${BODY_PT}pt`);
}

/**
 * TRUE APPEND: build the next body from the note's CURRENT (live) body so manual
 * deletions stick. Strip the old first line (title) and re-assert a fresh <h1>
 * title; normalize the surviving content; place the new entry per NEWEST_FIRST.
 */
function buildNextBody(currentBody: string | null, e: Entry): string {
  const fragment = entryHtml(e);
  if (currentBody === null) return TITLE_HTML + fragment;          // note doesn't exist yet
  const nl = currentBody.indexOf("\n");
  const survivors = normalizeFonts(nl === -1 ? "" : currentBody.slice(nl + 1));
  return NEWEST_FIRST ? TITLE_HTML + fragment + survivors : TITLE_HTML + survivors + fragment;
}

// ─────────────────────────────── Notes I/O ───────────────────────────────

function loadState(): { noteId?: string } {
  try { return JSON.parse(readFileSync(STATE_FILE, "utf8")); } catch { return {}; }
}
function saveState(s: { noteId?: string }) {
  writeFileSync(STATE_FILE, JSON.stringify(s, null, 2));
}

function permErr(err: string): Error | null {
  return /-1743/.test(err)
    ? new Error("PERMISSION DENIED (-1743): not authorized to control Notes. See `bun copy-notes.ts doctor`.")
    : null;
}

/** Read the live note body, or null if the note doesn't exist. */
async function getNoteBody(): Promise<string | null> {
  const { noteId } = loadState();
  const { code, out, err } = await sh(["osascript", APPLESCRIPT, "get", NOTE_NAME, noteId ?? ""]);
  if (code !== 0) throw permErr(err) ?? new Error(`osascript get failed (${code}): ${err.trim()}`);
  const body = out.replace(/\n$/, "");
  return body === "__MISSING__" ? null : body;
}

/** Overwrite the note body from clean HTML; returns the note id (creates if absent). */
async function setNoteBody(fullHtml: string): Promise<string> {
  const { noteId } = loadState();
  const { code, out, err } = await sh(["osascript", APPLESCRIPT, "set", NOTE_NAME, noteId ?? "", fullHtml]);
  if (code !== 0) throw permErr(err) ?? new Error(`osascript set failed (${code}): ${err.trim() || out.trim()}`);
  const id = out.trim();
  if (id && id !== noteId) saveState({ noteId: id });
  return id;
}

/** Append one capture to the note via true-append (respects manual deletions). */
async function appendEntryToNote(e: Entry): Promise<string> {
  const current = await getNoteBody();
  return setNoteBody(buildNextBody(current, e));
}

// ─────────────────────────────── Archive ───────────────────────────────

/** Append every capture to a local disk log — a pure backup. It NEVER drives the
 *  note (the live note is the source of truth), so it can't resurrect deletions. */
function appendCapture(e: Entry) {
  appendFileSync(CAPTURES, JSON.stringify(e) + "\n");
}

function captureCount(): number {
  if (!existsSync(CAPTURES)) return 0;
  return readFileSync(CAPTURES, "utf8").split("\n").filter(Boolean).length;
}

// ─────────────────────────────── Single-writer lock ───────────────────────────────

function pidAlive(pid: number): boolean {
  try { process.kill(pid, 0); return true; } catch (e: any) { return e?.code === "EPERM"; }
}

/** Acquire the single-instance lock. Returns false if another live instance holds it. */
function acquireLock(): boolean {
  if (existsSync(LOCK_FILE)) {
    const prev = parseInt(readFileSync(LOCK_FILE, "utf8").trim(), 10);
    if (Number.isFinite(prev) && prev !== process.pid && pidAlive(prev)) return false;
  }
  writeFileSync(LOCK_FILE, String(process.pid));
  const release = () => { try { if (readFileSync(LOCK_FILE, "utf8").trim() === String(process.pid)) rmSync(LOCK_FILE); } catch {} };
  process.on("exit", release);
  for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"] as const) {
    process.on(sig, () => { release(); process.exit(0); });
  }
  return true;
}

// ─────────────────────────────── The watcher ───────────────────────────────

async function runDaemon() {
  ensureDirs();
  if (!acquireLock()) {
    log("another mylife instance is already running — exiting.");
    process.exit(0);
  }
  if (!existsSync(APPLESCRIPT)) {
    log(`FATAL: missing AppleScript at ${APPLESCRIPT}`);
    process.exit(1);
  }

  // True-append mode: we never render from the archive, so we don't touch the note
  // on boot — whatever state it's in (including your manual deletions) is preserved.
  log(`mylife watcher starting. archive=${captureCount()} captures (backup only).`);

  // Initialize `last` to the CURRENT clipboard so we don't capture pre-existing content.
  let last = await readClipboardText();

  // Self-scheduling loop (never overlaps reads, even if a tick stalls).
  for (;;) {
    await Bun.sleep(POLL_MS);
    try {
      const info = await clipboardInfo();
      if (!infoHasText(info)) continue;          // image/file/empty pasteboard
      if (infoIsConcealed(info)) { last = ""; continue; }  // password-manager item

      const context = await privacyContext();
      if (!context || !context.source || context.concealed) { last = ""; continue; }

      const text = await readClipboardText();
      const after = await privacyContext();
      if (!after || after.generation !== context.generation || after.concealed ||
          rejectionReason(text, { ...after, requireSource: true })) continue;
      if (text === last) continue;               // no change (also dedups same-copy-twice)
      last = text;                               // advance at DETECTION, before append

      if (text.trim().length === 0) continue;    // whitespace-only
      if (text.length > MAX_BYTES) { log(`skipped oversized clipboard (${text.length} bytes)`); continue; }
      if (looksSecret(text)) { log("skipped likely-secret clipboard content"); continue; }
      if (rejectionReason(text, { ...context, requireSource: true })) { log("skipped protected context"); continue; }

      const entry: Entry = { ts: Date.now(), text };
      appendCapture(entry);                      // disk backup FIRST (survives any Notes failure)
      try {
        await appendEntryToNote(entry);          // true-append onto the LIVE note
        log(`captured ${text.length} chars`);
      } catch (e: any) {
        log("note write failed (capture is in backup log); inspect Notes permissions");
      }
    } catch (e: any) {
      log("tick error; clipboard contents and exception details are not logged");
    }
  }
}

// ─────────────────────────────── Commands ───────────────────────────────

async function cmdTest() {
  ensureDirs();
  const entry: Entry = { ts: Date.now(), text: `✅ mylife test entry — ${fmtTs(Date.now())}` };
  appendCapture(entry);
  try {
    const id = await appendEntryToNote(entry);
    console.log(`OK — appended to note "${NOTE_NAME}" (id ${id}). Open Notes to confirm.`);
  } catch (e: any) {
    console.error(`FAILED: ${e.message}`);
    if (/-1743|PERMISSION/.test(e.message)) printPermissionHelp();
    process.exit(1);
  }
}

async function cmdDoctor() {
  ensureDirs();
  console.log("mylife doctor");
  console.log("─".repeat(50));
  console.log(`bun:           ${BUN_PATH}`);
  console.log(`script:        ${SCRIPT_PATH}`);
  console.log(`applescript:   ${APPLESCRIPT} ${existsSync(APPLESCRIPT) ? "✓" : "✗ MISSING"}`);
  console.log(`data dir:      ${DATA_DIR}`);
  console.log(`archive:       ${captureCount()} captures`);
  console.log(`plist:         ${PLIST_PATH} ${existsSync(PLIST_PATH) ? "✓ installed" : "✗ not installed"}`);
  const lockHeld = existsSync(LOCK_FILE) && pidAlive(parseInt(readFileSync(LOCK_FILE, "utf8").trim(), 10));
  console.log(`watcher:       ${lockHeld ? "running (pid " + readFileSync(LOCK_FILE, "utf8").trim() + ")" : "not running"}`);

  process.stdout.write("Notes Automation permission… ");
  const { code, err } = await sh(["osascript", "-e", `tell application "Notes" to count notes`]);
  if (code === 0) console.log("✓ granted");
  else { console.log(`✗ DENIED (${err.trim()})`); printPermissionHelp(); }
}

function printPermissionHelp() {
  console.log("");
  console.log("  Notes Automation permission is required (error -1743).");
  console.log("  Fix: System Settings → Privacy & Security → Automation →");
  console.log("       enable Notes for your terminal app (and for 'bun' once it asks).");
  console.log("  Then run:  bun copy-notes.ts test");
  console.log("  If no prompt ever appears, run once from a normal Terminal so macOS can show it.");
  console.log("");
}

function plistXml(): string {
  const path = `${BUN_DIR}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`;
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${BUN_PATH}</string>
        <string>${SCRIPT_PATH}</string>
        <string>run</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>StandardOutPath</key>
    <string>${OUT_LOG}</string>
    <key>StandardErrorPath</key>
    <string>${ERR_LOG}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>${HOME}</string>
        <key>PATH</key>
        <string>${path}</string>
        <key>LANG</key>
        <string>en_US.UTF-8</string>
        <key>LC_CTYPE</key>
        <string>en_US.UTF-8</string>
    </dict>
</dict>
</plist>
`;
}

async function cmdInstall() {
  ensureDirs();
  const uid = process.getuid!();
  const target = `gui/${uid}/${LABEL}`;

  writeFileSync(PLIST_PATH, plistXml());
  console.log(`wrote ${PLIST_PATH}`);

  await sh(["launchctl", "bootout", target], 5000);                 // ignore failure (not loaded yet)
  await Bun.sleep(500);                                             // let bootout settle before re-bootstrap
  // bootstrap can transiently fail with "5: Input/output error" right after a bootout; retry.
  let boot = await sh(["launchctl", "bootstrap", `gui/${uid}`, PLIST_PATH], 5000);
  for (let i = 0; boot.code !== 0 && i < 3; i++) {
    await sh(["launchctl", "bootout", target], 5000);
    await Bun.sleep(1000);
    boot = await sh(["launchctl", "bootstrap", `gui/${uid}`, PLIST_PATH], 5000);
  }
  if (boot.code !== 0) {
    console.error(`bootstrap failed: ${boot.err.trim() || boot.out.trim()}`);
    process.exit(1);
  }
  await sh(["launchctl", "enable", target], 5000);
  // `kickstart` (NO -k) just ensures it's running. bootstrap already started it via
  // RunAtLoad; `-k` would kill that fresh process and trigger a ThrottleInterval delay.
  await sh(["launchctl", "kickstart", target], 5000);
  console.log(`bootstrapped + started ${LABEL}`);

  // Pre-seed / probe the TCC Automation grant (the #1 launchd gotcha).
  process.stdout.write("checking Notes Automation permission… ");
  const probe = await sh(["osascript", "-e", `tell application "Notes" to count notes`]);
  if (probe.code === 0) {
    console.log("✓ granted");
    console.log(`\nDone. Copy text anywhere and it will appear in your Notes note "${NOTE_NAME}".`);
  } else {
    console.log("✗ not yet granted");
    printPermissionHelp();
  }
}

async function cmdUninstall() {
  const uid = process.getuid!();
  const target = `gui/${uid}/${LABEL}`;
  await sh(["launchctl", "bootout", target], 5000);
  if (existsSync(PLIST_PATH)) { rmSync(PLIST_PATH); console.log(`removed ${PLIST_PATH}`); }
  console.log(`stopped + uninstalled ${LABEL}. (Archive kept at ${CAPTURES}.)`);
}

async function cmdStatus() {
  const uid = process.getuid!();
  const { code, out } = await sh(["launchctl", "print", `gui/${uid}/${LABEL}`], 5000);
  console.log(`installed plist: ${existsSync(PLIST_PATH) ? "yes" : "no"}`);
  console.log(`archive: ${captureCount()} captures (${CAPTURES})`);
  if (code === 0) {
    const state = out.match(/state = (\S+)/)?.[1] ?? "?";
    const pid = out.match(/pid = (\d+)/)?.[1] ?? "—";
    console.log(`launchd: ${state} (pid ${pid})`);
  } else {
    console.log("launchd: not loaded");
  }
}

async function cmdLogs() {
  console.log(`out: ${OUT_LOG}`);
  console.log(`err: ${ERR_LOG}`);
  console.log("─".repeat(50));
  for (const f of [OUT_LOG, ERR_LOG]) {
    if (existsSync(f)) {
      const lines = readFileSync(f, "utf8").split("\n").filter(Boolean).slice(-20);
      if (lines.length) { console.log(`# ${f}`); console.log(lines.join("\n")); }
    }
  }
}

function usage() {
  console.log(`copy-notes — clipboard → Notes("${NOTE_NAME}") capture agent

  bun copy-notes.ts run        run the watcher in the foreground
  bun copy-notes.ts test       append one test entry (also a permission probe)
  bun copy-notes.ts doctor     diagnose permissions / paths / install state
  bun copy-notes.ts install    install + start the LaunchAgent (runs at login)
  bun copy-notes.ts uninstall  stop + remove the LaunchAgent
  bun copy-notes.ts status     show running state + capture count
  bun copy-notes.ts logs       tail the agent logs`);
}

const cmd = process.argv[2] ?? "help";
switch (cmd) {
  case "run": await runDaemon(); break;
  case "test": await cmdTest(); break;
  case "doctor": await cmdDoctor(); break;
  case "install": await cmdInstall(); break;
  case "uninstall": await cmdUninstall(); break;
  case "status": await cmdStatus(); break;
  case "logs": await cmdLogs(); break;
  default: usage();
}
