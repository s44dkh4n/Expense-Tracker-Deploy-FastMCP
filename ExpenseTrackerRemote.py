from fastmcp import FastMCP
from typing import Optional
import aiosqlite
import os
import tempfile

mcp = FastMCP("Expense Tracker")

PATH = os.path.join(tempfile.gettempdir(), "Expenses.db")


async def init_db() -> None:
    """Initialize the SQLite database and create the expenses table if it doesn't exist."""
    async with aiosqlite.connect(PATH) as c:
        await c.execute("""
            CREATE TABLE IF NOT EXISTS expenses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT DEFAULT '',
                note TEXT NOT NULL
            )
        """)
        await c.commit()


@mcp.tool
async def add_expense(
    date: str,
    amount: float,
    category: str,
    subcategory: str = "",
    note: str = ""
) -> dict:
    """Add a new expense entry into the database."""
    async with aiosqlite.connect(PATH) as c:
        cur = await c.execute("""
            INSERT INTO expenses (date, amount, category, subcategory, note)
            VALUES (?, ?, ?, ?, ?)
        """, (date, amount, category, subcategory, note))

        expense_id = cur.lastrowid
        await c.commit()

        return {
            "status": "ok",
            "id": expense_id
        }


@mcp.tool
async def list_expense() -> list[dict]:
    """Fetch all expense entries stored in the database."""
    async with aiosqlite.connect(PATH) as c:
        c.row_factory = aiosqlite.Row

        cur = await c.execute("""
            SELECT * FROM expenses
            ORDER BY id ASC
        """)

        rows = await cur.fetchall()

        return [dict(row) for row in rows]


@mcp.tool
async def summarize_expense(
    start_date: str,
    end_date: str
) -> list[dict]:
    """Retrieve all expenses that fall within a specific date range."""
    async with aiosqlite.connect(PATH) as c:
        c.row_factory = aiosqlite.Row

        cur = await c.execute("""
            SELECT * FROM expenses
            WHERE date(date) BETWEEN date(?) AND date(?)
            ORDER BY date ASC
        """, (start_date, end_date))

        rows = await cur.fetchall()

        return [dict(row) for row in rows]


@mcp.tool
async def edit_expense(
    id: int,
    date: Optional[str] = None,
    amount: Optional[float] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    note: Optional[str] = None
) -> dict:
    """Update any combination of fields for an existing expense record by ID."""

    updates = {}

    if date is not None:
        updates["date"] = date

    if amount is not None:
        updates["amount"] = amount

    if category is not None:
        updates["category"] = category

    if subcategory is not None:
        updates["subcategory"] = subcategory

    if note is not None:
        updates["note"] = note

    if not updates:
        return {
            "status": "error",
            "message": "No fields provided to update."
        }

    set_clause = ", ".join(
        f"{column} = ?" for column in updates.keys()
    )

    values = list(updates.values()) + [id]

    async with aiosqlite.connect(PATH) as c:
        c.row_factory = aiosqlite.Row

        cur = await c.execute(f"""
            UPDATE expenses
            SET {set_clause}
            WHERE id = ?
            RETURNING *
        """, values)

        updated_row = await cur.fetchone()

        if not updated_row:
            return {
                "status": "error",
                "message": f"No expense found with ID {id}"
            }

        await c.commit()

        return {
            "status": "ok",
            "updated_expense": dict(updated_row)
        }


@mcp.tool
async def delete_expense(id: int) -> dict:
    """Delete an expense record by ID and return the removed entry."""

    async with aiosqlite.connect(PATH) as c:
        c.row_factory = aiosqlite.Row

        cur = await c.execute("""
            DELETE FROM expenses
            WHERE id = ?
            RETURNING *
        """, (id,))

        deleted_row = await cur.fetchone()

        if not deleted_row:
            return {
                "status": "error",
                "message": f"No expense found with ID {id}"
            }

        await c.commit()

        return {
            "status": "ok",
            "deleted_expense": dict(deleted_row)
        }


async def main():
    await init_db()
    await mcp.run_async(
        transport="http",
        port=8000,
        host="0.0.0.0"
    )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())