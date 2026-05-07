# Как местный — Travel Bot · INSTRUCTIONS для Claude Code

## Стек и хостинг
- Python 3.13, python-telegram-bot 20.3
- PostgreSQL на Railway (миграция с SQLite завершена май 2026)
- GitHub Pages: https://andreev032.github.io/Travel-Bot/
- Репозиторий: github.com/andreev032/Travel-Bot
- Деплой: GitHub Actions → Railway (~2 мин после мержа PR)

## Переменные окружения Railway (никогда не хардкодить!)
| Переменная | Описание |
|---|---|
| BOT_TOKEN | Токен бота от BotFather |
| ADMIN_ID | 462171750 |
| CHANNEL_ID | -1002079377291 (основной @like_a_local) |
| TEST_CHANNEL_ID | -1003580791059 (@likealocaltest) |
| DATABASE_URL | ${{Postgres.DATABASE_URL}} — Railway Reference |

## Структура файлов
```
bot.py              — основной файл бота
posts.py            — посты для автопостинга (отключён)
requirements.txt
Procfile
nixpacks.toml
webapp/
  index.html        — Мои страны
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
  landing.html      — Лендинг (Ozon Pay)
```

## База данных (PostgreSQL)
Инициализируется при старте. Плейсхолдеры: `%s` (не `?`). `SERIAL PRIMARY KEY` (не AUTOINCREMENT).

**users** — user_id (PK), username, first_name, first_seen, last_seen  
**user_countries** — user_id (PK), username, first_name, countries_count, updated_at  
**user_flags** — user_id + country_code (PK), collected_date  

## Страны
- Всего в боте: **201** (195 ООН + 6 территорий: HK, Macau, Taiwan, Kosovo, Приднестровье, Зап.Сахара)
- Викторина отвечает **195** (стандарт ООН)

## Структура меню
| Папка | Разделы |
|---|---|
| 🧭 Планирование | Подобрать страну, Страна по судьбе, Сезоны, Визы, Несовместимые страны, Чеклист, Куда слетать (5 подразделов), Погода |
| 🛠 Инструменты | Переводчик, Конвертер валют, Разница во времени, Общий счёт, Карта мира |
| 🗺 Мои путешествия | Мои страны, Рейтинг, Путешествия по России, Достопримечательности, Статистика, Дневник, Калькулятор расстояний |
| 📚 Знания | Инструкция, Дроны, Лаунджи, Круизы, Фильмы, Чудеса и наследие |
| 🎮 Игры | Викторина, Угадай где я, Найди пару, Страна дня |
| ✈️ Услуги | Путеводители, Авторские туры, Оформить визу |
| 🤝 Партнёры | Skyeng, MobiMatter eSIM |
| ⭐ Премиум | В разработке (200₽/мес, 1490₽/год) |
| 🛒 Магазин | — |
| 🆘 Поддержка | — |
| 📢 Наш канал | — |

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

Навигация: `HOME_BTN` → `go_home()` + сброс `context.user_data`. fallbacks: `/start`, `/menu`, `/cancel`, `HOME_BTN`.

## Партнёрки (Travelpayouts, ID 725703)
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
| Skyeng | (текущая ссылка из кода) |

## Оплата
- **ЮKassa** — основной провайдер (подписки), договор подписан, на проверке
- **Ozon Pay** — разовые платежи, на рассмотрении
- Подписка: **200₽/мес**, **1490₽/год**
- Путеводители: **149₽/страна**

**Оферта:** https://andreev032.github.io/Travel-Bot/oferta.html

## Премиум (для неподписанных)
- Показывает карточку товара с ценами
- Кнопка «Подключить Премиум» (заглушка)
- Ссылка на оферту
- `ADMIN_ID` видит экран неподписанного (`is_premium` возвращает `False` для админа)

## Соглашения по веткам
| Префикс | Назначение |
|---|---|
| feature/ | Новая функциональность |
| fix/ | Исправление бага |
| docs/ | Только документация |
| refactor/ | Рефакторинг |

**Правила:** всегда от `origin/main` → `git fetch origin` перед созданием. Одна ветка = одна задача = один PR.

## Правила Claude Code
1. Читать этот файл перед любой задачей
2. Никогда не хардкодить токены и ID — только `os.environ['...']`
3. Не трогать логику и тексты если задача только про DB или UI
4. Создавать PR, не пушить в main напрямую
5. Название PR — на английском, описательное
