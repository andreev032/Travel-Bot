# Как местный — Travel Bot · INSTRUCTIONS для Claude Code
## ⛔️ СТОП — ПРОЧИТАЙ ПЕРЕД ЛЮБЫМ ДЕЙСТВИЕМ

**Каждая задача начинается СТРОГО так:**

```
cat .claude/INSTRUCTIONS.md
```
Без этого — не делать ничего.

**ДЕЛАЙ ТОЛЬКО ТО ЧТО НАПИСАНО В ЗАДАЧЕ. НИЧЕГО ЛИШНЕГО.**
- Не рефакторить код рядом
- Не переименовывать переменные
- Не "улучшать" то, что не просили
- Не трогать файлы, которые не указаны в задаче
- Не менять логику, тексты, структуру если это не указано явно
- Одна задача = один файл (максимум два если явно указано)
- Нашёл что-то подозрительное рядом — ИГНОРИРУЙ, не трогай

**Если что-то непонятно — остановись и спроси. Не угадывай.**

## ПРАВИЛА РАБОТЫ — ОБЯЗАТЕЛЬНО
1. Читать этот файл перед любой задачей (`cat .claude/INSTRUCTIONS.md`)
2. ВСЕГДА создавать НОВУЮ ветку: `git fetch origin && git checkout -b <ветка> origin/main`
3. ВСЕГДА создавать PR и присылать ссылку
4. НИКОГДА не хардкодить токены и ID — только `os.environ['...']`
5. Не трогать логику и тексты если задача только про UI/навигацию
6. Плейсхолдер PostgreSQL — `%s`, не `?`
7. Название PR — на английском
## Стек и хостинг
- Python 3.13, python-telegram-bot 20.3, Flask (webhook)
- PostgreSQL на Railway — `DATABASE_URL` через `${{Postgres.DATABASE_URL}}`
- GitHub Pages: https://andreev032.github.io/Travel-Bot/
- Репозиторий: github.com/andreev032/Travel-Bot
- Деплой: GitHub Actions → Railway (~2 мин после мержа PR)
- Procfile: `web: python bot.py`
## Railway Variables (НИКОГДА не хардкодить!)
| Переменная | Значение |
|---|---|
| BOT_TOKEN | Токен бота |
| ADMIN_ID | 462171750 |
| CHANNEL_ID | -1002079377291 (@like_a_local) |
| TEST_CHANNEL_ID | -1003580791059 (@likealocaltest) |
| DATABASE_URL | `${{Postgres.DATABASE_URL}}` |
| YOOKASSA_SHOP_ID | 1350203 |
| YOOKASSA_SECRET_KEY | в Railway |
| YOOKASSA_WEBHOOK_SECRET | не выставлен — выставить после получения от ЮKassa |
## База данных (PostgreSQL)
Одна основная таблица `users`:
```sql
user_id BIGINT PRIMARY KEY
username TEXT
first_name TEXT
first_seen TIMESTAMP DEFAULT NOW()
last_seen TIMESTAMP DEFAULT NOW()
is_premium BOOLEAN DEFAULT FALSE
premium_expires_at TIMESTAMP
premium_trial_used BOOLEAN DEFAULT FALSE
subscription_type TEXT  -- 'trial','month','year','lifetime'
yookassa_payment_method_id TEXT
referral_code TEXT UNIQUE  -- собственный REF_ код
ref_code TEXT UNIQUE       -- старое поле (синоним referral_code)
ref_count INTEGER DEFAULT 0
referred_by BIGINT         -- user_id того кто пригласил
tokens INTEGER DEFAULT 0
registered_at TIMESTAMP DEFAULT NOW()
last_active_at TIMESTAMP
language_code TEXT
promo_source TEXT
```
Остальные таблицы: `user_countries`, `user_flags`, `post_queue`, `promo_codes`
## Премиум логика
- 7 дней триал при первом `/start` (в `record_user()`, только если `premium_trial_used=FALSE`)
- `is_premium()` — читает БД, сбрасывает флаг если дата истекла
- Продление: `GREATEST(NOW(), COALESCE(premium_expires_at, NOW())) + interval` — дни складываются
- Кнопка "Продлить подписку" видна всегда (и при активном премиуме)
- После оплаты — сообщение с датой и тарифом
**Платные функции (блокировать через `is_premium()`):**
Дневник путешественника, Моя статистика, Мои достопримечательности, Рейтинг путешественников, Угадай где я, Найди пару
**Бесплатно навсегда:**
Мои страны, все Инструменты, Планирование, Знания, Викторина, Страна дня
## ЮKassa
- ShopID 1350203, договор НЭК.436172.01
- Webhook URL: `https://travel-bot-production-9c46.up.railway.app/yookassa/webhook`
- Flask на PORT=8080 в daemon thread
- Подпись webhook через `YOOKASSA_WEBHOOK_SECRET` (hmac.compare_digest)
- IP-фильтр отключён (Railway proxy)
- Рекуррент на одобрении — карты пока не сохраняются
- СБП не подключён — ждём ЮKassa
- Цены: 200₽/мес, 1490₽/год
## Реферальная система
- `REF_` префикс — обычные рефералы
- `BLOG_` префикс — блогеры (вечный доступ через `/giveaccess`)
- `referred_by` хранит BIGINT (user_id реферера, не текстовый код)
- Токены: +1 за месяц друга, +3 за год друга
- Пороги: 7→1мес, 15→3мес, 25→6мес, 45→1год, 90→вечный
## Структура меню
| Папка | Разделы |
|---|---|
| 🧭 Планирование | Подобрать страну, Страна по судьбе, Сезоны, Погода, Визы, Несовместимые страны, Чеклист, Куда слетать |
| 🛠 Инструменты | Переводчик, Конвертер валют, Разница во времени, Общий счёт, Карта мира |
| 🗺 Мои путешествия | Мои страны, Рейтинг, Путешествия по России, Достопримечательности, Статистика, Дневник, Калькулятор расстояний |
| 📚 Знания | Инструкция, Дроны, Лаунджи, Круизы, Фильмы, Чудеса и наследие |
| 🎮 Игры | Викторина, Угадай где я, Найди пару, Страна дня |
| ✈️ Услуги | Путеводители, Авторские туры, Оформить визу |
| 🤝 Партнёры | Aviasales, Отели, Страховка, eSIM, Tripster, Круизы, Kiwitaxi, AirHelp |
| ⭐ Премиум | Реальная оплата ЮKassa |
| 🛒 Магазин | Заглушка |
| 🆘 Поддержка | Написать нам, Ошибка, Идея |
| 📢 Наш канал | @like_a_local |
## Страны
- В боте: **201** (195 ООН + HK, Macau, Taiwan, Kosovo, Приднестровье, Зап.Сахара)
- Викторина/ответы: **195** (стандарт ООН)
## Автопостинг
- @likealocaltest → одобрение ADMIN_ID → @like_a_local в 9:00 и 19:00 МСК
- Таблица `post_queue`, команда `/queue`
- `clean_post_text` убирает рекламу и хештеги
- Подпись: `🎒 Как местный | Подписаться`
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
## Навигация — стандарт — ОБЯЗАТЕЛЬНО ДЛЯ КАЖДОГО ЭКРАНА
- КАЖДЫЙ обработчик который показывает контент (текст + кнопка) ОБЯЗАН заканчиваться reply keyboard
- НЕЛЬЗЯ отправлять сообщение только с InlineKeyboardMarkup без reply keyboard после него
- Шаблон обязателен:

```python
# Сначала сообщение с контентом и inline-кнопкой
await update.message.reply_text("текст", reply_markup=InlineKeyboardMarkup([...]))
# Затем ОБЯЗАТЕЛЬНО reply keyboard
await update.message.reply_text("Выбери раздел 👇", reply_markup=get_main_keyboard())
return MAIN_MENU
```

* Исключений нет. Любой новый экран = этот шаблон.

## Inline-кнопки + reply keyboard — шаблон
Когда экран имеет inline-кнопку (ссылка, switch_inline_query) — Telegram не позволяет совместить её с reply keyboard в одном сообщении. Обязательный шаблон:

```python
# 1. Сообщение с контентом + inline-кнопка
await update.message.reply_text("текст", reply_markup=InlineKeyboardMarkup([[...]]))
# 2. Отдельное сообщение с reply keyboard — ОБЯЗАТЕЛЬНО
await update.message.reply_text("Выбери раздел 👇", reply_markup=ReplyKeyboardMarkup([["◀️ Назад", "🏠 Главное меню"]], resize_keyboard=True))
return MAIN_MENU
```

НЕЛЬЗЯ отправлять только inline без reply keyboard после него — меню пропадёт. НЕЛЬЗЯ использовать пустой текст " " — Telegram вернёт 400 Bad Request.

## WebApp — правила
- Все WebApp хранят данные ТОЛЬКО в localStorage — никакого fetch/API
- Новый WebApp = копировать `webapp/countries/index.html` как базу, менять только данные
- Ключи: `countries_visited`, `attractions_visited`, `waters_visited`
- bot.py не трогать при изменении WebApp файлов

## Соглашения по веткам
`feature/` новая функция · `fix/` баг · `docs/` документация · `refactor/` рефакторинг
