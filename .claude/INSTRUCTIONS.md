# Как местный — Travel Bot · INSTRUCTIONS для Claude Code

## ПРАВИЛА РАБОТЫ — ОБЯЗАТЕЛЬНО

1. **ВСЕГДА создавать НОВУЮ ветку для каждого задания — никогда не использовать существующую**
2. **ВСЕГДА создавать PR и присылать ссылку на мерж**
3. **НИКОГДА не писать "используй существующую ветку feature/xxx"**
4. **Одно задание = одна новая ветка = один PR = одна ссылка на мерж**
5. **Перед каждой задачей: `git fetch origin && git checkout -b <ветка> origin/main`**

## Стек и хостинг
- Python 3.13, python-telegram-bot 20.3
- PostgreSQL на Railway (DATABASE_URL через Railway Reference — не SQLite!)
- GitHub Pages: https://andreev032.github.io/Travel-Bot/
- Репозиторий: github.com/andreev032/Travel-Bot
- Деплой: GitHub Actions → Railway (~2 мин после мержа PR)

## Переменные окружения Railway (НИКОГДА не хардкодить!)
| Переменная | Значение |
|---|---|
| BOT_TOKEN | Токен бота от BotFather |
| ADMIN_ID | 462171750 |
| CHANNEL_ID | -1002079377291 (основной @like_a_local) |
| TEST_CHANNEL_ID | -1003580791059 (@likealocaltest) |
| DATABASE_URL | `${{Postgres.DATABASE_URL}}` — Railway Reference |

## Структура файлов
```
bot.py              — основной файл бота
posts.py            — посты для автопостинга
requirements.txt
Procfile
nixpacks.toml
.claude/
  INSTRUCTIONS.md   — этот файл
webapp/
  index.html        — Мои страны (201 страна)
  distance.html     — Калькулятор расстояний (Nominatim)
  russia.html       — Путешествия по России
  checklist.html    — Чеклист
  currency.html     — Конвертер валют
  timezone.html     — Разница во времени
  splitwise.html    — Общий счёт
  stats.html        — Моя статистика
  attractions.html  — Достопримечательности
  diary.html        — Дневник путешественника
  map.html          — Карта мира
  landing.html      — Лендинг
  oferta.html       — Публичная оферта
```

## База данных (PostgreSQL)
Плейсхолдеры: `%s` (не `?`). `SERIAL PRIMARY KEY` (не AUTOINCREMENT).

**Таблицы:**
- **users** — user_id (PK), username, first_name, first_seen, last_seen
- **user_countries** — user_id (PK), username, first_name, countries_count, updated_at
- **user_flags** — user_id + country_code (PK), collected_date
- **post_queue** — id, source_text, source_media, status (pending/approved/published/rejected), scheduled_time, created_at, published_at
- **subscriptions** — user_id (PK), plan (monthly/yearly/lifetime), expires_at, trial_ends_at, is_active, ref_code, referred_by, tokens
- **referrals** — id, referrer_id, referred_id, created_at, paid_at, tokens_awarded

## Страны
- Всего в боте: **201** (195 ООН + 6 территорий: HK, Macau, Taiwan, Kosovo, Приднестровье, Зап.Сахара)
- Викторина и ответы на вопросы о кол-ве стран: **195** (стандарт ООН)

## Структура меню
| Папка | Разделы |
|---|---|
| 🧭 Планирование | Подобрать страну, Страна по судьбе, Сезоны, Погода, Визы, Несовместимые страны, Чеклист, Куда слетать |
| 🛠 Инструменты | Переводчик, Конвертер валют, Разница во времени, Общий счёт, Карта мира |
| 🗺 Мои путешествия | Мои страны, Рейтинг путешественников, Путешествия по России, Достопримечательности, Статистика, Дневник, Калькулятор расстояний |
| 📚 Знания | Инструкция, Дроны, Лаунджи, Круизы, Фильмы, Чудеса и наследие |
| 🎮 Игры | Викторина, Угадай где я, Найди пару, Страна дня |
| ✈️ Услуги | Путеводители, Авторские туры, Оформить визу |
| 🤝 Партнёры | (см. партнёрки ниже) |
| ⭐ Премиум | Карточка с ценами + заглушка оплаты |
| 🛒 Магазин | Заглушка (в разработке) |
| 🆘 Поддержка | Написать нам, Сообщить об ошибке, Предложить идею |
| 📢 Наш канал | Ссылка на @like_a_local |

## Монетизация — Freemium
**Бесплатно навсегда:** Мои страны, все Инструменты, Планирование, Знания, Викторина, Страна дня

**Платно (200₽/мес или 1490₽/год, после 7 дней триала):**
- Дневник путешественника
- Моя статистика
- Мои достопримечательности
- Рейтинг путешественников
- Угадай где я
- Найди пару

**Путеводители:** 149₽/страна (через Ozon Pay — разовые платежи)

**Оплата:**
- ЮKassa — рекуррентные подписки (договор подписан, на проверке). Комиссия карты 2.8%+НДС (~3.4%), СБП 0.7%. Чеки подключены.
- Ozon Pay — разовые (путеводители, туры), заявка на рассмотрении
- Оферта: https://andreev032.github.io/Travel-Bot/oferta.html

## Реферальная система и токены
**Начисление токенов:**
- Друг оплатил месяц → +1 токен
- Друг оплатил год → +3 токена

**Бонусные токены (не за рефералов):**
- Отзыв в сторах → +1
- Отметил в Instagram → +1
- 30 дней подряд в боте → +1
- Отметил 50 стран → +1
- 50 вопросов викторины → +1

**Пороги доступа:**
- 7 токенов → 1 месяц бесплатно
- 15 токенов → 3 месяца
- 25 токенов → 6 месяцев
- 45 токенов → 1 год
- 90 токенов → вечный доступ 👑

**Уровни:**
- 0–6 → Новичок
- 7–14 → Путешественник
- 15–44 → Исследователь
- 45–89 → Амбассадор
- 90+ → Легенда

**Реф.ссылки:** `t.me/like_a_local_bot?start=REF_xxxxxxxx`
- Префикс `REF_` — обычные рефералы
- Префикс `BLOG_` — блогеры (бартер, вечный доступ через /giveaccess)
- `ADMIN_ID` всегда видит экран неподписанного (`is_premium=False`)

## Автопостинг
Запущен май 2026. Посты из @likealocaltest → одобрение в личку ADMIN_ID → публикация в @like_a_local в 9:00 и 19:00 МСК.
- Таблица: `post_queue`
- `clean_post_text` убирает рекламу (Удивительный Мир, Красивые места, Вокруг света, МАХ) и хештеги
- Подпись: `🎒 Как местный | Подписаться` (ссылка на канал)
- Предупреждение когда ≤10 постов в очереди
- Команда `/queue` — статистика очереди (только ADMIN_ID)

## Партнёрки (Travelpayouts ID 725703)
| Партнёр | Ссылка |
|---|---|
| Aviasales | https://aviasales.tpo.mx/JwJuaOjB |
| Cherehapa | https://cherehapa.tpo.mx/mleWBEwZ |
| Tripster | https://tripster.tpo.mx/2Bviy2vb |
| Островок | https://ostrovok.tpo.mx/AWzXT1nl |
| Яндекс.Путешествия | https://yandex.tpo.mx/O3BDe6cM |
| Kruiz.online | https://kruiz-online.tpo.mx/8LRnfhfo |
| Kiwitaxi | https://kiwitaxi.tpo.mx/tn9FuGlz |
| AirHelp | https://airhelp.tpo.mx/OreO2QvC |
| Airalo | https://airalo.tpo.mx/YtTdNPSd |
| Yesim | https://yesim.tpo.mx/PvssJqb7 |
| Tutu.ru | https://tutu.tpo.mx/TUhUyfAE |
| WeGoTrip | https://wegotrip.tpo.mx/sEJE5fXc |
| Sutochno.ru | https://sutochno.tpo.mx/E9oytnQF |
| MobiMatter | https://mobimatter.com?referrerId=AK09022081 |
| Skyeng | (из кода) |

На одобрении: Localrent, GetYourGuide, DiscoverCars

## Промо
- ChatPlace.io — Instagram бот (Pro 2000₽/мес)
- Схема: рилс → кодовое слово → проверка подписки → реф.ссылка → 7 дней триала
- Команда `/giveaccess` — вечный доступ блогерам по бартеру (только ADMIN_ID)

## Каналы
| Канал | ID |
|---|---|
| @like_a_local (основной) | -1002079377291 |
| @likealocaltest (тестовый) | -1003580791059 |

## ConversationHandler — 38 состояний
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
SHOP_MENU, SHOP_TYPING = range(38)
```

Навигация: `HOME_BTN` → `go_home()` + сброс `context.user_data`.
Fallbacks: `/start`, `/menu`, `/cancel`, `HOME_BTN`.

## Кнопки навигации — стандарт
Каждый раздел заканчивается строкой с двумя кнопками:
```python
[KeyboardButton("◀️ Назад"), KeyboardButton("🏠 Главное меню")]
```
Никогда не использовать точку `"."` или `"Навигация"` как текст сообщения.

## Соглашения по веткам
| Префикс | Назначение |
|---|---|
| feature/ | Новая функциональность |
| fix/ | Исправление бага |
| docs/ | Только документация |
| refactor/ | Рефакторинг |

## Правила Claude Code — обязательно
1. Читать этот файл перед любой задачей
2. Никогда не хардкодить токены и ID — только `os.environ['...']`
3. Не трогать логику и тексты если задача только про UI или навигацию
4. Создавать PR, не пушить в main напрямую
5. Название PR — на английском, описательное
6. Плейсхолдер PostgreSQL — `%s`, не `?`
