# Проверки одной командой. Требует dev-requirements.txt:
#   pip install -r requirements.txt -r dev-requirements.txt

PY := python3

.PHONY: check test lint typecheck bench install-dev

check: lint test          ## всё, что должно быть зелёным перед коммитом

test:                     ## тесты движка и оболочки
	$(PY) -m pytest

lint:                     ## ruff по конфигу из pyproject.toml
	$(PY) -m ruff check .

typecheck:                ## mypy по core/ и domain/ (см. pyproject.toml)
	$(PY) -m mypy

bench:                    ## базовые замеры (см. tools/bench.py --help)
	$(PY) tools/bench.py

install-dev:              ## окружение разработчика
	$(PY) -m pip install -r requirements.txt -r dev-requirements.txt
