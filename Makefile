# Проверки одной командой. Требует dev-requirements.txt:
#   pip install -r requirements.txt -r dev-requirements.txt

PY := python3

.PHONY: check test lint typecheck bench install-dev

check: lint test          ## всё, что должно быть зелёным перед коммитом

test:                     ## тесты движка и оболочки
	$(PY) -m pytest

lint:                     ## ruff по конфигу из pyproject.toml
	$(PY) -m ruff check .

typecheck:                ## mypy; включается вместе с модулями среза 1
	@test -d core || { echo "core/ ещё нет — mypy включается в срезе 1"; exit 0; }
	$(PY) -m mypy

bench:                    ## базовые замеры (см. tools/bench.py --help)
	$(PY) tools/bench.py

install-dev:              ## окружение разработчика
	$(PY) -m pip install -r requirements.txt -r dev-requirements.txt
