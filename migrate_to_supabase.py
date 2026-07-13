"""One-off migration script: copy local SQLite CRM data into Supabase PostgreSQL."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import psycopg2
import streamlit as st

from modules.db_crm import CRMDatabaseManager

SQLITE_DB = Path("influencer_crm.db")


def connect_sqlite() -> sqlite3.Connection:
    """Open the local SQLite database with row-factory dict-like access."""
    if not SQLITE_DB.exists():
        raise FileNotFoundError(f"未找到本地数据库文件: {SQLITE_DB.resolve()}")

    conn = sqlite3.connect(SQLITE_DB)
    conn.row_factory = sqlite3.Row
    return conn


def connect_supabase() -> psycopg2.extensions.connection:
    """Open a Supabase PostgreSQL connection from Streamlit secrets."""
    return psycopg2.connect(st.secrets["SUPABASE_URL"])


def clear_remote_tables(pg_conn: psycopg2.extensions.connection) -> None:
    """Clear remote tables in dependency-safe order."""
    delete_order = ("collaborations", "products", "influencers")
    with pg_conn.cursor() as cursor:
        for table_name in delete_order:
            cursor.execute(f"DELETE FROM {table_name}")
            print(f"已清空云端表: {table_name}")


def reset_serial_sequence(
    pg_conn: psycopg2.extensions.connection,
    table_name: str,
) -> None:
    """Align SERIAL sequences with the highest migrated primary key."""
    with pg_conn.cursor() as cursor:
        cursor.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table_name}")
        max_id = cursor.fetchone()[0]
        if max_id == 0:
            cursor.execute(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'), 1, false)",
                (table_name,),
            )
        else:
            cursor.execute(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'), %s, true)",
                (table_name, max_id),
            )


def migrate_products(
    sqlite_conn: sqlite3.Connection,
    pg_conn: psycopg2.extensions.connection,
) -> int:
    """Migrate all product rows from SQLite to Supabase."""
    rows = sqlite_conn.execute("SELECT * FROM products ORDER BY id").fetchall()
    total = len(rows)
    print(f"开始迁移 products，共 {total} 条记录...")

    with pg_conn.cursor() as cursor:
        for index, row in enumerate(rows, start=1):
            cursor.execute(
                """
                INSERT INTO products (
                    id, asin, name, price, selling_points,
                    pros_cons, negotiation_strategy, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row["id"],
                    row["asin"],
                    row["name"],
                    row["price"],
                    row["selling_points"],
                    row["pros_cons"],
                    row["negotiation_strategy"],
                    row["created_at"],
                ),
            )
            print(f"  products 进度: {index}/{total}")

    reset_serial_sequence(pg_conn, "products")
    print(f"products 迁移完成: {total} 条")
    return total


def migrate_influencers(
    sqlite_conn: sqlite3.Connection,
    pg_conn: psycopg2.extensions.connection,
) -> int:
    """Migrate all influencer rows, preserving avatar binary data."""
    rows = sqlite_conn.execute("SELECT * FROM influencers ORDER BY id").fetchall()
    total = len(rows)
    print(f"开始迁移 influencers，共 {total} 条记录...")

    with pg_conn.cursor() as cursor:
        for index, row in enumerate(rows, start=1):
            row_keys = row.keys()
            avatar_blob = row["avatar_blob"] if "avatar_blob" in row_keys else None
            if avatar_blob is not None:
                avatar_blob = psycopg2.Binary(avatar_blob)

            cursor.execute(
                """
                INSERT INTO influencers (
                    id, name, email, social_links, shipping_address,
                    phone, tags, avatar_blob, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row["id"],
                    row["name"],
                    row["email"],
                    row["social_links"],
                    row["shipping_address"],
                    row["phone"],
                    row["tags"],
                    avatar_blob,
                    row["created_at"],
                ),
            )
            print(f"  influencers 进度: {index}/{total}")

    reset_serial_sequence(pg_conn, "influencers")
    print(f"influencers 迁移完成: {total} 条")
    return total


def migrate_collaborations(
    sqlite_conn: sqlite3.Connection,
    pg_conn: psycopg2.extensions.connection,
) -> int:
    """Migrate all collaboration rows from SQLite to Supabase."""
    rows = sqlite_conn.execute("SELECT * FROM collaborations ORDER BY id").fetchall()
    total = len(rows)
    print(f"开始迁移 collaborations，共 {total} 条记录...")

    with pg_conn.cursor() as cursor:
        for index, row in enumerate(rows, start=1):
            row_keys = row.keys()
            order_number = row["order_number"] if "order_number" in row_keys else None
            cursor.execute(
                """
                INSERT INTO collaborations (
                    id, influencer_id, product_id, status, tracking_number,
                    order_number, chat_history, assigned_to,
                    last_interaction_date, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row["id"],
                    row["influencer_id"],
                    row["product_id"],
                    row["status"],
                    row["tracking_number"],
                    order_number,
                    row["chat_history"],
                    row["assigned_to"],
                    row["last_interaction_date"],
                    row["created_at"],
                ),
            )
            print(f"  collaborations 进度: {index}/{total}")

    reset_serial_sequence(pg_conn, "collaborations")
    print(f"collaborations 迁移完成: {total} 条")
    return total


def main() -> None:
    """Run the full SQLite -> Supabase migration pipeline."""
    print("正在初始化 Supabase 表结构...")
    CRMDatabaseManager._instance = None
    CRMDatabaseManager()

    sqlite_conn = connect_sqlite()
    pg_conn = connect_supabase()

    try:
        print("正在连接 Supabase 并清空云端旧数据...")
        clear_remote_tables(pg_conn)
        pg_conn.commit()

        counts: dict[str, int] = {}
        counts["products"] = migrate_products(sqlite_conn, pg_conn)
        pg_conn.commit()

        counts["influencers"] = migrate_influencers(sqlite_conn, pg_conn)
        pg_conn.commit()

        counts["collaborations"] = migrate_collaborations(sqlite_conn, pg_conn)
        pg_conn.commit()

        print(
            "迁移成功！"
            f" products={counts['products']},"
            f" influencers={counts['influencers']},"
            f" collaborations={counts['collaborations']}"
        )
    except Exception as exc:
        pg_conn.rollback()
        print(f"迁移失败: {exc}")
        raise
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()
