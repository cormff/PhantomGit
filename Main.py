import subprocess
import sys
import os
import json
import requests
import re
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "auto-committer"
CONFIG_FILE = CONFIG_DIR / "config.json"


def run_git_command(command, check=True):
    try:
        result = subprocess.run(command, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        if check:
            print(f"❌ Git Hatası: {' '.join(command)}")
            print(f"Hata detayı: {e.stderr.strip()}")
            sys.exit(1)
        return None


def get_or_ask_token():
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True)
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
            return config.get("github_token")

    token = input("GitHub Token: ").strip()
    with open(CONFIG_FILE, "w") as f:
        json.dump({"github_token": token}, f)
    return token


def get_next_version(token, username, shadow_repo_name):
    """Buluttaki en son versiyon numarasını bulur ve bir artırır."""
    headers = {"Authorization": f"token {token}"}
    url = f"https://api.github.com/repos/{username}/{shadow_repo_name}/branches"

    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        return 0  # Repo yeniyse v.0'dan başla

    branches = resp.json()
    version_numbers = []

    for b in branches:
        # 'autosave-v.' ile başlayan dalları bul ve rakamı çek
        match = re.search(r"autosave-v\.(\[0-9]+)", b['name'])
        if match:
            version_numbers.append(int(match.group(1)))

    if not version_numbers:
        return 0
    return max(version_numbers) + 1


def ensure_shadow_repo(token, local_project_name):
    shadow_repo_name = f"{local_project_name}-autosave"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

    user_resp = requests.get("https://api.github.com/user", headers=headers)
    username = user_resp.json()["login"]

    repo_url = f"https://api.github.com/repos/{username}/{shadow_repo_name}"
    repo_resp = requests.get(repo_url, headers=headers)

    if repo_resp.status_code == 200:
        return repo_resp.json()["clone_url"], username

    print(f"🛠️  Gölge repo oluşturuluyor: {shadow_repo_name}...")
    payload = {"name": shadow_repo_name, "private": True}
    create_resp = requests.post("https://api.github.com/user/repos", headers=headers, json=payload)
    return create_resp.json()["clone_url"], username


def auto_commit_and_push(shadow_clone_url, username):
    token = get_or_ask_token()
    shadow_repo_name = os.path.basename(shadow_clone_url).replace(".git", "")

    # Shadow remote kontrolü
    remotes = run_git_command(["git", "remote"])
    if "shadow" not in remotes:
        auth_url = shadow_clone_url.replace("https://", f"https://{token}@")
        run_git_command(["git", "remote", "add", "shadow", auth_url])

    # Değişiklik kontrolü
    if not run_git_command(["git", "status", "--porcelain"]):
        print("✅ Kaydedilecek yeni bir değişiklik yok.")
        return

    # Yeni versiyon ismini belirle
    next_v = get_next_version(token, username, shadow_repo_name)
    new_branch_name = f"autosave-v.{next_v}"

    print(f"📦 Yeni yedek noktası hazırlanıyor: {new_branch_name}")
    run_git_command(["git", "add", "."])

    # Şimdilik sabit mesaj
    run_git_command(["git", "commit", "-m", f"Autosave {new_branch_name}"])

    print(f"🚀 Buluta gönderiliyor ({new_branch_name})...")
    # Yeni bir dal olduğu için force'a gerek kalmadı
    run_git_command(["git", "push", "shadow", f"HEAD:refs/heads/{new_branch_name}"])

    # Görünmez geri al (Soft Reset)
    run_git_command(["git", "reset", "--soft", "HEAD~1"])
    print(f"🎉 İşlem tamam! Kodların '{new_branch_name}' dalına yedeklendi ve yerel çalışma alanın korunuyor.")


if __name__ == "__main__":
    project_name = os.path.basename(os.getcwd())
    token = get_or_ask_token()

    # İlk commit kontrolü
    if not run_git_command(["git", "rev-parse", "HEAD"], check=False):
        print("⚠️ Hata: Lütfen önce manuel bir ilk commit oluşturun.")
        sys.exit(1)

    url, user = ensure_shadow_repo(token, project_name)
    auto_commit_and_push(url, user)