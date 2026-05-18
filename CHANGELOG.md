# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

(Nothing yet.)

---

## [1.0.0] - 2026-05-19

First public release.

### Features

- **Cross-platform background service** that snapshots Git projects to a
  single private GitHub repository on each detected change. Supports
  Linux (systemd user service), macOS (LaunchAgent), and Windows (Task
  Scheduler, with a Startup-folder fallback).
- **Real-time change detection** via `watchdog`. Three modes,
  configurable via `snapshot.mode`:
  - `hybrid` (default): event-driven snapshots with an hourly safety-net
    poll.
  - `watch`: pure event-driven.
  - `polling`: interval-based polling, no extra dependencies (useful for
    network mounts where filesystem events don't fire).
  Events are debounced (default 30 s) so bursts of saves collapse into a
  single snapshot. The watcher automatically falls back to polling if
  `watchdog` cannot be loaded.
- **IDE-aware** operation: snapshot cycles only run while a configured
  editor is active. Built-in list covers JetBrains IDEs, the VS Code
  family (including Cursor and Windsurf), Zed, Sublime Text, Fleet,
  Vim/Neovim, Emacs, Xcode, Eclipse, Godot, Unity, Unreal, and Android
  Studio.
- **Pluggable AI commit-message providers**:
  - `none` — timestamp + file count fallback (no external calls).
  - `gemini` — Google Gemini API.
  - `openai` — OpenAI-compatible endpoints (OpenAI, Ollama, LM Studio,
    llama.cpp server, vLLM).
  - `anthropic` — Anthropic Claude API.
- **Single shared shadow repository** for all projects, with snapshots
  organized as branches under `{project_slug}/{timestamp}`.
- **Orphan-commit snapshot strategy** keeps the shadow repo compact and
  never touches the user's working tree, staging area, or branches.
- **Automatic retention**: snapshot branches older than `retention_days`
  (default 7) are pruned in bulk every `cleanup_interval_seconds`
  (default 6 hours).
- **Sensitive-file guard**: projects with uncommitted changes to files
  matching `ignore_patterns` (`.env`, `id_rsa`, `credentials.json`, etc.)
  are automatically skipped.
- **Comprehensive CLI** on `install.py`:
  - `install` (default), `uninstall`, `status`, `rescan`.
  - `config show / get / set` for inspecting or modifying any config key.
  - `reconfigure-ai`, `reconfigure-mode`, `reconfigure-token` wizards.
  - `check-update` and `update` for in-place updates from GitHub
    Releases, with SHA256 verification and `.bak` rollback for binaries.
- **Opt-in watch-event debug logging** for diagnosing "snapshots aren't
  happening" issues. Enable via `debug.watch_events: true` in config or
  the `PHANTOMGIT_DEBUG_WATCH=1` environment variable; the service then
  logs every queued filesystem event and every debounce state transition.
- **PyInstaller binaries** for Linux, macOS (Apple Silicon), and Windows,
  with all OS-specific watchdog backends bundled.
- **GitHub Actions workflows**:
  - `build.yml` — cross-platform PyInstaller builds, auto-published to
    GitHub Releases on version tags, with SHA256SUMS.txt.
  - `lint.yml` — ruff lint + format check + multi-version Python syntax
    check (3.9–3.12).

### Security

- GitHub tokens are stored at `~/.config/phantomgit/config.json` with
  `0600` permissions on POSIX systems.
- Tokens are never written to `.git/config`. Each git push uses a
  one-shot HTTP Basic auth header (matching `actions/checkout`'s
  `x-access-token` scheme), which works reliably for both classic and
  fine-grained Personal Access Tokens.
- `credential.helper` is blanked for all authenticated git calls, so
  authentication failures fail fast instead of hanging the daemon.
- The binary updater requires a matching SHA256 entry in the release's
  `SHA256SUMS.txt`; updates are aborted on mismatch or missing manifest.
  The previous binary is preserved as a `.bak` sibling for manual
  rollback.
- All HTTP calls have configurable timeouts to prevent service hangs.
- Token redaction is applied to logged git command lines for both
  `Authorization:` headers and `https://user:token@host` URL forms.

### Documentation

- `README.md` with full feature overview, security/privacy disclosure,
  installation instructions for both binary and source, configuration
  reference, AI provider setup, troubleshooting.
- `SECURITY.md` with vulnerability reporting policy via GitHub Private
  Vulnerability Reporting.
- `CONTRIBUTING.md` with development setup, code style, architecture
  overview, and release process.
- `config.example.json` with annotated examples for every supported AI
  provider.

---

[Unreleased]: https://github.com/cormff/phantomgit/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/cormff/phantomgit/releases/tag/v1.0.0