# NU Course Bot

Telegram bot for Nazarbayev University students: monitors the [Public Course Catalog](https://registrar.nu.edu.kz/course-catalog) and sends a push notification when free seats go from zero to a positive number (aggregated across sections).

## Stack

- Python 3.11+
- aiogram 3.x, Playwright (async), aiosqlite, APScheduler, pydantic-settings

## Setup

```bash
cd nu_course_bot
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
make install
cp .env.example .env
# Edit .env — set BOT_TOKEN and optionally CATALOG_TERM_ID
make migrate
make run
```

`CATALOG_TERM_ID` matches the registrar JSON `getSemesters` ids (e.g. `823` for Spring 2026). If omitted, the first semester in the dropdown is used.

## Commands

- `/start` — welcome and inline menu
- `/subscribe CSCI 151` — watch a course (validates code, scrapes once, saves subscription)
- `/unsubscribe CSCI 151` — deactivate
- `/mysubs` — list subscriptions with last known availability
- `/check CSCI 151` — one-off check without subscribing

## Scraper notes

The catalog is a Drupal + jQuery app. The bot opens the catalog in headless Chromium, fills quick search, then calls the same JSON endpoints as the website (`getSearchData`, `getSchedule`) via Playwright’s request context. Per-course scraping is rate-limited (default: 180 seconds minimum between runs for the same code).

## License

Use and modify for your campus needs; not affiliated with Nazarbayev University.
