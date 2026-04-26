# Как местный — Travel Bot

## О проекте

Telegram бот [@like_a_local_bot](https://t.me/like_a_local_bot) — travel-помощник для самостоятельных путешественников на русском языке.

---

## Технический стек

- **Python 3.13**
- **python-telegram-bot 20.3**
- **SQLite** (Railway Volume `/app/data/users.db`)
- **GitHub Pages** для WebApp страниц
- **Railway** для хостинга
- **GitHub репозиторий:** [andreev032/Travel-Bot](https://github.com/andreev032/Travel-Bot)

---

## Структура файлов

```
bot.py              — основной файл бота
posts.py            — 90 постов для автопостинга в канал
requirements.txt    — зависимости
Procfile            — команда запуска
nixpacks.toml       — конфигурация Railway
webapp/             — WebApp страницы на GitHub Pages
  index.html        — Мои страны
  russia.html       — Путешествия по России
  checklist.html    — Чеклист для путешествия
  currency.html     — Конвертер валют
  timezone.html     — Разница во времени
  splitwise.html    — Общий счёт
  stats.html        — Моя статистика
  attractions.html  — Мои достопримечательности
  diary.html        — Дневник путешественника
  map.html          — Карта мира
  landing.html      — Лендинг страница
```

---

## Переменные окружения Railway

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен бота от BotFather |
| `DATABASE_URL` | PostgreSQL (не используется, используем SQLite) |
| `PEXELS_API_KEY` | Ключ Pexels (не используется) |

---

## Администратор

- `ADMIN_ID = 462171750`
- Команды только для админа: `/stats`, `/testpost`

---

## Каналы

| | ID | Описание |
|---|---|---|
| Основной канал | `-1002079377291` | [@like_a_local](https://t.me/like_a_local) |
| Тестовый канал | `-1003580791059` | [@likealocaltest](https://t.me/likealocaltest) |

> Автопостинг временно отключён.

---

## Структура главного меню

| Папка | Разделы |
|---|---|
| 🧭 Планирование | Подобрать страну, Страна по судьбе, Сезоны, Визы, Несовместимые страны, Чеклист |
| 🛠 Инструменты | Переводчик, Конвертер валют, Разница во времени, Общий счёт, Карта мира |
| 🗺 Мои путешествия | Мои страны, Рейтинг путешественников, Путешествия по России, Мои достопримечательности, Моя статистика, Дневник путешественника |
| 📚 Знания | Инструкция для новичка, Дроны, Лаунджи, Круизы, Фильмы, Чудеса и наследие |
| 🎮 Игры | Викторина, Угадай где я, Найди пару, Страна дня |
| ✈️ Услуги | Путеводители, Авторские туры, Оформить визу |
| ⭐ Премиум | _(в разработке)_ |
| 🛒 Магазин | |
| 🤝 Партнёры | Skyeng |
| 🆘 Поддержка | |
| 📢 Наш канал | |

---

## Монетизация (в разработке)

- **Freemium модель**
- Премиум: 149 ₽/месяц, 1 490 ₽/год
- Путеводители: от 149 ₽ за страну
- Партнёрские программы

---

## Рабочий процесс обновления бота

1. Написать задание Claude Code
2. Claude Code вносит изменения и создаёт PR
3. Зайти на [github.com/andreev032/Travel-Bot/pulls](https://github.com/andreev032/Travel-Bot/pulls)
4. Смержить PR
5. Railway автоматически деплоит за ~2 минуты

---

## Важные заметки

- Токен бота хранится прямо в `bot.py` — небезопасно, исправить при росте проекта
- SQLite файл: `/app/data/users.db` на Railway Volume
- WebApp страницы: <https://andreev032.github.io/Travel-Bot/>
- Автопостинг отключён — нужно доработать фото и тексты постов

---

## Константы

| Константа | Значение | Описание |
|---|---|---|
| `TOKEN` | `8701321387:AAHwb_W…` | Токен бота (BotFather) |
| `ADMIN_ID` | `462171750` | Telegram user_id администратора |
| `CHANNEL_ID` | `-1003580791059` | Сейчас тестовый канал (временно) |
| `TEST_CHANNEL_ID` | `-1003580791059` | Тестовый канал для `/testpost` |
| `MOSCOW_TZ` | `Europe/Moscow` | Временная зона для всех дат |
| `_SQLITE_PATH` | `/app/data/users.db` | Путь к SQLite базе |

> **Примечание:** `CHANNEL_ID` временно указывает на тестовый канал. Для продакшна вернуть `-1002079377291` (`@like_a_local`).

---

## Схема базы данных

БД автоматически инициализируется при старте. Поддерживаются три бэкенда в порядке приоритета: **PostgreSQL → SQLite → JSON**.

### Таблица `users`
Регистрирует каждого пользователя при первом `/start` и обновляет `last_seen` при каждом обращении.

| Колонка | Тип | Описание |
|---|---|---|
| `user_id` | `BIGINT / INTEGER` | PRIMARY KEY — Telegram user_id |
| `username` | `TEXT` | @username (может быть NULL) |
| `first_name` | `TEXT` | Имя пользователя |
| `first_seen` | `TIMESTAMP / TEXT` | Дата первого запуска |
| `last_seen` | `TIMESTAMP / TEXT` | Дата последней активности |

### Таблица `user_countries`
Хранит количество отмеченных стран (из WebApp `index.html`).

| Колонка | Тип | Описание |
|---|---|---|
| `user_id` | `BIGINT / INTEGER` | PRIMARY KEY — Telegram user_id |
| `username` | `TEXT` | @username |
| `first_name` | `TEXT` | Имя пользователя |
| `countries_count` | `INTEGER` | Количество посещённых стран |
| `updated_at` | `TIMESTAMP / TEXT` | Дата последнего обновления |

### Таблица `user_flags`
Коллекция флагов игры «🌍 Страна дня». Один флаг на страну на пользователя.

| Колонка | Тип | Описание |
|---|---|---|
| `user_id` | `BIGINT / INTEGER` | Telegram user_id |
| `country_code` | `TEXT` | Двухбуквенный код страны (ISO 3166-1) |
| `collected_date` | `TEXT` | Дата сбора (`YYYY-MM-DD`) |
| — | — | PRIMARY KEY: `(user_id, country_code)` |

---

## Архитектура ConversationHandler

Бот построен на одном `ConversationHandler` с 38 целочисленными состояниями (`range(38)`).

```python
MAIN_MENU, ANSWERING, HELP_MENU, HELP_TOPIC, TRANSLATING,
VISA_MENU, VISA_CATEGORY,
MOVIES_MENU, MOVIES_REGION, MOVIES_LIST,
INCOMPATIBLE_MENU, INCOMPATIBLE_TOPIC,
DRONE_MENU, DRONE_SECTION,
SEASON_MENU, SEASON_REGION,
LOUNGE_MENU, LOUNGE_SECTION,
SUPPORT_MENU, SUPPORT_TYPING,
CRUISE_MENU, CRUISE_SECTION,
WONDERS_MENU, WONDERS_SEVEN_MENU, WONDERS_SECTION, UNESCO_MENU, UNESCO_REGION,
PARTNERS_MENU,
TOURS_MENU, TOURS_TYPING,
DESTINY_TYPING,
QUIZ_ACTIVE,
GAMES_MENU, GUESS_ACTIVE, PAIR_ACTIVE,
COUNTRY_OF_DAY,
SHOP_MENU, SHOP_TYPING  = range(38)
```

### Описание состояний

| Состояние | # | Описание |
|---|---|---|
| `MAIN_MENU` | 0 | Главное меню — точка входа |
| `ANSWERING` | 1 | Квиз «🌍 Подобрать страну» (пошаговые вопросы) |
| `HELP_MENU` | 2 | Меню «📖 Инструкция для новичка» |
| `HELP_TOPIC` | 3 | Просмотр темы инструкции |
| `TRANSLATING` | 4 | Переводчик (ввод текста) |
| `VISA_MENU` | 5 | Меню раздела «🛂 Визы» |
| `VISA_CATEGORY` | 6 | Просмотр визовой категории страны |
| `MOVIES_MENU` | 7 | Меню «🎬 Фильмы о путешествиях» |
| `MOVIES_REGION` | 8 | Выбор региона фильмов / локаций |
| `MOVIES_LIST` | 9 | Список фильмов по региону |
| `INCOMPATIBLE_MENU` | 10 | Меню «⛔ Несовместимые страны» |
| `INCOMPATIBLE_TOPIC` | 11 | Просмотр несовместимости |
| `DRONE_MENU` | 12 | Меню «🚁 Дроны» |
| `DRONE_SECTION` | 13 | Правила дронов по стране |
| `SEASON_MENU` | 14 | Меню «🌤 Сезоны» |
| `SEASON_REGION` | 15 | Выбор страны/региона сезонов |
| `LOUNGE_MENU` | 16 | Меню «🛋 Лаунджи» |
| `LOUNGE_SECTION` | 17 | Информация о лаундже |
| `SUPPORT_MENU` | 18 | Меню «🆘 Поддержка» |
| `SUPPORT_TYPING` | 19 | Ввод сообщения в поддержку |
| `CRUISE_MENU` | 20 | Меню «🚢 Круизы» |
| `CRUISE_SECTION` | 21 | Раздел круизов |
| `WONDERS_MENU` | 22 | Меню «🏛 Чудеса и наследие» |
| `WONDERS_SEVEN_MENU` | 23 | Семь чудес света |
| `WONDERS_SECTION` | 24 | Конкретное чудо/наследие |
| `UNESCO_MENU` | 25 | Меню ЮНЕСКО |
| `UNESCO_REGION` | 26 | Объекты ЮНЕСКО по региону |
| `PARTNERS_MENU` | 27 | Меню «🤝 Партнёры» |
| `TOURS_MENU` | 28 | Меню «✈️ Авторские туры» |
| `TOURS_TYPING` | 29 | Заявка на тур (ввод текста) |
| `DESTINY_TYPING` | 30 | «🔮 Страна по судьбе» (нумерология) |
| `QUIZ_ACTIVE` | 31 | Игра «🧠 Викторина о путешествиях» |
| `GAMES_MENU` | 32 | Подменю «🎮 Игры» |
| `GUESS_ACTIVE` | 33 | Игра «🎯 Угадай где я?» |
| `PAIR_ACTIVE` | 34 | Игра «🤝 Найди пару» |
| `COUNTRY_OF_DAY` | 35 | Игра «🌍 Страна дня» |
| `SHOP_MENU` | 36 | Меню «🛒 Магазин» |
| `SHOP_TYPING` | 37 | Ввод запроса в магазине |

### Навигация
- Кнопка `🏠 Главное меню` (HOME_BTN) — зарегистрирована как отдельный хэндлер (`home`) в каждом состоянии, вызывает `go_home()` и сбрасывает `context.user_data`
- Кнопка `◀️ Назад` — обрабатывается внутри каждого state-хэндлера, возвращает в родительское меню
- `fallbacks`: `/start`, `/menu`, `/cancel`, HOME_BTN

---

## Соглашения по именованию веток

| Префикс | Назначение | Пример |
|---|---|---|
| `feature/` | Новая функциональность | `feature/country-of-day` |
| `fix/` | Исправление бага | `fix/destination-filter` |
| `docs/` | Только документация | `docs/instructions` |
| `refactor/` | Рефакторинг без новой функции | `refactor/db-helpers` |

**Правила:**
- Ветка всегда создаётся от актуального `origin/main` (`git checkout -b ... origin/main`)
- Перед созданием ветки — `git fetch origin main`
- Одна ветка = одна задача = один PR
- После мержа PR Railway деплоит автоматически (~2 мин)
