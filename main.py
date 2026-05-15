"""
PhantomGit Service
======================
Watches Git projects, generates AI commit messages (optional), and pushes
snapshots as orphan commits to a single private "shadow" repository.

Snapshot branches follow the pattern `{project}-{hash}/{timestamp}` and are
automatically pruned after a configurable retention period.
"""

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    print("[ERROR] 'requests' package missing. Run install.py first.")
    sys.exit(1)

try:
    import psutil
except ImportError:
    print("[ERROR] 'psutil' package missing. Run install.py first.")
    sys.exit(1)

# ============================================================
#  Paths
# ============================================================
CONFIG_DIR = Path.home() / ".config" / "phantomgit"
CONFIG_FILE = CONFIG_DIR / "config.json"
PROJECTS_FILE = CONFIG_DIR / "projects.json"
LOG_FILE = CONFIG_DIR / "service.log"

# ============================================================
#  Logging
# ============================================================
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

_log_handlers: list = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
# Only write to stdout when running interactively.
# When systemd/launchd redirects stdout to the log file, adding a
# StreamHandler would write every line twice.
if sys.stdout.isatty():
    _log_handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_log_handlers,
)
log = logging.getLogger("phantomgit")
log.propagate = False  # prevent double-logging if root logger has handlers too


# ============================================================
#  Config loading
# ============================================================
def load_config() -> dict:
    if not CONFIG_FILE.exists():
        log.error("Config not found. Run install.py first.")
        sys.exit(1)
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def load_projects() -> dict:
    if not PROJECTS_FILE.exists():
        return {"active_projects": [], "excluded_projects": []}
    return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))


# ============================================================
#  AI commit message providers (pluggable)
# ============================================================
def _truncate_diff(diff: str, max_chars: int) -> str:
    if len(diff) > max_chars:
        return diff[:max_chars] + "\n\n[... diff truncated ...]"
    return diff


def _build_prompt(diff: str, project_name: str, ide_name: str) -> str:
    return (
        f"You are a senior software engineer. Below is the diff from changes "
        f"made in the project '{project_name}' using the '{ide_name}' editor. "
        f"Write a single-sentence, professional commit message that summarizes "
        f"the change. Output the commit message only, with no quotes or "
        f"extra commentary.\n\nDiff:\n{diff}"
    )


def _clean_response(text: str) -> str:
    msg = (text or "").strip().replace('"', "").replace("\n", " ").strip()
    if len(msg) > 200:
        msg = msg[:197] + "..."
    return msg


_gemini_client = None
_gemini_key_used: Optional[str] = None


def _gemini_provider(
    provider_cfg: dict, diff: str, project_name: str, ide_name: str, max_chars: int
) -> str:
    from google import genai

    global _gemini_client, _gemini_key_used
    api_key = provider_cfg["api_key"]
    model = provider_cfg["model"]
    if _gemini_client is None or _gemini_key_used != api_key:
        _gemini_client = genai.Client(api_key=api_key)
        _gemini_key_used = api_key
    prompt = _build_prompt(_truncate_diff(diff, max_chars), project_name, ide_name)
    response = _gemini_client.models.generate_content(model=model, contents=prompt)
    return _clean_response(response.text)


def _openai_compatible_provider(
    provider_cfg: dict, diff: str, project_name: str, ide_name: str, max_chars: int, timeout: int
) -> str:
    base_url = provider_cfg["base_url"].rstrip("/")
    api_key = provider_cfg.get("api_key", "")
    model = provider_cfg["model"]
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": _build_prompt(_truncate_diff(diff, max_chars), project_name, ide_name),
            }
        ],
        "temperature": 0.3,
        "max_tokens": 100,
    }
    r = requests.post(
        f"{base_url}/chat/completions", headers=headers, json=payload, timeout=timeout
    )
    r.raise_for_status()
    return _clean_response(r.json()["choices"][0]["message"]["content"])


def _anthropic_provider(
    provider_cfg: dict, diff: str, project_name: str, ide_name: str, max_chars: int, timeout: int
) -> str:
    base_url = provider_cfg.get("base_url", "https://api.anthropic.com").rstrip("/")
    api_key = provider_cfg["api_key"]
    model = provider_cfg["model"]
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": model,
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": _build_prompt(_truncate_diff(diff, max_chars), project_name, ide_name),
            }
        ],
    }
    r = requests.post(f"{base_url}/v1/messages", headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    text_blocks = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return _clean_response(" ".join(text_blocks))


def get_commit_message(
    config: dict, diff: str, project_name: str, ide_name: str, file_count: int
) -> str:
    snap = config.get("snapshot", {})
    max_chars = int(snap.get("max_diff_chars", 30000))
    fallback_tpl = snap.get(
        "no_ai_message_template", "Backup {timestamp} ({file_count} file(s) changed)"
    )
    fallback = fallback_tpl.format(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
        file_count=file_count,
    )

    provider_cfg = config.get("ai_provider", {"type": "none"})
    provider_type = provider_cfg.get("type", "none")
    if provider_type == "none":
        return fallback

    timeout = int(config.get("timeouts", {}).get("request_seconds", 15))
    try:
        if provider_type == "gemini":
            msg = _gemini_provider(provider_cfg, diff, project_name, ide_name, max_chars)
        elif provider_type == "openai":
            msg = _openai_compatible_provider(
                provider_cfg, diff, project_name, ide_name, max_chars, timeout
            )
        elif provider_type == "anthropic":
            msg = _anthropic_provider(
                provider_cfg, diff, project_name, ide_name, max_chars, timeout
            )
        else:
            log.warning(f"Unknown AI provider '{provider_type}', using fallback.")
            return fallback
        return msg or fallback
    except Exception as e:
        log.warning(f"AI provider '{provider_type}' failed ({e.__class__.__name__}): {e}")
        return fallback


# ============================================================
#  Git helpers (cwd= for every call; never os.chdir)
# ============================================================
_TOKEN_PATTERNS = [
    re.compile(r"(Authorization:\s*(?:token|Bearer)\s+)\S+", re.IGNORECASE),
    re.compile(r"(https?://)[^@\s/]+(?::[^@\s/]+)?@"),
]


def _redact(text: str) -> str:
    """Hide tokens that may appear inside auth headers or URL credentials."""
    text = _TOKEN_PATTERNS[0].sub(r"\1***", text)
    text = _TOKEN_PATTERNS[1].sub(r"\1***@", text)
    return text


def run_git(args: list, cwd: str, config: dict, check: bool = False) -> Optional[str]:
    timeout = int(config.get("timeouts", {}).get("git_seconds", 60))
    env = os.environ.copy()
    # Daemon has no TTY; never let git fall back to an interactive prompt.
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode != 0:
            if check:
                pretty = _redact(" ".join(args))
                stderr = _redact(result.stderr.strip())
                log.error(f"Git error [{cwd}] ({pretty}): {stderr}")
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        log.error(f"Git timeout [{cwd}]: {_redact(' '.join(args))}")
        return None
    except FileNotFoundError:
        log.error("git command not found. Make sure git is on PATH.")
        return None
    except Exception as e:
        log.error(f"Git exception [{cwd}]: {e}")
        return None


def auth_header_arg(token: str) -> list:
    """Git args for a one-shot authenticated call.

    Sends the token via an HTTP Authorization header and blocks any local
    credential helper from intervening, so a 401 surfaces directly instead
    of bouncing into a username/password prompt that the daemon can't answer.
    """
    return [
        "-c",
        "credential.helper=",
        "-c",
        f"http.extraHeader=Authorization: Bearer {token}",
    ]


# ============================================================
#  IDE detection
# ============================================================
def get_active_ide(supported_ides: list) -> Optional[str]:
    """Match a running process against the configured IDE list."""
    ides_lower = [i.lower() for i in supported_ides]
    for proc in psutil.process_iter(["name"]):
        try:
            raw_name = (proc.info["name"] or "").lower()
            if not raw_name:
                continue
            base = re.sub(r"\.(exe|app)$", "", raw_name)
            for ide_l in ides_lower:
                if base == ide_l:
                    return ide_l
                if base.startswith(ide_l):
                    suffix = base[len(ide_l) :]
                    if suffix and re.match(r"^[\d._-]", suffix):
                        return ide_l
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return None


# ============================================================
#  GitHub API
# ============================================================
def github_request(method: str, path: str, config: dict, **kwargs) -> requests.Response:
    gh = config.get("github", {})
    token = gh["token"]
    api_url = gh.get("api_url", "https://api.github.com").rstrip("/")
    timeout = int(config.get("timeouts", {}).get("request_seconds", 15))

    headers = kwargs.pop("headers", {})
    headers.update(
        {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "phantomgit",
        }
    )
    return requests.request(method, f"{api_url}{path}", headers=headers, timeout=timeout, **kwargs)


_username_cache: Optional[str] = None


def get_github_username(config: dict) -> Optional[str]:
    global _username_cache
    if _username_cache:
        return _username_cache
    try:
        r = github_request("GET", "/user", config)
        if r.status_code != 200:
            log.error(f"GitHub token error: {r.status_code} {r.text[:200]}")
            return None
        _username_cache = r.json()["login"]
        return _username_cache
    except requests.RequestException as e:
        log.error(f"GitHub API access error: {e}")
        return None


# ============================================================
#  Shadow repository (single, shared across all projects)
# ============================================================
_repo_info_cache: Optional[dict] = None


def get_shadow_repo(config: dict) -> Optional[dict]:
    """Ensure the single shadow repo exists; cache the result for the session."""
    global _repo_info_cache
    if _repo_info_cache:
        return _repo_info_cache

    snap = config.get("snapshot", {})
    repo_name = snap.get("shadow_repo_name", "phantomgit-shadow")
    description = snap.get("shadow_repo_description", "PhantomGit snapshots for all projects")

    username = get_github_username(config)
    if not username:
        return None

    try:
        r = github_request("GET", f"/repos/{username}/{repo_name}", config)
        if r.status_code == 200:
            _repo_info_cache = {
                "clone_url": r.json()["clone_url"],
                "name": repo_name,
                "username": username,
            }
            return _repo_info_cache
        if r.status_code != 404:
            log.warning(f"Unexpected repo lookup response: {r.status_code} {r.text[:200]}")
    except requests.RequestException as e:
        log.error(f"Repo lookup error: {e}")
        return None

    payload = {
        "name": repo_name,
        "private": True,
        "description": description,
        "auto_init": True,
    }
    log.info(f"Creating shadow repo: {repo_name}")
    try:
        r = github_request("POST", "/user/repos", config, json=payload)
        if r.status_code == 201:
            _repo_info_cache = {
                "clone_url": r.json()["clone_url"],
                "name": repo_name,
                "username": username,
            }
            return _repo_info_cache
        log.error(f"Repo creation failed: {r.status_code} {r.text[:200]}")
    except requests.RequestException as e:
        log.error(f"Repo creation error: {e}")
    return None


# ============================================================
#  Sensitive file detection
# ============================================================
def has_sensitive_files(project_path: str, ignore_patterns: list, config: dict) -> bool:
    status = run_git(["status", "--porcelain"], cwd=project_path, config=config)
    if not status:
        return False
    patterns_lower = [p.lower() for p in ignore_patterns]
    for line in status.splitlines():
        filename = line[3:].strip().strip('"').lower()
        for pattern in patterns_lower:
            if pattern in filename:
                log.warning(f"Sensitive file detected, skipping project: {filename}")
                return True
    return False


# ============================================================
#  Legacy remote cleanup
# ============================================================
def cleanup_legacy_remote(project_path: str, config: dict) -> None:
    """Old versions wrote tokens into .git/config; remove that remote."""
    remotes = run_git(["remote"], cwd=project_path, config=config)
    if remotes and "shadow" in remotes.split():
        url = run_git(["remote", "get-url", "shadow"], cwd=project_path, config=config) or ""
        if "@github.com" in url and "https://" in url:
            log.warning(
                f"[{os.path.basename(project_path)}] Removing insecure legacy "
                f"'shadow' remote. Revoke that token on GitHub if you haven't already."
            )
            run_git(["remote", "remove", "shadow"], cwd=project_path, config=config)


# ============================================================
#  Project slug -> branch name
# ============================================================
def project_slug(project_path: str) -> str:
    name = os.path.basename(project_path)
    h = hashlib.md5(project_path.encode(), usedforsecurity=False).hexdigest()[:6]
    raw = f"{name}-{h}"
    # Sanitize for git ref naming
    return re.sub(r"[^A-Za-z0-9._-]", "-", raw)[:80]


def make_branch_name(config: dict, slug: str) -> str:
    snap = config.get("snapshot", {})
    template = snap.get("branch_template", "{project_slug}/{timestamp}")
    ts_format = snap.get("timestamp_format", "%Y%m%d-%H%M%S")
    return template.format(
        project_slug=slug,
        timestamp=datetime.now().strftime(ts_format),
    )


# ============================================================
#  Snapshot creation (orphan commits, preserves user's index)
# ============================================================
def create_snapshot(config: dict, project_path: str, repo_info: dict, ide_name: str) -> bool:
    """
    Create a snapshot without touching the user's working tree, index, or HEAD.

    Flow:
      1. `git stash create -u` -> dangling commit capturing index + worktree.
         Does NOT modify the staging area or working tree.
      2. Re-commit the stash's tree as an ORPHAN commit (no parent).
         This keeps the shadow repo compact: git only uploads new blobs.
      3. Push that orphan commit as a new branch in the shadow repo.
    """
    snap = config.get("snapshot", {})
    msg_prefix_tpl = snap.get("commit_message_prefix", "[{ide}]")

    project_name = os.path.basename(project_path)

    if not run_git(["rev-parse", "HEAD"], cwd=project_path, config=config):
        log.warning(f"[{project_name}] No initial commit found. Skipping.")
        return False

    stash_sha = run_git(["stash", "create", "-u"], cwd=project_path, config=config, check=True)
    if not stash_sha:
        return False

    diff = run_git(["diff", "HEAD", stash_sha], cwd=project_path, config=config) or ""
    if not diff.strip():
        return False

    name_only = (
        run_git(["diff", "--name-only", "HEAD", stash_sha], cwd=project_path, config=config) or ""
    )
    file_count = len([line for line in name_only.splitlines() if line.strip()])

    log.info(f"[{project_name}] Change detected ({file_count} file(s)). Creating snapshot...")

    ai_msg = get_commit_message(config, diff, project_name, ide_name, file_count)
    full_msg = f"{msg_prefix_tpl.format(ide=ide_name.upper())} {ai_msg}".strip()

    stash_tree = run_git(
        ["rev-parse", f"{stash_sha}^{{tree}}"], cwd=project_path, config=config, check=True
    )
    if not stash_tree:
        return False

    # Orphan commit (no -p): shadow repo stays compact
    commit_sha = run_git(
        ["commit-tree", stash_tree, "-m", full_msg], cwd=project_path, config=config, check=True
    )
    if not commit_sha:
        return False

    branch_name = make_branch_name(config, project_slug(project_path))
    token = config["github"]["token"]
    push_url = repo_info["clone_url"]

    push_args = [
        *auth_header_arg(token),
        "push",
        push_url,
        f"{commit_sha}:refs/heads/{branch_name}",
    ]
    push_result = run_git(push_args, cwd=project_path, config=config, check=True)

    if push_result is None:
        log.error(f"[{project_name}] Push failed.")
        return False

    log.info(f"[{project_name}] Snapshot pushed: {branch_name}")
    return True


# ============================================================
#  Branch retention / cleanup
# ============================================================
def list_remote_branches(config: dict, repo_info: dict) -> list:
    """Return list of branch names in the shadow repo via git ls-remote."""
    token = config["github"]["token"]
    push_url = repo_info["clone_url"]
    args = [*auth_header_arg(token), "ls-remote", "--heads", push_url]
    # Run from temp dir so we don't depend on any user repo
    with tempfile.TemporaryDirectory() as tmp:
        output = run_git(args, cwd=tmp, config=config, check=True)
    if not output:
        return []
    branches = []
    for line in output.splitlines():
        # Format: "<sha>\trefs/heads/<branch>"
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        ref = parts[1]
        if ref.startswith("refs/heads/"):
            branches.append(ref[len("refs/heads/") :])
    return branches


def find_expired_branches(branches: list, config: dict) -> list:
    """Pick out snapshot branches older than retention_days."""
    snap = config.get("snapshot", {})
    retention_days = int(snap.get("retention_days", 7))
    ts_format = snap.get("timestamp_format", "%Y%m%d-%H%M%S")
    template = snap.get("branch_template", "{project_slug}/{timestamp}")

    # Build a regex by escaping the template and replacing placeholders.
    # We assume {project_slug} contains git-safe chars and {timestamp} is at the end.
    if not template.endswith("{timestamp}"):
        log.warning("branch_template doesn't end with {timestamp}; cleanup disabled.")
        return []
    prefix = template[: -len("{timestamp}")].replace("{project_slug}", "(?P<slug>[^/]+)")
    # More forgiving pattern: match any digit/separator sequence at the end
    pattern = re.compile("^" + prefix + r"(?P<ts>[\d_\-]+)$")

    cutoff = datetime.now() - timedelta(days=retention_days)
    expired = []
    for name in branches:
        m = pattern.match(name)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group("ts"), ts_format)
        except ValueError:
            continue
        if ts < cutoff:
            expired.append(name)
    return expired


def delete_remote_branches(config: dict, repo_info: dict, branches: list) -> int:
    """Bulk delete branches via `git push` from a temp dir. Returns count deleted."""
    if not branches:
        return 0
    token = config["github"]["token"]
    push_url = repo_info["clone_url"]
    deleted = 0
    chunk_size = 50

    with tempfile.TemporaryDirectory() as tmp:
        # init a throwaway repo so `git push` has a workdir
        init_result = subprocess.run(
            ["git", "init", "-q"],
            cwd=tmp,
            capture_output=True,
            text=True,
        )
        if init_result.returncode != 0:
            log.error(f"Failed to init temp repo for cleanup: {init_result.stderr}")
            return 0

        for i in range(0, len(branches), chunk_size):
            chunk = branches[i : i + chunk_size]
            refspecs = [f":refs/heads/{name}" for name in chunk]
            args = [*auth_header_arg(token), "push", push_url, *refspecs]
            result = run_git(args, cwd=tmp, config=config, check=True)
            if result is not None:
                deleted += len(chunk)
            else:
                log.error(f"Cleanup chunk push failed ({len(chunk)} branches).")
    return deleted


def run_branch_cleanup(config: dict) -> None:
    snap = config.get("snapshot", {})
    if not snap.get("cleanup_enabled", True):
        return
    repo_info = get_shadow_repo(config)
    if not repo_info:
        return
    try:
        all_branches = list_remote_branches(config, repo_info)
        expired = find_expired_branches(all_branches, config)
        if not expired:
            log.info(
                f"Cleanup: no expired branches "
                f"(scanned {len(all_branches)}, retention "
                f"{snap.get('retention_days', 7)}d)."
            )
            return
        log.info(
            f"Cleanup: deleting {len(expired)} expired branches (of {len(all_branches)} total)..."
        )
        deleted = delete_remote_branches(config, repo_info, expired)
        log.info(f"Cleanup: deleted {deleted} branches.")
    except Exception as e:
        log.exception(f"Branch cleanup error: {e}")


# ============================================================
#  Project loop
# ============================================================
def process_single_project(
    config: dict, project_path: str, ide_name: str, repo_info: Optional[dict] = None
) -> bool:
    """
    Process one project. Returns True if a snapshot was pushed.

    `repo_info` may be passed in to avoid re-fetching when processing many
    projects in sequence; if None, we fetch it ourselves.
    """
    ignore_patterns = config.get("scanning", {}).get("ignore_patterns", [])

    if not os.path.isdir(os.path.join(project_path, ".git")):
        return False

    try:
        cleanup_legacy_remote(project_path, config)

        if has_sensitive_files(project_path, ignore_patterns, config):
            return False

        status = run_git(["status", "--porcelain"], cwd=project_path, config=config)
        if not status:
            return False

        if repo_info is None:
            repo_info = get_shadow_repo(config)
            if not repo_info:
                log.error(f"[{os.path.basename(project_path)}] Could not reach shadow repo.")
                return False

        return create_snapshot(config, project_path, repo_info, ide_name)
    except Exception as e:
        log.exception(f"[{os.path.basename(project_path)}] Processing error: {e}")
        return False


def process_projects(config: dict, projects: list, excluded: list, ide_name: str) -> int:
    """Process all projects; return number of snapshots successfully pushed."""
    repo_info = None  # lazily fetched on first project with changes
    pushed = 0

    for project_path in projects:
        if project_path in excluded:
            continue
        if not os.path.isdir(os.path.join(project_path, ".git")):
            continue

        # Fetch shadow repo on first project that actually has changes
        if repo_info is None:
            status = run_git(["status", "--porcelain"], cwd=project_path, config=config)
            if status:
                repo_info = get_shadow_repo(config)
                if not repo_info:
                    log.error("Could not reach shadow repo; aborting cycle.")
                    return pushed

        if process_single_project(config, project_path, ide_name, repo_info):
            pushed += 1
    return pushed


# ============================================================
#  File-watcher mode (optional, requires `watchdog`)
# ============================================================
#
#  Architecture:
#    * One watchdog Observer thread monitors all project paths.
#    * On any filesystem event, we enqueue (project_path, timestamp) into a
#      thread-safe queue.
#    * The main thread drains the queue periodically. For each unique project
#      with pending events, it waits until `debounce_seconds` have passed
#      since the LAST event before triggering a snapshot. This collapses a
#      burst of file saves (e.g. an IDE auto-formatting on save) into a
#      single snapshot.
#    * Filesystem events that fall inside `exclude_dirs` or that target the
#      `.git/` directory itself are ignored at the event-handler level.


def _try_import_watchdog():
    """Return (Observer, FileSystemEventHandler) or (None, None) if unavailable."""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        return Observer, FileSystemEventHandler
    except ImportError:
        return None, None


class _ProjectEventHandler:
    """Created per-project. Reports events into a shared queue."""

    def __init__(self, project_path: str, event_queue, exclude_dirs: set, ignore_extensions: set):
        self.project_path = project_path
        self.event_queue = event_queue
        self.exclude_dirs = exclude_dirs
        self.ignore_extensions = ignore_extensions

    def _is_relevant(self, src_path: str) -> bool:
        if not src_path:
            return False
        path = Path(src_path)
        parts = path.parts
        # Ignore anything inside .git or excluded directories
        for part in parts:
            if part == ".git" or part in self.exclude_dirs:
                return False
        # Ignore common editor temp/swap files and binary build artifacts
        suffix = path.suffix.lower()
        if suffix in self.ignore_extensions:
            return False
        # Vim swap files, Emacs lock files, etc.
        name = path.name
        if name.startswith(".#") or name.endswith("~") or name.endswith(".swp"):
            return False
        return True

    def dispatch(self, event):
        # Watchdog calls this for every event. We forward only relevant ones.
        if getattr(event, "is_directory", False):
            return
        if not self._is_relevant(getattr(event, "src_path", "")):
            return
        try:
            self.event_queue.put_nowait((self.project_path, time.time()))
        except Exception:
            pass


def _build_event_handler_class(base_handler_cls):
    """Compose our handler with watchdog's base class at runtime."""

    class _Handler(base_handler_cls, _ProjectEventHandler):
        def __init__(self, *args, **kwargs):
            _ProjectEventHandler.__init__(self, *args, **kwargs)
            base_handler_cls.__init__(self)

    return _Handler


class FileWatcher:
    """
    Coordinates a watchdog Observer plus a debounce dispatcher.

    Usage:
        watcher = FileWatcher(config, projects, excluded)
        watcher.start()
        ...
        triggered = watcher.drain_pending()  # returns list of project_paths
        ...
        watcher.stop()
    """

    # File extensions that watchdog will report on but git almost certainly
    # doesn't care about. Filtering these reduces noise dramatically.
    NOISY_EXTENSIONS = frozenset(
        [
            ".pyc",
            ".pyo",
            ".pyd",
            ".class",
            ".o",
            ".obj",
            ".log",
            ".tmp",
            ".temp",
            ".cache",
            ".lock",
            ".swo",
            ".swn",
        ]
    )

    def __init__(self, config: dict, projects: list, excluded: list):
        self.config = config
        self.projects = [p for p in projects if p not in excluded]
        self.observer = None
        self.event_queue = None  # set in start()
        self._last_event_time = {}  # project_path -> last event timestamp
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def start(self) -> bool:
        Observer, FileSystemEventHandler = _try_import_watchdog()
        if Observer is None:
            log.warning(
                "Watch mode requested but the 'watchdog' package is not "
                "installed. Falling back to polling. Install with: "
                "pip install watchdog"
            )
            return False

        # Lazy stdlib import — only needed in watch mode
        import queue as _queue

        self.event_queue = _queue.Queue(maxsize=10000)

        exclude_dirs = set(self.config.get("scanning", {}).get("exclude_dirs", []))
        HandlerCls = _build_event_handler_class(FileSystemEventHandler)

        self.observer = Observer()
        watched_count = 0
        for project_path in self.projects:
            if not os.path.isdir(project_path):
                continue
            handler = HandlerCls(
                project_path=project_path,
                event_queue=self.event_queue,
                exclude_dirs=exclude_dirs,
                ignore_extensions=self.NOISY_EXTENSIONS,
            )
            try:
                self.observer.schedule(handler, project_path, recursive=True)
                watched_count += 1
            except Exception as e:
                log.warning(f"Could not watch {project_path}: {e}")

        if watched_count == 0:
            log.warning("No projects could be watched; falling back to polling.")
            return False

        self.observer.daemon = True
        self.observer.start()
        log.info(f"File watcher started ({watched_count} project(s)).")
        self._available = True
        return True

    def drain_pending(self) -> list:
        """
        Return the list of project paths that have stable, debounced events
        ready for snapshotting. Each project appears at most once.
        """
        if not self.event_queue:
            return []

        # Pull every queued event and update last-event timestamps.
        import queue as _queue

        try:
            while True:
                project_path, ts = self.event_queue.get_nowait()
                # Track the MOST RECENT event time per project.
                prev = self._last_event_time.get(project_path, 0.0)
                if ts > prev:
                    self._last_event_time[project_path] = ts
        except _queue.Empty:
            pass

        # Decide which projects have been "quiet" long enough.
        snap_cfg = self.config.get("snapshot", {})
        debounce = float(snap_cfg.get("debounce_seconds", 30))
        now = time.time()
        ready = []
        for project_path, last_ts in list(self._last_event_time.items()):
            if now - last_ts >= debounce:
                ready.append(project_path)
                # Reset so we don't fire again until a new event arrives.
                del self._last_event_time[project_path]
        return ready

    def update_projects(self, projects: list, excluded: list) -> None:
        """Restart the observer if the watched project set has changed."""
        new_set = set(p for p in projects if p not in excluded)
        old_set = set(self.projects)
        if new_set == old_set:
            return
        log.info("Watched project set changed; restarting watcher.")
        self.stop()
        self.projects = list(new_set)
        self.start()

    def stop(self) -> None:
        if self.observer is not None:
            try:
                self.observer.stop()
                self.observer.join(timeout=5)
            except Exception as e:
                log.warning(f"Watcher stop error: {e}")
            self.observer = None
        self._available = False


# ============================================================
#  Main loop
# ============================================================
def _run_polling_loop(config: dict) -> None:
    """The original polling-based main loop (now factored out)."""
    snap = config.get("snapshot", {})
    cleanup_interval = int(snap.get("cleanup_interval_seconds", 21600))
    last_ide_state = False
    last_cleanup_time = 0.0

    while True:
        try:
            config = load_config()
            data = load_projects()
            active_projects = data.get("active_projects", [])
            excluded = data.get("excluded_projects", [])
            supported_ides = config.get("scanning", {}).get("supported_ides", [])

            current_ide = get_active_ide(supported_ides)

            if current_ide:
                log.info(f"{current_ide.upper()} active. Scanning projects...")
                process_projects(config, active_projects, excluded, current_ide)
                last_ide_state = True
            elif last_ide_state:
                log.info("IDE closed. Running final checks...")
                process_projects(config, active_projects, excluded, "shutdown-backup")
                last_ide_state = False
                log.info("Idle.")

            now = time.time()
            if now - last_cleanup_time >= cleanup_interval:
                run_branch_cleanup(config)
                last_cleanup_time = now

            poll_seconds = int(config.get("snapshot", {}).get("poll_interval_seconds", 900))
            time.sleep(poll_seconds)

        except KeyboardInterrupt:
            log.info("Service stopped.")
            return
        except Exception as e:
            log.exception(f"Main loop error: {e}")
            time.sleep(60)


def _run_watch_loop(config: dict, hybrid: bool) -> None:
    """
    Event-driven main loop using watchdog.

    In `hybrid` mode, we ALSO run a slow polling sweep every
    `safety_poll_interval_seconds` (default 1 hour) as a safety net for:
      - network/sshfs/SMB mounts where inotify doesn't fire
      - Events lost during temporary watcher restarts
      - First-time bootstrapping after install
    """
    data = load_projects()
    active_projects = data.get("active_projects", [])
    excluded = data.get("excluded_projects", [])

    watcher = FileWatcher(config, active_projects, excluded)
    if not watcher.start():
        # watchdog missing or no projects watchable -> fall back
        log.warning("Falling back to polling mode.")
        _run_polling_loop(config)
        return

    snap = config.get("snapshot", {})
    cleanup_interval = int(snap.get("cleanup_interval_seconds", 21600))
    safety_poll_interval = int(snap.get("safety_poll_interval_seconds", 3600))
    tick_seconds = float(snap.get("watch_tick_seconds", 5))

    last_cleanup_time = 0.0
    last_safety_poll_time = time.time()
    last_known_projects_data = data

    try:
        while True:
            try:
                # Reload config so live edits take effect (poll interval, etc.)
                config = load_config()
                data = load_projects()

                if data != last_known_projects_data:
                    # User added/removed projects via `rescan`
                    watcher.update_projects(
                        data.get("active_projects", []),
                        data.get("excluded_projects", []),
                    )
                    last_known_projects_data = data

                # 1) Process any debounced filesystem events.
                ready = watcher.drain_pending()
                if ready:
                    supported_ides = config.get("scanning", {}).get("supported_ides", [])
                    ide_name = get_active_ide(supported_ides) or "watch"
                    repo_info = None
                    for project_path in ready:
                        if project_path in data.get("excluded_projects", []):
                            continue
                        if repo_info is None:
                            repo_info = get_shadow_repo(config)
                            if not repo_info:
                                log.error("Could not reach shadow repo.")
                                break
                        process_single_project(config, project_path, ide_name, repo_info)

                # 2) Hybrid mode: occasional full polling sweep as a safety net.
                now = time.time()
                if hybrid and (now - last_safety_poll_time >= safety_poll_interval):
                    log.info("Running periodic safety-net poll...")
                    supported_ides = config.get("scanning", {}).get("supported_ides", [])
                    ide_name = get_active_ide(supported_ides) or "safety-poll"
                    process_projects(
                        config,
                        data.get("active_projects", []),
                        data.get("excluded_projects", []),
                        ide_name,
                    )
                    last_safety_poll_time = now

                # 3) Periodic cleanup of expired snapshot branches.
                if now - last_cleanup_time >= cleanup_interval:
                    run_branch_cleanup(config)
                    last_cleanup_time = now

                time.sleep(tick_seconds)

            except KeyboardInterrupt:
                log.info("Service stopped.")
                return
            except Exception as e:
                log.exception(f"Watch loop error: {e}")
                time.sleep(30)
    finally:
        watcher.stop()


def main() -> None:
    log.info("PhantomGit service started.")
    config = load_config()
    mode = config.get("snapshot", {}).get("mode", "hybrid").lower()

    if mode == "polling":
        log.info("Mode: polling")
        _run_polling_loop(config)
    elif mode == "watch":
        log.info("Mode: watch (event-driven)")
        _run_watch_loop(config, hybrid=False)
    elif mode == "hybrid":
        log.info("Mode: hybrid (watch + safety-net polling)")
        _run_watch_loop(config, hybrid=True)
    else:
        log.warning(f"Unknown mode '{mode}'; using polling.")
        _run_polling_loop(config)


if __name__ == "__main__":
    main()
