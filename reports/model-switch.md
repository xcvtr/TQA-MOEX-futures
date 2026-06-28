# Автоматическое переключение моделей (Gemini Flash‑Lite ↔ free)

## Когда использовать
- **Большой контекст** (например, весь проект, большие датасеты, мульти‑модальные входы) → переключаемся на **Google Gemini 3.1 Flash‑Lite** (платная, но очень дешёвая).
- **Малый/средний контекст** → используем любую **бесплатную** модель, доступную в OpenRouter (например, `nvidia/nemotron-3-super-120b-a12b:free`, `openrouter/owl-alpha`, `inclusionai/ring-2.6-1t:free` и т.д.).

## Основные параметры моделей (из каталога OpenRouter)

| Модель | Контекст | Промпт $/токен | Генерация $/токен |
|--------|----------|----------------|-------------------|
| Google Gemini 3.1 Flash‑Lite | 1 048 576 токенов (~1 M) | 0.00000025 | 0.0000015 |
| Anthropic Claude Opus 4.7‑Fast (для сравнения) | 1 000 000 токенов | 0.00003 | 0.00015 |
| Anthropic Claude Haiku‑Latest | 200 000 токенов | 0.000001 | 0.000005 |
| Free‑пример: `nvidia/nemotron-3-super-120b-a12b:free` | ~128 к токенов | 0 | 0 |

> **Стоимость примерного запроса** (25 k prompt + 5 k completion)  
> - Gemini Flash‑Lite: ≈ $0.0138  
> - Claude Haiku: ≈ $0.05  
> - Claude Opus: ≈ $1.50  

## Алгоритм переключения

1. **Собрать контекст** – объединить все файлы проекта (или нужный подмножество) и добавить пользовательский запрос.
2. **Оценить количество токенов** в собранном тексте.
   - Быстрая аппроксимация: `tokens ≈ ceil(len(text) / 4)` (1 токен ≈ 4 символа латиницы).
   - Для более точной оценки можно использовать `tiktoken` с encoding `cl100k_base`.
3. **Сравнить с порогом** (`THRESHOLD_TOKENS`).  
   - Если `tokens > THRESHOLD` → используем **Gemini 3.1 Flash‑Lite**.  
   - Иначе → используем **бесплатную модель**.
4. **Выполнить запрос** к OpenRouter API с выбранным `model_id`.
5. **Вернуть ответ** пользователю (или сохранить в файл).

## Пример реализации (Python)

Сохраните этот скрипт, например, как `~/hermes/scripts/auto_model_switch.py`:

```python
import os, json, math, subprocess, sys
from pathlib import Path

# ---------- Конфигурация ----------
GEMINI_MODEL   = "google/gemini-3.1-flash-lite"
FREE_MODEL     = "nvidia/nemotron-3-super-120b-a12b:free"   # пример free‑модели
THRESHOLD_TOKENS = int(os.getenv("MODEL_SWITCH_THRESHOLD", "200000"))  # порог в токенах

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_KEY:
    sys.exit("ERROR: переменная окружения OPENROUTER_API_KEY не установлена")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ---------- Оценка токенов ----------
def estimate_tokens(text: str) -> int:
    # Быстрая аппроксимация (1 токен ≈ 4 символа)
    return math.ceil(len(text) / 4)
    # Для точного подсчёта раскомментировать:
    # import tiktoken
    # enc = tiktoken.get_encoding("cl100k_base")
    # return len(enc.encode(text))

# ---------- Сбор контекста ----------
def gather_context(root_path: Path, extra_prompt: str = "") -> str:
    parts = []
    for file in root_path.rglob("*"):
        if file.is_file() and file.suffix in {".py", ".json", ".yaml", ".yml", ".txt", ".md", ".js", ".ts", ".html", ".css"}:
            try:
                content = file.read_text(errors="ignore")
                if len(content) < 2_000_000:   # ограничиваем отдельный файл ~2 МБ
                    parts.append(f"# File: {file.relative_to(root_path)}\n{content}\n")
            except Exception:
                continue
    if extra_prompt:
        parts.append(f"\n# USER REQUEST\n{extra_prompt}\n")
    return "\n".join(parts)

# ---------- Вызов OpenRouter ----------
def call_openrouter(model_id: str, prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user",   "content": prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 4096
    }
    cmd = [
        "curl", "-s", "-X", "POST", OPENROUTER_URL,
        "-H", f"Authorization: Bearer {OPENROUTER_KEY}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps(payload)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"Curl failed: {result.stderr}")
    try:
        resp = json.loads(result.stdout)
        return resp["choices"][0]["message"]["content"]
    except (KeyError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Bad response: {result.stdout}") from e

# ---------- Основная логика ----------
def main():
    project_root = Path(os.getenv("PROJECT_ROOT", "/home/user/projects/matrix"))
    user_request = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else \
                   "Объясни, как работает весь проект и предложи улучшения."

    print("[*] Сбор контекста из", project_root)
    context = gather_context(project_root, extra_prompt=user_request)
    print(f"[*] Собранный текст: {len(context)} символов")

    token_est = estimate_tokens(context)
    print(f"[*] Оценка токенов: {token_est:,}")

    if token_est > THRESHOLD_TOKENS:
        model_id = GEMINI_MODEL
        print("[*] Выбрана платная модель (Gemini) из‑за большого контекста")
    else:
        model_id = FREE_MODEL
        print("[*] Выбрана бесплатная модель")

    print("[*] Отправляю запрос к OpenRouter …")
    answer = call_openrouter(model_id, context)
    print("\n=== Ответ модели ===\n")
    print(answer)

if __name__ == "__main__":
    main()
```

### Как запустить

1. **Экспортировать переменные окружения** (можно добавить в `~/.hermes/env` или экспортировать в текущей сессии):
   ```bash
   export OPENROUTER_API_KEY=sk-or-v1-...   # ваш ключ из OpenRouter
   export PROJECT_ROOT=/home/user/projects/matrix   # optional
   export MODEL_SWITCH_THRESHOLD=200000   # optional, default 200k tokens
   ```

2. **Запустить через Hermes** (пример с `execute_code`):
   ```json
   {
     "action": "execute_code",
     "code": "import subprocess, os, sys; subprocess.run([sys.executable, '/home/user/hermes/scripts/auto_model_switch.py', 'Объясни, как работает модуль auth'], check=True)"
   }
   ```

3. **Или оформить как skill** (см. ниже).

## Оформление как skill (опционально)

Создайте директорию skill: `~/.hermes/skills/auto_model_switch/` и поместите туда:

- `SKILL.md` (описание)
- `scripts/auto_model_switch.py` (скрипт выше)

Пример `SKILL.md`:

```yaml
name: auto_model_switch
description: Автоматически выбирает модель (Gemini Flash‑Lite для большого контекста, иначе free‑модель) и выполняет запрос к OpenRouter.
category: software-development
script: scripts/auto_model_switch.py
```

Затем можно вызывать:

```bash
hermes skill run auto_model_switch --context '{"user_request":"Сделай рефакторинг ..."}'
```

## Настройка порога и тонкой подстройки

| Параметр | Как изменить | Эффект |
|----------|--------------|--------|
| `THRESHOLD_TOKENS` | Изменить в скрипте или через переменную `MODEL_SWITCH_THRESHOLD` | Чем выше порог – реже используется Gemini (экономия, но риск обрезки контекста). Чем ниже – чаще Gemini (больше контекст, выше стоимость). |
| Токенизатор | Раскомментировать блок с `tiktoken` и установить `pip install tiktoken` | Более точная оценка токенов → меньше ложных срабатываний около границы. |
| Бесплатная модель | Поменять переменную `FREE_MODEL` на любую другую free‑модель из каталога OpenRouter | Позволяет подобрать модель с нужным соотношением скорости/качества при небольшом контексте. |
| `max_tokens` в payload | Изменить значение (например, 8192) | Увеличивает допустимую длину ответа, но повышает стоимость output‑токенов. |
| `temperature` | Изменить значение (0.0‑2.0) | Ниже – более детерминированный код, выше – более креативный ответ. |

## Пример расчёта бюджета

Допустим, средний запрос: **30 k prompt + 6 k completion**.

- Стоимость на Gemini:  
  `30k * 0.00000025 + 6k * 0.0000015 = $0.0075 + $0.009 = $0.0165`
- При 150 таких запросах в день → ~$2.48/день, ~$74/мес.

Чтобы уложиться в **$10/мес**, либо:
- Сократить количество запросов (например, до 60/дню → ≈ $3/мес)
- Уменьшить средний размер промпта (кешировать системный инструктив, отправлять только изменённые файлы)
- Использовать бесплатную модель для части запросов (скрипт уже делает это автоматически, если контекст ниже порога).

## Заключение

С этим подходом вы получаете **полностью автоматическое переключение**:
- Большие контексты → мощная, но всё ещё очень дешёвая Gemini 3.1 Flash‑Lite.
- Маленькие контексты → полностью бесплатная модель.

Расходы остаются предсказуемыми и легко контролируемыми через дашборд OpenRouter. При необходимости просто корректируйте порог или меняете free‑модель – и ваш workflow будет оптимизирован под любой бюджет и объём работы.

**Удачной автоматизации!** 🚀