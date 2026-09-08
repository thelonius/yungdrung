from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.deps import ctx, moment
from core import dates as core_dates
from core import feed as core_feed
from core import mark as core_mark
from core import search as core_search
from core.context import Context
from core.errors import ValidationError
from core.models import (
    BacklogResult, ExtractResult, FeedResult, MarkResult, SearchResult, WhenResult,
)
from domain.ru_dates import parse_date_input

router = APIRouter(prefix="/api/v1", tags=["v1"])


class TextIn(BaseModel):
    text: str | None = None


class MarkIn(BaseModel):
    """Отметка шага. `to` — человеческий ввод («завтра в 9», «+3», ISO):
    разбирает ядро, оболочка дату не трогает (`CONTRACT.md`)."""

    op: str
    reason: str | None = None
    to: str | None = None


def _to(raw, today: date, now: datetime):
    сырая = (raw or "").strip()
    if not сырая:
        return None
    try:
        # «через час» считается от настоящего момента — он есть только здесь,
        # в окне контроля, поэтому `now` передаётся именно сюда.
        return parse_date_input(сырая, today, now=now)
    except (ValueError, TypeError):
        raise ValidationError.single("to", core_dates.НЕ_ПОНЯЛ) from None


@router.get("/feed", response_model=FeedResult)
def feed(c: Context = Depends(ctx), now: datetime = Depends(moment)):
    return core_feed.feed(c, now, c.work())


@router.get("/backlog", response_model=BacklogResult)
def backlog(c: Context = Depends(ctx), now: datetime = Depends(moment)):
    return core_feed.backlog(c, now, c.work())


@router.post("/tasks/{task_id}/steps/{step_id}/mark", response_model=MarkResult)
def mark(task_id: int, step_id: int, body: MarkIn, c: Context = Depends(ctx),
         now: datetime = Depends(moment)):
    today = now.date()
    return core_mark.mark(c, body.op, task_id, step_id, reason=body.reason,
                         to=_to(body.to, today, now), today=today, now=now, work=c.work())


@router.post("/tasks/{task_id}/steps/{step_id}/undo", response_model=MarkResult)
def undo(task_id: int, step_id: int, c: Context = Depends(ctx),
         now: datetime = Depends(moment)):
    return core_mark.undo(c, task_id, step_id, today=now.date(), now=now, work=c.work())


@router.post("/parse-date", response_model=WhenResult)
def parse_date(body: TextIn, now: datetime = Depends(moment)):
    return core_dates.parse_when(body.text, now.date(), now=now)


@router.post("/extract-when", response_model=ExtractResult)
def extract_when(body: TextIn, now: datetime = Depends(moment)):
    return core_dates.extract(body.text, now.date(), now=now)


@router.get("/search", response_model=SearchResult)
def search(q: str | None = None, kind: str | None = None,
           limit: int = Query(50, ge=1, le=500), c: Context = Depends(ctx)):
    return core_search.search(c, q, kind=kind, limit=limit)
