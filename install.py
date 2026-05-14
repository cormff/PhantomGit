"""
PhantomGit Installer & Management CLI
=========================================
Subcommands:
    install              Interactive setup (default if no args).
    uninstall            Remove the background service and optionally config.
    config show          Print current configuration (secrets masked).
    config get <key>     Print one value (dot notation, e.g. ai_provider.model).
    config set <key> [v] Set one value. Omit value to be prompted interactively.
    reconfigure-ai       Re-run the AI provider wizard.
    reconfigure-token    Re-enter the GitHub token.
    status               Show service state and recent log entries.
    rescan               Re-scan disk for git projects.
    check-update         Check if a newer release is available on GitHub.
    update               Download and apply the latest release.
"""
import os
import sys
import json
import shutil
import getpass
import hashlib
import argparse
import platform
import tempfile
import subprocess
from pathlib import Path

__version__ = "0.1.0"
DEFAULT_UPDATE_REPO = "YOUR_USERNAME/phantomgit"

# ============================================================
#  Paths
# ============================================================
CONFIG_DIR = Path.home() / ".config" / "phantomgit"
CONFIG_FILE = CONFIG_DIR / "config.json"
PROJECTS_FILE = CONFIG_DIR / "projects.json"
LOG_FILE = CONFIG_DIR / "service.log"

# ============================================================
#  Default configuration values
# ============================================================
DEFAULT_SUPPORTED_IDES = [
    # JetBrains
    "pycharm", "pycharm64", "idea", "idea64", "webstorm", "phpstorm",
    "rider", "clion", "goland", "rubymine", "datagrip", "appcode",
    # Microsoft / VS family
    "code", "code-insiders", "codium", "devenv",
    # AI-powered editors
    "cursor", "windsurf", "zed",
    # Modern editors
    "sublime_text", "atom", "fleet",
    # Game engines
    "godot", "unity", "unityeditor", "unrealeditor", "ue4editor", "ue5editor",
    # Android / Mobile
    "studio", "studio64",
    # Terminal editors
    "nvim", "vim", "emacs",
    # Classics
    "xcode", "eclipse",
]

DEFAULT_EXCLUDE_DIRS = [
    "node_modules", "venv", ".venv", "env", "__pycache__",
    "target", "build", "dist", ".idea", ".vscode", ".gradle",
    ".next", ".nuxt", "vendor", ".tox",
]

DEFAULT_IGNORE_PATTERNS = [
    ".env", "id_rsa", "id_ed25519", "credentials.json",
    ".pem", "secrets.json", ".key", "aws/credentials",
]


def get_default_search_dirs() -> list:
    home = Path.home()
    candidates = [
        home / "Documents", home / "Projects", home / "projects",
        home / "Code", home / "code", home / "Repos", home / "repos",
        home / "dev", home / "Development", home / "workspace",
        home / "PycharmProjects", home / "WebstormProjects", home / "IdeaProjects",
        home / "Desktop",
    ]
    if platform.system() == "Windows":
        candidates.extend([
            home / "source" / "repos",
            home / "OneDrive" / "Documents",
        ])
    return [str(d) for d in candidates if d.exists()]


def build_default_config(github_token: str, ai_provider: dict) -> dict:
    return {
        "github": {
            "token": github_token,
            "api_url": "https://api.github.com",
        },
        "ai_provider": ai_provider,
        "snapshot": {
            "mode": "hybrid",
            "poll_interval_seconds": 900,
            "debounce_seconds": 30,
            "watch_tick_seconds": 5,
            "safety_poll_interval_seconds": 3600,
            "max_diff_chars": 30000,
            "shadow_repo_name": "phantomgit-shadow",
            "shadow_repo_description": "PhantomGit snapshots for all projects",
            "branch_template": "{project_slug}/{timestamp}",
            "timestamp_format": "%Y%m%d-%H%M%S",
            "commit_message_prefix": "[{ide}]",
            "no_ai_message_template": "Backup {timestamp} ({file_count} file(s) changed)",
            "retention_days": 7,
            "cleanup_enabled": True,
            "cleanup_interval_seconds": 21600,
        },
        "scanning": {
            "supported_ides": DEFAULT_SUPPORTED_IDES,
            "exclude_dirs": DEFAULT_EXCLUDE_DIRS,
            "ignore_patterns": DEFAULT_IGNORE_PATTERNS,
            "search_dirs": get_default_search_dirs(),
        },
        "timeouts": {
            "request_seconds": 15,
            "git_seconds": 60,
        },
        "update": {
            "github_repo": DEFAULT_UPDATE_REPO,
            "include_prereleases": False,
            "auto_check_enabled": True,
            "auto_check_interval_days": 7,
            "asset_pattern": "phantomgit-{platform}{ext}",
            "download_timeout_seconds": 600,
        },
    }


# ============================================================
#  General helpers
# ============================================================
def secure_write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)


def read_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"[ERROR] No configuration at {CONFIG_FILE}. Run 'install' first.")
        sys.exit(1)
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def write_config(config: dict) -> None:
    secure_write(CONFIG_FILE, json.dumps(config, indent=4))


def prompt_secret(label: str) -> str:
    try:
        return getpass.getpass(label).strip()
    except Exception:
        return input(label).strip()


def get_python_executable() -> str:
    if platform.system() == "Windows":
        py = Path(sys.executable)
        pyw = py.with_name("pythonw.exe")
        if pyw.exists():
            return str(pyw)
    return sys.executable


# ============================================================
#  Nested dict access for `config get/set`
# ============================================================
SECRET_KEY_HINTS = ("token", "api_key", "secret", "password")


def is_secret_key_path(key_path: str) -> bool:
    return any(h in key_path.lower() for h in SECRET_KEY_HINTS)


def mask_value(value):
    if not isinstance(value, str) or not value:
        return value
    if len(value) <= 8:
        return "***"
    return value[:4] + "..." + value[-2:]


def mask_secrets(obj, path: str = ""):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            sub_path = f"{path}.{k}" if path else k
            if is_secret_key_path(sub_path) and isinstance(v, str):
                out[k] = mask_value(v)
            else:
                out[k] = mask_secrets(v, sub_path)
        return out
    if isinstance(obj, list):
        return [mask_secrets(x, path) for x in obj]
    return obj


def get_nested(d: dict, key_path: str):
    parts = key_path.split(".")
    current = d
    for p in parts:
        if isinstance(current, dict) and p in current:
            current = current[p]
        else:
            return None
    return current


def set_nested(d: dict, key_path: str, value) -> None:
    parts = key_path.split(".")
    current = d
    for p in parts[:-1]:
        if p not in current or not isinstance(current[p], dict):
            current[p] = {}
        current = current[p]
    current[parts[-1]] = value


# ============================================================
#  AI provider wizard
# ============================================================
def ask_for_ai_provider(existing: dict) -> dict:
    print("\n--- AI Commit Message Provider ---")
    print("Choose how commit messages will be generated:")
    print("  1. None       - Timestamp + file count (no AI, no API key needed)")
    print("  2. Gemini     - Google Gemini API (cloud)")
    print("  3. OpenAI-compatible - OpenAI / Ollama / LM Studio / llama.cpp / vLLM")
    print("  4. Anthropic  - Anthropic Claude API (cloud)")

    existing_type = (existing or {}).get("ai_provider", {}).get("type")
    if existing_type:
        print(f"\n(Current setting: {existing_type})")

    choice = input("\nSelect [1-4, default 1]: ").strip() or "1"

    if choice == "1":
        return {"type": "none"}

    if choice == "2":
        api_key = prompt_secret("Gemini API key (masked): ")
        model = input("Model name [gemini-2.5-flash-lite]: ").strip() or "gemini-2.5-flash-lite"
        return {"type": "gemini", "api_key": api_key, "model": model}

    if choice == "3":
        print("\nCommon base URLs:")
        print("  OpenAI:    https://api.openai.com/v1")
        print("  Ollama:    http://localhost:11434/v1")
        print("  LM Studio: http://localhost:1234/v1")
        print("  llama.cpp: http://localhost:8080/v1")
        print("  vLLM:      http://localhost:8000/v1")
        base_url = input("Base URL [https://api.openai.com/v1]: ").strip() \
            or "https://api.openai.com/v1"
        is_local = any(h in base_url for h in ("localhost", "127.0.0.1", "0.0.0.0"))
        if is_local:
            api_key = input("API key (usually empty for local LLMs, press Enter to skip): ").strip()
        else:
            api_key = prompt_secret("API key (masked): ")
        model = input("Model name [gpt-4o-mini]: ").strip() or "gpt-4o-mini"
        return {"type": "openai", "base_url": base_url, "api_key": api_key, "model": model}

    if choice == "4":
        api_key = prompt_secret("Anthropic API key (masked): ")
        model = input("Model name [claude-haiku-4-5]: ").strip() or "claude-haiku-4-5"
        return {
            "type": "anthropic",
            "api_key": api_key,
            "base_url": "https://api.anthropic.com",
            "model": model,
        }

    print("Invalid choice, defaulting to None.")
    return {"type": "none"}


def ask_for_github_token(existing: dict) -> str:
    existing_token = (existing or {}).get("github", {}).get("token", "")
    if existing_token:
        keep = input("\nA GitHub token is already saved. Keep it? [Y/n]: ").strip().lower()
        if keep in ("", "y", "yes"):
            return existing_token
    return prompt_secret("\nGitHub Personal Access Token (masked): ")


def ask_for_mode(existing: dict) -> str:
    """Ask the user how PhantomGit should detect changes."""
    print("\n--- Change Detection Mode ---")
    print("How should PhantomGit detect changes in your projects?")
    print("  1. Hybrid    - Real-time file-watcher + hourly safety-net polling. RECOMMENDED.")
    print("  2. Watch     - Real-time file-watcher only (slightly lower overhead).")
    print("  3. Polling   - Check every N minutes (works on any filesystem; no extra deps).")

    existing_mode = (existing or {}).get("snapshot", {}).get("mode")
    if existing_mode:
        print(f"\n(Current setting: {existing_mode})")

    choice = input("\nSelect [1-3, default 1]: ").strip() or "1"
    if choice == "2":
        return "watch"
    if choice == "3":
        return "polling"
    return "hybrid"


def get_required_packages(ai_provider_type: str, mode: str = "hybrid") -> dict:
    """Return {import_name: pip_name} for packages needed given the user's choices."""
    packages = {"requests": "requests", "psutil": "psutil"}
    if ai_provider_type == "gemini":
        packages["google.genai"] = "google-genai"
    if mode in ("watch", "hybrid"):
        packages["watchdog"] = "watchdog"
    return packages


def get_missing_packages(required: dict) -> list:
    import importlib.util
    return [
        pip_name for import_name, pip_name in required.items()
        if importlib.util.find_spec(import_name) is None
    ]


def _run_pip(extra_args: list, packages: list) -> tuple:
    cmd = [sys.executable, "-m", "pip", "install",
           "--disable-pip-version-check"] + extra_args + packages
    print(f"   $ {' '.join(cmd[1:])}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return True, ""
    err_lines = (result.stderr or result.stdout).strip().splitlines()
    return False, (err_lines[-1] if err_lines else "unknown error")[:200]


def install_packages(packages: list) -> bool:
    if not packages:
        return True
    in_venv = sys.prefix != sys.base_prefix
    print(f"\n[INFO] Installing missing packages: {', '.join(packages)}")
    if in_venv:
        print("   Virtual environment detected.")
        strategies = [[]]
    else:
        print("   System Python detected; will use --user mode.")
        strategies = [["--user"], ["--user", "--break-system-packages"]]
    for extra_args in strategies:
        success, last_error = _run_pip(extra_args, packages)
        if success:
            print("[OK] Packages installed successfully.")
            return True
        print(f"   [WARN] Strategy failed: {last_error}")
    print("\n[ERROR] Automatic installation failed.")
    print(f"   Try manually: {sys.executable} -m pip install {' '.join(packages)}")
    return False


def ensure_dependencies(ai_provider_type: str, mode: str = "hybrid") -> None:
    if getattr(sys, "frozen", False):
        return
    required = get_required_packages(ai_provider_type, mode)
    missing = get_missing_packages(required)
    if not missing:
        print("[OK] All dependencies are present.")
        return
    if not install_packages(missing):
        sys.exit(1)
    import importlib
    importlib.invalidate_caches()
    still_missing = get_missing_packages(required)
    if still_missing:
        print(f"[ERROR] Still missing after install: {', '.join(still_missing)}")
        print("   Open a new terminal so PATH changes take effect, then re-run.")
        sys.exit(1)


# ============================================================
#  Project scanning
# ============================================================
def scan_projects(config: dict, interactive: bool = True) -> int:
    print("\n[INFO] Scanning for Git projects...")
    search_dirs = [Path(p) for p in config["scanning"]["search_dirs"]]

    if interactive:
        extra = input("Add an extra scan directory? (path or empty): ").strip()
        if extra:
            p = Path(extra).expanduser()
            if p.exists():
                search_dirs.append(p)
            else:
                print(f"[WARN] Directory not found, ignoring: {p}")

    exclude_set = set(config["scanning"]["exclude_dirs"])
    found = []
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for root, dirs, _ in os.walk(search_dir):
            dirs[:] = [d for d in dirs if d not in exclude_set]
            if ".git" in dirs:
                resolved = str(Path(root).resolve())
                found.append(resolved)
                dirs.remove(".git")
                print(f"   * {os.path.basename(root)}  ({resolved})")

    # Preserve excluded list from prior runs
    existing_excluded = []
    if PROJECTS_FILE.exists():
        try:
            existing_excluded = json.loads(
                PROJECTS_FILE.read_text(encoding="utf-8")
            ).get("excluded_projects", [])
        except json.JSONDecodeError:
            pass

    data = {
        "active_projects": sorted(set(found)),
        "excluded_projects": existing_excluded,
    }
    secure_write(PROJECTS_FILE, json.dumps(data, indent=4))
    print(f"\n[OK] {len(found)} project(s) in watch list.")
    return len(found)


# ============================================================
#  Service installation per OS
# ============================================================
def get_service_invocation() -> tuple:
    """
    Determine how to launch the background service.

    Returns (exec_command_string, working_dir_path).

    - PyInstaller binary: invokes the install binary itself with `--service`
      (main.py is bundled inside the binary).
    - Python source: invokes the configured Python interpreter on main.py.
    """
    if getattr(sys, "frozen", False):
        # We're running inside a PyInstaller bundle
        binary_path = Path(sys.executable).resolve()
        exec_str = f'"{binary_path}" --service'
        return exec_str, binary_path.parent

    main_script = (Path(__file__).resolve().parent / "main.py").resolve()
    if not main_script.exists():
        raise FileNotFoundError(
            f"main.py not found next to install.py: {main_script}"
        )
    python_exec = get_python_executable()
    exec_str = f'"{python_exec}" "{main_script}"'
    return exec_str, main_script.parent


def install_systemd_service(exec_command: str, working_dir: Path) -> None:
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_path = service_dir / "phantomgit.service"

    # systemd doesn't want the executable string quoted, so we strip the
    # outer quotes we added for shell-style display. Split into parts but
    # preserve quoting via shlex.
    import shlex
    exec_args = shlex.split(exec_command)
    exec_start = " ".join(exec_args)

    service_content = (
        "[Unit]\n"
        "Description=PhantomGit Background Service\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={working_dir}\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=30\n"
        f"StandardOutput=append:{LOG_FILE}\n"
        f"StandardError=append:{LOG_FILE}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    service_path.write_text(service_content, encoding="utf-8")

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "phantomgit.service"], check=False)
    subprocess.run(["systemctl", "--user", "restart", "phantomgit.service"], check=False)

    print(f"[OK] Systemd service installed: {service_path}")
    print("     Status: systemctl --user status phantomgit")
    print(f"     Logs:   tail -f {LOG_FILE}")
    print("     For run-after-logout: loginctl enable-linger $USER")


def install_launchd_agent(exec_command: str, working_dir: Path) -> None:
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.user.phantomgit.plist"

    import shlex
    exec_args = shlex.split(exec_command)
    args_xml = "\n".join(f"        <string>{a}</string>" for a in exec_args)

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.phantomgit</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>
    <key>WorkingDirectory</key>
    <string>{working_dir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{LOG_FILE}</string>
    <key>StandardErrorPath</key>
    <string>{LOG_FILE}</string>
</dict>
</plist>
"""
    plist_path.write_text(plist_content, encoding="utf-8")

    subprocess.run(["launchctl", "unload", str(plist_path)],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = subprocess.run(["launchctl", "load", str(plist_path)],
                            check=False, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"[OK] LaunchAgent installed: {plist_path}")
        print(f"     Logs: tail -f {LOG_FILE}")
    else:
        print(f"[WARN] LaunchAgent file created but 'load' failed: {result.stderr.strip()}")


def install_windows_task(exec_command: str, working_dir: Path) -> None:
    task_name = "PhantomGit"

    subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = subprocess.run([
        "schtasks", "/Create", "/TN", task_name, "/TR", exec_command,
        "/SC", "ONLOGON", "/RL", "LIMITED", "/F",
    ], capture_output=True, text=True)

    if result.returncode == 0:
        print(f"[OK] Task Scheduler task installed: {task_name}")
        subprocess.run(["schtasks", "/Run", "/TN", task_name],
                       check=False, capture_output=True)
        print(f"     Status: schtasks /Query /TN {task_name}")
        print(f"     Logs:   {LOG_FILE}")
        return

    print(f"[WARN] schtasks failed ({result.stderr.strip()}). Falling back to Startup folder...")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        print("[ERROR] APPDATA environment variable not found; cannot complete Windows install.")
        return
    startup_dir = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup_dir.mkdir(parents=True, exist_ok=True)
    bat_path = startup_dir / "phantomgit.bat"
    bat_path.write_text(
        f'@echo off\nstart "" {exec_command}\n',
        encoding="utf-8",
    )
    print(f"[OK] Startup batch file written: {bat_path}")


def install_service() -> None:
    try:
        exec_command, working_dir = get_service_invocation()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return

    system = platform.system().lower()
    print(f"\n[INFO] Installing background service ({system})...")
    if system == "linux":
        install_systemd_service(exec_command, working_dir)
    elif system == "darwin":
        install_launchd_agent(exec_command, working_dir)
    elif system == "windows":
        install_windows_task(exec_command, working_dir)
    else:
        print(f"[ERROR] Unsupported system: {system}")


# ============================================================
#  Service uninstall per OS
# ============================================================
def uninstall_systemd_service() -> None:
    service_path = Path.home() / ".config" / "systemd" / "user" / "phantomgit.service"
    subprocess.run(["systemctl", "--user", "stop", "phantomgit.service"],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["systemctl", "--user", "disable", "phantomgit.service"],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if service_path.exists():
        service_path.unlink()
        print(f"[OK] Removed systemd unit: {service_path}")
    else:
        print(f"[INFO] No systemd unit at {service_path}")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)


def uninstall_launchd_agent() -> None:
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.user.phantomgit.plist"
    subprocess.run(["launchctl", "unload", str(plist_path)],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if plist_path.exists():
        plist_path.unlink()
        print(f"[OK] Removed LaunchAgent: {plist_path}")
    else:
        print(f"[INFO] No LaunchAgent at {plist_path}")


def uninstall_windows_task() -> None:
    task_name = "PhantomGit"
    result = subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"],
                            check=False, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"[OK] Removed Task Scheduler task: {task_name}")
    else:
        print(f"[INFO] No task named '{task_name}' (or already removed)")
    appdata = os.environ.get("APPDATA")
    if appdata:
        bat = (Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
               / "Programs" / "Startup" / "phantomgit.bat")
        if bat.exists():
            bat.unlink()
            print(f"[OK] Removed Startup entry: {bat}")


# ============================================================
#  Service state detection (for `status`)
# ============================================================
def service_state() -> str:
    system = platform.system().lower()
    if system == "linux":
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "phantomgit.service"],
            capture_output=True, text=True,
        )
        return r.stdout.strip() or "unknown"
    if system == "darwin":
        r = subprocess.run(["launchctl", "list", "com.user.phantomgit"],
                           capture_output=True, text=True)
        return "loaded" if r.returncode == 0 else "not loaded"
    if system == "windows":
        r = subprocess.run(["schtasks", "/Query", "/TN", "PhantomGit"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return "not installed"
        out = r.stdout
        if "Running" in out:
            return "running"
        if "Ready" in out:
            return "scheduled"
        return "installed"
    return "unsupported platform"


# ============================================================
#  Commands
# ============================================================
def cmd_install(args) -> None:
    print("=" * 50)
    print("PhantomGit Setup Wizard")
    print("=" * 50)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(CONFIG_DIR, 0o700)
    print(f"[OK] Config directory ready: {CONFIG_DIR}")

    existing = {}
    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("[WARN] Existing config is corrupt; starting fresh.")

    github_token = ask_for_github_token(existing)
    ai_provider = ask_for_ai_provider(existing)
    mode = ask_for_mode(existing)
    ensure_dependencies(ai_provider["type"], mode)

    config = build_default_config(github_token, ai_provider)
    config["snapshot"]["mode"] = mode
    # Carry over any custom snapshot/scanning/timeout tweaks from existing config
    for section in ("snapshot", "scanning", "timeouts"):
        if section in existing and isinstance(existing[section], dict):
            for k, v in existing[section].items():
                if k not in config[section]:
                    config[section][k] = v

    write_config(config)
    print(f"[OK] Configuration written to {CONFIG_FILE}")

    scan_projects(config, interactive=True)
    install_service()

    print("\n" + "=" * 50)
    print("Setup complete. PhantomGit is now running in the background.")
    print("=" * 50)


def cmd_uninstall(args) -> None:
    print("=" * 50)
    print("PhantomGit Uninstaller")
    print("=" * 50)

    system = platform.system().lower()
    if system == "linux":
        uninstall_systemd_service()
    elif system == "darwin":
        uninstall_launchd_agent()
    elif system == "windows":
        uninstall_windows_task()
    else:
        print(f"[WARN] Unsupported system: {system}")

    if CONFIG_DIR.exists():
        response = input(
            f"\nDelete configuration directory {CONFIG_DIR}? "
            "This removes API keys and the project list. [y/N]: "
        ).strip().lower()
        if response == "y":
            shutil.rmtree(CONFIG_DIR)
            print(f"[OK] Removed: {CONFIG_DIR}")
        else:
            print(f"[INFO] Configuration preserved at: {CONFIG_DIR}")
    else:
        print("[INFO] No configuration directory to remove.")

    print("\nNote: the shadow GitHub repository was NOT deleted.")
    print("      Visit https://github.com/settings/repositories to remove it.")
    print("\nUninstall complete.")


def cmd_config_show(args) -> None:
    config = read_config()
    if args.unmask:
        print(json.dumps(config, indent=2))
    else:
        print(json.dumps(mask_secrets(config), indent=2))


def cmd_config_get(args) -> None:
    config = read_config()
    value = get_nested(config, args.key)
    if value is None:
        print(f"[ERROR] Key not found: {args.key}")
        sys.exit(1)
    if is_secret_key_path(args.key) and not args.unmask and isinstance(value, str):
        value = mask_value(value)
    if isinstance(value, (dict, list)):
        print(json.dumps(mask_secrets(value, args.key) if not args.unmask else value, indent=2))
    else:
        print(value)


def cmd_config_set(args) -> None:
    config = read_config()
    # If no value provided, prompt (masked for secrets)
    if args.value is None:
        if is_secret_key_path(args.key):
            value_str = prompt_secret(f"Value for {args.key}: ")
        else:
            value_str = input(f"Value for {args.key}: ")
    else:
        value_str = args.value

    # Try JSON parsing first (handles int, float, bool, null, lists, objects).
    # Fall back to raw string.
    try:
        value = json.loads(value_str)
    except json.JSONDecodeError:
        value = value_str

    set_nested(config, args.key, value)
    write_config(config)
    display = mask_value(value) if is_secret_key_path(args.key) and isinstance(value, str) else value
    print(f"[OK] Set {args.key} = {display}")


def cmd_reconfigure_ai(args) -> None:
    config = read_config()
    new_provider = ask_for_ai_provider(config)
    config["ai_provider"] = new_provider
    mode = config.get("snapshot", {}).get("mode", "hybrid")
    ensure_dependencies(new_provider["type"], mode)
    write_config(config)
    print(f"[OK] AI provider updated to: {new_provider['type']}")


def cmd_reconfigure_mode(args) -> None:
    config = read_config()
    new_mode = ask_for_mode(config)
    ai_type = config.get("ai_provider", {}).get("type", "none")
    ensure_dependencies(ai_type, new_mode)
    config.setdefault("snapshot", {})["mode"] = new_mode
    write_config(config)
    print(f"[OK] Change detection mode set to: {new_mode}")
    print("[INFO] Restart the service for the new mode to take effect:")
    system = platform.system().lower()
    if system == "linux":
        print("       systemctl --user restart phantomgit.service")
    elif system == "darwin":
        print("       launchctl unload ~/Library/LaunchAgents/com.user.phantomgit.plist && "
              "launchctl load ~/Library/LaunchAgents/com.user.phantomgit.plist")
    elif system == "windows":
        print('       schtasks /End /TN PhantomGit && schtasks /Run /TN PhantomGit')


def cmd_reconfigure_token(args) -> None:
    config = read_config()
    new_token = prompt_secret("\nGitHub Personal Access Token (masked): ")
    if not new_token:
        print("[INFO] No token entered; keeping the existing one.")
        return
    config.setdefault("github", {})["token"] = new_token
    write_config(config)
    print("[OK] GitHub token updated.")


def cmd_status(args) -> None:
    print("=" * 50)
    print("PhantomGit Status")
    print("=" * 50)

    print(f"Version:       {__version__}")
    print(f"Running as:    {'PyInstaller binary' if is_frozen() else 'Python source'}")
    print(f"Service state: {service_state()}")

    if not CONFIG_FILE.exists():
        print(f"\n[WARN] No config at {CONFIG_FILE}.")
        return

    config = read_config()
    ai = config.get("ai_provider", {})
    snap = config.get("snapshot", {})
    print(f"\nAI provider:        {ai.get('type', 'none')}")
    if ai.get("model"):
        print(f"AI model:           {ai['model']}")
    if ai.get("base_url"):
        print(f"AI base URL:        {ai['base_url']}")
    print(f"Poll interval:      {snap.get('poll_interval_seconds', '?')}s")
    print(f"Shadow repo name:   {snap.get('shadow_repo_name', '?')}")
    print(f"Branch template:    {snap.get('branch_template', '?')}")
    print(f"Retention:          {snap.get('retention_days', '?')} day(s)")
    print(f"Cleanup interval:   {snap.get('cleanup_interval_seconds', '?')}s")

    if PROJECTS_FILE.exists():
        try:
            projects = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
            print(f"\nWatched projects:   {len(projects.get('active_projects', []))}")
            print(f"Excluded projects:  {len(projects.get('excluded_projects', []))}")
        except json.JSONDecodeError:
            print("\n[WARN] projects.json is corrupt.")
    else:
        print("\n[INFO] No projects.json yet.")

    if LOG_FILE.exists():
        print(f"\nRecent log entries (last {args.log_lines} lines):")
        print("-" * 50)
        try:
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as e:
            print(f"[WARN] Could not read log: {e}")
            return
        for line in lines[-args.log_lines:]:
            print(f"  {line}")
    else:
        print("\n[INFO] No log file yet.")


def cmd_rescan(args) -> None:
    config = read_config()
    scan_projects(config, interactive=not args.no_interactive)


# ============================================================
#  Auto-update
# ============================================================
#
#  Two modes:
#
#  - Source mode (running from `python install.py`):
#      git pull + pip install -r requirements.txt + restart service.
#
#  - Frozen mode (PyInstaller binary):
#      Download the platform-appropriate asset from the latest GitHub
#      release, verify against SHA256SUMS.txt, atomically replace the
#      current binary, restart the service.
#
#  Security model:
#    * Release info comes from https://api.github.com (TLS-verified by
#      `urllib` since we don't depend on `requests` here).
#    * Every downloaded binary is SHA256-verified against the manifest
#      published in the release. A mismatch aborts the update.
#    * The previous binary is moved to a `.bak` sibling before replacement,
#      so a failed update can be manually rolled back.

def parse_semver(s: str) -> tuple:
    """
    Parse a 'v1.2.3' / '1.2.3-beta1' style string into a comparable tuple.
    Pre-release versions sort before the matching release.
    """
    s = s.strip().lstrip("v")
    base, _, pre = s.partition("-")
    parts = []
    for chunk in base.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    # A release (no pre-release suffix) sorts AFTER any of its pre-releases.
    # We encode this by appending (1, "") for releases, (0, pre) for pre.
    if pre:
        return (parts[0], parts[1], parts[2], 0, pre)
    return (parts[0], parts[1], parts[2], 1, "")


def get_platform_asset_suffix() -> str:
    """Return the asset suffix matching the platform we're running on."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        return "linux-x86_64"
    if system == "windows":
        return "windows-x86_64"
    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "macos-arm64"
        return "macos-x86_64"
    return None


def http_get_json(url: str, timeout: int = 15) -> dict:
    """Fetch a JSON document via stdlib (no `requests` dependency)."""
    import urllib.request
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "phantomgit-updater",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_download(url: str, dest: Path,
                  connect_timeout: int = 15,
                  read_timeout: int = 600,
                  expected_size: int = 0) -> None:
    """
    Download *url* to *dest* with separate connect / read timeouts.

    connect_timeout  – seconds to wait for the TCP handshake (default 15).
    read_timeout     – seconds allowed between any two consecutive chunks
                       (default 600). This is a socket-level timeout, not a
                       total-transfer timeout, so large files on slow
                       connections are handled correctly.
    expected_size    – if > 0, raises ValueError when the downloaded size
                       does not match (catches truncated transfers that
                       terminate cleanly without an error).
    """
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "phantomgit-updater"})

    # Python's urllib accepts a single timeout that applies to both the
    # connect and every subsequent socket read.  We use read_timeout here
    # because it is the more generous bound; connect failures typically
    # surface quickly regardless of the value chosen.
    ctx = None  # use default SSL context (validates certificates)
    with urllib.request.urlopen(req, timeout=read_timeout) as resp, \
            open(dest, "wb") as f:
        chunk_size = 128 * 1024  # 128 KB per chunk
        downloaded = 0
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)

    if expected_size > 0 and downloaded != expected_size:
        dest.unlink(missing_ok=True)
        raise ValueError(
            f"Download size mismatch: expected {expected_size} bytes, "
            f"got {downloaded} bytes. The transfer was likely truncated."
        )


def _download_with_retry(url: str, dest: Path,
                         connect_timeout: int,
                         read_timeout: int,
                         expected_size: int,
                         max_attempts: int = 3) -> None:
    """Retry http_download up to max_attempts times with exponential back-off."""
    import time as _time
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            http_download(url, dest,
                          connect_timeout=connect_timeout,
                          read_timeout=read_timeout,
                          expected_size=expected_size)
            return  # success
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts:
                wait = 2 ** attempt   # 2, 4 seconds
                print(f"   [WARN] Attempt {attempt}/{max_attempts} failed "
                      f"({exc.__class__.__name__}: {exc}). "
                      f"Retrying in {wait}s...")
                _time.sleep(wait)
                if dest.exists():
                    dest.unlink(missing_ok=True)
    raise last_exc


def get_latest_release(config: dict) -> dict:
    """Fetch the latest release metadata from GitHub. Returns None on failure."""
    upd = config.get("update", {})
    repo = upd.get("github_repo", DEFAULT_UPDATE_REPO)
    include_pre = upd.get("include_prereleases", False)
    timeout = int(config.get("timeouts", {}).get("request_seconds", 15))

    if repo == DEFAULT_UPDATE_REPO or "YOUR_USERNAME" in repo:
        print(f"[ERROR] update.github_repo is not set (currently '{repo}').")
        print("   Set it with: install.py config set update.github_repo owner/repo")
        return None

    try:
        if include_pre:
            # /releases returns all (including pre-releases), newest first
            releases = http_get_json(
                f"https://api.github.com/repos/{repo}/releases", timeout=timeout
            )
            if not releases:
                return None
            return releases[0]
        else:
            # /releases/latest skips drafts and pre-releases
            return http_get_json(
                f"https://api.github.com/repos/{repo}/releases/latest", timeout=timeout
            )
    except Exception as e:
        print(f"[ERROR] Failed to fetch release info: {e}")
        return None


def find_asset(release: dict, suffix: str, is_windows: bool) -> dict:
    """Find the asset matching this platform from a release's asset list."""
    if not release or not release.get("assets"):
        return None
    candidates = []
    for asset in release["assets"]:
        name = asset.get("name", "")
        if suffix in name and (name.endswith(".exe") == is_windows):
            candidates.append(asset)
    return candidates[0] if candidates else None


def fetch_checksums(release: dict, connect_timeout: int, read_timeout: int) -> dict:
    """Download SHA256SUMS.txt and parse it into {filename: sha256}."""
    sums_asset = None
    for asset in release.get("assets", []):
        if asset.get("name", "").upper() in ("SHA256SUMS.TXT", "SHA256SUMS"):
            sums_asset = asset
            break
    if not sums_asset:
        return None

    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        tmp_path = Path(tmp.name)
    try:
        # SHA256SUMS.txt is tiny; use connect_timeout for both phases.
        http_download(sums_asset["browser_download_url"], tmp_path,
                      connect_timeout=connect_timeout,
                      read_timeout=connect_timeout,
                      expected_size=sums_asset.get("size", 0))
        result = {}
        for line in tmp_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # sha256sum format: "<hash>  <filename>" (two spaces)
            # binary mode:      "<hash> *<filename>" (space + asterisk)
            parts = line.split(None, 1)
            if len(parts) == 2:
                result[parts[1].lstrip("*")] = parts[0].lower()
        return result
    finally:
        tmp_path.unlink(missing_ok=True)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def stop_service() -> None:
    """Best-effort stop of the running service so we can replace its binary."""
    system = platform.system().lower()
    if system == "linux":
        subprocess.run(["systemctl", "--user", "stop", "phantomgit.service"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif system == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "com.user.phantomgit.plist"
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif system == "windows":
        subprocess.run(["schtasks", "/End", "/TN", "PhantomGit"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_service() -> None:
    """Best-effort restart after a successful update."""
    system = platform.system().lower()
    if system == "linux":
        subprocess.run(["systemctl", "--user", "start", "phantomgit.service"], check=False)
    elif system == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "com.user.phantomgit.plist"
        if plist.exists():
            subprocess.run(["launchctl", "load", str(plist)], check=False)
    elif system == "windows":
        subprocess.run(["schtasks", "/Run", "/TN", "PhantomGit"],
                       check=False, capture_output=True)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def check_updates_available(config: dict, quiet: bool = False) -> tuple:
    """
    Return (latest_release_dict, is_newer_bool) or (None, False) on failure.
    """
    release = get_latest_release(config)
    if not release:
        return None, False

    latest_tag = release.get("tag_name", "").lstrip("v")
    if not latest_tag:
        if not quiet:
            print("[ERROR] Latest release has no tag_name.")
        return None, False

    current = parse_semver(__version__)
    latest = parse_semver(latest_tag)
    is_newer = latest > current

    if not quiet:
        if is_newer:
            print(f"[INFO] Update available: {__version__} -> {latest_tag}")
            print(f"       Release notes: {release.get('html_url', '?')}")
        else:
            print(f"[OK] You are on the latest version ({__version__}).")
    return release, is_newer


def update_from_source(config: dict, force: bool) -> bool:
    """Update by git-pulling the source tree this install.py lives in."""
    here = Path(__file__).resolve().parent

    if not (here / ".git").is_dir():
        print("[ERROR] Not a git checkout. Either:")
        print("   - Re-clone the repository, or")
        print("   - Download a release binary and use the binary update path.")
        return False

    print("[INFO] Running 'git pull' in source directory...")
    result = subprocess.run(
        ["git", "-C", str(here), "pull", "--ff-only"],
        capture_output=True, text=True,
    )
    print(result.stdout.strip())
    if result.returncode != 0:
        print(f"[ERROR] git pull failed: {result.stderr.strip()}")
        if not force:
            return False
        print("[WARN] --force given; continuing despite git error.")

    req = here / "requirements.txt"
    if req.exists():
        print("[INFO] Refreshing Python dependencies...")
        in_venv = sys.prefix != sys.base_prefix
        pip_args = [sys.executable, "-m", "pip", "install",
                    "--disable-pip-version-check", "-r", str(req)]
        if not in_venv:
            pip_args.insert(4, "--user")
        subprocess.run(pip_args, check=False)

    print("[INFO] Restarting service...")
    stop_service()
    start_service()
    print("[OK] Source update complete.")
    return True


def update_binary(config: dict, release: dict, force: bool) -> bool:
    """Update the running PyInstaller binary in-place."""
    upd = config.get("update", {})
    timeouts = config.get("timeouts", {})
    connect_timeout = int(timeouts.get("request_seconds", 15))
    # Binary downloads can be large (10–50 MB). Give them a generous per-chunk
    # socket timeout (default 600 s) that is separate from the API call timeout.
    read_timeout = int(upd.get("download_timeout_seconds", 600))

    is_windows = platform.system().lower() == "windows"

    suffix = get_platform_asset_suffix()
    if not suffix:
        print(f"[ERROR] No release asset available for platform "
              f"{platform.system()} {platform.machine()}.")
        return False

    asset = find_asset(release, suffix, is_windows)
    if not asset:
        print(f"[ERROR] No matching asset found for suffix '{suffix}'.")
        print(f"        Assets in release: "
              f"{[a.get('name') for a in release.get('assets', [])]}")
        return False

    # --------------------------------------------------------
    # Step 1: Download and verify the SHA256 manifest first.
    # If the manifest is missing we refuse to proceed -- we
    # will not replace a binary we cannot verify.
    # --------------------------------------------------------
    print("[INFO] Fetching SHA256 manifest...")
    expected_sums = fetch_checksums(release, connect_timeout, read_timeout)
    if expected_sums is None:
        print("[ERROR] SHA256SUMS.txt not found in this release.")
        print("        Cannot update without integrity verification.")
        print("        If you built this release yourself, regenerate "
              "it with: sha256sum phantomgit-* > SHA256SUMS.txt")
        return False

    expected_hash = expected_sums.get(asset["name"])
    if not expected_hash:
        print(f"[ERROR] No checksum entry for '{asset['name']}' in SHA256SUMS.txt.")
        print(f"        Available entries: {list(expected_sums.keys())}")
        return False

    # --------------------------------------------------------
    # Step 2: Download the binary with retry + size check.
    # --------------------------------------------------------
    asset_size = asset.get("size", 0)
    print(f"[INFO] Downloading {asset['name']} "
          f"({asset_size // 1024:,} KB, up to {read_timeout}s per chunk)...")

    current_binary = Path(sys.executable).resolve()
    tmp_dir = Path(tempfile.mkdtemp(prefix="phantomgit-update-"))
    tmp_binary = tmp_dir / asset["name"]
    try:
        try:
            _download_with_retry(
                asset["browser_download_url"],
                tmp_binary,
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                expected_size=asset_size,
                max_attempts=3,
            )
        except Exception as exc:
            print(f"[ERROR] Download failed after all retries: {exc}")
            return False

        # --------------------------------------------------------
        # Step 3: Verify SHA256 before touching anything on disk.
        # --------------------------------------------------------
        print("[INFO] Verifying SHA256...")
        actual_hash = sha256_of(tmp_binary)

        if actual_hash.lower() != expected_hash.lower():
            print("[ERROR] SHA256 mismatch — the downloaded file is corrupt "
                  "or was tampered with.")
            print(f"        Expected : {expected_hash}")
            print(f"        Actual   : {actual_hash}")
            print(f"        File size: {tmp_binary.stat().st_size:,} bytes "
                  f"(expected {asset_size:,})")
            print()
            print("Possible causes:")
            print("  1. The download was truncated (network error).")
            print("     → Try again: python install.py update")
            print("  2. The SHA256SUMS.txt in the release is stale.")
            print("     → The release may need to be re-published.")
            print("  3. A proxy or CDN served a different file.")
            print("     → Try on a different network / disable proxies.")
            return False

        print(f"[OK] SHA256 verified: {actual_hash[:16]}...")

        if not is_windows:
            os.chmod(tmp_binary, 0o755)

        # --------------------------------------------------------
        # Step 4: Atomic replace (only reached after clean verify).
        # --------------------------------------------------------
        print("[INFO] Stopping service...")
        stop_service()

        backup_path = current_binary.with_suffix(current_binary.suffix + ".bak")
        try:
            if backup_path.exists():
                backup_path.unlink()
        except Exception:
            pass

        print(f"[INFO] Replacing binary: {current_binary}")
        try:
            if is_windows:
                # Windows cannot delete the running .exe, but renaming is
                # allowed. Rename current → .bak, then move new → current.
                os.replace(current_binary, backup_path)
                shutil.move(str(tmp_binary), str(current_binary))
            else:
                shutil.move(str(current_binary), str(backup_path))
                shutil.move(str(tmp_binary), str(current_binary))
                os.chmod(current_binary, 0o755)
        except Exception as e:
            print(f"[ERROR] Binary replacement failed: {e}")
            # Attempt rollback.
            try:
                if backup_path.exists() and not current_binary.exists():
                    shutil.move(str(backup_path), str(current_binary))
                    print("[INFO] Rolled back to previous binary.")
            except Exception as rollback_exc:
                print(f"[ERROR] Rollback also failed: {rollback_exc}")
                print(f"        Your previous binary is at: {backup_path}")
            return False

        print(f"[INFO] Previous binary saved as: {backup_path}")
        print("[INFO] Restarting service...")
        start_service()
        print(f"[OK] Updated to {release.get('tag_name', '?')}.")
        if is_windows:
            print("[INFO] You can delete the .bak file once the new version "
                  "looks healthy.")
        return True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def cmd_check_update(args) -> None:
    config = read_config()
    release, is_newer = check_updates_available(config, quiet=False)
    if not release:
        sys.exit(1)
    sys.exit(0 if not is_newer else 2)
    # Exit codes: 0 = up to date, 2 = update available, 1 = error.
    # Useful for scripts: `install.py check-update; if [ $? = 2 ]; then ...`


def cmd_update(args) -> None:
    config = read_config()

    if args.check:
        cmd_check_update(args)
        return

    release, is_newer = check_updates_available(config, quiet=False)
    if not release:
        sys.exit(1)
    if not is_newer and not args.force:
        return  # already on latest

    if not args.yes:
        prompt = (f"Update from {__version__} to {release.get('tag_name', '?')}? "
                  f"[y/N]: ")
        if input(prompt).strip().lower() != "y":
            print("[INFO] Update cancelled.")
            return

    if is_frozen():
        success = update_binary(config, release, args.force)
    else:
        success = update_from_source(config, args.force)
    sys.exit(0 if success else 1)


# ============================================================
#  Argument parser
# ============================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PhantomGit installer and management CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version",
        version=f"phantomgit {__version__}",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("install", help="Interactive setup (also the default).")
    sub.add_parser("uninstall", help="Remove the background service.")

    # config
    p_config = sub.add_parser("config", help="Inspect or modify the configuration.")
    config_sub = p_config.add_subparsers(dest="config_command", metavar="SUBCOMMAND")

    p_show = config_sub.add_parser("show", help="Print the configuration.")
    p_show.add_argument("--unmask", action="store_true",
                        help="Show secrets in clear text.")

    p_get = config_sub.add_parser("get", help="Print one configuration value.")
    p_get.add_argument("key", help="Dot-notation key, e.g. ai_provider.model")
    p_get.add_argument("--unmask", action="store_true",
                       help="Show secrets in clear text.")

    p_set = config_sub.add_parser("set", help="Set one configuration value.")
    p_set.add_argument("key", help="Dot-notation key, e.g. snapshot.retention_days")
    p_set.add_argument("value", nargs="?",
                       help="JSON value (omit to be prompted interactively).")

    sub.add_parser("reconfigure-ai", help="Re-run the AI provider wizard.")
    sub.add_parser("reconfigure-mode", help="Switch between polling/watch/hybrid modes.")
    sub.add_parser("reconfigure-token", help="Re-enter the GitHub token.")

    p_status = sub.add_parser("status", help="Show service state and recent logs.")
    p_status.add_argument("--log-lines", type=int, default=15,
                          help="Number of recent log lines to display (default 15).")

    p_rescan = sub.add_parser("rescan", help="Re-scan disk for git projects.")
    p_rescan.add_argument("--no-interactive", action="store_true",
                          help="Don't prompt for extra scan directories.")

    sub.add_parser(
        "check-update",
        help="Check whether a newer release is available (exit 0 = up to date, "
             "2 = update available, 1 = error).",
    )

    p_update = sub.add_parser(
        "update",
        help="Download and apply the latest release.",
    )
    p_update.add_argument("--check", action="store_true",
                          help="Only check for updates, don't download.")
    p_update.add_argument("--force", action="store_true",
                          help="Reinstall even if already on the latest version.")
    p_update.add_argument("-y", "--yes", action="store_true",
                          help="Skip the confirmation prompt.")

    return parser


COMMANDS = {
    "install": cmd_install,
    "uninstall": cmd_uninstall,
    "reconfigure-ai": cmd_reconfigure_ai,
    "reconfigure-mode": cmd_reconfigure_mode,
    "reconfigure-token": cmd_reconfigure_token,
    "status": cmd_status,
    "rescan": cmd_rescan,
    "check-update": cmd_check_update,
    "update": cmd_update,
}

CONFIG_COMMANDS = {
    "show": cmd_config_show,
    "get": cmd_config_get,
    "set": cmd_config_set,
}


def main() -> None:
    # Special mode: when running as the background service (typically when
    # invoked by systemd / launchd / Task Scheduler with --service), delegate
    # to main.py's main loop. This is the entry point for PyInstaller
    # binaries; in source mode the service uses main.py directly.
    if "--service" in sys.argv[1:]:
        try:
            import main
        except ImportError as e:
            print(f"[ERROR] Could not import Main module: {e}")
            print("        The binary may be corrupt. Re-run: phantomgit update --force")
            sys.exit(1)
        main.main()
        return

    # Diagnostic flag used by smoke tests and health checks.
    # Verifies that the frozen Main module is importable and that the service
    # entry-point function exists, without actually starting the service.
    if "--service-check" in sys.argv[1:]:
        try:
            import main  # noqa: F401
            assert callable(getattr(main, "main", None)), "main.main is not callable"
            print(f"[OK] Main module OK (phantomgit {__version__})")
            sys.exit(0)
        except Exception as e:
            print(f"[ERROR] Main module check failed: {e}")
            print("        The binary may be corrupt. Re-run: phantomgit update --force")
            sys.exit(1)

    parser = build_parser()
    args = parser.parse_args()

    # Default to `install` when no subcommand is given
    if args.command is None:
        args.command = "install"

    try:
        if args.command == "config":
            if not getattr(args, "config_command", None):
                parser.parse_args(["config", "--help"])
                return
            CONFIG_COMMANDS[args.config_command](args)
        else:
            COMMANDS[args.command](args)
    except KeyboardInterrupt:
        print("\n[INFO] Cancelled by user.")
        sys.exit(130)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()