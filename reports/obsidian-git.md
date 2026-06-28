# Подключение Obsidian Vault к приватному репозиторию GitHub (как в проекте tgbot)

## Что используется в tgbot
- **Репозиторий**: `https://github.com/xcvtr/obsidian_bot_notes.git`
- **Последний коммит (HEAD)**: `56b7c2427dbdbcd55e25b5427fe4b56ce050c78d`
- **Механизм синхронизации**: Obsidian Git plugin (авто‑commit + auto‑push)  
  (можно также использовать простой cron‑скрипт).

## Шаги для собственного Vault

### 1. Создайте приватный репозиторий на GitHub
1. GitHub → **New repository** → задайте имя (например, `my-obsidian-vault`), выберите **Private**.  
2. Оставьте репозиторий пустым (без README, .gitignore, licence).  
3. Скопируйте URL репозитория:  
   - HTTPS: `https://github.com/<username>/my-obsidian-vault.git`  
   - SSH:   `git@github.com:<username>/my-obsidian-vault.git`

### 2. Инициализируйте Git‑репозиторий внутри вашего Obsidian Vault
```bash
cd ~/path/to/your/vault   # например, ~/ObsidianVault
git init
git config user.name "Ваше Имя"
git config user.email "you@example.com"
```

### 3. Привяжите удалённый репозиторий
```bash
git remote add origin https://github.com/<username>/my-obsidian-vault.git
# либо, если используете SSH:
# git remote add origin git@github.com:<username>/my-obsidian-vault.git
```

### 4. Настройте аутентификацию
**Вариант A – SSH‑ключ (рекомендуется)**
```bash
ssh-keygen -t ed25519 -C "your_email@example.com"   # если ключа ещё нет
# Добавьте содержимое ~/.ssh/id_ed25519.pub в GitHub → Settings → SSH and GPG keys → New SSH key
ssh -T git@github.com   # должно приветствовать вас
```

**Вариант B – HTTPS + Personal Access Token (PAT)**
1. Сгенерируйте PAT с правом `repo` (Settings → Developer settings → Personal access tokens).  
2. Сохраните токен в credential‑helper:  
   ```bash
   git config --global credential.helper store
   ```
3. При первом `git push` введите логин и PAT как пароль – токен сохранится в `~/.git-credentials`.

### 5. Сделать начальный коммит и_push
```bash
git add .
git commit -m "Initial import of Obsidian vault"
git push -u origin main   # создаёт ветку main на удалённом репозитории
```

### 6. Автоматическая фиксация и push

#### Вариант 1 – Obsidian Git plugin (как в tgbot)
1. Откройте Obsidian → **Settings → Community plugins → Browse** → найдите **Git**, установите и включите.  
2. В настройках плагина:  
   - **Repository Path** – оставьте пустым (по‑умолчанию корень Vault).  
   - **Auto commit on file change** – включите, задайте задержку (например, 2 сек).  
   - **Auto push** – включите, установите интервал (например, каждые 5 минут) либо отметьте «Push on commit».  
   - **Commit message template** – можно оставить по‑умолчанию (`Auto‑save: {{date}} {{time}}`) или задать свой.  
   - **Ignore list** – добавьте то, чего не нужно версионировать (`.obsidian/plugins/*`, `.obsidian/workspace*`, `*.log`, большие файлы в `attachments/` и т.п.).  
3. Сохраните настройки. При каждом сохранении заметки плагин выполнит `git add .`, `git commit -m "... "` и, если настроено, `git push origin main`.

#### Вариант 2 – Простой cron‑скрипт (если плагин не нужен)
Создайте файл `~/scripts/obsidian_git_sync.sh`:
```bash
#!/usr/bin/env bash
VAULT="$HOME/my-obsidian-vault"
LOG="$HOME/obsidian_git_sync.log"

cd "$VAULT" || exit 1

git add -A
if ! git diff-index --quiet HEAD --; then
    TS=$(date '+%Y-%m-%d %H:%M:%S')
    git commit -m "Auto‑sync $TS"
    git push origin main
    echo "[$TS] Pushed changes" >> "$LOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] No changes" >> "$LOG"
fi
```
Сделайте исполняемым и добавьте в crontab (каждые 5 минут):
```bash
chmod +x ~/scripts/obsidian_git_sync.sh
crontab -e
# добавить строку:
*/5 * * * * /home/youruser/scripts/obsidian_git_sync.sh >> /home/youruser/obsidian_git_sync_cron.log 2>&1
```

### 7. Интеграция с Hermes
Теперь ваш Vault – обычный Git‑репозиторий с авто‑commit/push. Hermes может просто записывать файлы в эту папку (через `write_file`, `patch`, `execute_code` и т.д.). После записи файл появится в Vault, плагин Obsidian Git (или cron‑скрипт) зафиксирует изменение и отправит его в ваш приватный репозиторий на GitHub. Изменения будут видны в десктопной и мобильной версии Obsidian, а также в истории коммитов на GitHub.

**Пример записи заметки через Hermes:**
```json
{
  "action": "write_file",
  "path": "/home/youruser/my-obsidian-vault/Заметки/Идея_2026-05-24.md",
  "content": "# Идея 2026-05-24\n\n- Здесь описываем идею...\n"
}
```

### 8. Если захотите переключиться на собственный репо позже
1. Создайте новый пустой приватный репо на GitHub (например, `myuser/my-obsidian-vault`).  
2. Измените URL remote:  
   ```bash
   git remote set-url origin https://github.com/myuser/my-obsidian-vault.git
   # либо SSH: git@github.com:myuser/my-obsidian-vault.git
   ```
3. Выполните `git push -u origin main` (или просто `git push` если upstream уже установлен).  
Все остальные настройки (плагин/скрипт) остаются без изменений.

## Кратко: что вы уже имеете из tgbot
| Элемент | Значение | Где взять |
|---------|----------|-----------|
| Repository URL | `https://github.com/xcvtr/obsidian_bot_notes.git` | `projects/tgbot/obsidian_notes/.git/config` → `[remote "origin"] url` |
| Последний коммит (HEAD) | `56b7c2427dbdbcd55e25b5427fe4b56ce050c78d` | `git ls-remote … HEAD` |
| Авто‑коммит/push | Обеспечен **Obsidian Git plugin** (видно по наличию папки `.obsidian/plugins/obsidian-git/` внутри `obsidian_notes`). | Плагин Obsidian → Git |

Склонировав тот же репозиторий и включив Obsidian Git plugin (или настроив аналогичный cron‑скрипт), вы получите идентичную рабочую процедуру: **Hermes → файл в Vault → автоматический git‑commit → push в ваш приватный GitHub‑репо → синхронно доступно в Obsidian на всех устройствах**.

Если понадобится помощь с генерацией SSH‑ключа, созданием PAT или точной настройкой плагина – дайте знать, я подготовлю конкретные команды. 🚀