.PHONY: install install-dev run migrate migration

install:
	python3 -m pip install -r requirements.txt

install-dev:
	python3 -m pip install -r requirements-dev.txt

run:
	python3 -m bot.main

# Apply pending Alembic migrations to the SQLite file at $$DATABASE_URL
# (defaults from bot/config.py). bot.main also runs this at startup; this
# target is for one-off manual migrations or scripted deploys.
migrate:
	python3 -m alembic upgrade head

# Generate a new empty migration: `make migration name="add foo column"`
migration:
	python3 -m alembic revision -m "$(name)"
