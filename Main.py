import subprocess
import sys
import os
import json
import requests
from pathlib import Path

print("Hello World")

# Arch Linux ve diğer sistemler için standart konfigürasyon dizini
CONFIG_DIR = Path.home() / ".config" / "auto-committer"
CONFIG_FILE = CONFIG_DIR / "config.json"


def run_git_command(command, check=True):
    """Git komutlarını çalıştırır ve çıktısını döndürür."""
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
    """Kullanıcının GitHub Personal Access Token'ını (PAT) alır veya kaydeder."""
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True)

    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
            return config.get("github_token")

    print("\n🔑 Auto-Committer ilk kurulumu yapılıyor...")
    print("Lütfen GitHub Personal Access Token (PAT) girin.")
    print("Bu token, sizin adınıza sadece gizli (private) yedek repolar oluşturmak için kullanılacaktır.")
    token = input("GitHub Token: ").strip()

    with open(CONFIG_FILE, "w") as f:
        json.dump({"github_token": token}, f)
    print("✅ Token güvenle ~/.config/auto-committer/config.json içine kaydedildi.\n")
    return token


def ensure_shadow_repo(token, local_project_name):
    """GitHub'da gizli repo var mı kontrol eder, yoksa oluşturur."""
    shadow_repo_name = f"{local_project_name}-autosave"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    # 1. Kullanıcının kimliğini ve kullanıcı adını al
    user_resp = requests.get("https://api.github.com/user", headers=headers)
    if user_resp.status_code != 200:
        print("❌ Token geçersiz veya GitHub API'ye ulaşılamıyor.")
        sys.exit(1)
    username = user_resp.json()["login"]

    # 2. Repo var mı diye kontrol et
    repo_url = f"https://api.github.com/repos/{username}/{shadow_repo_name}"
    repo_resp = requests.get(repo_url, headers=headers)

    if repo_resp.status_code == 200:
        print(f"☁️  Bulutta gizli repo zaten mevcut: {shadow_repo_name}")
        return repo_resp.json()["clone_url"]

    # 3. Yoksa oluştur
    print(f"🛠️  Bulutta yeni gizli repo oluşturuluyor: {shadow_repo_name}...")
    create_url = "https://api.github.com/user/repos"
    payload = {
        "name": shadow_repo_name,
        "private": True,
        "description": "Auto-Committer tarafindan otomatik olusturulan yedek reposu."
    }
    create_resp = requests.post(create_url, headers=headers, json=payload)

    if create_resp.status_code == 201:
        print("✅ Gizli repo başarıyla oluşturuldu!")
        return create_resp.json()["clone_url"]
    else:
        print(f"❌ Repo oluşturulamadı: {create_resp.text}")
        sys.exit(1)


def auto_commit_and_push(shadow_clone_url):
    """Değişiklikleri alır, shadow repoya yollar ve görünmez şekilde geri alır."""
    # Projede shadow adında bir remote var mı kontrol et, yoksa ekle
    remotes = run_git_command(["git", "remote"])
    if "shadow" not in remotes:
        # Token ile yetkilendirilmiş URL formatına çevir (şifresiz push yapabilmek için)
        auth_url = shadow_clone_url.replace("https://", f"https://{get_or_ask_token()}@")
        run_git_command(["git", "remote", "add", "shadow", auth_url])
        print("🔗 Shadow remote başarıyla yerel git ayarlarına eklendi.")

    # Değişiklik var mı kontrolü
    status = run_git_command(["git", "status", "--porcelain"])
    if not status:
        print("✅ Commitlenecek yeni bir değişiklik yok.")
        return

    current_branch = run_git_command(["git", "branch", "--show-current"])
    shadow_branch_name = f"autosave-{current_branch}"

    print("📝 Değişiklikler işleniyor...")
    run_git_command(["git", "add", "."])

    # Şimdilik sabit mesaj, LLM eklendiğinde burası değişecek
    commit_message = f"[Otopilot] {current_branch} dalındaki değişiklikler yedeklendi."
    run_git_command(["git", "commit", "-m", commit_message])

    print("🚀 Gizli repoya gönderiliyor (Push)...")
    # Dosyaları shadow remote'a pushla
    run_git_command(["git", "push", "shadow", f"HEAD:{shadow_branch_name}"])

    # MAGIC MOVE: Yerel commit'i sil ama dosyaları olduğu gibi bırak
    run_git_command(["git", "reset", "--soft", "HEAD~1"])

    print("🎉 Arkaplan yedekleme işlemi başarıyla tamamlandı. Yerel çalışma alanı temiz tutuldu!")


if __name__ == "__main__":
    # Projenin klasör ismini al (Örn: 'unibuddy' veya 'matter-of-pride')
    project_folder_name = os.path.basename(os.getcwd())

    token = get_or_ask_token()

    # İlk commiti atmamış yepyeni bir repo ise soft reset çöker. Güvenlik kontrolü:
    if not run_git_command(["git", "rev-parse", "HEAD"], check=False):
        print("⚠️ Uyarı: Bu repoda henüz hiç commit yok. Lütfen önce manuel olarak bir 'İlk Commit' oluşturun.")
        sys.exit(1)

    shadow_url = ensure_shadow_repo(token, project_folder_name)
    auto_commit_and_push(shadow_url)

print("Good bye World")