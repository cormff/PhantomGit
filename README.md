# PhantomGit

> *Phantom commits to a shadow repo. Your code is safe — your history stays clean.*

A cross-platform background service that automatically snapshots your Git
projects to a single private GitHub repository, with optional AI-generated
commit messages.

Designed for developers who want a safety net against lost work without
polluting their main branch history. PhantomGit runs invisibly: it never
touches your working tree, staging area, or branches — it captures
snapshots and pushes them out of band, as if a ghost were committing for
you.

---

## Features

- **Real-time change detection** — uses `watchdog` to react to file
  changes in seconds, not minutes. Falls back to polling automatically
  if `watchdog` isn't available or the filesystem doesn't support it.
- **Zero-touch backups** — captures your working tree on a schedule without
  modifying your branch, staging area, or working files.
- **Single shadow repository** — all your projects share one private repo on
  GitHub, organized into branches by project. No more 50 separate auto-backup
  repos cluttering your account.
- **Automatic retention** — snapshot branches older than a configurable
  threshold (default 7 days) are pruned automatically, so the shadow repo
  stays small.
- **Optional AI commit messages** — pluggable provider system. Use Google
  Gemini, OpenAI, Anthropic Claude, or any OpenAI-compatible local LLM
  (Ollama, LM Studio, llama.cpp, vLLM). Or skip AI entirely.
- **Cross-platform** — Linux (systemd user service), macOS (LaunchAgent),
  and Windows (Task Scheduler).
- **IDE-aware** — only runs when one of the configured editors (VS Code,
  PyCharm, Cursor, Vim, Godot, Unity, and many others) is active.
- **Sensitive-file guard** — projects containing files like `.env`, SSH keys,
  or credential files are automatically skipped.
- **Compact storage** — uses orphan commits so the shadow repo grows with
  your changes, not with your entire project history.

---

## Security & Privacy: What You Should Know Before Installing

Before installing, please understand exactly what this service does with
your data:

1. **Your code is pushed to GitHub.** A private repository (default name:
   `phantomgit-shadow`) is created in your GitHub account. Every
   snapshot pushes the full state of your tracked + untracked files
   (except those matching `ignore_patterns`) to a branch in this repo.

2. **If you enable AI commit messages, your diffs are sent to the AI provider.**
   - "Gemini" sends diffs to Google.
   - "OpenAI-compatible" sends diffs to whatever endpoint you configure (this
     can be a local LLM like Ollama running entirely on your machine — in
     that case nothing leaves your computer).
   - "Anthropic" sends diffs to Anthropic.
   - "None" sends nothing to any AI provider; commit messages are just
     timestamp + file count.

3. **Your GitHub token is stored locally** at
   `~/.config/phantomgit/config.json` with `0600` permissions (POSIX) so
   only your user can read it. The token is **never** written into any
   project's `.git/config` — it is passed to git via a one-shot
   `http.extraHeader` argument for each push.

4. **Sensitive files are filtered** based on configurable patterns
   (`.env`, `id_rsa`, `credentials.json`, etc.). If a project has uncommitted
   changes to any matching file, the entire project is skipped for that
   cycle. This is a safety net, not a guarantee — always review the
   `ignore_patterns` list in your config.

5. **Recommended GitHub token scope:** use a fine-grained Personal Access
   Token limited to:
   - Read/write access to **repositories** (Contents, Administration).
   - Do **not** grant access to organization repositories unless you
     specifically want shared backups.

---

## Installation

> **Important:** Install from a **permanent location**, not from `/tmp` or
> any other directory that gets cleared on reboot. The installer records
> absolute paths in the OS service definition; if those paths disappear,
> the service will fail to start. Good locations are `~/phantomgit`,
> `~/code/phantomgit`, or anywhere else under your home directory.

### Option A: Pre-built binary (recommended for end users)

1. Download the binary for your platform from the
   [Releases page](https://github.com/cormff/phantomgit/releases/latest).
2. Verify the checksum against `SHA256SUMS.txt` in the same release.
3. Run it:

   ```bash
   chmod +x phantomgit-linux-x86_64
   ./phantomgit-linux-x86_64
   ```

### Option B: From source

```bash
# 1. Clone to a permanent location (NOT /tmp)
git clone https://github.com/cormff/phantomgit.git ~/phantomgit
cd ~/phantomgit

# 2. (Optional but recommended) create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Linux / macOS
# OR
.venv\Scripts\activate      # Windows

# 3. Run the installer
python install.py
```

The installer will:

1. Ask for your GitHub Personal Access Token.
2. Walk you through choosing an AI provider (or skipping AI).
3. Ask you to pick a change-detection mode (hybrid / watch / polling).
4. Install any missing Python packages automatically.
5. Scan your home directory for Git projects.
6. Register a background service appropriate for your OS.

That's it. The service is now running and will create the shadow GitHub
repo on the first detected change.

> **Note on virtual environments:** if you used a venv during install,
> the service is bound to that venv's Python interpreter. Don't delete
> or move the venv afterwards, or the service will fail to start.

---

## CLI Commands

`install.py` is both an installer and a management tool. When running the
binary, the executable name (`phantomgit`) replaces `python install.py`
in all commands below.

```bash
# Default (install/setup)
python install.py
python install.py install

# Remove the background service
python install.py uninstall

# Show current configuration (secrets masked)
python install.py config show
python install.py config show --unmask           # show secrets in clear text

# Read a single configuration value
python install.py config get ai_provider.model
python install.py config get snapshot.retention_days

# Set a configuration value (JSON-parsed if possible)
python install.py config set snapshot.retention_days 14
python install.py config set snapshot.poll_interval_seconds 600
python install.py config set snapshot.cleanup_enabled false
python install.py config set ai_provider.model gpt-4

# Re-run the AI provider wizard
python install.py reconfigure-ai

# Switch between polling / watch / hybrid change detection modes
python install.py reconfigure-mode

# Replace the stored GitHub token
python install.py reconfigure-token

# Show service status, configuration summary, and recent log lines
python install.py status
python install.py status --log-lines 50

# Re-scan disk for Git projects (e.g., after adding new projects)
python install.py rescan
python install.py rescan --no-interactive

# Check for / apply updates
python install.py check-update              # exit 0 = up-to-date, 2 = available, 1 = error
python install.py update                    # interactive update
python install.py update -y                 # skip confirmation
python install.py update --force            # reinstall current version
python install.py --version                 # show installed version
```

---

## How It Works

### Snapshot creation

When the service detects changes in a watched project, it:

1. Calls `git stash create -u` to capture the current working tree and
   index into a *dangling commit object*. This does **not** modify your
   working tree, staging area, or any branches.
2. Generates a commit message — either via your configured AI provider, or
   a simple timestamp-based fallback.
3. Re-commits the captured tree as an **orphan commit** (no parent), then
   pushes it as a new branch in the shadow repo.

Because the snapshot commits are orphans, the shadow repo only stores
*new blobs* between snapshots — it stays compact even after hundreds of
snapshots.

### Branch naming

Snapshot branches follow the pattern:

```
{project_slug}/{timestamp}
```

For example: `myapp-a3f9c2/20260514-143022`

The slug includes a hash of the absolute project path, so two folders both
named `frontend` in different directories will never collide.

### Retention

Every `cleanup_interval_seconds` (default 6 hours), the service:

1. Lists all branches in the shadow repo via `git ls-remote --heads`.
2. Parses the timestamp from each branch name.
3. Deletes any branch older than `retention_days` (default 7) in bulk
   via a single `git push` with deletion refspecs.

Set `cleanup_enabled` to `false` to disable this.

### IDE detection

The service checks running processes against `scanning.supported_ides`.
Snapshots only run while at least one of these editors is active, plus
one final pass when the editor closes.

The matching is strict (exact name or known suffix patterns like
`pycharm64`, `studio64`) to avoid false positives like the old
`code` → `qrencode` collision.

### Change detection modes

PhantomGit supports three modes, configurable via `snapshot.mode`:

| Mode | How it works | When to use |
|------|--------------|-------------|
| **`hybrid`** (default) | Real-time file watching via `watchdog` + a periodic "safety net" poll every hour. | Best for almost everyone. Reacts in seconds, plus a backstop for missed events. |
| **`watch`** | Pure event-driven via `watchdog`. No periodic polling. | Slightly lower overhead. Use if your projects live on a local filesystem you fully trust. |
| **`polling`** | Checks every `poll_interval_seconds` (default 15 min). No `watchdog` dependency. | Use for projects on network mounts (NFS, SMB, SSHFS) where filesystem events don't fire, or on systems where `watchdog` won't install. |

In `watch` and `hybrid` modes:

- Filesystem events trigger a **debounce timer** (`debounce_seconds`,
  default 30). When you save a file, PhantomGit waits 30 seconds of
  inactivity before snapshotting. This collapses bursts of saves
  (auto-format-on-save, find-and-replace across many files) into a
  single snapshot.
- Events inside `.git/`, inside any `exclude_dirs` directory, or matching
  noisy extensions (`.pyc`, `.log`, `.swp`, `.tmp`, etc.) are filtered
  out at the source.
- The watcher automatically restarts when you add or remove projects
  via `rescan`.

To switch modes after install:

```bash
python install.py reconfigure-mode
```

---

## Configuration Reference

The configuration lives at `~/.config/phantomgit/config.json`. You can
edit it directly, but it's safer to use `install.py config set` to avoid
syntax errors.

### Key paths

| Path | Default | Description |
|---|---|---|
| `github.token` | (prompted) | GitHub Personal Access Token. |
| `github.api_url` | `https://api.github.com` | GitHub API base. Change for GitHub Enterprise. |
| `ai_provider.type` | `none` | One of `none`, `gemini`, `openai`, `anthropic`. |
| `snapshot.poll_interval_seconds` | `900` | How often (in seconds) to check for changes in `polling` mode. |
| `snapshot.mode` | `hybrid` | `polling` / `watch` / `hybrid`. See "Change detection modes" above. |
| `snapshot.debounce_seconds` | `30` | In watch/hybrid mode, wait this long for the next event before snapshotting. |
| `snapshot.watch_tick_seconds` | `5` | How often the watch loop wakes to process queued events. |
| `snapshot.safety_poll_interval_seconds` | `3600` | In hybrid mode, run a full polling sweep this often as a backstop. |
| `snapshot.shadow_repo_name` | `phantomgit-shadow` | Name of the private repo on GitHub. |
| `snapshot.branch_template` | `{project_slug}/{timestamp}` | Branch naming pattern. |
| `snapshot.retention_days` | `7` | Snapshots older than this are deleted. |
| `snapshot.cleanup_enabled` | `true` | Master switch for branch pruning. |
| `snapshot.cleanup_interval_seconds` | `21600` | How often to run pruning (default 6 hours). |
| `snapshot.max_diff_chars` | `30000` | Truncate diffs longer than this before sending to AI. |
| `scanning.supported_ides` | (long list) | Process names that trigger snapshot cycles. |
| `scanning.exclude_dirs` | `[node_modules, venv, ...]` | Directories skipped during project scan. |
| `scanning.ignore_patterns` | `[.env, id_rsa, ...]` | Patterns that disqualify a project from being snapshotted. |
| `scanning.search_dirs` | (auto-detected) | Where to look for Git projects during scan. |
| `timeouts.request_seconds` | `15` | HTTP timeout for GitHub and AI requests. |
| `timeouts.git_seconds` | `60` | Timeout for individual git commands. |
| `update.github_repo` | `cormff/phantomgit` | Repository to fetch updates from. Change this if you maintain a fork. |
| `update.include_prereleases` | `false` | When `true`, `update` considers RC / beta releases. |
| `update.auto_check_enabled` | `true` | Reserved for a future background-check feature. |
| `update.auto_check_interval_days` | `7` | Reserved for a future background-check feature. |
| `debug.watch_events` | `false` | When `true`, the service logs every filesystem event it queues and every debounce check, prefixed with `[DEBUG]`. Useful for diagnosing "snapshots aren't happening" issues in watch / hybrid mode. Off by default. |

See `config.example.json` for a full annotated example.

---

## AI Provider Setup

### None (no API key needed)

Commit messages will look like:
`Backup 2026-05-14 14:30 (3 file(s) changed)`

```bash
python install.py config set ai_provider '{"type":"none"}'
```

### Google Gemini

Get an API key from [Google AI Studio](https://aistudio.google.com/apikey).

```bash
python install.py reconfigure-ai   # easiest
```

### OpenAI

Get an API key from [OpenAI Platform](https://platform.openai.com/api-keys).

### Local LLMs (Ollama, LM Studio, llama.cpp, vLLM)

All of these expose an OpenAI-compatible API. Choose "OpenAI-compatible"
in the wizard and point to your local endpoint:

| Backend | Default base URL | API key |
|---|---|---|
| Ollama | `http://localhost:11434/v1` | empty |
| LM Studio | `http://localhost:1234/v1` | empty |
| llama.cpp server | `http://localhost:8080/v1` | empty |
| vLLM | `http://localhost:8000/v1` | empty |

When the wizard detects a localhost URL, it skips the API key prompt
(local LLMs typically don't need auth).

**This is the most privacy-preserving option** — your diffs never leave
your machine.

### Anthropic Claude

Get an API key from the [Anthropic Console](https://console.anthropic.com/).

---

## Updating

PhantomGit can update itself, regardless of whether you installed
from source or from a pre-built binary.

```bash
# Check whether a newer version exists (no download)
python install.py check-update

# Download and apply the latest release (prompts for confirmation)
python install.py update

# Apply without the confirmation prompt (useful for cron / scripts)
python install.py update -y

# Reinstall the current version (recovery)
python install.py update --force

# Show your installed version
python install.py --version
```

When you run as a **PyInstaller binary**, `update` does this:

1. Calls the GitHub Releases API to find the latest release.
2. Downloads the asset for your platform (e.g. `phantomgit-linux-x86_64`).
3. Downloads `SHA256SUMS.txt` and **verifies the checksum**. A mismatch
   aborts the update.
4. Stops the running service.
5. Atomically replaces the binary (the previous version is preserved as
   `phantomgit.bak` for manual rollback).
6. Restarts the service.

When you run from **Python source**, `update` does this:

1. Runs `git pull --ff-only` in the source directory.
2. Re-installs `requirements.txt`.
3. Restarts the service.

### Configuring updates

```bash
# Use a fork instead of the upstream repository
python install.py config set update.github_repo myuser/my-fork

# Include pre-releases (RC, beta, etc.)
python install.py config set update.include_prereleases true

# Disable automatic update checks
python install.py config set update.auto_check_enabled false
```

### Exit codes for `check-update`

`check-update` is designed to be script-friendly:

| Exit code | Meaning            |
|-----------|--------------------|
| 0         | Already up to date |
| 2         | Update available   |
| 1         | Error              |

Example:

```bash
python install.py check-update
case $? in
  0) echo "Up to date";;
  2) python install.py update -y;;
  *) echo "Update check failed";;
esac
```

---

## Troubleshooting

### Service won't start

Check the log file:

```bash
tail -f ~/.config/phantomgit/service.log
```

Or use the status command:

```bash
python install.py status --log-lines 50
```

If the log file is empty but the service shows as "active", check whether
the service is in a restart loop:

```bash
# Linux
journalctl --user -u phantomgit.service -n 50
# macOS
log show --predicate 'subsystem == "com.user.phantomgit"' --last 10m
```

A common cause is that the installer was run from a temporary directory
(like `/tmp`) or a virtual environment that no longer exists. Reinstall
from a permanent location:

```bash
python install.py uninstall
mv phantomgit ~/phantomgit   # or wherever
cd ~/phantomgit
python install.py
```

### "git command not found"

Ensure `git` is installed and on `PATH`. PhantomGit doesn't bundle git.

### Linux: service stops after logout

By default, systemd user services only run while you're logged in. To
keep the service running after logout:

```bash
loginctl enable-linger $USER
```

### GitHub: "Bad credentials"

Your token may have been revoked or expired. Regenerate it on GitHub and
update with:

```bash
python install.py reconfigure-token
```

### Snapshots happen too frequently / not frequently enough

In `polling` mode:

```bash
python install.py config set snapshot.poll_interval_seconds 300   # 5 minutes
python install.py config set snapshot.poll_interval_seconds 1800  # 30 minutes
```

In `watch` or `hybrid` mode, tune the debounce window instead:

```bash
python install.py config set snapshot.debounce_seconds 10   # snapshot 10s after last save
python install.py config set snapshot.debounce_seconds 60   # be more patient
```

### Watch mode isn't reacting to changes

- Check the log: `tail -f ~/.config/phantomgit/service.log` — there
  should be a line like "File watcher started (N project(s))".
- If you see "Falling back to polling mode", the `watchdog` package
  probably isn't installed. Run `pip install watchdog` (or
  `python install.py reconfigure-mode` and pick hybrid again — it
  reinstalls dependencies).
- Network mounts (NFS, SMB, SSHFS) generally don't deliver filesystem
  events. Switch to `polling` mode for those projects:
  `python install.py config set snapshot.mode polling`.
- On Linux, the kernel limits the number of inotify watches per user
  (`/proc/sys/fs/inotify/max_user_watches`). Large monorepos can hit
  this limit. Raise it with: `echo fs.inotify.max_user_watches=524288
  | sudo tee -a /etc/sysctl.conf && sudo sysctl -p`.
- If none of the above helps, enable **watch-event debug logging** (see
  below) to see exactly which events PhantomGit is receiving and how
  the debounce timer is behaving.

### Enabling watch-event debug logging

When file changes seem to be ignored, you can ask the service to log
every event it queues and every debounce tick. There are two ways to
turn it on:

**Persistent (config file):**

```bash
python install.py config set debug.watch_events true
systemctl --user restart phantomgit.service     # Linux
# Or restart the LaunchAgent / Task Scheduler task on macOS / Windows.
```

**One-shot (foreground run, no service restart):**

```bash
systemctl --user stop phantomgit.service
PHANTOMGIT_DEBUG_WATCH=1 ~/phantomgit/.venv/bin/python ~/phantomgit/main.py
# Reproduce the issue, then Ctrl+C and restart the service:
systemctl --user start phantomgit.service
```

With debug logging on, you will see lines like:

```
[INFO] [DEBUG] dispatch queued event=modified path=.../foo.py qsize=3
[INFO] [DEBUG] drain_pending waiting debounce=30s ages={'.../proj': 12.4}
[INFO] [DEBUG] drain_pending ready=['/home/me/myproject']
```

What to look for:

- **No `dispatch queued` lines at all** → the watcher isn't seeing the
  events. Most likely the project is on a network mount or hit the
  inotify watch limit.
- **`dispatch queued` lines but no `drain_pending ready`** → events are
  arriving but never settling because something keeps touching files in
  the project. Increase `snapshot.debounce_seconds` or look at the
  `path=` values in the queued lines to identify the noisy source.
- **`dispatch queued` with `qsize` climbing to 10000** → the queue is
  full. PhantomGit's default queue size is 10,000 events; if you have
  an active project tree with constant noise (large `.venv`, indexer
  output, etc.) you may need to add the noisy dirs to `exclude_dirs`.

Don't forget to turn debug logging off when you're done — it produces a
lot of output:

```bash
python install.py config set debug.watch_events false
systemctl --user restart phantomgit.service
```

### My local Ollama / LM Studio isn't being called

- Verify the endpoint with `curl http://localhost:11434/v1/models`.
- Check `python install.py config show` and confirm the `base_url`.
- Inspect the log for `[WARN] AI provider 'openai' failed`.

### I want to exclude a specific project

Edit `~/.config/phantomgit/projects.json` and move the path from
`active_projects` to `excluded_projects`.

### I want to migrate to a different shadow repo name

```bash
python install.py config set snapshot.shadow_repo_name my-new-name
```

The new repo will be created automatically on the next snapshot. (Old
snapshots in the previous repo are not migrated.)

---

## Uninstalling

```bash
python install.py uninstall
```

This will:

1. Stop and remove the background service from your OS.
2. Optionally delete the local configuration directory (`~/.config/phantomgit/`).

It will **not** delete:

- The shadow repository on GitHub. Go to
  [your GitHub repositories](https://github.com/settings/repositories)
  to remove it manually.
- Any past snapshots already pushed.

---

## File Layout

```
phantomgit/
├── install.py            # Installer + CLI management tool
├── main.py               # The background service entry point
├── requirements.txt
├── config.example.json   # Annotated example of every config option
├── phantomgit.spec       # PyInstaller build spec
├── README.md
├── CHANGELOG.md
├── SECURITY.md
├── CONTRIBUTING.md
├── LICENSE
└── .gitignore
```

After installation, runtime files live in:

```
~/.config/phantomgit/
├── config.json           # Your config (chmod 0600 on POSIX)
├── projects.json         # Active + excluded project paths
└── service.log           # Service log
```

---

## Project Status

PhantomGit is a young project. Core functionality (snapshotting,
retention, AI commit messages, cross-platform service installation) is
stable enough for daily use, but please:

- **Back up anything irreplaceable independently.** PhantomGit is a
  safety net, not your primary backup.
- **Report bugs!** This project doesn't have an automated test suite yet
  (see [CONTRIBUTING.md](CONTRIBUTING.md)), so real-world feedback is
  how rough edges get found.

---

## Contributing

Bug reports, feature requests, and pull requests are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) for development setup and the PR
checklist.

When reporting a bug, please include:

- Your OS and Python version.
- The output of `python install.py status`.
- The last ~50 lines of `~/.config/phantomgit/service.log`.

For security issues, please follow the private reporting process in
[SECURITY.md](SECURITY.md) instead of opening a public issue.

---

## License

See [LICENSE](LICENSE). PhantomGit is MIT-licensed.