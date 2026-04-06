import os
import logging
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = "8701321387:AAHwb_WkmrimPtInwDftv8jb0d03gTkogqA"

ANSWERING = 0

QUESTIONS = [
    {"id": "company", "text": "Привет! Я твой travel-помощник 🌍\n\nС кем планируешь путешествие?", "opts": ["Один", "С партнёром", "С друзьями", "С семьёй и детьми"]},
    {"id": "nature", "text": "Что больше притягивает в путешествии?", "opts": ["Море и пляжи", "Горы и природа", "Города и культура", "Джунгли и экзотика"]},
    {"id": "budget", "text": "Бюджет на человека (перелёт + отель + еда)?", "opts": ["До 50 000 ₽", "50–100 000 ₽", "100–200 000 ₽", "Без ограничений"]},
    {"id": "passport", "text": "Есть загранпаспорт?", "opts": ["Да, биометрический", "Да, обычный", "Нет загранпаспорта"]},
    {"id": "duration", "text": "Сколько дней планируешь?", "opts": ["До 7 дней", "1–2 недели", "2–4 недели", "Больше месяца"]},
    {"id": "climate", "text": "Какой климат предпочитаешь?", "opts": ["Жара +30 и выше", "Тепло +20–28", "Прохладно +10–18", "Не важно"]},
    {"id": "food", "text": "Как относишься к экзотической еде?", "opts": ["Пробую всё подряд", "Осторожно, но пробую", "Предпочитаю привычное", "Только европейская кухня"]},
    {"id": "vibe", "text": "Что главное в поездке?", "opts": ["Полный отдых и пляж", "Культура и история", "Гастрономия и рынки", "Экстрим и активность"]},
    {"id": "accommodation", "text": "Где предпочитаешь жить?", "opts": ["Отель 4–5 звёзд", "Отель 2–3 звезды", "Хостел или гестхаус", "Апартаменты"]},
    {"id": "experience", "text": "Опыт самостоятельных путешествий?", "opts": ["Первый раз за рубеж", "Иногда езжу", "Опытный путешественник", "Постоянно в дороге"]},
    {"id": "visa", "text": "Готов оформлять визу?", "opts": ["Да, любую", "Только e-visa онлайн", "Только безвизовые страны", "Не знаю как это делать"]},
    {"id": "activity", "text": "Любимое занятие в поездке?", "opts": ["Пляж и купание", "Экскурсии и музеи", "Шоппинг и рынки", "Трекинг и природа"]},
    {"id": "transport", "text": "Как передвигаешься внутри страны?", "opts": ["Аренда авто или мото", "Общественный транспорт", "Такси и трансферы", "Пешком"]},
    {"id": "language", "text": "Знаешь иностранные языки?", "opts": ["Английский хорошо", "Английский базово", "Только русский", "Несколько языков"]},
    {"id": "goal", "text": "Последний вопрос! Главная цель поездки?", "opts": ["Полностью отключиться", "Увидеть максимум мест", "Погрузиться в культуру", "Совместить работу и отдых"]},
]

DESTINATIONS = [
    {"country": "Таиланд", "city": "Бангкок", "flag": "🇹🇭", "why": "Идеальный баланс пляжей, культуры, уличной еды и доступных цен.", "highlight": "Буддийские храмы, ночные рынки и острова с бирюзовой водой", "best_time": "Ноябрь – февраль", "budget": "от 2 500 ₽/день", "tip": "Покупай симку в аэропорту сразу — интернет дешёвый и быстрый везде", "visa": "Безвизово 30 дней", "tags": {"nature": ["Море и пляжи", "Джунгли и экзотика"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Полный отдых и пляж", "Гастрономия и рынки"]}},
    {"country": "Вьетнам", "city": "Ханой", "flag": "🇻🇳", "why": "Разнообразие на любой вкус: мегаполисы, рисовые поля, пляжи и вкуснейшая уличная еда.", "highlight": "Бухта Халонг, фонарики Хойана и фо за 50 рублей", "best_time": "Февраль – апрель", "budget": "от 2 000 ₽/день", "tip": "Торгуйся везде — первая цена для туристов завышена в 2–3 раза", "visa": "E-visa онлайн, 90 дней", "tags": {"nature": ["Море и пляжи", "Горы и природа"], "climate": ["Жара +30 и выше", "Тепло +20–28"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки"]}},
    {"country": "Япония", "city": "Токио", "flag": "🇯🇵", "why": "Уникальное сочетание древней культуры и ультрасовременного города.", "highlight": "Фудзи, суши, сакура и технологии будущего", "best_time": "Март – май, октябрь – ноябрь", "budget": "от 6 000 ₽/день", "tip": "Купи JR Pass до въезда в Японию — сэкономишь на синкансэнах", "visa": "Безвизово 90 дней", "tags": {"nature": ["Горы и природа", "Города и культура"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["100–200 000 ₽", "Без ограничений"], "vibe": ["Культура и история", "Гастрономия и рынки"]}},
    {"country": "Бали", "city": "Денпасар", "flag": "🇮🇩", "why": "Духовная атмосфера, рисовые террасы, сёрфинг и незабываемые закаты.", "highlight": "Храм Танах Лот, вулкан Батур и рисовые поля Тегаллаланг", "best_time": "Апрель – октябрь", "budget": "от 2 500 ₽/день", "tip": "Арендуй скутер — единственный нормальный способ передвижения по острову", "visa": "Безвизово 30 дней", "tags": {"nature": ["Море и пляжи", "Горы и природа"], "climate": ["Жара +30 и выше"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Полный отдых и пляж", "Культура и история"]}},
    {"country": "Грузия", "city": "Тбилиси", "flag": "🇬🇪", "why": "Безвизово, дёшево, вкусно и невероятно красиво — горы, вино и гостеприимство.", "highlight": "Кавказские горы, хачапури, вино из квеври и старый Тбилиси", "best_time": "Май – июнь, сентябрь – октябрь", "budget": "от 2 000 ₽/день", "tip": "Возьми машину — общественного транспорта в горах почти нет", "visa": "Безвизово 365 дней", "tags": {"nature": ["Горы и природа", "Города и культура"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки"]}},
    {"country": "Камбоджа", "city": "Сием Рип", "flag": "🇰🇭", "why": "Ангкор Ват — одно из величайших чудес света и древняя цивилизация вокруг.", "highlight": "Ангкор Ват на рассвете, плавучие деревни и закаты", "best_time": "Ноябрь – март", "budget": "от 1 800 ₽/день", "tip": "Вставай в 4 утра чтобы встретить рассвет в Ангкор Вате — незабываемо", "visa": "E-visa 30 долларов", "tags": {"nature": ["Джунгли и экзотика", "Города и культура"], "climate": ["Жара +30 и выше"], "budget": ["До 50 000 ₽"], "vibe": ["Культура и история"]}},
    {"country": "Турция", "city": "Стамбул", "flag": "🇹🇷", "why": "Два континента, тысячелетняя история, море и отличная кухня по доступным ценам.", "highlight": "Голубая мечеть, Каппадокия и Средиземноморское побережье", "best_time": "Апрель – июнь, сентябрь – ноябрь", "budget": "от 3 000 ₽/день", "tip": "Стамбульская карта выгоднее разовых билетов — купи сразу", "visa": "Безвизово 60 дней", "tags": {"nature": ["Море и пляжи", "Города и культура"], "climate": ["Тепло +20–28", "Жара +30 и выше"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки"]}},
    {"country": "Филиппины", "city": "Манила", "flag": "🇵🇭", "why": "Более 7000 островов с бирюзовой водой, кораллами и белым песком.", "highlight": "Острова Палаван, Боракай и дайвинг", "best_time": "Декабрь – май", "budget": "от 2 500 ₽/день", "tip": "Летай внутренними рейсами Cebu Pacific — дёшево между островами", "visa": "Безвизово 30 дней", "tags": {"nature": ["Море и пляжи", "Джунгли и экзотика"], "climate": ["Жара +30 и выше"], "budget": ["До 50 000 ₽", "50–100 000 ₽"], "vibe": ["Полный отдых и пляж", "Экстрим и активность"]}},
    {"country": "Армения", "city": "Ереван", "flag": "🇦🇲", "why": "Древнейшая христианская страна с монастырями, горами и коньяком.", "highlight": "Монастырь Гегард, гора Арарат и рынок Вернисаж", "best_time": "Май – октябрь", "budget": "от 1 800 ₽/день", "tip": "Попробуй коньяк на заводе Арарат — экскурсия с дегустацией стоит копейки", "visa": "Безвизово", "tags": {"nature": ["Горы и природа", "Города и культура"], "climate": ["Тепло +20–28", "Прохладно +10–18"], "budget": ["До 50 000 ₽"], "vibe": ["Культура и история", "Гастрономия и рынки"]}},
    {"country": "Сингапур", "city": "Сингапур", "flag": "🇸🇬", "why": "Самый чистый и безопасный город Азии — архитектура, еда и шоппинг.", "highlight": "Gardens by the Bay, Марина Бэй Сэндс и хокер-центры", "best_time": "Февраль – апрель", "budget": "от 7 000 ₽/день", "tip": "Ешь в хокер-центрах — еда дешевле и вкуснее ресторанов в разы", "visa": "Безвизово 30 дней", "tags": {"nature": ["Города и культура"], "climate": ["Жара +30 и выше"], "budget": ["100–200 000 ₽", "Без ограничений"], "vibe": ["Культура и история", "Гастрономия и рынки"]}},
]


def score_destination(dest, answers):
    s = 0
    tags = dest.get("tags", {})
    if answers.get("nature") in tags.get("nature", []): s += 3
    if answers.get("climate") in tags.get("climate", []): s += 2
    if answers.get("budget") in tags.get("budget", []): s += 2
    if answers.get("vibe") in tags.get("vibe", []): s += 3
    if answers.get("passport") == "Нет загранпаспорта" and "Безвизово" in dest.get("visa", ""): s += 3
    if answers.get("visa") == "Только безвизовые страны" and "Безвизово" in dest.get("visa", ""): s += 2
    return s


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["answers"] = {}
    context.user_data["step"] = 0
    q = QUESTIONS[0]
    keyboard = [[opt] for opt in q["opts"]]
    await update.message.reply_text(
        q["text"],
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return ANSWERING


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("step", 0)
    answers = context.user_data.get("answers", {})
    text = update.message.text

    valid_opts = QUESTIONS[step]["opts"]
    if text not in valid_opts:
        keyboard = [[opt] for opt in valid_opts]
        await update.message.reply_text(
            "Выбери один из вариантов 👇",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        )
        return ANSWERING

    answers[QUESTIONS[step]["id"]] = text
    context.user_data["answers"] = answers
    step += 1
    context.user_data["step"] = step

    if step < len(QUESTIONS):
        q = QUESTIONS[step]
        keyboard = [[opt] for opt in q["opts"]]
        progress = f"Вопрос {step + 1} из {len(QUESTIONS)}\n\n"
        await update.message.reply_text(
            progress + q["text"],
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        )
        return ANSWERING
    else:
        return await show_result(update, context)


async def show_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answers = context.user_data.get("answers", {})
    await update.message.reply_text("Анализирую твои ответы... 🔍", reply_markup=ReplyKeyboardRemove())

    scored = sorted(DESTINATIONS, key=lambda d: score_destination(d, answers), reverse=True)
    rec = scored[0]
    alt = scored[1]

    result = (
        f"{rec['flag']} *Твоё идеальное направление — {rec['country']}*\n\n"
        f"🏙 *Старт из:* {rec['city']}\n"
        f"💡 *Почему:* {rec['why']}\n"
        f"✨ *Главная фишка:* {rec['highlight']}\n"
        f"📅 *Лучшее время:* {rec['best_time']}\n"
        f"💰 *Бюджет:* {rec['budget']}\n"
        f"🛂 *Виза:* {rec['visa']}\n"
        f"🎯 *Совет эксперта:* {rec['tip']}\n\n"
        f"Также подойдёт: {alt['flag']} {alt['country']}\n\n"
        f"Пройти ещё раз — /start"
    )

    await update.message.reply_text(result, parse_mode="Markdown")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("До встречи! Напиши /start чтобы начать заново.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main():
    app = Application.builder().token(TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={ANSWERING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_answer)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)
    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
