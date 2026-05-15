# Contributing to PhantomGit

Thanks for your interest in contributing! This document covers everything
you need to know to get your changes merged.

---

## Quick Start

```bash
git clone https://github.com/cormff/phantomgit.git
cd phantomgit
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows
pip install -r requirements.txt
pip install ruff pyinstaller       # dev tools
```

To run the service directly without installing it system-wide:

```bash
python install.py             # interactive setup
python main.py                # run the service in the foreground
```

To build a release binary locally:

```bash
pyinstaller phantomgit.spec --clean --noconfirm
./dist/phantomgit --help
```

---

## Before You Start a Big Change

Open an issue first to discuss the design. This avoids two common failure
modes:

1. You build something that doesn't match the project direction.
2. Two people independently start working on the same thing.

For small fixes (typos, obvious bugs, small refactors), feel free to send
a PR directly.

---

## Code Style

We use [Ruff](https://docs.astral.sh/ruff/) for both linting and
formatting. The CI checks both.

```bash
ruff check .          # lint
ruff format .         # auto-format
ruff check --fix .    # auto-fix what's safe
```

General principles:

- **No new hardcoded values**. Everything user-configurable goes into
  `config.json` with sensible defaults.
- **English only** for user-facing strings, log messages, and code
  comments. Issue templates and discussions can be in any language.
- **No emoji** in log output (use `[INFO]`, `[OK]`, `[WARN]`, `[ERROR]`
  prefixes). Emoji is fine in README and discussions.
- **POSIX file permissions**: anything containing secrets gets `chmod 0600`.
- **Never write secrets to disk in plaintext outside of `~/.config/phantomgit/`**.
- **`cwd=` parameter for git calls**, never `os.chdir`.

---

## Architecture Overview

Two files, one logical program:

- **`install.py`** — CLI entry point. Handles setup, uninstall, config
  management, and status reporting. When invoked with `--service`, it
  delegates to `Main.main()` (this is how the PyInstaller binary becomes
  the service).
- **`Main.py`** — The service main loop. Watches projects, generates
  commit messages, pushes snapshots, runs cleanup.

The PyInstaller build (`phantomgit.spec`) bundles both into a single
executable named `phantomgit`.

### Adding a new AI provider

1. Add a `_yourprovider_provider(...)` function in `Main.py` that takes
   the standard signature and returns a `str`.
2. Add a branch in `get_commit_message(...)` that dispatches to it.
3. Add a case in `ask_for_ai_provider(...)` in `install.py` to collect
   the user's credentials and model name.
4. Update `get_required_packages(...)` in `install.py` if you need a
   new dependency.
5. Update the PyInstaller spec (`hidden_imports`) if the dependency has
   submodules.
6. Update the README's "AI Provider Setup" section.
7. Add an entry in `config.example.json` under `ai_provider._examples`.

### Adding a new config key

1. Add it to `build_default_config(...)` in `install.py` with a sensible
   default.
2. Use it in `Main.py` via `config.get("section", {}).get("key", default)`
   so old configs without that key still work.
3. Document it in the README's "Configuration Reference" table.
4. Add it to `config.example.json`.

---

## Testing

This project currently has no automated test suite (yes, we know). When
making changes, **manual testing across at least one OS** is required:

### Minimum test checklist for a PR

- [ ] Service installs without errors: `python install.py`
- [ ] Service starts and reaches "Idle" state
- [ ] Make a change in a watched project, wait one polling cycle, verify
      a snapshot branch appears in the shadow repo
- [ ] Verify your working tree and staging area are untouched
- [ ] `python install.py status` shows correct service state
- [ ] `python install.py uninstall` cleanly removes the service

### If you touched the AI provider code

- [ ] Test with at least one cloud provider AND one local LLM
- [ ] Verify the "fallback" message appears when the AI provider is
      unreachable

### If you touched the cleanup logic

- [ ] Manually set `retention_days` to 0, verify all snapshot branches
      get deleted on the next cleanup cycle
- [ ] Verify the cleanup doesn't touch non-snapshot branches (`main`,
      etc.)

---

## Commit Messages

Use a brief, descriptive imperative title. Examples:

✅ Good:
- `Fix systemd unit file having inline comments`
- `Add Anthropic provider support`
- `Make retention_days configurable per-project`

❌ Avoid:
- `update`
- `Fixed bug`
- `Misc changes`

If the change is non-trivial, include a body explaining *why*.

---

## Pull Request Process

1. Fork the repo and create a branch from `main`.
2. Make your changes, run `ruff check .` and `ruff format .`.
3. Update `CHANGELOG.md` under the `## [Unreleased]` section.
4. Push and open a PR using the [PR template](.github/PULL_REQUEST_TEMPLATE.md).
5. CI must pass (ruff + syntax check + cross-platform build).
6. Address review comments by pushing additional commits.

We typically merge with a squash to keep history clean. Your commit
messages will be combined into the squash commit message.

---

## Release Process (maintainers only)

1. Update `CHANGELOG.md`: move `[Unreleased]` entries under a new
   version heading with the release date.
2. Commit: `Release v1.2.3`
3. Tag: `git tag v1.2.3 && git push origin v1.2.3`
4. The build workflow will automatically:
   - Build binaries for Linux, macOS (Intel + ARM), Windows.
   - Generate SHA256 checksums.
   - Create a GitHub Release with all binaries attached.
5. Edit the release notes on GitHub if needed.

---

## Reporting Security Issues

Please **do not** open public issues for security vulnerabilities. See
[SECURITY.md](SECURITY.md) for the private reporting process.

---

## Code of Conduct

Be respectful. Disagree with code, not people. Assume good faith.

If something feels off, contact the maintainers privately.

---

Thanks again — your contributions make this project better.