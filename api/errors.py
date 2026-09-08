"""Перевод исключений ядра и ошибок разбора запроса в ответы контракта.

Тело всегда одно: `{"ok": false, "errors": [{"field": ..., "error": ...}]}` —
форма подсвечивает поля, бот собирает фразу (`CONTRACT.md`). Код ответа
говорит, что случилось: 404 адреса нет, 409 состояние уже не то, 422 ввод не
годится.
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core import errors as core_errors

STATUS = {
    core_errors.NotFound: 404,
    core_errors.Ambiguous: 409,
    core_errors.Conflict: 409,
    core_errors.Unmarkable: 422,
}


def _body(errors):
    return {"ok": False, "errors": errors}


def install(app: FastAPI) -> None:
    @app.exception_handler(core_errors.ValidationError)
    async def _validation(request: Request, exc: core_errors.ValidationError):
        return JSONResponse(_body(exc.errors), status_code=422)

    @app.exception_handler(core_errors.CoreError)
    async def _core(request: Request, exc: core_errors.CoreError):
        code = next((c for t, c in STATUS.items() if isinstance(exc, t)), 400)
        return JSONResponse(_body([{"field": None, "error": str(exc)}]), status_code=code)

    @app.exception_handler(RequestValidationError)
    async def _request(request: Request, exc: RequestValidationError):
        # Путь поля из Pydantic — «body → steps → 2 → control_date» — сводится
        # к тому же виду, что даёт ядро: `steps[2].control_date`.
        errors = []
        for e in exc.errors():
            части = [str(x) for x in e.get("loc", ()) if x not in ("body", "query", "path")]
            поле = ""
            for ч in части:
                поле += f"[{ч}]" if ч.isdigit() else (f".{ч}" if поле else ч)
            errors.append({"field": поле or None, "error": e.get("msg", "неверное значение")})
        return JSONResponse(_body(errors), status_code=422)
