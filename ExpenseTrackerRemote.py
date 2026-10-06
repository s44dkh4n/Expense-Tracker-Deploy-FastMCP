from fastmcp import FastMCP
from typing import Optional
import sqlite3
import os
import tempfile

mcp = FastMCP("Expense Tracker")

PATH = os.path.join(tempfile.gettempdir(), "Expenses.db")

ALLOWED_COLUMNS = {"date", "amount", "category", "subcategory", "note"}

def init_db() -> None:
    """Initialize the SQLite database and create the expenses table if it doesn't exist."""
    with sqlite3.connect(PATH) as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS expenses(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            subcategory TEXT DEFAULT '',
            note TEXT NOT NULL
        )
        """)

@mcp.tool
def add_expense(
    date: str,
    amount: float,
    category: str,
    subcategory: str = "",
    note: str = ""
) -> dict:
    """
    Add a new expense entry into the database.

    Args:
        date: Date of the expense in ISO-8601 format (YYYY-MM-DD).
        amount: Cost of the expense (e.g., 45.50).
        category: Broad classification of the expense (e.g., 'Food', 'Utilities').
        subcategory: Optional detailed classification (e.g., 'Groceries', 'Electricity').
        note: Additional notes or description regarding the expense.

    Returns:
        A dictionary containing the status and the assigned database ID.
    """
    with sqlite3.connect(PATH) as c:
        cur = c.execute("""
            INSERT INTO expenses (date, amount, category, subcategory, note)
            VALUES (?, ?, ?, ?, ?)
        """, (date, amount, category, subcategory, note))

        return {"status": "ok", "id": cur.lastrowid}

@mcp.tool
def list_expense() -> list[dict]:
    """
    Fetch all expense entries stored in the database.

    Returns:
        A list of dictionaries, where each dictionary represents an expense record
        sorted by ID in ascending order.
    """
    with sqlite3.connect(PATH) as c:
        c.row_factory = sqlite3.Row
        cur = c.execute("SELECT * FROM expenses ORDER BY id ASC")
        return [dict(row) for row in cur.fetchall()]

@mcp.tool
def summarize_expense(start_date: str, end_date: str) -> list[dict]:
    """
    Retrieve all expenses that fall within a specific date range.

    Args:
        start_date: The start date for filtering in YYYY-MM-DD format (inclusive).
        end_date: The end date for filtering in YYYY-MM-DD format (inclusive).

    Returns:
        A list of dictionaries matching expenses within the given timeframe.
    """
    with sqlite3.connect(PATH) as c:
        c.row_factory = sqlite3.Row
        cur = c.execute("""
            SELECT * FROM expenses 
            WHERE date(date) BETWEEN date(?) AND date(?)
        """, (start_date, end_date))
        return [dict(row) for row in cur.fetchall()]

@mcp.tool
def edit_expense(
    id: int,
    date: Optional[str] = None,
    amount: Optional[float] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    note: Optional[str] = None
) -> dict:
    """
    Update any combination of fields for an existing expense record by ID.
    """
    # Build dictionary of provided fields (excluding None)
    updates = {}
    if date is not None: updates["date"] = date
    if amount is not None: updates["amount"] = amount
    if category is not None: updates["category"] = category
    if subcategory is not None: updates["subcategory"] = subcategory
    if note is not None: updates["note"] = note

    if not updates:
        return {"status": "error", "message": "No fields provided to update."}

    set_clause = ", ".join(f"{col} = ?" for col in updates.keys())
    values = list(updates.values()) + [id]

    with sqlite3.connect(PATH) as c:
        c.row_factory = sqlite3.Row
        cur = c.execute(f"""
            UPDATE expenses
            SET {set_clause}
            WHERE id = ?
            RETURNING *
        """, values)
        
        updated_row = cur.fetchone()
        if not updated_row:
            return {"status": "error", "message": f"No expense found with ID {id}"}

        return {"status": "ok", "updated_expense": dict(updated_row)}
    
@mcp.tool
def delete_expense(id: int) -> dict:
    """
    Delete an expense record from the database by its ID and return the removed entry.

    Args:
        id: The unique database ID of the expense record to delete.

    Returns:
        A dictionary containing the execution status and details of the deleted expense,
        or an error message if the ID was not found.
    """
    with sqlite3.connect(PATH) as c:
        c.row_factory = sqlite3.Row
        cur = c.execute("""
            DELETE FROM expenses
            WHERE id = ?
            RETURNING *
        """, (id,))
        
        deleted_row = cur.fetchone()

        if not deleted_row:
            return {"status": "error", "message": f"No expense found with ID {id}"}
        
        return {"status": "ok", "deleted_expense": dict(deleted_row)}

init_db()

if __name__ == "__main__":
    
    mcp.run("http",port=8000,host="0.0.0.0")