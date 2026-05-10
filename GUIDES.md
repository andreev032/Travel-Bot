# Как местный — GUIDES.md · Стандарт путеводителей v2

> Этот файл читается Claude Code ПЕРЕД любой задачей связанной с путеводителями.
> Версия 2.0 · Май 2026 · Обновлён после тестирования дизайна.

## ФИЛОСОФИЯ ПРОДУКТА

Путеводители «Как местный» — живой продукт с личностью и опытом.
Основатель Кирилл Андреев стоит за каждым путеводителем. Люди идут на людей.

Наша ниша: красивый + белый дизайн + кликабельные карты + живые фото + личная история основателя + в Telegram.

## ДИЗАЙН — ГЛАВНЫЕ ПРАВИЛА

РЕФЕРЕНСЫ — изучить перед любой задачей:
- https://www.aviasales.ru/psgr/article/zolotye-gory
- https://youtravel.me/tours/region/dagestan
- https://kudakuda.ru

ЦВЕТА:
--bg:           #ffffff   (белый фон — ВСЕГДА)
--text:         #111111
--muted:        #6f6f6f
--line:         #e8e8e8
--accent:       #C4622D   (терракотовый)
--accent-dark:  #a24e22
--accent-bg:    #fff7f1
--shadow:       0 10px 30px rgba(17,17,17,.06)
--shadow-hover: 0 20px 40px rgba(17,17,17,.10)

ШРИФТЫ:
Заголовки: Unbounded (700, 900)
Тело: Manrope (400, 500, 600, 700)

<link href="https://fonts.googleapis.com/css2?family=Unbounded:wght@700;900&family=Manrope:wght@400;500;600;700&display=swap" rel="stylesheet">

ТИПОГРАФИКА:
h1 hero:    Unbounded 900, clamp(54px,11vw,112px), letter-spacing:-0.06em
h2 секции: Unbounded 900, clamp(32px,4vw,56px), letter-spacing:-0.05em
h3:         Manrope 800, 20-24px
p:          Manrope 400-500, 16-18px, line-height:1.6
eyebrow:    Manrope 800, 13px, letter-spacing:0.12em, uppercase, color:accent

ЗАПРЕЩЕНО НАВСЕГДА:
- Тёмный фон (#080808, #111, #1a1a1a) на основном фоне страницы
- Шрифты Inter, Roboto, Arial, Cormorant Garamond как основные
- Коричневые/тёмные блоки тура
- Градиентные цветные карточки вместо фото
- Эмодзи вместо реальных фото в карточках мест

## КАРТОЧКИ МЕСТ

.place-card {
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 16px;
  overflow: hidden;
  transition: transform .3s ease, box-shadow .3s ease;
}
.place-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 20px 40px rgba(17,17,17,.10);
}
.place-image { width:100%; height:200px; object-fit:cover; background:#ededed; }
.place-card:first-child { grid-column: span 2; }
.place-card:first-child .place-image { height: 280px; }

СЕТКА:
.places-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0,1fr));
  gap: 22px;
}
@media (max-width:760px) {
  .places-grid { grid-template-columns: 1fr; }
  .place-card:first-child { grid-column: span 1; }
}

КНОПКА КАРТЫ:
.map-link {
  background: #fff7f1; color: #C4622D;
  border: 1px solid #f1d8ca; border-radius: 999px;
  padding: 10px 14px; font-size: 14px; font-weight: 700;
}
.map-link:hover { background: #C4622D; color: #fff; }

## НАВИГАЦИЯ

.topbar {
  position: sticky; top: 0; z-index: 1000;
  background: rgba(255,255,255,.92);
  backdrop-filter: saturate(180%) blur(14px);
  border-bottom: 1px solid rgba(17,17,17,.06);
}
.topbar-inner {
  display: flex; align-items: center; justify-content: space-between;
  min-height: 72px; max-width: 1180px; margin: 0 auto; padding: 0 24px;
}
.brand { font-family: Unbounded, sans-serif; font-size: 16px; font-weight: 700; }

Ссылки: О регионе / Места / Кухня / Советы / Тур
Кнопка: Открыть бота → https://t.me/like_a_local_bot

## HERO

background: linear-gradient(rgba(0,0,0,.26), rgba(0,0,0,.56)), [ФОТО] center/cover;
.hero::after { height:28vh; background:linear-gradient(to top,rgba(0,0,0,.40),transparent); }
.hero-content { color:#fff; padding:120px 24px 72px; max-width:980px; margin:0 auto; }
.hero-kicker {
  padding:10px 14px; border-radius:999px;
  background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.18);
  backdrop-filter:blur(10px); font-size:14px; font-weight:600;
}

## БЛОК ТУРА

.tour {
  background: linear-gradient(180deg, #fffaf7 0%, #fff 100%);
  border: 1px solid #efd9cd; border-radius: 28px; padding: 34px;
  display: grid; grid-template-columns: 1.25fr .75fr; gap: 24px;
}
@media (max-width:980px) { .tour { grid-template-columns: 1fr; } }

Содержимое:
- Eyebrow: ТУР С КИРИЛЛОМ
- Заголовок: Хочешь вместе изучить [РЕГИОН]?
- Текст Кирилла (личная история)
- Теги маршрута (pills)
- Цена с дисклеймером про проверку спроса
- ОДНА кнопка: Хочу в тур → https://t.me/like_a_local_bot
- Боковая карточка с мини-фактами

Для Дагестана:
- Цена: ~70 000 ₽
- Маршрут: 5 дней · Сулакский каньон · Гамсутль · Дербент · горные сёла · форель на углях
- Сезоны: лето или осень 2026

## КУХНЯ

.food-row {
  display: grid; grid-auto-flow: column;
  grid-auto-columns: minmax(250px, 280px);
  gap: 18px; overflow-x: auto; scroll-snap-type: x proximity;
}
.food-card { border:1px solid #e8e8e8; border-radius:16px; overflow:hidden; }
.food-image { width:100%; height:200px; object-fit:cover; background:#ececec; }

## АНИМАЦИИ

.reveal { opacity:0; transform:translateY(24px); transition:opacity .7s ease,transform .7s ease; }
.reveal.is-visible { opacity:1; transform:translateY(0); }

const obs = new IntersectionObserver(entries => {
  entries.forEach(e => { if(e.isIntersecting){ e.target.classList.add('is-visible'); obs.unobserve(e.target); } });
}, {threshold:0.14, rootMargin:'0px 0px -40px 0px'});
document.querySelectorAll('.reveal').forEach(el => obs.observe(el));

## ФОТО

Источники:
1. Unsplash: https://images.unsplash.com/photo-[ID]?auto=format&fit=crop&w=1200&q=80
2. Wikimedia: https://upload.wikimedia.org/wikipedia/commons/thumb/...
3. Pexels: CC0

Fallback: onerror="this.onerror=null;this.style.background='#ededed';"

Google Maps — ТОЛЬКО в этом формате:
https://www.google.com/maps/search/[название]/@[lat],[lng],[zoom]z
НЕ использовать maps.app.goo.gl — они не работают!

## СТРУКТУРА ФАЙЛОВ

webapp/guides/[регион]/
  index.html   ← hero + статистика + превью мест + кухня + тур + footer
  places.html  ← все места с фото и Google Maps
  food.html    ← кухня, блюда, сувениры
  tips.html    ← лайфхаки, традиции, одежда, безопасность
  tour.html    ← программа по дням, что включено, FAQ

Одинаковый sticky header на всех 5 страницах.

## СОВЕТЫ И ТРАДИЦИИ — ДАГЕСТАН

ОДЕЖДА — это уважение к культуре:

Женщинам:
- Плечи и декольте закрыты ВСЕГДА — в сёлах и аулах, не только в мечети
- Короткие юбки, шорты, открытые топы — только на пляже
- В мечеть — платок и одежда до щиколоток обязательно
- Купальник — только на пляже, не в городе

Мужчинам:
- Шорты только ниже колен, в сёлах лучше брюки
- В мечеть — длинные брюки, плечи закрыты
- Майки-безрукавки в населённых пунктах — неуважение

Всем:
- Не фотографировать людей без разрешения — особенно женщин
- Алкоголь в общественных местах — табу
- Если пригласили домой — не отказывай, это большая честь
- Принимай угощение — отказ обижает хозяина

## WORKFLOW ДЛЯ CLAUDE CODE

Перед каждой задачей:
1. cat GUIDES.md
2. cat INSTRUCTIONS.md
3. Создать ветку feature/guide-[регион]

Каждое задание начинается с:
ВАЖНО: Делай ТОЛЬКО то что написано ниже

ЗАПРЕЩЕНО:
- Тёмный фон на основных страницах
- Выдумывать URL фото или Google Maps
- Одностраничник вместо 5 страниц
- Шрифты Inter/Roboto/Arial/Cormorant как основные
- Тёмные/коричневые блоки
- Эмодзи вместо фото в карточках мест

## ФОРМАТЫ ПРОДУКТА

1. Веб — GitHub Pages, 5 страниц, открывается из бота
2. PDF — для WB/Ozon/Яндекс Маркет, 149-299р, через Playwright
3. Кот + PDF — 3D-печать кота с символикой + путеводитель, коллекционный набор

## РОАДМАП

Россия (бесплатно по подписке):
1. Дагестан (дизайн утверждён: белый, Unbounded+Manrope)
2. Алтай · 3. Камчатка · 4. Карелия · 5. Байкал · 6. Плато Путорана

Мир (149р): Таиланд, Турция, Грузия, Армения, ОАЭ, Япония, Бали, Вьетнам

v2.0 · Май 2026 · Как местный
Утверждено: белый фон #ffffff, Unbounded + Manrope, стиль Авиасейлс
