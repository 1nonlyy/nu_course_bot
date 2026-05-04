.PHONY: install run migrate

install:
	python3 -m pip install -r requirements.txt
	python3 -m playwright install chromium

run:
	python3 -m bot.main

migrate:
	python3 -c "import asyncio; from bot.config import get_settings; from bot.db.database import get_database; asyncio.run(get_database(get_settings()).init_schema()); print('OK')"
