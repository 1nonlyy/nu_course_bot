# 🎓 NU Course Bot

**Telegram-бот для студентов Nazarbayev University**, который отслеживает [Public Course Catalog](https://registrar.nu.edu.kz/course-catalog) и **присылает push-уведомление, когда суммарно по всем секциям курса появляются свободные места** (переход «0 → больше нуля»). Не нужно вручную обновлять страницу регистратора: бот сам опрашивает каталог и помнит последний снимок по каждому коду.

> **Киллер-фича:** умные уведомления именно о **появлении** мест (а не о каждом изменении числа), с детализацией по секциям (лекции, лаборатории, речитации) и опросом тех же JSON-эндпоинтов регистратора, что использует сайт (`getSearchData`, `getSchedule`), по HTTP ([httpx](https://www.python-httpx.org/)).

---

## ✨ Основные возможности

- **Подписка на курс** — сохранение наблюдения по коду вида `CSCI 151` с валидацией и первичной проверкой каталога.
- **Фоновый опрос** — периодический опрос всех курсов с активными подписчиками (интервал настраивается).
- **Уведомление 0 → N** — сообщение всем подписчикам, когда сумма свободных мест по секциям выросла с нуля до положительного значения.
- **Разовая проверка** — `/check` без подписки.
- **Список подписок** — `/mysubs` с последним известным числом мест и временем обновления.
- **Inline-меню** — быстрые подсказки и справка из главного экрана.
- **Ограничение нагрузки на каталог** — минимальный интервал между запросами одного и того же курса (по умолчанию 180 с).
- **SQLite** — пользователи, подписки и снимки состояния курсов хранятся локально.

---

## 👤 Как начать пользоваться

1. Открой бота в Telegram: `https://t.me/nucoursenotifierbot`
2. Нажми **Start** или отправь `/start`.
3. Подпишись на курс, например: `/subscribe CSCI 151`
4. Дальше бот сам пришлёт уведомление, когда по курсу появятся места (порог **0 → >0**).

Подсказки и справка: `/help`.

---

## 📱 Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и главное меню |
| `/help` | Справка по командам |
| `/subscribe КОД` | Подписаться на курс (например `CSCI 151`) |
| `/unsubscribe КОД` | Отписаться |
| `/mysubs` | Активные подписки и последние известные места |
| `/check КОД` | Однократная проверка без подписки |

Интерфейс сообщений и справки в боте — **на русском языке**.

---

## 🛠 Технологический стек

| Компонент | Технология |
|-----------|------------|
| Язык | Python |
| Telegram | [aiogram](https://docs.aiogram.dev/) 3.x |
| HTTP-клиент (каталог) | [httpx](https://www.python-httpx.org/) (async) |
| Планировщик | [APScheduler](https://apscheduler.readthedocs.io/) (async) |
| База данных | SQLite через [aiosqlite](https://github.com/omnilib/aiosqlite) |
| Конфигурация | [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/), [python-dotenv](https://github.com/theskumar/python-dotenv) |

Бейджи:

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0?style=flat&logo=telegram&logoColor=white)](https://docs.aiogram.dev/)
[![httpx](https://img.shields.io/badge/httpx-HTTP-088787?style=flat)](https://www.python-httpx.org/)
[![SQLite](https://img.shields.io/badge/SQLite-aiosqlite-003B57?style=flat&logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![License](https://img.shields.io/badge/License-Use%20freely-8A2BE2?style=flat)](#-лицензия-и-дисклеймер)

---

## 🧑‍💻 Для разработчиков (развёртывание)

<details>
<summary><b>Быстрый старт (локально)</b></summary>

```bash
git clone <url-репозитория> && cd nu_course_bot
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
make install
cp .env.example .env
# Укажите BOT_TOKEN и при необходимости CATALOG_TERM_ID
make migrate
make run
```

- **`make install`** — зависимости из `requirements.txt`.
- **`make migrate`** — инициализация схемы SQLite.
- **`make run`** — запуск бота (`python3 -m bot.main`).

### Переменные окружения

См. [`.env.example`](.env.example). Ключевые параметры:

| Переменная | Назначение |
|------------|------------|
| `BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) |
| `POLL_INTERVAL_MINUTES` | Период фонового опроса каталога (минуты) |
| `DATABASE_URL` | URL SQLite, например `sqlite+aiosqlite:///./data/nu_bot.db` |
| `CATALOG_BASE_URL` | Базовый URL регистратора (по умолчанию NU) |
| `CATALOG_TERM_ID` | ID семестра в каталоге (как в выпадающем списке на сайте, напр. `824` — Summer 2026). Пустое значение: взять первый реальный семестр из HTML страницы каталога |
| `LOG_LEVEL` | Уровень логирования (`INFO`, `DEBUG`, …) |
| `SCRAPE_MIN_INTERVAL_SECONDS` | Мин. пауза между скрапами одного курса (опционально) |
| `CATALOG_IGNORE_TLS_ERRORS` | Пропуск проверки TLS для каталога при проблемах с цепочкой сертификатов (`true` / `false`, по умолчанию `true`) |

</details>

---

## 🔍 Как устроен скрапер

Каталог на стороне NU — **Drupal**-страница с клиентским UI, но данные по секциям и местам отдаются теми же **POST JSON**-методами, что вызывает браузер: `getSearchData` и `getSchedule` на пути `/my-registrar/public-course-catalog/json`. Бот делает **GET** на страницу каталога (сессия/куки, как у пользователя), затем вызывает эти эндпоинты через **httpx** без браузера. Если `CATALOG_TERM_ID` не задан, ID семестра читается из серверного HTML (`#semesterComboId`).

**Обновление для разработчиков:** ранее использовались Playwright и класс `BrowserManager`; они удалены. Внешний код не должен импортировать `BrowserManager` из `bot.scraper`.

---

## ⚖ Лицензия и дисклеймер

Проект можно свободно использовать и дорабатывать под свои задачи. **Не аффилирован с Nazarbayev University.** Убедитесь, что ваши сценарии использования соответствуют правилам регистратора и политике Telegram.
