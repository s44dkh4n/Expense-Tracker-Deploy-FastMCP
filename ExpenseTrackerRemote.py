import asyncio
import datetime as dt
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated, Optional

import asyncpg
from fastmcp import FastMCP
from pydantic import Field

# Set DATABASE_URL in the host environment (for example a Neon Postgres connection string)
DATABASE_URL = os.getenv("DATABASE_URL")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS expenses (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    date TIMESTAMP NOT NULL,
    amount NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    category TEXT NOT NULL,
    subcategory TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses (date);
"""

_pool: Optional[asyncpg.Pool] = None
_pool_lock = asyncio.Lock()


async def get_pool() -> asyncpg.Pool:
    """Create the shared connection pool on first use and make sure the table exists."""
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                if not DATABASE_URL:
                    raise RuntimeError("DATABASE_URL environment variable is not set")
                pool = await asyncpg.create_pool(
                    DATABASE_URL,
                    min_size=1,
                    max_size=5,
                    command_timeout=30,
                    # Needed when the host uses a pooled (pgbouncer) connection string
                    statement_cache_size=0,
                )
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        # Lock so several instances starting together do not race on CREATE TABLE
                        await conn.execute("SELECT pg_advisory_xact_lock(727001)")
                        await conn.execute(SCHEMA_SQL)
                _pool = pool
    return _pool


@asynccontextmanager
async def lifespan(server):
    global _pool
    await get_pool()
    try:
        yield
    finally:
        if _pool is not None:
            await _pool.close()
            _pool = None


mcp = FastMCP("Expense Tracker", lifespan=lifespan)

def _naive(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=None)


def _money(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _to_dict(row: asyncpg.Record) -> dict:
    return {
        "id": row["id"],
        "date": row["date"].strftime("%Y-%m-%d %H:%M:%S"),
        "amount": float(row["amount"]),
        "category": row["category"],
        "subcategory": row["subcategory"],
        "note": row["note"],
    }


def _date_filter(
    start_date: Optional[dt.date], end_date: Optional[dt.date]
) -> tuple[str, list]:
    # Only placeholders are put into the SQL text, the values always travel as parameters
    conditions: list[str] = []
    params: list = []
    if start_date is not None:
        params.append(start_date)
        conditions.append(f"date >= ${len(params)}::date")
    if end_date is not None:
        params.append(end_date)
        # Adding one day makes end_date inclusive of the whole day
        conditions.append(f"date < (${len(params)}::date + 1)")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return where, params


def _invalid_range(
    start_date: Optional[dt.date], end_date: Optional[dt.date]
) -> Optional[dict]:
    if start_date and end_date and start_date > end_date:
        return {"status": "error", "message": "start_date must not be after end_date."}
    return None


@mcp.tool
async def add_expense(
    date: dt.datetime,
    amount: Annotated[float, Field(gt=0)],
    category: Annotated[str, Field(min_length=1)],
    subcategory: str = "",
    note: str = "",
) -> dict:
    """Add one expense. Date format is YYYY-MM-DD or YYYY-MM-DD HH:MM:SS. Amount must be positive.
    Returns the saved record including its id."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO expenses (date, amount, category, subcategory, note)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *
        """,
        _naive(date),
        _money(amount),
        category,
        subcategory,
        note,
    )
    return {"status": "ok", "expense": _to_dict(row)}

@mcp.tool
async def list_expense(
    start_date: Optional[dt.date] = None,
    end_date: Optional[dt.date] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict:
    """List expense records, oldest first. Optionally filter by start_date and end_date
    (YYYY-MM-DD, both inclusive). Results are paged: if has_more is true, call again with a
    larger offset to get the next rows. Use total to report the real number of matches."""
    error = _invalid_range(start_date, end_date)
    if error:
        return error

    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    where, params = _date_filter(start_date, end_date)

    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM expenses {where}", *params)
        rows = await conn.fetch(
            f"""
            SELECT * FROM expenses {where}
            ORDER BY date, id
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """,
            *params,
            limit,
            offset,
        )

    return {
        "expenses": [_to_dict(row) for row in rows],
        "returned": len(rows),
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }

@mcp.tool
async def summarize_expense(
    start_date: Optional[dt.date] = None,
    end_date: Optional[dt.date] = None,
) -> dict:
    """Summarize spending: total amount and number of expenses per category, plus a grand total.
    Optionally filter by start_date and end_date (YYYY-MM-DD, both inclusive)."""
    error = _invalid_range(start_date, end_date)
    if error:
        return error

    where, params = _date_filter(start_date, end_date)

    pool = await get_pool()
    rows = await pool.fetch(
        f"""
        SELECT category, COUNT(*) AS count, SUM(amount) AS total
        FROM expenses {where}
        GROUP BY category
        ORDER BY total DESC
        """,
        *params,
    )

    grand_total = sum((row["total"] for row in rows), Decimal("0"))
    return {
        "by_category": [
            {
                "category": row["category"],
                "count": row["count"],
                "total": float(row["total"]),
            }
            for row in rows
        ],
        "expense_count": sum(row["count"] for row in rows),
        "grand_total": float(grand_total),
    }

@mcp.tool
async def edit_expense(
    id: int,
    date: Optional[dt.datetime] = None,
    amount: Optional[Annotated[float, Field(gt=0)]] = None,
    category: Optional[Annotated[str, Field(min_length=1)]] = None,
    subcategory: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """Update any fields of one expense by id. Only the fields you pass are changed.
    Returns the updated record."""
    candidates = {
        "date": _naive(date) if date is not None else None,
        "amount": _money(amount) if amount is not None else None,
        "category": category,
        "subcategory": subcategory,
        "note": note,
    }
    updates = {column: value for column, value in candidates.items() if value is not None}

    if not updates:
        return {"status": "error", "message": "No fields provided to update."}

    set_clause = ", ".join(
        f"{column} = ${position}" for position, column in enumerate(updates, start=1)
    )
    values = [*updates.values(), id]

    pool = await get_pool()
    row = await pool.fetchrow(
        f"UPDATE expenses SET {set_clause} WHERE id = ${len(values)} RETURNING *",
        *values,
    )

    if row is None:
        return {"status": "error", "message": f"No expense found with ID {id}"}
    return {"status": "ok", "updated_expense": _to_dict(row)}


@mcp.tool
async def delete_expense(id: int) -> dict:
    """Delete one expense by id and return the removed record."""
    pool = await get_pool()
    row = await pool.fetchrow("DELETE FROM expenses WHERE id = $1 RETURNING *", id)

    if row is None:
        return {"status": "error", "message": f"No expense found with ID {id}"}
    return {"status": "ok", "deleted_expense": _to_dict(row)}


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)