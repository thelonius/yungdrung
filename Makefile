# Проверки одной командой. Требует dev-requirements.txt:
#   pip install -r requirements.txt -r dev-requirements.txt

PY := python3

.PHONY: check test lint typecheck bench install-dev openapi types web-check web-build e2e

check: lint test          ## всё, что должно быть зелёным перед коммитом

test:                     ## тесты движка и оболочки
	$(PY) -m pytest

lint:                     ## ruff по конфигу из pyproject.toml
	$(PY) -m ruff check .

typecheck:                ## mypy по core/, domain/ и api/ (см. pyproject.toml)
	$(PY) -m mypy

bench:                    ## базовые замеры (см. tools/bench.py --help)
	$(PY) tools/bench.py

install-dev:              ## окружение разработчика
	$(PY) -m pip install -r requirements.txt -r dev-requirements.txt

openapi:                  ## снимок схемы в api/openapi.json (тест следит за свежестью)
	$(PY) tools/openapi.py

types: openapi            ## типы клиента из схемы; нужен Node (npx openapi-typescript)
	@mkdir -p apps/web/src/api
	npx --yes openapi-typescript api/openapi.json -o apps/web/src/api/schema.d.ts

web-check:                ## клиент: tsc, eslint, vitest (нужен Node и npm ci в apps/web)
	cd apps/web && npx tsc -b && npx eslint . && npx vitest run

web-build:                ## собрать клиент в apps/web/dist
	cd apps/web && npm run build

e2e: web-build            ## приёмка «утро за десять нажатий» (Playwright, chromium)
	cd apps/web && npx playwright test
