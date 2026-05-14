# Security Policy

## Why This Matters

PhantomGit handles GitHub Personal Access Tokens, optionally talks to
AI providers, and pushes your source code to a remote repository on a
regular basis. A vulnerability here could leak credentials or expose
proprietary code. We take security reports seriously.

---

## Supported Versions

Only the latest release receives security updates. If you're running an
older version, please update before reporting.

| Version | Supported          |
| ------- | ------------------ |
| Latest  | ✅                 |
| Older   | ❌ (please update) |

---

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub
issues, discussions, or pull requests.**

Instead, please report them privately via **GitHub Private Vulnerability
Reporting**:

👉 [Report a vulnerability privately](../../security/advisories/new)

This creates a private advisory that only the maintainers can see. You'll
receive an acknowledgment, and we'll work with you on a fix and
coordinated disclosure.

If, for some reason, you cannot use GitHub's reporting flow, you can also
reach out via the contact information in this repository's GitHub profile
page.

### What to Include

A useful report typically contains:

- A description of the vulnerability and its impact.
- Steps to reproduce, ideally with a minimal example.
- The version (release tag or commit SHA) you tested against.
- Your environment (OS, Python version).
- Suggested mitigation, if you have one in mind.

### What to Expect

- **Initial response**: within 7 days, acknowledging your report.
- **Status updates**: every 14 days while we investigate.
- **Fix timeline**: depends on severity, but we aim for:
  - **Critical**: hotfix release within 7 days of confirmation.
  - **High**: fix in the next patch release (typically within 30 days).
  - **Medium / Low**: fix in the next minor release.
- **Credit**: with your permission, we'll credit you in the security
  advisory and release notes.

---

## Known Security-Relevant Design Decisions

To help reviewers and security researchers, here are deliberate design
decisions that affect the security surface of this project:

### 1. Token storage

The GitHub Personal Access Token is stored in plain JSON at
`~/.config/phantomgit/config.json`. On POSIX systems, the file is set
to mode `0600` (user read/write only). On Windows, the file inherits the
user's profile ACL.

**Tradeoff**: storing in plaintext is the simplest approach and works
without OS keyring dependencies. A future improvement would be to
integrate `keyring` for OS-level credential storage (Keychain, Credential
Manager, libsecret).

### 2. Token transmission to git

The token is **never** written into `.git/config`. For each push, the
token is passed as a transient HTTP header via:

```
git -c http.extraHeader="Authorization: token <TOKEN>" push ...
```

This setting only applies to that single command invocation and is not
persisted.

### 3. Sensitive file detection

Projects with uncommitted changes matching `ignore_patterns` (default:
`.env`, `id_rsa`, `id_ed25519`, `credentials.json`, `.pem`, `secrets.json`,
`.key`, `aws/credentials`) are **skipped entirely** for the current cycle.

This is a safety net, not a guarantee. Users should still maintain a
proper `.gitignore` in each project.

### 4. AI provider data flow

- `none`: no data is sent anywhere.
- `gemini` / `openai` (cloud) / `anthropic`: the diff (truncated to
  `max_diff_chars`, default 30,000) is sent to the configured provider.
- `openai` (local LLM, e.g. Ollama): the diff is sent only to the local
  endpoint; nothing leaves the user's machine.

Users are warned in the README that diffs may leave their machine when a
cloud provider is selected.

### 5. Shadow repository visibility

The shadow repository is created as **private** by default. The
`auto_init: true` flag is used to ensure it has at least one commit
before the first push.

### 6. Cleanup operations

Branch cleanup uses `git push` with deletion refspecs from a temporary
throwaway repository. The operation is scoped to the configured shadow
repo and only deletes branches matching the configured
`branch_template` pattern.

---

## Out of Scope

The following are **not** considered security vulnerabilities for the
purpose of this project:

- The act of the service pushing your code to your own GitHub account —
  this is the documented intended behavior.
- The act of the service sending diffs to your chosen AI provider — also
  documented intended behavior.
- Self-inflicted credential exposure (e.g., committing your `config.json`
  to a public repo).
- Issues caused by third-party dependencies, unless those have not yet
  been patched upstream and there's a clear mitigation we can ship.
- Reports based on automated scanners with no exploitation context.

---

Thanks for helping keep PhantomGit (and its users) safe.