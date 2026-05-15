# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- **Real-time change detection** via the `watchdog` library. Three modes
  selectable at install or via `reconfigure-mode`:
  - `hybrid` (default): event-driven snapshots + hourly safety-net poll.
  - `watch`: pure event-driven.
  - `polling`: original interval-based polling (no extra dependencies).
  Events are debounced for 30 seconds by default (configurable via
  `snapshot.debounce_seconds`) so bursts of saves collapse into a single
  snapshot. The watcher automatically falls back to polling if
  `watchdog` is unavailable or no projects can be watched.
- New CLI subcommand: `reconfigure-mode` switches between detection
  modes and installs the appropriate dependencies.
- Configuration keys: `snapshot.mode`, `snapshot.debounce_seconds`,
  `snapshot.watch_tick_seconds`, `snapshot.safety_poll_interval_seconds`.
- PyInstaller binaries now ship with `watchdog` and all its OS-specific
  observer backends (inotify on Linux, FSEvents on macOS,
  ReadDirectoryChangesW on Windows) bundled, so watch mode works out of
  the box for binary installations.

### Changed

- **Project renamed to PhantomGit** (previously "Auto-Committer").
  Configuration directory moved from `~/.config/auto-committer/` to
  `~/.config/phantomgit/`. Service identifiers, shadow repo default
  (`phantomgit-shadow`), binary names, and PyInstaller spec
  (`phantomgit.spec`) all reflect the new name. Source filenames
  (`install.py`, `Main.py`) remain the same so existing scripts/links keep
  working.

### Added

- `check-update` and `update` subcommands for automatic in-place updates.
  Works for both Python-source installations (`git pull` + dependency
  refresh) and PyInstaller binaries (downloads platform-specific asset
  from GitHub Releases and verifies SHA256 before atomic replace).
- `--version` flag and `__version__` constant; status output now shows
  the running version and whether it's a source or binary install.
- `update.*` configuration section: `github_repo`, `include_prereleases`,
  `auto_check_enabled`, `auto_check_interval_days`, `asset_pattern`.
- Build workflow now injects the release tag's version into the binary
  on tag pushes, so released binaries report their actual version.

### Security

- The binary updater **requires** a matching SHA256 entry in the release's
  `SHA256SUMS.txt`. Updates are aborted on mismatch or missing manifest.
- The previous binary is preserved as a `.bak` sibling, enabling manual
  rollback after a failed update.

---

## [0.1.0] - Unreleased

Initial public release.

### Added

- Cross-platform background service for snapshotting Git projects to GitHub.
  Supports Linux (systemd user service), macOS (LaunchAgent), and Windows
  (Task Scheduler with Startup-folder fallback).
- IDE-aware operation: snapshot cycles only run while a configured editor
  is active. Configurable list including JetBrains IDEs, VS Code family,
  Cursor, Windsurf, Zed, Vim/Neovim, Emacs, Godot, Unity, Unreal, Xcode,
  and others.
- Pluggable AI commit-message providers:
  - `none` — timestamp + file count fallback (no external calls).
  - `gemini` — Google Gemini API.
  - `openai` — OpenAI-compatible endpoints (OpenAI, Ollama, LM Studio,
    llama.cpp server, vLLM).
  - `anthropic` — Anthropic Claude API.
- Single shared "shadow" private repository for all projects, with
  snapshots organized as branches under `{project_slug}/{timestamp}`.
- Automatic cleanup of expired snapshot branches, configurable via
  `retention_days` and `cleanup_interval_seconds`.
- Orphan-commit snapshot strategy that keeps the shadow repo compact and
  never touches the user's working tree, staging area, or branches.
- Comprehensive CLI on `install.py`:
  - `install` (default) — interactive setup wizard.
  - `uninstall` — removes the OS service and optionally the config dir.
  - `config show / get / set` — inspect or modify any config key.
  - `reconfigure-ai` — re-run the AI provider wizard.
  - `reconfigure-token` — replace the stored GitHub token.
  - `status` — show service state, config summary, recent log lines.
  - `rescan` — re-scan disk for Git projects.
- Sensitive-file detection: projects with uncommitted changes to files
  matching `ignore_patterns` are automatically skipped.
- PyInstaller spec for building single-file binaries on Linux, macOS
  (Intel + Apple Silicon), and Windows.
- GitHub Actions workflows:
  - `build.yml`: cross-platform PyInstaller builds, auto-publishing to
    GitHub Releases on version tags.
  - `lint.yml`: ruff lint + format check + multi-version Python syntax check.

### Security

- GitHub tokens are stored at `~/.config/phantomgit/config.json` with
  `0600` permissions on POSIX systems.
- Tokens are never written to `.git/config`; they are passed to git via
  one-shot `http.extraHeader` arguments.
- Legacy versions of this tool wrote tokens into `.git/config`; the service
  now detects and removes such legacy `shadow` remotes on the first run
  in each project, with a warning suggesting the old token be revoked.
- All HTTP calls have configurable timeouts (default 15 s) to prevent
  service hangs.

### Documentation

- `README.md` with full feature overview, security/privacy disclosure,
  installation, configuration reference, troubleshooting.
- `SECURITY.md` with vulnerability reporting policy via GitHub Private
  Vulnerability Reporting.
- `CONTRIBUTING.md` with development setup, code style, architecture
  overview, and release process.
- `config.example.json` with annotated examples of all configuration
  options including example configs for every supported AI provider.

---

[Unreleased]: https://github.com/cormff/phantomgit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/cormff/phantomgit/releases/tag/v0.1.0