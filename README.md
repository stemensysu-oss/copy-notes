# copy-notes

## This fork: Windows journal + conservative privacy filtering

Forked from [lijigang/copy-notes](https://github.com/lijigang/copy-notes); original MIT license retained.

- **macOS still writes to Apple Notes.** The Bun/AppleScript implementation remains below. A shared strict text policy now runs before either Notes or its local backup is written. AppKit checks concealed/transient formats and the frontmost app as an additional privacy veto; clipboard generation is rechecked before capture. This macOS change requires validation on a Mac; Windows tests do not establish macOS runtime compatibility.
- **Windows writes one local Markdown journal per day** to a directory selected during installation. Each accepted copy appends a timestamped section to `YYYY-MM-DD.md`; a new day starts a new file. It does not write Apple Notes and makes no network requests. A sync folder may independently sync those notes.
- **Text only.** Images and files are intentionally excluded in this version.
- **Automatic startup and recovery:** per-user interactive login task, a watchdog which restarts the worker, job-object cleanup, and a recurring task trigger to recover the watchdog. No admin account or stored password is needed. Clipboard access starts after the user signs in, not at the pre-login boot screen.

### Strict privacy policy (read before enabling)

The default deliberately skips short text, standalone identifiers/names, email addresses, account/password labels, common key/token shapes, long opaque strings, digit sequences and URLs containing query/fragment data. Content is Unicode-normalized **for checking only**; accepted original text is preserved. Windows additionally checks clipboard protection formats, owner/foreground application names and sensitive window titles. Unknown source or unreadable privacy metadata fails closed. Password-manager sources are excluded.

Rejected text is **not saved** in notes, backup files, status files or error logs. Diagnostics contain only counters, process IDs and fixed reason codes. The rule data is in `privacy-rules.json` and is shared by both platforms.

**This is conservative filtering, not a guarantee that arbitrary credentials can be recognized.** A password/passphrase embedded in otherwise ordinary prose without a label can still look like legitimate text. Generic clipboard APIs do not reliably identify a username field or a browser login page. The strict defaults trade missed ordinary text for fewer accidental captures. Pause capture before working with credentials. Do not use a clipboard journal where an absolute no-secret-capture guarantee is required. Existing upstream captures are not retroactively scrubbed.

### Windows install

Requires Windows 10/11 and Python 3.10+ including `pythonw.exe`; no pip dependencies.

```powershell
git clone https://github.com/stemensysu-oss/copy-notes.git
cd copy-notes
& .\windows\install.ps1 -OutputDirectory 'D:\YourVault\raw\journals\Win-Copy'
```

The installer copies the application to `%USERPROFILE%\Documents\WinCopy\app`, stores your private destination in `%USERPROFILE%\Documents\WinCopy\config.json`, and creates the `WinCopy` task. It uses interactive user access with limited privileges. If task registration is denied, installation is **not** complete. Do not move or remove Python while it is installed.

Commands (PowerShell):

```powershell
$app = Join-Path $env:USERPROFILE 'Documents\WinCopy\app\windows\win_copy.py'
python $app status
python $app pause
python $app resume
& (Join-Path $env:USERPROFILE 'Documents\WinCopy\app\windows\uninstall.ps1')
```

Pause survives restarts; resume skips clipboard contents already present. Uninstall removes the startup task and stops its processes but preserves the program and all captured notes. To start again after uninstall, rerun the repository's installer. `stop` is an internal persistent stop flag; use pause/resume for ordinary controls.

Files are named `YYYY-MM-DD.md`, using the local date at capture time. Each entry has a time heading, full timestamp, clipboard-owner process name, literal text block and hidden capture ID for retry deduplication. Clipboard content is fenced as literal text. No window titles, usernames, machine names, clipboard history or unsanitized exception messages are stored in diagnostics. Accepted text is kept locally in plaintext; the destination's encryption/sync settings apply.

Daily writes use a local writer lock and a short-lived transaction in `.win-copy-state`. A partial append is completed after restart; an already committed append is not repeated. A conflicting manual edit preserves both the edit and pending transaction for review, rather than overwriting it. The transaction is removed after a successful append. Duplicate copies within 30 seconds are suppressed. Disk failure retries in memory (up to 100 entries); a crash before the transaction is durable can lose unsaved entries. No guarantee of capturing every rapid clipboard replacement is made. Avoid simultaneously editing/syncing the daily file while capture is running.

To migrate from the old per-capture layout, pause capture, update the installed application, and call `storage.migrate_legacy(output_directory)` using the new Windows module. It groups records by their original local date and retains byte-identical originals as `.win-copy-legacy/*.md.bak`. Unknown or malformed files are left untouched. Re-running the migration does not duplicate entries. Resume only after migration is complete.

### Validation

```powershell
python -m unittest discover -s tests -v
node tests/privacy.test.mjs
# Optional live clipboard test; use a new disposable work directory:
python tests/windows_integration.py --work-dir 'D:\Scratch\WinCopy-Test'
```

Unit checks cover credential and protected-context cases, UTF-8 bytes, idempotent file commits and incomplete-write recovery. The live test uses synthetic clipboard data, checks actual Markdown output and exclusions, kills the worker to verify recovery, and checks pause/resume. It temporarily uses the clipboard; avoid copying other material while it runs. Scheduler recovery and real reboot/login must be checked separately on the target computer.

Implementation references: [Windows clipboard listeners](https://learn.microsoft.com/en-us/windows/win32/dataxchg/using-the-clipboard), [privacy formats](https://learn.microsoft.com/en-us/windows/win32/dataxchg/clipboard-formats).

---

## Original macOS documentation

The following upstream text describes the original behavior. This fork's stricter filtering takes precedence: many short/non-prose copies are intentionally skipped.

**Copy text anywhere on macOS → it's automatically appended to a single note in Apple Notes.**

A tiny background agent (bun + TypeScript, zero dependencies) that watches your clipboard. Whenever you copy text in any app, it appends that text — with a timestamp — to one note in the macOS Notes app (named `mylife` by default). Think of it as a frictionless "clipboard journal": everything you copy, collected in one place, searchable and synced across your devices via iCloud.

> macOS Notes has no plugin API, so a third party can't literally "install a plugin" into it. `copy-notes` is the supported equivalent: a `launchd` LaunchAgent that reads the clipboard and writes the note via AppleScript. The effect is exactly what you'd want from a plugin.

---

## Features

- **Capture anywhere** — copy text in any app, it lands in your note within ~half a second.
- **True append, your deletions stick** — entries you delete in the note are *not* resurrected on the next copy. The live note is the source of truth.
- **Proper Title headline** — the note name is a real Notes *Title* style.
- **Consistent, readable formatting** — works around several long-standing Notes/AppleScript rendering bugs (see [Notes quirks](#the-macos-notes-quirks-this-works-around)).
- **Secret-aware** — skips clipboard items marked *concealed* by password managers, plus a configurable denylist (private keys, common API-token shapes).
- **Robust background agent** — runs at login, restarts on crash, single-writer lock, UTF-8 safe (Chinese/emoji/etc.), wedged-process timeouts.
- **Local backup** — every capture is also appended to a local `captures.jsonl` (never used to render the note).

## Requirements

- **macOS** (developed and tested on macOS 26.x; AppleScript + `pbpaste` + `launchctl` are all stock).
- **[bun](https://bun.sh)** (`curl -fsSL https://bun.sh/install | bash`).

## Install

```bash
git clone https://github.com/lijigang/copy-notes.git
cd copy-notes

bun copy-notes.ts test       # writes a test entry; triggers the macOS Automation prompt → click "Allow"
bun copy-notes.ts install    # install + start the LaunchAgent (runs at login)
```

That's it. Copy some text in any app and watch it appear in your `mylife` note.

### First-run permission

Controlling Notes needs the **Automation** permission (Apple Events). The first run pops a dialog — click **Allow**. If it doesn't appear, or you see error `-1743`:

> **System Settings → Privacy & Security → Automation** → enable **Notes** for your terminal app (and for `bun`).

Because a `launchd` agent's permission subject is the `bun` binary, granting it once from an interactive terminal lets the background agent inherit it.

## Usage

| Command | What it does |
|---------|--------------|
| `bun copy-notes.ts install` | install + start the LaunchAgent (runs at login) |
| `bun copy-notes.ts uninstall` | stop + remove the LaunchAgent (your backup log is kept) |
| `bun copy-notes.ts status` | show running state + capture count |
| `bun copy-notes.ts doctor` | diagnose permission / paths / install state |
| `bun copy-notes.ts logs` | tail the agent logs |
| `bun copy-notes.ts test` | append one test entry (also a permission probe) |
| `bun copy-notes.ts run` | run the watcher in the foreground (what `launchd` runs) |

## Configuration

Edit the constants at the top of `copy-notes.ts`:

| Constant | Default | Meaning |
|----------|---------|---------|
| `NOTE_NAME` | `"mylife"` | the Notes note to capture into |
| `POLL_MS` | `400` | clipboard poll interval (ms) |
| `MAX_BYTES` | `1_000_000` | skip clipboards larger than this |
| `BODY_PT` | `14` | body font size (pt; timestamps are bolded) |
| `NEWEST_FIRST` | `true` | place the newest capture right under the title |
| `SECRET_PATTERNS` | see file | regexes that, if matched, skip the capture |

After editing, re-run `bun copy-notes.ts install` to restart the agent.

## ⚠️ Privacy

A clipboard watcher inherently sees everything you copy, and your note **syncs via iCloud** to all your devices. `copy-notes` already skips password-manager *concealed* items and a denylist of obvious secret shapes — but it **cannot** catch everything (e.g. a password you copy by hand). Decide accordingly, and extend `SECRET_PATTERNS` to taste.

## Notes & caveats

- **Text only.** Images / files / non-plain-text clipboards are skipped.
- **You can prune freely.** Delete any entry in the note and it stays gone. As the note grows, delete old entries to keep it snappy (the whole body is re-written on each capture, so very large notes get slower).
- **The title line is managed** (re-asserted every write) — don't rename it; the body entries are yours to edit/delete.
- **Copying the same text twice in a row** isn't recorded twice (no observable change).

## Uninstall

```bash
bun copy-notes.ts uninstall
```

Your `mylife` note and the local backup at `~/Library/Application Support/copy-notes/captures.jsonl` are left in place; delete them by hand if you want.

## How it works

A self-scheduling loop polls the clipboard every `POLL_MS`. On a new text value it:

1. appends a JSON line to a local backup log (`captures.jsonl`),
2. reads the note's **current** body via AppleScript (so your manual deletions are respected),
3. re-asserts a fresh `<h1>` title, normalizes the surviving content's font size, prepends the new entry, and
4. writes the whole body back.

Files it uses:

| Thing | Path |
|-------|------|
| Local backup (not used to render) | `~/Library/Application Support/copy-notes/captures.jsonl` |
| State (`{ noteId }`) | `~/Library/Application Support/copy-notes/state.json` |
| Single-writer lock | `~/Library/Application Support/copy-notes/copy-notes.lock` |
| Logs | `~/Library/Logs/copy-notes.{out,err}.log` |
| LaunchAgent | `~/Library/LaunchAgents/com.copy-notes.agent.plist` |

### The macOS Notes quirks this works around

Apple Notes' AppleScript bridge has several non-obvious behaviors (all verified live):

- **No append primitive** — you must read the whole `body`, concatenate, and set the whole `body` back.
- **Lossy read-back** — reading `body` flattens every font size to a tiny px value, and doesn't reflect the rendered size. So each write re-normalizes surviving content back to one size; older entries don't shrink.
- **`px` ignored, `pt` honored** — CSS `font-size` in px renders tiny; pt works.
- **Title = first body line**, and a real *Title (Headline)* style comes from `<h1>`.
- **`launchd` has no locale** — child `pbpaste` would mangle non-ASCII (e.g. Chinese → `?`); the agent forces `LANG`/`LC_CTYPE=en_US.UTF-8`.

---

## 中文说明

**在 macOS 任何地方复制文字 → 自动追加到 Apple Notes 的一条笔记里**（默认笔记名 `mylife`）。

一个极小的后台代理（bun + TypeScript，零依赖），监听剪贴板：你在任何 app 里复制文字，它就把这段文字加上时间戳，追加到备忘录的那条笔记里。相当于一个无摩擦的「剪贴板日记」——你复制的一切都汇到一处，可搜索，并通过 iCloud 在多设备同步。

> macOS 备忘录没有插件 API，第三方代码无法真的「装插件」。`copy-notes` 是系统支持的等价做法：一个 `launchd` 后台代理，监听剪贴板，用 AppleScript 写笔记。效果和插件完全一样。

### 特点

- **任何地方都能捕获**：在任意 app 复制文字，约半秒后落进笔记。
- **真追加，删了就没**：你在笔记里删掉的条目，下次复制**不会**被恢复——实时笔记本身就是真相源。
- **标题是真正的大标题样式**（Notes Title / Headline）。
- **字体一致、清晰可读**：绕过了备忘录 / AppleScript 的几个老 bug。
- **避开敏感内容**：跳过密码管理器标记 concealed 的剪贴板，外加可配置的正则黑名单（私钥、常见 API token 前缀）。
- **稳健的后台代理**：开机自启、崩溃重启、单写者锁、UTF-8 安全（中文 / emoji 都正常）、卡死进程超时兜底。
- **本地备份**：每条捕获也写进本地 `captures.jsonl`（不参与笔记渲染）。

### 安装

```bash
git clone https://github.com/lijigang/copy-notes.git
cd copy-notes

bun copy-notes.ts test       # 写一条测试，触发「自动化」授权弹窗 → 点「允许」
bun copy-notes.ts install    # 安装并启动后台代理（开机自启）
```

装好后，在任意 app 里复制文字，它就会出现在 `mylife` 笔记里。

第一次需要授予**「自动化」权限**（控制备忘录）。若没弹窗或报 `-1743`：**系统设置 → 隐私与安全性 → 自动化** → 给终端 app（和 `bun`）打开「备忘录」开关。

### 命令

`install` 安装启动 / `uninstall` 停止移除 / `status` 状态 / `doctor` 体检 / `logs` 看日志 / `test` 测试一条 / `run` 前台运行。可调参数在 `copy-notes.ts` 顶部（`NOTE_NAME`、`POLL_MS`、`BODY_PT`、`NEWEST_FIRST`、`SECRET_PATTERNS` 等）。

### ⚠️ 隐私

剪贴板监听本质上会看到你复制的一切，而笔记会经 iCloud 同步到所有设备。本工具已跳过密码管理器 concealed 内容与明显的密钥黑名单，但**挡不全**（比如你手动复制的一段密码）。请据此决定，并按需扩充 `SECRET_PATTERNS`。

---

## License

[MIT](./LICENSE) © 2026 lijigang
