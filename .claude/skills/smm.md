---
name: smm
description: Автономный контент-завод для соцсетей. Парсит залетевшие тренды, разбирает механику победителя, делает безопасный ремикс без копирования, готовит арт-дирекшен и публикует через n8n. Запуск — одна команда `/smm`.
tags: [smm, content, social-media, claude-code, instagram, telegram, linkedin, threads, reels, ai-art-director]
version: 3.0
language: ru
author: AGINE AI
---

# SMM — Content Factory v3

> Production-ready Claude Code skill. Один `.md` файл. Девять платформ. Два часа в день.

Этот скилл — флагман нашего контент-завода. Запускается командой `/smm` внутри Claude Code и проводит вас через полный цикл: разведка трендов → выбор тем → разбор механики → безопасный ремикс → визуальный арт-дирекшен → превью → публикация.

Без скилла: 4 часа в день на 1-2 платформы.
С скиллом: 15 минут на 9 платформ.

---

## Table of Contents

1. [Что делает этот скилл](#что-делает-этот-скилл)
2. [Требования](#требования)
3. [Quick Start](#quick-start)
4. [Что получаете на выходе](#что-получаете-на-выходе)
5. [Режимы работы](#режимы-работы)
6. [Жёсткие правила](#жёсткие-правила)
7. [Шаг 1 — Загрузка контекста](#шаг-1--загрузка-контекста)
8. [Шаг 2 — Разведка по 5 потокам](#шаг-2--разведка-по-5-потокам)
9. [Шаг 3 — Презентация тем + Экспертная панель](#шаг-3--презентация-тем--экспертная-панель)
10. [Шаг 4 — Reference Deconstruction](#шаг-4--reference-deconstruction)
11. [Шаг 5 — Safe Remix](#шаг-5--safe-remix)
12. [Шаг 5.4 — Auto-trigger CTA](#шаг-54--auto-trigger-cta)
13. [Шаг 5.5 — Script-first Carousel](#шаг-55--script-first-carousel)
14. [Шаг 6 — AI Art Director](#шаг-6--ai-art-director)
15. [Шаг 7 — Сохранение в очередь](#шаг-7--сохранение-в-очередь)
16. [Шаг 8 — Генерация HTML-слайдов](#шаг-8--генерация-html-слайдов--playwright-скриншоты)
17. [Шаг 8.5 — Self-QA слайдов](#шаг-85--self-qa-html-слайдов)
18. [Шаг 9 — Preview в Telegram](#шаг-9--preview-в-telegram)
19. [Шаг 10 — Публикация](#шаг-10--публикация)
20. [Threads Comments Mode](#threads-comments-mode)
21. [Карусели — правила HTML рендера](#карусели--правила-html-рендера)
22. [Standalone обложки (LinkedIn / Telegram)](#standalone-обложки--linkedin--telegram)
23. [Библиотека роботов](#библиотека-роботов)
24. [Customization — настройка под свой бренд](#customization--настройка-под-свой-бренд)
25. [Troubleshooting](#troubleshooting)
26. [Example Run](#example-run)

---

## Что делает этот скилл

`/smm` — автономный пайплайн из 10 шагов, который:

1. **Парсит тренды** через ScrapeCreators (Threads + Reels) и Reddit (бесплатно). Берёт топ-3 поста с каждой платформы.
2. **Подключает экспертную панель** из 5 топ-маркетологов (Gary Vee, Hormozi, Naval, Godin, Mr. Beast) — каждый рекомендует свой выбор тем.
3. **Разбирает механику** победителя: хук, структура, ритм, CTA.
4. **Делает безопасный ремикс** — сохраняет работающую механику, меняет конкретику.
5. **Рендерит карусели** через HTML + Playwright (Instagram 1080×1440, Telegram 1280×720, LinkedIn 1080×1350).
6. **Отправляет preview** в Telegram на одобрение.
7. **Публикует через n8n** на 9 платформ только после явного `publish`.

Ключевой принцип v3: **механика без копии**. Не переписываем чужой пост «под себя» до потери того, что заставило его залететь.

---

## Требования

### Зависимости (CLI)

| Tool | Зачем | Установка |
|---|---|---|
| `bash` | Запуск пайплайна | preinstalled (macOS/Linux) |
| `curl` | HTTP-запросы к API | preinstalled |
| `python3` | Парсинг JSON, рендер слайдов | preinstalled |
| `ffmpeg` | Извлечение аудио из Reels | `brew install ffmpeg` |
| `playwright` (MCP) | Скриншоты HTML-слайдов | через Claude Code MCP |
| `whisper` (Python) | Транскрипция Reels | `pip install openai-whisper` |
| `rembg` (Python) | Удаление фона у роботов | `pip install rembg` |

### Переменные окружения (`.env`)

Создайте `.env` в корне проекта со следующими ключами:

```bash
# Парсинг трендов (платный, ~$20/мес за 100 кредитов)
SCRAPECREATORS_API_KEY=$SCRAPECREATORS_API_KEY

# Telegram превью и публикация
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
TELEGRAM_CHANNEL_ID=$TELEGRAM_CHANNEL_ID          # ID канала (формат -100XXXXXXXXX)
TELEGRAM_PREVIEW_CHAT_ID=$TELEGRAM_PREVIEW_CHAT_ID # ID личного чата для превью

# LinkedIn (опционально, если публикуете на LinkedIn)
LINKEDIN_ACCESS_TOKEN=$LINKEDIN_ACCESS_TOKEN

# n8n webhook (если используете n8n для multi-platform публикации)
N8N_WEBHOOK_URL=$N8N_WEBHOOK_URL
```

> 💡 **Tip:** Reddit-разведка работает без ключей — она использует публичный JSON API. Бесплатно.

### Файловая структура проекта

```
~/your-content-project/
├── CLAUDE.md
├── .env
├── voice.md                          # тон бренда (заполните под себя)
├── briefs/today.md                   # автогенерируется скиллом
├── templates/
│   ├── instagram.md
│   ├── telegram.md
│   ├── remix/
│   │   ├── reference_deconstruction.md
│   │   └── safe_remix_rules.md
│   ├── art_director/
│   │   ├── system_prompt.md
│   │   ├── visual_identity.md
│   │   └── carousel_playbook.md
│   ├── carousel/styles/
│   │   └── tenfold-tech-poster-v1/   # HTML-шаблоны слайдов
│   └── threads/reply_style.md
├── scripts/
│   ├── publish_to_n8n.py
│   ├── render_covers.py
│   └── generate_robots.py
├── assets/robot/                     # библиотека роботов
└── queue/YYYY-MM-DD/                 # ежедневная очередь публикаций
```

> 🔴 **Critical:** перед первым запуском заполните `voice.md` под свой бренд. Это анти-AI фильтр качества — без него посты звучат как стандартный ChatGPT.

---

## Quick Start

```bash
# 1. Загрузить переменные окружения (один раз за сессию)
export $(grep -v '^#' ~/your-content-project/.env | xargs)

# 2. Открыть Claude Code в директории проекта
cd ~/your-content-project && claude

# 3. Запустить скилл
/smm
```

Дальше скилл сам проведёт вас через все 10 шагов. На каждом ключевом этапе он остановится и спросит подтверждение.

---

## Что получаете на выходе

После полного прогона в `queue/YYYY-MM-DD/` появятся:

| Файл / папка | Что внутри |
|---|---|
| `threads/post.txt` | Короткий пост для Threads, готов к копи-пасту |
| `instagram/reels-script.txt` | Сценарий Reels (60 сек) + caption |
| `instagram/carousel-post.txt` | Caption для карусели + ссылка на изображения |
| `linkedin/post.txt` | B2B-пост 300-500 слов |
| `telegram/post.txt` | Пост ≤ 950 символов (запас на HTML-теги) |
| `html/slide-0N.html` | Исходники слайдов карусели (редактируемые) |
| `images/slide-0N.png` | Финальные PNG 1080×1440 для Instagram |
| `covers/tg-XXX.png` | Обложка для Telegram 1280×720 |
| `covers/li-XXX.png` | Обложка для LinkedIn 1080×1350 |
| `creative_brief.json` | Метаданные кампании (концепт, trigger CTA, preset) |
| `briefs/today.md` | Резюме дня: выбранные референсы, deconstruction, статус |

---

## Режимы работы

| Команда | Что делает |
|---|---|
| `/smm` | Полный контент-цикл (10 шагов) |
| `/smm comments` (или `комменты`, `ответь на комментарии`) | Режим черновиков ответов на комментарии в Threads. См. [Threads Comments Mode](#threads-comments-mode) |

---

## Жёсткие правила

> 🔴 **Critical:** эти правила нарушать нельзя. Они защищают от перерасхода API и от мусора в публикации.

- **Не вызывайте Anthropic API напрямую.** Вы уже работаете внутри Claude Code — текст генерируется вашим текущим контекстом.
- **ScrapeCreators: максимум 7 запросов за сессию.** Лимит — 100 кредитов в месяц. Один прогон `/smm` = 2 запроса.
- **Reddit — бесплатно** через публичный JSON API (3 запроса на прогон).
- **n8n публикация — только после явного `publish`** / `публикуй` / `опубликовать`. Никаких автоматических деплоев.
- **`voice.md` — это анти-AI фильтр**, а не команда продавать продукт в каждом посте. Если тема — про чужой инструмент, не превращайте пост в рекламу.
- **Приоритетная вертикаль: Claude Code.** Если среди трендов есть Claude Code, agents, MCP, skills, subagents, browser automation — поднимайте их выше остальных.

---

## Шаг 1 — Загрузка контекста

Перед началом работы скилл читает 5 базовых файлов конфигурации:

```bash
# Эти файлы скилл читает автоматически:
~/your-content-project/voice.md
~/your-content-project/templates/instagram.md
~/your-content-project/templates/telegram.md
~/your-content-project/templates/remix/reference_deconstruction.md
~/your-content-project/templates/remix/safe_remix_rules.md
```

И загружает переменные окружения для внешних API:

```bash
# Подгружаем .env в текущую shell-сессию
export $(grep -v '^#' ~/your-content-project/.env | xargs)
echo "Content env loaded"
```

> 💡 **Tip:** если видите ошибку `SCRAPECREATORS_API_KEY: unbound variable` — `.env` не подгрузился. Проверьте путь к файлу.

---

## Шаг 2 — Разведка по 5 потокам

Скилл делает 5 запросов: 2 платных (ScrapeCreators для Threads и Reels) + 3 бесплатных (Reddit). Результаты сохраняются в `/tmp/` для последующей обработки.

### [T] Threads — Claude Code / AI automation

```bash
# Поиск топовых постов в Threads по теме AI/Claude Code
curl -s "https://api.scrapecreators.com/v1/threads/search?query=Claude+Code+AI+automation+business" \
  -H "x-api-key: $SCRAPECREATORS_API_KEY" > /tmp/sc_threads.json
```

### [R] Instagram Reels — AI / Claude / automation

```bash
# Топ-8 Reels по запросу (платный endpoint, 1 кредит)
curl -s "https://api.scrapecreators.com/v2/instagram/reels/search?query=Claude+Code+AI+automation&page_size=8" \
  -H "x-api-key: $SCRAPECREATORS_API_KEY" > /tmp/sc_reels.json
```

### [C] Reddit r/artificial → идеи под Instagram-карусели

ScrapeCreators убрал поиск Instagram-постов, поэтому идеи для каруселей берём из Reddit (бесплатно):

```bash
# Топ-постов про карусели/threads/списки за неделю
curl -s "https://www.reddit.com/r/artificial/search.json?q=carousel+OR+thread+OR+tips&sort=top&t=week&limit=8&restrict_sr=on" \
  -H "User-Agent: SMM-Agent/3.0" > /tmp/sc_posts.json
```

### [L] Reddit r/ChatGPT → LinkedIn темы

```bash
# Топ за неделю — для B2B-углов на LinkedIn
curl -s "https://www.reddit.com/r/ChatGPT/top.json?t=week&limit=8" \
  -H "User-Agent: SMM-Agent/3.0" > /tmp/reddit_chatgpt.json
```

### [TG] Reddit r/artificial → Telegram AI-новости

```bash
# Топ за неделю — для AI-новостных постов в Telegram
curl -s "https://www.reddit.com/r/artificial/top.json?t=week&limit=8" \
  -H "User-Agent: SMM-Agent/3.0" > /tmp/reddit_ai.json
```

### Парсим топы из всех 5 файлов

После запросов выводим топ-5 по каждому потоку одной командой:

```bash
python3 << 'PYEOF'
import json

def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def text(v):
    # Обрезаем до 140 символов для краткого превью
    return (v or '').replace('\n', ' ')[:140]

threads = load('/tmp/sc_threads.json')
reels = load('/tmp/sc_reels.json')
posts = load('/tmp/sc_posts.json')
chatgpt = load('/tmp/reddit_chatgpt.json')
artificial = load('/tmp/reddit_ai.json')

print('[T] Threads')
for i, p in enumerate((threads.get('threads') or threads.get('posts') or threads.get('data') or [])[:5], 1):
    user = p.get('username') or (p.get('user') or {}).get('username') or '?'
    likes = p.get('like_count') or p.get('likes') or 0
    body = p.get('text') or p.get('caption') or ''
    print(f'T{i}. @{user} | {likes} likes | {text(body)}')

print('\n[R] Reels')
for i, r in enumerate((reels.get('reels') or reels.get('data') or [])[:5], 1):
    user = (r.get('owner') or {}).get('username') or r.get('username') or '?'
    plays = r.get('play_count') or r.get('view_count') or 0
    body = r.get('caption') or ''
    print(f'R{i}. @{user} | {plays} views | {text(body)}')

print('\n[C] Reddit / идеи под Instagram-карусели')
for i, p in enumerate(posts.get('data', {}).get('children', [])[:5], 1):
    d = p.get('data', {})
    print(f"C{i}. {d.get('ups', 0)} upv · {d.get('num_comments', 0)} comments | {text(d.get('title'))}")

print('\n[L] Reddit r/ChatGPT')
for i, p in enumerate(chatgpt.get('data', {}).get('children', [])[:5], 1):
    d = p.get('data', {})
    print(f"L{i}. {d.get('ups', 0)} upv · {d.get('num_comments', 0)} comments | {text(d.get('title'))}")

print('\n[TG] Reddit r/artificial')
for i, p in enumerate(artificial.get('data', {}).get('children', [])[:5], 1):
    d = p.get('data', {})
    print(f"TG{i}. {d.get('ups', 0)} upv · {d.get('num_comments', 0)} comments | {text(d.get('title'))}")
PYEOF
```

> 💡 **Tip:** если ScrapeCreators возвращает пустой массив — попробуйте другой `query`. Лимит на запрос — 1 кредит независимо от результата.

---

## Шаг 3 — Презентация тем + Экспертная панель

Этот шаг состоит из **двух частей** в одном выводе:

- **Часть А** — показ всех 15 тем (5 платформ × 3 варианта) с полным переводом на русский
- **Часть Б** — экспертная панель из 5 топ-маркетологов, которая разбирает варианты и даёт рекомендацию

### Часть А — Презентация тем

**Перед показом** скилл читает полные тексты постов из `/tmp/sc_threads.json`, `/tmp/sc_reels.json`, `/tmp/sc_posts.json`, `/tmp/reddit_chatgpt.json`, `/tmp/reddit_ai.json` — не ограничивается обрезкой 140 символов из парсера. Берёт полный `text` / `caption` / `selftext` и переводит его целиком.

Затем показывает топ-3 по каждому потоку **полностью на русском языке**.

> 🔴 **Critical:** многие фаундеры не читают на английском. Переводите дословно, не пересказывайте своими словами.

**Правила перевода:**

1. Переводите **весь существенный текст поста** — заголовок + 2-4 строки сути. Не только хук.
2. Перевод **дословный**: сохраняйте структуру и интонацию автора.
3. **Профессиональные англ-термины** объясняйте в скобках при первом упоминании:
   - `harness` → «harness (обвязка вокруг модели)»
   - `agent` → «agent (AI-агент)»
   - `MCP` → «MCP (протокол подключения инструментов)»
   - `compute` → «compute (вычислительные мощности)»
   - `vibe coding` → «vibe coding (программирование "по ощущению")»
4. **Бренды и ники** оставляйте как есть: `@danmartell`, `Claude Code`, `Anthropic`, `OpenAI`, `Cursor`, `Sora`, `ChatGPT`.
5. Если в посте лонгрид — выбирайте **3-4 ключевые мысли**, переводите их полностью.
6. Не оставляйте **ни одного непереведённого английского слова** кроме брендов и ников из правил выше.

**Структура каждого пункта:**
- Автор (англ. ник)
- Метрика (likes / views / upvotes)
- **Перевод поста** — полный, дословный, 2-4 строки
- **Почему залетело** — тип механики на русском (`провокация`, `лист-формат`, `регрет-хук`, `контр-нарратив`, `личная история`)

**Формат вывода (строго соблюдайте отступы и разделители):**

```text
╔══════════════════════════════════════════════════╗
║   ВЫБЕРИ ТЕМУ ДЛЯ КАЖДОЙ ПЛАТФОРМЫ              ║
║   приоритет — Claude Code / agents / MCP        ║
╚══════════════════════════════════════════════════╝


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🧵  THREADS  ·  короткий пост / дискуссия
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  T1  @moneylobsters  ·  384 ❤️
      Перевод:
        «Бесплатный AI-агент (агент-приложение) за 5 шагов.
         Берёшь OpenClaw, подключаешь к GLM 5 как модели
         и оборачиваешь в Kilocode для интерфейса. Всё.
         Получаешь полный аналог Cursor бесплатно.»
      Почему залетело: лист-хук «5 шагов» + бесплатный CTA

  T2  @unwind_ai  ·  285 ❤️
      Перевод:
        «5 бесплатных AI-agent-тулов (агент-приложений)
         о которых вы не знали. Это не Deep Research и не
         Operator за $20 в месяц. 100% open-source —
         можешь скачать и поставить себе.»
      Почему залетело: анти-paid-нарратив + лист 5

  T3  ...


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎬  REELS  ·  сценарий
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  R1  @parthknowsai  ·  84 286 👁
      Перевод:
        «Исследователи говорят: Claude Code — это на 98%
         НЕ AI. То, что вы используете — это harness
         (обвязка вокруг модели), а не сама модель.
         Важность harness растёт быстрее чем модели.»
      Почему залетело: провокация-хук 🔥, разрушение мифа

  R2  ...
  R3  ...


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎠  КАРУСЕЛЬ  ·  Instagram carousel
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  C1  ...
  C2  ...
  C3  ...


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💼  LINKEDIN  ·  B2B / экспертный пост
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  L1  ...
  L2  ...
  L3  ...


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✈️  TELEGRAM  ·  AI-новость / инсайт
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  TG1  ...
  TG2  ...
  TG3  ...


╔══════════════════════════════════════════════════╗
║   Ответь:  T2, R1, C3, L1, TG2                  ║
║   Можно пропустить любую платформу.             ║
╚══════════════════════════════════════════════════╝
```

**Правила оформления:**
- Между платформами — пустая строка + двойной разделитель (━━━).
- Внутри пункта — отступ 2 пробела, выравнивание `Перевод:` / `Почему залетело:` под одну колонку.
- Эмодзи у платформы (🧵 🎬 🎠 💼 ✈️) — обязательны.
- Метрику пишите коротко: `384 ❤️`, `84 286 👁`, `1.2k ⬆️ · 56 💬`.
- Если описание не помещается — переносите на следующую строку с тем же отступом.

### Часть Б — Экспертная панель

**После показа всех 15 тем — обязательно подключите экспертную панель.** Это 5 топ-маркетологов мирового уровня. Каждый смотрит на контент со своего угла и рекомендует свой выбор по каждой платформе.

| # | Эксперт | Угол анализа |
|---|---|---|
| 1 | **Gary Vaynerchuk (GaryVee)** | Native-формат под платформу, attention-механика, что залетает прямо сейчас |
| 2 | **Alex Hormozi** | Value-стек: какую конкретную пользу читатель получает за 10 секунд внимания |
| 3 | **Naval Ravikant** | Долгосрочный резонанс: будет ли пост работать через год; глубина мысли |
| 4 | **Seth Godin** | Позиционирование и племя: «кто это прочитает и скажет — это про меня» |
| 5 | **Mr. Beast (Jimmy Donaldson)** | Hook и retention первых 3 секунд: удержит ли скроллящего |

**Каждый эксперт даёт:**
- 1 свой выбор по каждой из 5 платформ (T/R/C/L/TG)
- Аргументацию 2-3 строки на русском, в характерной манере:
  - Gary — энергично-отрывисто
  - Hormozi — формула value
  - Naval — афористично
  - Godin — короткий тезис
  - Mr. Beast — про крючок и удержание

**Формат вывода:**

```text
╔══════════════════════════════════════════════════╗
║   ЭКСПЕРТНАЯ ПАНЕЛЬ — 5 МАРКЕТОЛОГОВ L99        ║
╚══════════════════════════════════════════════════╝


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯  Gary Vaynerchuk  ·  attention-механика
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Threads:  T2  → лист бесплатных тулов залетит на 100%,
                  это native-формат для Threads сейчас.
  Reels:    R1  → провокация-хук про 98%-not-AI это хлеб
                  для AI-вертикали, ретеншн будет дикий.
  ...


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰  Alex Hormozi  ·  value-стек
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Threads:  T1 → даёт конкретный список из 5 шагов с
                 названиями инструментов. Читатель уносит
                 рабочую инструкцию. Это и есть value.
  ...


╔══════════════════════════════════════════════════╗
║   ЗАКЛЮЧЕНИЕ ПАНЕЛИ                             ║
╚══════════════════════════════════════════════════╝

  Консенсус (≥3 из 5 голосов):
    🧵  Threads  → T2  (Gary, Hormozi, Mr. Beast)
    🎬  Reels    → R1  (Gary, Naval, Mr. Beast)
    🎠  Карусель → C1  (Hormozi, Godin, Naval)
    💼  LinkedIn → L2  (Naval, Godin, Hormozi)
    ✈️  Telegram → TG1 (Gary, Mr. Beast, Hormozi)


╔══════════════════════════════════════════════════╗
║   Согласен с выбором панели?                    ║
║                                                  ║
║   [согласен]  — идём с T2, R1, C1, L2, TG1      ║
║   [свой выбор: T1, R3, ...] — ручной выбор      ║
║   [пересобрать] — перезапуск Шага 2-3           ║
╚══════════════════════════════════════════════════╝
```

**Правила экспертной панели:**
- Каждый эксперт говорит **в своём узнаваемом стиле**, не в нейтральном.
- Аргумент — **2-3 строки максимум**, не лонгрид.
- Если эксперт не согласен с выбором других — пишет это явно: `«Не согласен с T2 — слишком общо. Беру T3.»`
- Заключение — это **математика голосов** (какая тема набрала больше всего), а не среднее мнение.
- Если голоса разделились 2-2-1 — отдайте выбор по дополнительному аргументу самого сильного эксперта (Hormozi или GaryVee).

Скилл ждёт ответа: `согласен` / `свой выбор: ...` / `пересобрать`.

---

## Шаг 4 — Reference Deconstruction

После выбора скилл извлекает полный текст выбранного референса из `/tmp/*.json`.

### Если выбран Reel (R1/R2/R3) — сначала транскрибируйте

Caption Instagram-Reels почти всегда пустой или 1-2 строки. Remix по caption = писать по заголовку без книги. **Обязательно** скачайте и транскрибируйте выбранный Reel перед deconstruction:

```python
import json, subprocess, os, whisper

# 1. Читаем сырой ответ ScrapeCreators
with open('/tmp/sc_reels.json') as f:
    d = json.load(f)

reels = d.get('reels', [])
# 2. Находим нужный по индексу (R1 = reels[0], R2 = reels[1], etc.)
r = reels[N]  # N = выбранный индекс

video_url = r.get('video_url', '')
slug = (r.get('owner') or {}).get('username', 'reel')
video_path = f"/tmp/reel_{slug}.mp4"
audio_path = f"/tmp/reel_{slug}.mp3"

# 3. Скачиваем видео (max 30 сек timeout)
print(f"Скачиваю @{slug}...")
subprocess.run(['curl', '-sL', '-o', video_path, '--max-time', '30', video_url],
               capture_output=True)

# 4. Если видео скачалось — извлекаем аудио и транскрибируем через Whisper
size = os.path.getsize(video_path) if os.path.exists(video_path) else 0
if size > 10000:
    subprocess.run(['ffmpeg', '-i', video_path, '-ar', '16000', '-ac', '1', '-y', audio_path],
                   capture_output=True)
    model = whisper.load_model("small")
    result = model.transcribe(audio_path, language=None, task="transcribe")
    transcript = result["text"].strip()
    print(f"Транскрипт [{result['language']}]:\n{transcript}")
else:
    # Fallback: если видео не скачалось — используем caption
    transcript = r.get('caption', '')
    print(f"Видео не скачалось, использую caption:\n{transcript}")
```

После транскрипции делайте deconstruction на основе **реального текста**, а не caption.

### Для всех платформ — разбор:

- почему пост мог залететь;
- какой тип хука;
- какая структура (для Reels — побитово, с длительностью);
- какие элементы нельзя ломать;
- какие элементы нужно заменить;
- если это карусель — логика каждого слайда.

Не показывайте пользователю длинный технический JSON, покажите кратко:

```text
Я сохраняю механику:
• хук: ...
• структура: ...
• CTA: ...
• что меняю: формулировки, визуал, чужие ссылки/бренд
```

---

## Шаг 5 — Safe Remix

Создайте контент по выбранным платформам.

**Главное правило: механика без копии.**

### Что сохраняем

- хук-механику;
- ритм;
- структуру;
- порядок смыслов;
- CTA-тип;
- слайдовую логику.

### Что меняем

- точные формулировки;
- чужие имена, ссылки, бренды;
- слишком личные утверждения автора;
- язык и локализацию;
- визуал.

### Что НЕ делаем

- прямую рекламу продукта, если тема не про наш workflow;
- переписывание «с нуля», которое убивает выигрышную структуру;
- дословное копирование.

### Правило хука — обязательно при работе по референсу

> 🔴 **Critical:** хук (первый слайд / первая строка поста) берём из референса БЕЗ ИЗМЕНЕНИЙ по структуре и энергии.

Референсные хуки залетели потому, что их механика протестирована на живой аудитории. Придумывать «свой» хук вместо него = потерять 80% охвата.

**Меняем только:** язык (рус), бренд (наш), конкретные примеры.
**Не меняем:** тип хука, ритм, обещание, энергетику.

**Примеры правильной адаптации:**

| Референс | Наш хук |
|---|---|
| «60 Claude Prompts Every Beginner Needs» | «60 промптов для Claude, которые нужны каждому» |
| «Claude Code is 98% NOT AI» | «Claude Code на 98% — не то, что ты думаешь» |
| «Master Claude in a Weekend» | «Освой Claude за выходные» |

**Как НЕ надо** (придумано с нуля, скучно):
- «КЛОД — ЭТО НЕ ЧАТ-БОТ.» — редакционный тезис, не хук
- «Что такое Claude» — заголовок статьи, не хук

**Правило:** первый слайд = зеркало референсного хука. Остальные слайды адаптируем свободнее.

Покажите все тексты пользователю в чате.

---

## Шаг 5.4 — Auto-trigger CTA

После того как ремикс готов, **обязательно встройте trigger-CTA блок** в каждый формат публикации. Это часть DM-funnel system: пользователь пишет ключевое слово в коммент → бот отправляет ссылку на гайд.

### Авто-выбор trigger-слова по теме

```text
IF тема ∈ {Claude, Claude Code, MCP, agent, subagent, skill,
          Anthropic, prompt-engineering, Code workflow}
   → trigger = «КЛОД»
   → URL = https://your-domain.com/claude/mcp

ELIF структура поста = list (есть «N способов», «N ошибок»,
     «N штук», «N фишек», «N шагов»)
   → trigger = «СПИСОК»
   → URL = https://your-domain.com/lists

ELSE (любое другое)
   → trigger = «ГАЙД»
   → URL = https://your-domain.com
```

### Куда встраивать CTA — по платформам

> 🔴 **Critical:** голос CTA — ВСЕГДА от первого лица. Никаких «бот отправит», «получишь автоматически», объяснений про группу или канал — пользователю не нужно знать механику. Просто: пишет «КЛОД» → получает гайд.

**Как работает механика (внутренне, не для постов):**
сервис вроде Chatplace ловит кодовое слово в IG-комментарии → отправляет DM с кнопкой «Забрать гайд» → кнопка ведёт в закрытый TG-канал по invite-ссылке `tg://join?invite=YOUR_INVITE_CODE` → там в закрепе ссылка на ваш гайд. Пользователю всё это знать не нужно.

**Carousel (Instagram):**
- Последний (payoff) слайд содержит **trigger CTA** — одна короткая строка:
  ```
  Напиши «КЛОД» в коммент — пришлю гайд.
  ```
- Подпись поста заканчивается:
  ```
  → напиши «КЛОД» — пришлю гайд.
  ```

**Static post (Threads, LinkedIn, Facebook):**
- Текст поста заканчивается:
  ```
  → напиши «КЛОД» в коммент — пришлю.
  ```

**Reels:**
- **Overlay text в видео** (последние 4 секунды ролика):
  ```
  Напиши «КЛОД» — пришлю гайд
  ```
- **Подпись под Reel** (caption):
  ```
  → напиши «КЛОД» — пришлю гайд.
  ```

**Telegram, Bluesky, Mastodon, Hashnode, Tumblr:**
- Trigger-CTA НЕ ставим — там DM не работает.
  Вместо неё — прямая ссылка:
  ```
  → your-domain.com/claude
  ```

### Жёсткие правила CTA

- Никогда не упоминать бота, автоответчик, группу, канал — только «пришлю».
- Никогда не объяснять что будет после (TG, закреп, гайд) — просто «пришлю».
- CTA всегда максимально короткий: 1 строка, 5-7 слов.
- Авторская подпись «— Имя» не нужна — аккаунт и так от лица бренда.
- **Один trigger на пост**, не «или КЛОД или СПИСОК».
- Слово ПИШЕТСЯ в кавычках или в CAPS чтобы выделялось.
- Подпись CTA-блока — без upsell, без «отметь в сторис», без «подпишись».

### Лог в creative_brief.json

Скилл сохраняет выбор в `creative_brief.json`:

```json
{
  "trigger_cta": {
    "word": "КЛОД",
    "landing_url": "https://your-domain.com/claude/mcp",
    "applied_to": [
      "instagram_carousel_payoff_slide",
      "instagram_carousel_caption",
      "reels_overlay",
      "reels_caption",
      "threads_post"
    ]
  }
}
```

**Что значат поля:**
- `word` — кодовое слово, на которое будет триггериться DM-воронка.
- `landing_url` — куда ведёт ссылка из DM (или прямая, если платформа без DM-воронки).
- `applied_to` — где именно в финальных файлах встроен CTA-блок.

---

## Шаг 5.5 — Script-first Carousel

**Если выбрана карусель** — ДО Art Director напишите единый связный текст карусели (`carousel_script`) на 50-80 слов. Этот текст читается как **одно высказывание** и потом режется на N законченных фраз — по одной на слайд.

> 🔴 **Правило:** прочитайте слайды 1→7 подряд без подписи поста. Должна получиться одна мысль. Если каждый слайд звучит как отдельный факт — переписывайте.

Подробности — в `templates/art_director/carousel_playbook.md`, раздел «Главное правило — SCRIPT-FIRST».

---

## Шаг 6 — AI Art Director

> ⭐ **Визуальный стиль по умолчанию — Tenfold Tech Poster (Preset 6)**

Все карусели, LinkedIn и Telegram делаем в стиле `tenfold-tech-poster-v1`. Этот стиль определён в `templates/art_director/visual_identity.md` → Preset 6. Референсы — в `assets/references/`.

**Всё визуальное — через HTML-шаблоны + Playwright:**
- **Instagram carousel** → HTML-шаблоны в `templates/carousel/styles/tenfold-tech-poster-v1/`
- **LinkedIn и Telegram** → одиночный HTML-слайд из той же папки (`slide-01-cover.html` как база)
- **Без Gemini, без Imagen** — все визуалы детерминированно из HTML

Прочитайте `templates/art_director/carousel_playbook.md` для структуры слайдов (Hook / Setup / Body×4 / Payoff).

Для каждой карусели:
1. Скопируйте шаблон `tenfold-tech-poster-v1/slide-0N-*.html`
2. Замените текст (headline, subline, terminal-команду) под тему
3. Сохраните в `queue/$TODAY/html/`

### Шаг 6.5 — Показ слайдов перед скриншотами

Покажите текст слайдов фаундеру в виде краткой карточки:

```text
КАРУСЕЛЬ: [название]
Слайдов: 7

01/07 — [заголовок слайда]
02/07 — [заголовок слайда]
...

[ok] — генерировать HTML и скриншоты
[правки] — исправить текст конкретного слайда
```

> 🔴 **Critical:** без явного `[ok]` HTML файлы не создаём.

### Self-check перед HTML

Проверьте каждый слайд по чек-листу перед тем как писать HTML:

```text
☐ Текст на русском, не на английском?
☐ Headline ≤ 7 слов?
☐ У каждого body слайда есть заголовок + короткое объяснение?
☐ Слайды 1→7 разворачивают одну мысль, не повторяют друг друга?
☐ CTA-слайд содержит keyword для триггера воронки?
```

Покажите пользователю **краткую визитку карусели**:

```text
╔══════════════════════════════════════════════════╗
║   КАРУСЕЛЬ ГОТОВА К ГЕНЕРАЦИИ                    ║
╚══════════════════════════════════════════════════╝

Концепт:      [1 строка на русском]
Слайдов:      7 (Hook → Setup → Body×4 → Payoff)
Keyword CTA:  [КЛОД / СПИСОК / ГАЙД]

01 — [headline слайда]
02 — [headline слайда]
...

[ok] — создать HTML + скриншоты
[правки X] — исправить слайд N
```

---

## Шаг 7 — Сохранение в очередь

Создайте директории для сегодняшней публикации:

```bash
# $TODAY = текущая дата в формате YYYY-MM-DD
TODAY=$(date +%Y-%m-%d)

# Создаём 5 поддиректорий + images
mkdir -p "$HOME/your-content-project/queue/$TODAY/threads"
mkdir -p "$HOME/your-content-project/queue/$TODAY/instagram"
mkdir -p "$HOME/your-content-project/queue/$TODAY/linkedin"
mkdir -p "$HOME/your-content-project/queue/$TODAY/telegram"
mkdir -p "$HOME/your-content-project/queue/$TODAY/images"
```

Сохраните:

| Файл | Что внутри |
|---|---|
| `threads/post.txt` | Текст поста для Threads |
| `instagram/reels-script.txt` | Сценарий Reels (60 сек) + caption |
| `instagram/carousel-post.txt` | Caption для карусели |
| `linkedin/post.txt` | B2B-пост |
| `telegram/post.txt` | TG-пост (≤ 950 символов) |
| `briefs/today.md` | Резюме дня: референсы, deconstruction, статус `Preview only` |

---

## Шаг 8 — Генерация HTML-слайдов + Playwright скриншоты

> 🔴 **Critical:** только после явного `[ok]` от фаундера.

### Создаём HTML файлы

Для каждого слайда карусели:
1. Копируем соответствующий шаблон из `templates/carousel/styles/tenfold-tech-poster-v1/`
2. Заменяем текст (headline, subline, terminal-команду, номер слайда)
3. Сохраняем в `queue/$TODAY/html/slide-0N.html`

```bash
TODAY=$(date +%Y-%m-%d)
mkdir -p "$HOME/your-content-project/queue/$TODAY/html"
mkdir -p "$HOME/your-content-project/queue/$TODAY/images"
```

### Снимаем скриншоты через Playwright

```bash
# Поднимаем локальный HTTP-сервер чтобы шрифты и assets загружались по http://
cd "$HOME/your-content-project/templates/carousel/styles/tenfold-tech-poster-v1"
python3 -m http.server 9001 &>/tmp/slide-srv.log &
SLIDE_SRV_PID=$!
```

Затем для каждого HTML файла используйте Playwright MCP:

1. `browser_navigate` → `http://localhost:9001/../../../../../../queue/$TODAY/html/slide-0N.html`
   (или открывайте через `file://` с абсолютным путём)
2. `browser_resize` → **1080×1440** (Instagram 4:5 формат)
3. `browser_take_screenshot` → сохраните в `queue/$TODAY/images/slide-0N.png`

После всех слайдов:

```bash
# Останавливаем локальный сервер
kill $SLIDE_SRV_PID
```

**Что лежит после генерации:**
- `queue/$TODAY/html/slide-0N.html` — исходники (редактируемые)
- `queue/$TODAY/images/slide-0N.png` — финальные PNG для публикации

---

## Шаг 8.5 — Self-QA HTML-слайдов

> 🔴 **Critical:** никаких `Preview only` пока слайды не проверены. Это не опционально — детские баги (текст обрезан, цифра вылезла, перенос сломал смысл) недопустимо отправлять в публикацию.

### Как проверять

Запустите локальный HTTP-сервер для слайдов:

```bash
cd "$(dirname /путь/к/слайдам/)" && python3 -m http.server 9001 &>/tmp/slide-srv.log &
```

Для каждого слайда:
1. Откройте в Playwright через `browser_navigate` → `http://localhost:9001/slide-0N-xxx.html`
2. Размер окна — **1080×1440** (`browser_resize`)
3. Сделайте скриншот (`browser_take_screenshot`)
4. Визуально осмотрите — **весь текст читается полностью, ничего не обрезано**

### Чек-лист (каждый слайд)

```text
☐ Весь заголовок виден целиком — не обрезан справа, не уходит за край
☐ Цифра (step-num) не перекрывает основной текст
☐ Переносы строк не ломают смысл слова или фразы
☐ CTA (последний слайд) — без «бот», без «DM», без объяснений механики
☐ Текст НА РУССКОМ — объяснения понятны рядовому пользователю
☐ Технические названия (Claude Code, Playwright MCP) — OK на английском
☐ НО объяснения рядом с техническими терминами — на простом русском
   ✅ «через Playwright MCP сам поднимает превью» — понятно
   ❌ «leverages Playwright MCP's screenshot capture API» — недопустимо
☐ CTA-бокс соответствует правилу: «Напиши «КЛОД» — пришлю гайд.»
```

### Если найден баг

- Исправьте HTML файл
- Перезагрузите и проверьте скриншот снова
- Не отправляйте дальше пока все 7 слайдов не прошли чек-лист

После проверки убейте сервер:

```bash
# Находим процесс по порту и убиваем
kill $(lsof -ti:9001) 2>/dev/null; true
```

---

## Шаг 9 — Preview в Telegram

Отправьте preview в Telegram после сохранения файлов. **Это не публикация.**

Покажите пользователю:

```text
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Preview отправлен в Telegram.

Что делаем?
[publish] — опубликовать через n8n
[rewrite T/R/C/L/TG] — переписать конкретную платформу
[image redo id] — перегенерировать конкретный ассет
[stop] — остановить
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Шаг 10 — Публикация

> 🔴 **Critical:** только после явного `publish`.

```bash
# Загружаем env (на случай если сессия рестартовала)
export $(grep -v '^#' ~/your-content-project/.env | xargs)

TODAY=$(date +%Y-%m-%d)
COVERS_DIR="$HOME/your-content-project/queue/$TODAY/covers"

# Скрипт публикации читает токены из .env, обновлять Google Sheets не нужно
python3 ~/your-content-project/scripts/publish_to_n8n.py \
  --queue-dir "$HOME/your-content-project/queue/$TODAY" \
  --covers-dir "$COVERS_DIR" \
  --platforms linkedin,telegram \
  --date "$TODAY"
```

### Правила публикации Telegram

**Формат: `sendPhoto` + `caption` — одно сообщение, картинка сверху, текст снизу.**

- Если обложка есть → `sendPhoto` с caption (изображение над текстом)
- Caption лимит = **1020 символов** (включая HTML-теги `<b>`, `<i>`)
- При написании `telegram/post.txt` с обложкой → целиться в **≤ 950 символов** (запас на теги)
- Если пост длиннее — сократить, сохранив: заголовок + список + **главный вывод** (вывод не обрезать)
- Канал: `$TELEGRAM_CHANNEL_ID`, бот: `$TELEGRAM_BOT_TOKEN`

### Правила публикации LinkedIn

- LinkedIn + Telegram публикуются напрямую (без n8n для текста)
- Обложка прикрепляется через 3-шаговый upload (`registerUpload` → `PUT` → `ugcPost`)
- Токен берётся из `$LINKEDIN_ACCESS_TOKEN` — обновлять вручную раз в 60 дней

> 💡 **Note:** Reels-сценарий не публикуется автоматически — это материал для съёмки, остаётся в очереди.

---

## Threads Comments Mode

Используется для `/smm comments`.

### Что читаем

```text
~/your-content-project/templates/threads/reply_style.md
~/your-content-project/voice.md
```

### Алгоритм

Режим v1: **черновики + OK**.

1. Получите комментарии из ссылки / браузера / экспорта пользователя.
2. Для каждого комментария определите тип:
   - согласие
   - вопрос
   - сомнение
   - спор
   - хейт
   - потенциальный лид
3. Напишите короткий живой ответ.
4. Если уместно — закончите открытым вопросом.
5. Не публикуйте сами. Покажите черновики пользователю.

### Формат вывода

```text
COMMENT 1
Оригинал: ...
Ответ: ...
Почему так: ...

COMMENT 2
...

Ответь:
[ok comments] — публиковать/использовать
[rewrite 2] — переписать ответ 2
[stop] — не использовать
```

---

## Карусели — правила HTML рендера

> 💡 Эти правила выстраданы на практике. Нарушение любого = сломанный макет, пустые зоны, нечитаемый текст.

### 1. `.slide` wrapper — обязателен

В `make_slide()` тело слайда ВСЕГДА оборачивается в `<div class="slide">`:

```python
content = (
    header + "\n"
    + f'<div class="{slide_class}">\n'
    + tech + "\n" + corners_fixed + "\n"
    + body_html + "\n"
    + "</div>\n"
    + SHARED_FOOTER
)
```

**Почему:** класс `.slide` в `_shared.css` задаёт `display:flex; flex-direction:column; padding:64px 72px 56px`. Без этого враппера `flex:1`, `margin-top:auto` и все остальные flex-паттерны тихо не работают. Весь контент лепится к верхнему краю, нижняя половина слайда пустая.

### 2. Минимальные размеры шрифтов

Слайды просматривают в ленте с телефона, мельком. Если текст не читается с вытянутой руки — это сломанный слайд.

| Элемент | Минимум |
|---|---|
| Headline | 80px (обычно 118-144px) |
| Subline | 30px |
| Body text | 26px |
| `.prompt-item`, `.flow-step` | 24-26px |
| Terminal text | 22px |
| `.table-tool` | 26px |
| `.table-role` | 24px |
| Eyebrow, counter | 18px |

**Как проверять:** откройте скриншот и уменьшите до 30% масштаба. Весь текст должен читаться.

### 3. Паттерн раскладки для листовых слайдов

Контент заполняет весь слайд, нет пустых зон.

```text
[headline большой — визуальный якорь сверху]
[flex:1 — дышащее пространство, circuit bg заполняет]
[список промптов / контент]
[terminal block]
[watermark]
```

Как реализовать в HTML:

```html
<h2 class="headline smaller">КАТЕГОРИЯ</h2>
<div style="flex:1;"></div>        <!-- дышащее пространство -->
<div class="prompt-grid">...</div>
<div class="terminal-row" style="margin-top:24px;">...</div>
```

В CSS:
- `.terminal-row { margin-top:auto; }` — только если нет `flex:1` перед контентом
- `.watermark-under { margin-top:auto; padding-top:24px; }` — всегда якорит watermark к низу

> 🔴 **Important:** `flex:1` и `margin-top:auto` конкурируют за свободное пространство. Используйте `flex:1` как единственный поглотитель, потом `margin-top:24px` для фиксированных отступов.

### 4. Форматирование счётчика слайдов

```python
# ❌ Неправильно — даёт "002 / 07"
def top_row(n, total):
    return f'...<span class="number">0{n} / 0{total}</span>...'

# ✅ Правильно — всегда "02 / 07"
def top_row(n, total):
    return f'...<span class="number">{str(n).zfill(2)} / {str(total).zfill(2)}</span>...'
```

И передавайте `int`, не строку: `top_row(2, 7)`, не `top_row("02", 7)`.

### 5. Subline overflow — проверяйте длинные строки

Если subline длиннее ~50 символов — она выйдет за правый край слайда (ширина 1080px).

Решение: добавьте `<br>` в логичном месте и сократите текст:

```html
<!-- ❌ обрезается -->
<p class="subline">Один .md файл. Девять платформ. Два часа в день.</p>

<!-- ✅ переносится правильно -->
<p class="subline">Один .md файл.<br>Девять платформ. Два часа.</p>
```

### 6. Тёмные блоки terminal — нижний padding

В `_shared.css` у `.terminal` нижний padding должен быть `44px`, не `26px`:

```css
.terminal {
  padding: 24px 30px 44px;  /* top right bottom — увеличен bottom */
}
```

Без этого последняя строка кода в терминале упирается в нижний край блока и выглядит срезанной.

### 7. Тёмные слайды без терминала — watermark якорится через `margin-top:auto`

У слайдов без `.terminal-row` watermark не имеет естественного ориентира внизу. Без `margin-top:auto` он висит в середине слайда.

```css
.watermark-under {
  text-align: center;
  font-family: 'GeistMono';
  /* ... */
  margin-top: auto;   /* ← якорит к низу flex-контейнера */
  padding-top: 24px;
}
```

### 8. Чеклист перед рендером каруселей

```text
☐ make_slide() оборачивает body в <div class="slide"> ?
☐ Все шрифты ≥ 22px в терминале, ≥ 24px в контенте, ≥ 80px в headline?
☐ Счётчик через str(n).zfill(2) ?
☐ Subline ≤ 50 символов на строке, иначе <br> ?
☐ Terminal padding-bottom = 44px ?
☐ flex:1 spacer между headline и контент-блоком на листовых слайдах?
☐ margin-top:auto на .watermark-under для тёмных слайдов без терминала?
☐ После рендера открыть PNG на 30% масштаба — весь текст читается?
```

---

## Standalone обложки — LinkedIn и Telegram

Карусели — не единственный формат. Для LinkedIn-постов и Telegram-каналов нужна отдельная **обложка-картинка**. Та же HTML + Playwright технология, другие размеры и визуальная концепция.

### Пайплайн

Скрипт: `scripts/render_covers.py`

```bash
python3 scripts/render_covers.py             # рендер всех обложек в queue/YYYY-MM-DD/covers/
python3 scripts/render_covers.py --id tg-001 # одну конкретную
python3 scripts/render_covers.py --send      # рендер + отправить в Telegram превью
```

### Форматы и размеры

| Платформа | Размер | Соотношение | Класс HTML |
|---|---|---|---|
| Telegram | 1280×720 | 16:9 | `.tg-cover` |
| LinkedIn | 1080×1350 | 4:5 | `.li-cover` |

### Концепция Telegram (16:9)

> 🔴 **Important:** Telegram-картинка НЕ повторяет текст поста дословно. Она дополняет — задаёт настроение, визуализирует идею.

Пост и картинка — разные вещи:
- Текст поста: читают в канале
- Картинка: зацепляет взгляд в ленте, усиливает главную идею

**Два стиля TG-обложек:**

**A) Стиль карусели** (рекомендуется) — тот же `tenfold-tech-poster-v1`:
- Тёмный фон `var(--ink)`, corner marks, tech-bg
- Большая цифра/слово в headline-стиле (оранжевый акцент)
- Терминальный блок с живым примером (команда/результат)
- Робот из библиотеки `/assets/robot/`
- Короткий subline (2 строки максимум)
- MCP badge / `@your-handle` watermark
- Используйте `custom_html` + отдельный HTML-файл

**B) Чистый визуал** — абстракция, метафора, без текста:
- Только символ/цифра + брендовая метка
- Для постов где атмосфера важнее информации
- Используйте `make_tg_visual()` в `render_covers.py`

**Чего НЕ должно быть в любом случае:**
- ❌ Дословный повтор заголовка/подзаголовка поста
- ❌ Полные списки и пункты из поста
- ❌ CTA («напиши КЛОД»)

**Готовые CSS-компоненты для стиля B:**
- `grid-9` — сетка 3×3 с подсвеченными ячейками (для «9 чего-то»)
- `tg-split` — тёмный/оранжевый split (для сравнений)
- `grid + .MD overlay` — тёмный фон + большой символ + кодовый путь (для файлов/кода)

**Как добавить стиль A (carousel-style cover):**

1. Создайте `cover-tg-XXX.html` в `templates/carousel/styles/tenfold-tech-poster-v1/`
2. Линкуйте `_shared.css`, переопределите `html,body` → 1280×720
3. Используйте `.slide-tg` класс, углы, тёмный фон, терминал, робот
4. В `render_covers.py → build_covers()` добавьте `"custom_html": "path/to/file.html"`

### Концепция LinkedIn (4:5)

Цель: **визуальный авторитет**, B2B-уверенность.

Один блок снизу, много пространства сверху:
- Кремовый фон (`var(--bone)`) — чистый, editorial
- Большая фоновая цифра/символ — прозрачная, 4% opacity (декор)
- Оранжевая полоса сверху (8px)
- Headline: 96px, жирный, до 3 строк
- Divider оранжевый под заголовком
- Subline: 28px, спокойный тон

Правила:
- Никакого тёмного фона — LinkedIn требует clean, credible
- Не более одного акцента — оранжевый divider ИЛИ цифра в фоне
- CTA-текст не нужен — всё в посте

### Как добавить новую обложку

В `scripts/render_covers.py` добавьте объект в `build_covers()`:

**Telegram (визуальный):**
```python
covers.append({
    "id": "tg-004",                # уникальный ID
    "type": "telegram",
    "label": "Категория · Тема",   # маленький лейбл снизу (опционально)
    "body": """
<!-- Здесь HTML-визуал: сетка, split, абстракция, etc. -->
<!-- НЕТ заголовков поста, НЕТ subline, НЕТ CTA -->
<div style="position:absolute;inset:0;background:var(--ink);">
  <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
       font-family:Geist;font-weight:900;font-size:300px;color:rgba(255,255,255,0.06);">
    СИМВОЛ
  </div>
</div>
""",
})
```

**LinkedIn:**
```python
covers.append({
    "id": "li-003",
    "type": "linkedin",
    "tag": "Категория · Тема",
    "headline": 'СТРОКА<br><span class="accent">АКЦЕНТ.</span>',
    "subline": "2-3 предложения. Сдержанный тон, B2B.",
    "bg_num": "7",  # большая цифра/символ в фоне (декор)
})
```

### Правило: одна картинка = один пост

Каждый Telegram-пост и LinkedIn-пост получает свою обложку. ID обложки = тематически связан с постом.

---

## Библиотека роботов

### Где хранятся

```text
assets/robot/robot-XX-name.png    # RGBA (прозрачный фон, rembg)
assets/robot/robot_manifest.json  # реестр всех роботов
```

### Базовая библиотека (10 роботов)

| id | Эмоция | Когда использовать |
|---|---|---|
| `robot-01-thinking` | задумался, рука у подбородка | анализ, размышление, setup-слайды |
| `robot-02-pointing` | указывает влево | «вот решение», направление |
| `robot-03-celebrating` | руки вверх, победа | CTA, payoff, успех |
| `robot-04-working` | наклонился, работает | workflow, процесс, автоматизация |
| `robot-05-waving` | машет рукой | приветствие, cover-слайды |
| `robot-06-facepalm` | рука на лице, «ой нет» | anti-pattern слайды, ошибки |
| `robot-07-mindblown` | руки у головы, шок | инсайт-слайды, «wow»-моменты |
| `robot-08-sleeping` | спит сидя, глаза закрыты | «до автоматизации», ручной труд |
| `robot-09-flexing` | флексит бицепс, кубики пресса | возможности, мощь, «Claude делает это» |
| `robot-10-mic-drop` | бросает микрофон | финальный вердикт, CTA, drop the mic |

### Как добавить новых роботов

Скрипт: `scripts/generate_robots.py`

1. Добавьте новый объект в список `ROBOTS` в скрипте:
   ```python
   {
       "id": "robot-11-name",
       "scene": "Описание позы и эмоции на английском. Сцена = только робот, белый фон.",
   }
   ```
2. Запустите: `python3 scripts/generate_robots.py`
3. Скрипт сам: генерирует через Imagen 4 Fast → удаляет фон (rembg) → сохраняет RGBA PNG → обновляет manifest.

### Правило: фон всегда удалять

> 🔴 **Critical:** каждый новый робот = обязательно `rembg`. Без этого на тёмных слайдах робот стоит на белом прямоугольнике — это сломанный дизайн.

Процесс `rembg` вручную если нужно:

```python
from rembg import remove
from PIL import Image

raw = open("robot.raw.png", "rb").read()
result = remove(raw)
open("robot.png", "wb").write(result)
```

### Промпт-формула для роботов

Стиль фиксированный — менять только `=== SCENE ===`:
- Оранжевый 3D toy figurine, white trim, matte plastic
- Описание позы: конкретная, выразительная, понятная с первого взгляда
- Всегда: «Full body visible. Clean white background.»
- Никакого текста, никаких объектов кроме робота (допустимо: предмет в руке — микрофон, кофе, книга)

---

## Customization — настройка под свой бренд

Чтобы адаптировать скилл под свой контент-проект, отредактируйте 5 точек:

### 1. Голос бренда (`voice.md`)

Опишите как звучит ваш бренд: тон, ритм, главные приёмы, что НИКОГДА не пишете, примеры «как звучим МЫ vs как звучит AI». Это анти-AI фильтр — без него посты будут как стандартный ChatGPT.

### 2. Trigger CTA — кодовые слова и URLs (Шаг 5.4)

Замените в скилле блок `Авто-выбор trigger-слова`:
- `КЛОД` → ваше тематическое слово
- `https://your-domain.com/claude/mcp` → ваш landing
- DM-funnel: подключите Chatplace / ManyChat / любой сервис, который ловит коммент → отправляет DM

### 3. Визуальный пресет (`templates/art_director/visual_identity.md`)

По умолчанию — Tenfold Tech Poster (тёмный фон, оранжевый акцент, terminal-блоки). Чтобы поменять:
- Замените `--ink` (фон) и `--accent` (акцент) в `_shared.css`
- Замените шрифты (Geist → ваш бренд-шрифт)
- Замените corner marks и watermark на свои

### 4. Платформы публикации (`scripts/publish_to_n8n.py`)

В аргументе `--platforms linkedin,telegram` укажите свой список. Чтобы добавить новую платформу — расширьте n8n workflow.

### 5. Экспертная панель (Шаг 3 Часть Б)

5 маркетологов по умолчанию — Gary Vee, Hormozi, Naval, Godin, Mr. Beast. Можно заменить на любых других — главное чтобы у каждого был **свой узнаваемый угол** (attention, value, долгосрочный резонанс, племя, hook). Минимум 5 экспертов для нечётного голосования.

---

## Troubleshooting

### `SCRAPECREATORS_API_KEY: unbound variable`

`.env` не подгрузился в текущую shell-сессию. Решение:

```bash
export $(grep -v '^#' ~/your-content-project/.env | xargs)
```

### ScrapeCreators возвращает пустой массив

Запрос не нашёл постов. Попробуйте:
- Изменить `query` (более широкий или конкретный)
- Использовать английские термины (большинство трендов на английском)
- Проверить лимит: `curl https://api.scrapecreators.com/v1/account` с вашим API-key

### Reddit возвращает 429 / Too Many Requests

Reddit рейт-лимитит. Решение:
- Подождите 1-2 минуты
- Уникальный `User-Agent` (не дефолтный `curl/7.X`)
- Не делайте больше 1 запроса в секунду на один endpoint

### Whisper не транскрибирует Reel

Возможные причины:
- Видео не скачалось (timeout 30 сек) → проверьте `os.path.getsize(video_path)`
- `ffmpeg` не установлен → `brew install ffmpeg`
- Whisper модель не скачана → `whisper.load_model("small")` качает её в первый раз (~500 MB)
- Аудио на неподдерживаемом языке → передавайте `language=None` для auto-detect

### Playwright скриншот пустой / белый

- Локальный HTTP-сервер не запущен → `python3 -m http.server 9001`
- Шрифты не загружаются (нужен http://, не file://) → используйте только `http://localhost:9001/...`
- Браузер не успел отрендериться → добавьте `browser_wait_for` 500ms перед скриншотом

### Telegram caption обрезается

Caption лимит = **1020 символов** (включая HTML-теги). Решение:
- Целиться в ≤ 950 символов при написании
- Если пост длиннее — сократить, **главный вывод не обрезать**
- Альтернатива: `sendMessage` отдельно от `sendPhoto` (но тогда два сообщения, картинка не привязана)

### Robot стоит на белом прямоугольнике

`rembg` не отработал, фон не удалён. Решение:

```python
from rembg import remove
raw = open("robot.raw.png", "rb").read()
result = remove(raw)
open("robot.png", "wb").write(result)
```

### LinkedIn 401 Unauthorized

Токен истёк (LinkedIn токены живут 60 дней). Решение:
- Перевыпустите в LinkedIn Developer Portal
- Обновите `LINKEDIN_ACCESS_TOKEN` в `.env`
- Запустите `export $(grep -v '^#' ~/your-content-project/.env | xargs)`

### Слайд карусели рендерится с пустой нижней половиной

`.slide` wrapper отсутствует. Проверьте `make_slide()` — body должен быть обёрнут в `<div class="slide">`. См. [Карусели — правило 1](#1-slide-wrapper--обязателен).

---

## Example Run

Реальный прогон `/smm` 2026-05-01:

```text
$ /smm

[1/10] Loading context...
✓ voice.md (1.2 KB)
✓ templates/instagram.md (2.1 KB)
✓ templates/telegram.md (1.8 KB)
✓ templates/remix/reference_deconstruction.md (1.5 KB)
✓ templates/remix/safe_remix_rules.md (1.4 KB)
✓ .env loaded (5 vars)

[2/10] Scanning 5 streams...
✓ Threads     → 10 posts (ScrapeCreators, 1 credit)
✓ Reels       → 8 reels  (ScrapeCreators, 1 credit)
✓ Reddit/AI   → 8 posts  (free)
✓ Reddit/ChatGPT → 8 posts (free)
✓ Reddit/r-art → 8 posts  (free)

[3/10] Presenting topics + expert panel...

╔══════════════════════════════════════════════════╗
║   ВЫБЕРИ ТЕМУ ДЛЯ КАЖДОЙ ПЛАТФОРМЫ              ║
╚══════════════════════════════════════════════════╝

[T] Threads
  T1 @moneylobsters · 384 ❤️ — «5 шагов к бесплатному AI-агенту»
  T2 @unwind_ai · 285 ❤️    — «5 бесплатных AI-tools»
  T3 @codewithjacob · 140 ❤️ — «Free agent в 30 строк Python»

[R] Reels
  R1 @parthknowsai · 84 286 👁 — «Claude Code на 98% — НЕ AI»
  R2 @artificialintelligenceee · 40 835 👁 — «Compute > skill»
  R3 @danmartell · 26 995 👁 — «AI automation мертва»

...

╔══════════════════════════════════════════════════╗
║   ЭКСПЕРТНАЯ ПАНЕЛЬ                             ║
╚══════════════════════════════════════════════════╝

Gary Vee:    T2, R1, C1, L2, TG2
Hormozi:     T1, R1, C1, L2, TG2
Naval:       T2, R1, C2, L2, TG1
Godin:       T2, R2, C1, L3, TG2
Mr. Beast:   T1, R1, C1, L1, TG2

Консенсус: T2, R1, C1, L2, TG2

> согласен

[4/10] Deconstructing references...
✓ T2: list-hook «5 free X» + anti-paid narrative
✓ R1: contradiction hook «X is NOT Y» + harness payoff
✓ C1: 7-slide list carousel (cover + 5 tools + CTA)
✓ L2: B2B insight «skill gap → compute gap»
✓ TG2: open-loop + список + анализ

[5/10] Safe remix...
✓ threads/post.txt        (487 chars)
✓ instagram/reels-script.txt (60 sec, 1240 chars)
✓ instagram/carousel-post.txt (caption, 380 chars)
✓ linkedin/post.txt       (1820 chars, 320 words)
✓ telegram/post.txt       (892 chars, fits ≤950 limit)

[5.4] Auto-trigger CTA → «КЛОД» (theme: Claude Code)
[5.5] Carousel script: 67 words, 7 phrases

[6/10] Art Director — Tenfold Tech Poster preset
[6.5] Showing slide cards...

> ok

[7/10] Saving to queue/2026-05-01/
[8/10] Rendering 7 HTML slides...
[8/10] Playwright screenshots (1080×1440)...
✓ slide-01.png ... slide-07.png

[8.5] Self-QA...
✓ Все слайды прошли чек-лист

[9/10] Telegram preview sent.

> publish

[10/10] Publishing via n8n...
✓ LinkedIn  → posted (ugcPost ID: abc123)
✓ Telegram  → posted (msg ID: 4521)
✓ Threads   → queued for n8n
✓ Instagram → queued for n8n

Done. Total time: 14 min. ScrapeCreators credits used: 2.
```

---

## Лицензия и поддержка

Этот скилл распространяется как часть гайдов AGINE AI. Если вы хотите внести улучшение или сообщить о баге — пишите в комментарии под гайдом или в нашем Telegram-канале.

> 💡 **Совет:** перед первым прогоном — сделайте dry-run без публикации. Дойдите до Шага 9 (Preview), посмотрите что получилось, и только потом отдавайте команду `publish`.
