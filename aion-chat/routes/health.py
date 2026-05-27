"""
健康功能 API：戒指最新快照、体重记录、姨妈期记录。
"""

import json
import re
import time
from datetime import date, datetime
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Query
from pydantic import BaseModel

from database import get_db
from ws import manager

router = APIRouter(prefix="/api/health", tags=["health"])

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid_date(value: str) -> bool:
    if not value or not DATE_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _row_dict(row):
    return dict(row) if row else None


class RingSleep(BaseModel):
    start_at: Optional[float] = None
    end_at: Optional[float] = None
    total_min: Optional[int] = None
    deep_min: Optional[int] = None
    light_min: Optional[int] = None
    rem_min: Optional[int] = None
    wake_min: Optional[int] = None
    wake_count: Optional[int] = None


class RingSnapshot(BaseModel):
    device_name: str = ""
    heart_rate: Optional[int] = None
    systolic_bp: Optional[int] = None
    diastolic_bp: Optional[int] = None
    spo2: Optional[int] = None
    hrv: Optional[float] = None
    measured_at: Optional[float] = None
    sleep: Optional[RingSleep] = None
    raw: Optional[dict] = None


class WeightEntry(BaseModel):
    date: str
    weight_kg: float
    note: str = ""


class PeriodEntry(BaseModel):
    id: str = ""
    start_date: str
    end_date: str = ""
    flow: str = ""
    symptoms: str = ""
    note: str = ""


@router.get("/summary")
async def get_health_summary():
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM health_ring_latest WHERE id=1")
        ring = _row_dict(await cur.fetchone())
        cur = await db.execute(
            "SELECT date, weight_kg, note, created_at, updated_at "
            "FROM health_weight_entries ORDER BY date DESC LIMIT 90"
        )
        weights = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT id, start_date, end_date, flow, symptoms, note, created_at, updated_at "
            "FROM health_period_entries ORDER BY start_date DESC LIMIT 24"
        )
        periods = [dict(r) for r in await cur.fetchall()]
    return {"ring": ring, "weights": weights, "periods": periods}


@router.post("/ring/latest")
async def save_ring_latest(body: RingSnapshot):
    now = time.time()
    measured_at = body.measured_at or now
    sleep = body.sleep or RingSleep()
    raw_json = json.dumps(body.raw or {}, ensure_ascii=False)
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO health_ring_latest (
                id, device_name, heart_rate, systolic_bp, diastolic_bp, spo2, hrv,
                measured_at, sleep_start_at, sleep_end_at, sleep_total_min,
                sleep_deep_min, sleep_light_min, sleep_rem_min, sleep_wake_min,
                sleep_wake_count, raw_json, synced_at
            ) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                device_name=excluded.device_name,
                heart_rate=excluded.heart_rate,
                systolic_bp=excluded.systolic_bp,
                diastolic_bp=excluded.diastolic_bp,
                spo2=excluded.spo2,
                hrv=excluded.hrv,
                measured_at=excluded.measured_at,
                sleep_start_at=excluded.sleep_start_at,
                sleep_end_at=excluded.sleep_end_at,
                sleep_total_min=excluded.sleep_total_min,
                sleep_deep_min=excluded.sleep_deep_min,
                sleep_light_min=excluded.sleep_light_min,
                sleep_rem_min=excluded.sleep_rem_min,
                sleep_wake_min=excluded.sleep_wake_min,
                sleep_wake_count=excluded.sleep_wake_count,
                raw_json=excluded.raw_json,
                synced_at=excluded.synced_at
            """,
            (
                body.device_name.strip(),
                body.heart_rate,
                body.systolic_bp,
                body.diastolic_bp,
                body.spo2,
                body.hrv,
                measured_at,
                sleep.start_at,
                sleep.end_at,
                sleep.total_min,
                sleep.deep_min,
                sleep.light_min,
                sleep.rem_min,
                sleep.wake_min,
                sleep.wake_count,
                raw_json,
                now,
            ),
        )
        await db.commit()
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM health_ring_latest WHERE id=1")
        row = dict(await cur.fetchone())
    await manager.broadcast({"type": "health_ring_updated", "data": row})
    return row


@router.get("/weights")
async def list_weights(
    start: str = Query("", max_length=10),
    end: str = Query("", max_length=10),
    limit: int = Query(180, ge=1, le=1000),
):
    where = []
    params: list[object] = []
    if start and _valid_date(start):
        where.append("date >= ?")
        params.append(start)
    if end and _valid_date(end):
        where.append("date <= ?")
        params.append(end)
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT date, weight_kg, note, created_at, updated_at "
            f"FROM health_weight_entries {where_sql} ORDER BY date DESC LIMIT ?",
            [*params, limit],
        )
        rows = [dict(r) for r in await cur.fetchall()]
    return {"items": rows}


@router.post("/weights")
async def upsert_weight(body: WeightEntry):
    if not _valid_date(body.date):
        return {"error": "日期格式不正确"}
    if body.weight_kg <= 0 or body.weight_kg > 500:
        return {"error": "体重数值不正确"}
    now = time.time()
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO health_weight_entries (date, weight_kg, note, created_at, updated_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET
                weight_kg=excluded.weight_kg,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            (body.date, body.weight_kg, body.note.strip(), now, now),
        )
        await db.commit()
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT date, weight_kg, note, created_at, updated_at FROM health_weight_entries WHERE date=?",
            (body.date,),
        )
        row = dict(await cur.fetchone())
    await manager.broadcast({"type": "health_weight_updated", "data": row})
    return row


@router.delete("/weights/{entry_date}")
async def delete_weight(entry_date: str):
    if not _valid_date(entry_date):
        return {"error": "日期格式不正确"}
    async with get_db() as db:
        await db.execute("DELETE FROM health_weight_entries WHERE date=?", (entry_date,))
        await db.commit()
    return {"ok": True}


@router.get("/periods")
async def list_periods(limit: int = Query(36, ge=1, le=200)):
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, start_date, end_date, flow, symptoms, note, created_at, updated_at "
            "FROM health_period_entries ORDER BY start_date DESC LIMIT ?",
            (limit,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    return {"items": rows}


@router.post("/periods")
async def upsert_period(body: PeriodEntry):
    if not _valid_date(body.start_date):
        return {"error": "开始日期格式不正确"}
    if body.end_date and not _valid_date(body.end_date):
        return {"error": "结束日期格式不正确"}
    if body.end_date and body.end_date < body.start_date:
        return {"error": "结束日期不能早于开始日期"}
    now = time.time()
    entry_id = body.id.strip() or f"hp_{int(now * 1000)}"
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO health_period_entries
                (id, start_date, end_date, flow, symptoms, note, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                start_date=excluded.start_date,
                end_date=excluded.end_date,
                flow=excluded.flow,
                symptoms=excluded.symptoms,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            (
                entry_id,
                body.start_date,
                body.end_date,
                body.flow.strip(),
                body.symptoms.strip(),
                body.note.strip(),
                now,
                now,
            ),
        )
        await db.commit()
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, start_date, end_date, flow, symptoms, note, created_at, updated_at "
            "FROM health_period_entries WHERE id=?",
            (entry_id,),
        )
        row = dict(await cur.fetchone())
    await manager.broadcast({"type": "health_period_updated", "data": row})
    return row


@router.delete("/periods/{entry_id}")
async def delete_period(entry_id: str):
    async with get_db() as db:
        await db.execute("DELETE FROM health_period_entries WHERE id=?", (entry_id,))
        await db.commit()
    return {"ok": True}
