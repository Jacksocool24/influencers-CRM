"""PostgreSQL (Supabase) database layer for the Amazon influencer CRM system."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Generator, Optional

import psycopg2
import psycopg2.extras
import streamlit as st
from psycopg2 import errorcodes
from psycopg2.extensions import connection as PgConnection
from psycopg2.extensions import cursor as PgCursor

# Collaboration status machine values
COLLAB_STATUS_PENDING = "待沟通"
COLLAB_STATUS_SHIPPING = "样品寄送中"
COLLAB_STATUS_RECEIVED = "已签收"
COLLAB_STATUS_PUBLISHED = "视频已上线"
COLLAB_STATUS_TERMINATED = "合作终止"

VALID_COLLAB_STATUSES = frozenset(
    {
        COLLAB_STATUS_PENDING,
        COLLAB_STATUS_SHIPPING,
        COLLAB_STATUS_RECEIVED,
        COLLAB_STATUS_PUBLISHED,
        COLLAB_STATUS_TERMINATED,
    }
)


class CRMDatabaseManager:
    """Singleton manager for the influencer CRM Supabase PostgreSQL database."""

    _instance: Optional["CRMDatabaseManager"] = None
    _init_lock = threading.Lock()

    def __new__(cls, db_path: Any = None) -> "CRMDatabaseManager":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance

    def __init__(self, db_path: Any = None) -> None:
        if self._initialized:
            return
        self._conn_lock = threading.Lock()
        self._initialize_database()
        self._initialized = True

    @contextmanager
    def _get_connection(self) -> Generator[PgConnection, None, None]:
        """Yield a PostgreSQL connection backed by Supabase credentials."""
        with self._conn_lock:
            conn = psycopg2.connect(st.secrets["SUPABASE_URL"])
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    @contextmanager
    def _get_cursor(
        self,
        conn: PgConnection,
    ) -> Generator[PgCursor, None, None]:
        """Yield a RealDictCursor so rows behave like dict records."""
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            yield cursor

    def _initialize_database(self) -> None:
        """Create core tables if they do not exist."""
        ddl_statements = (
            """
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                asin TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                price REAL,
                selling_points TEXT,
                pros_cons TEXT,
                negotiation_strategy TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS influencers (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE,
                social_links TEXT,
                shipping_address TEXT,
                phone TEXT,
                tags TEXT,
                avatar_blob BYTEA,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS collaborations (
                id SERIAL PRIMARY KEY,
                influencer_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT '待沟通',
                tracking_number TEXT,
                order_number TEXT,
                chat_history TEXT,
                assigned_to TEXT,
                last_interaction_date TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (influencer_id)
                    REFERENCES influencers(id) ON DELETE CASCADE,
                FOREIGN KEY (product_id)
                    REFERENCES products(id) ON DELETE CASCADE
            )
            """,
        )

        with self._get_connection() as conn:
            with self._get_cursor(conn) as cursor:
                for statement in ddl_statements:
                    cursor.execute(statement)

    # ------------------------------------------------------------------
    # products
    # ------------------------------------------------------------------

    def add_product(
        self,
        asin: str,
        name: str,
        price: float | None = None,
        selling_points: str | None = None,
        pros_cons: str | None = None,
        negotiation_strategy: str | None = None,
    ) -> int:
        """Insert a product record and return its primary key."""
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cursor:
                cursor.execute(
                    """
                    INSERT INTO products (
                        asin, name, price, selling_points,
                        pros_cons, negotiation_strategy
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (asin, name, price, selling_points, pros_cons, negotiation_strategy),
                )
                row = cursor.fetchone()
                return int(row["id"])

    def get_all_products(self) -> list[dict[str, Any]]:
        """Return all products ordered by creation time (newest first)."""
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cursor:
                cursor.execute(
                    "SELECT * FROM products ORDER BY created_at DESC"
                )
                rows = cursor.fetchall()
                return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # influencers
    # ------------------------------------------------------------------

    def upsert_influencer(
        self,
        name: str,
        email: str | None = None,
        social_links: str | None = None,
        shipping_address: str | None = None,
        phone: str | None = None,
        tags: str | None = None,
        avatar_blob: bytes | None = None,
        influencer_id: int | None = None,
    ) -> int:
        """
        Insert or update an influencer record.

        When influencer_id is provided, updates that exact row (dashboard saves).
        Otherwise matches by email, or inserts a new record.
        Returns the influencer primary key.
        """
        name = name.strip()
        email = email.strip() if email else None
        social_links = social_links.strip() if social_links else None
        shipping_address = shipping_address.strip() if shipping_address else None
        phone = phone.strip() if phone else None
        tags = tags.strip() if tags else None

        if influencer_id:
            with self._get_connection() as conn:
                with self._get_cursor(conn) as cursor:
                    try:
                        cursor.execute(
                            """
                            UPDATE influencers
                            SET name = %s,
                                email = %s,
                                social_links = %s,
                                shipping_address = %s,
                                phone = %s,
                                tags = %s,
                                avatar_blob = COALESCE(%s, avatar_blob)
                            WHERE id = %s
                            """,
                            (
                                name,
                                email,
                                social_links,
                                shipping_address,
                                phone,
                                tags,
                                avatar_blob,
                                influencer_id,
                            ),
                        )
                    except psycopg2.IntegrityError as exc:
                        if email and self._is_email_unique_violation(exc):
                            conflicting = self._find_conflicting_influencer_by_email(
                                cursor,
                                email,
                                influencer_id,
                            )
                            if conflicting:
                                raise ValueError(
                                    f"邮箱 '{email}' 已被红人 '{conflicting['name']}' "
                                    f"(ID: {conflicting['id']}) 使用，"
                                    "请检查是否填写错误或需合并记录。"
                                ) from exc
                        raise
                return int(influencer_id)

        if email:
            with self._get_connection() as conn:
                with self._get_cursor(conn) as cursor:
                    cursor.execute(
                        "SELECT id FROM influencers WHERE email = %s",
                        (email,),
                    )
                    existing = cursor.fetchone()
                    if existing:
                        cursor.execute(
                            """
                            UPDATE influencers
                            SET name = %s,
                                social_links = %s,
                                shipping_address = %s,
                                phone = %s,
                                tags = %s,
                                avatar_blob = COALESCE(%s, avatar_blob)
                            WHERE id = %s
                            """,
                            (
                                name,
                                social_links,
                                shipping_address,
                                phone,
                                tags,
                                avatar_blob,
                                existing["id"],
                            ),
                        )
                        return int(existing["id"])

        with self._get_connection() as conn:
            with self._get_cursor(conn) as cursor:
                cursor.execute(
                    """
                    INSERT INTO influencers (
                        name, email, social_links,
                        shipping_address, phone, tags, avatar_blob
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (name, email, social_links, shipping_address, phone, tags, avatar_blob),
                )
                row = cursor.fetchone()
                return int(row["id"])

    # ------------------------------------------------------------------
    # collaborations
    # ------------------------------------------------------------------

    def create_collaboration(
        self,
        influencer_id: int,
        product_id: int,
        status: str = COLLAB_STATUS_PENDING,
        tracking_number: str | None = None,
        chat_history: str | None = None,
        assigned_to: str | None = None,
        last_interaction_date: datetime | str | None = None,
    ) -> int:
        """Create a collaboration record linking an influencer to a product."""
        if status not in VALID_COLLAB_STATUSES:
            raise ValueError(
                f"Invalid status '{status}'. "
                f"Must be one of: {', '.join(sorted(VALID_COLLAB_STATUSES))}"
            )

        interaction_ts = self._format_timestamp(last_interaction_date)

        with self._get_connection() as conn:
            with self._get_cursor(conn) as cursor:
                cursor.execute(
                    """
                    INSERT INTO collaborations (
                        influencer_id, product_id, status,
                        tracking_number, chat_history,
                        assigned_to, last_interaction_date
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        influencer_id,
                        product_id,
                        status,
                        tracking_number,
                        chat_history,
                        assigned_to,
                        interaction_ts,
                    ),
                )
                row = cursor.fetchone()
                return int(row["id"])

    def update_collaboration_status(
        self,
        collaboration_id: int,
        status: str,
        tracking_number: str | None = None,
        order_number: str | None = None,
        chat_history: str | None = None,
        assigned_to: str | None = None,
        last_interaction_date: datetime | str | None = None,
    ) -> bool:
        """
        Update collaboration status and optionally refresh related fields.

        Returns True if a row was updated, False if the ID was not found.
        """
        if status not in VALID_COLLAB_STATUSES:
            raise ValueError(
                f"Invalid status '{status}'. "
                f"Must be one of: {', '.join(sorted(VALID_COLLAB_STATUSES))}"
            )

        interaction_ts = self._format_timestamp(last_interaction_date)

        with self._get_connection() as conn:
            with self._get_cursor(conn) as cursor:
                cursor.execute(
                    """
                    UPDATE collaborations
                    SET status = %s,
                        tracking_number = COALESCE(%s, tracking_number),
                        order_number = COALESCE(%s, order_number),
                        chat_history = COALESCE(%s, chat_history),
                        assigned_to = COALESCE(%s, assigned_to),
                        last_interaction_date = COALESCE(%s, last_interaction_date)
                    WHERE id = %s
                    """,
                    (
                        status,
                        tracking_number,
                        order_number,
                        chat_history,
                        assigned_to,
                        interaction_ts,
                        collaboration_id,
                    ),
                )
                return cursor.rowcount > 0

    def get_all_collaborations(self) -> list[dict[str, Any]]:
        """Return collaborations joined with influencer and product details."""
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cursor:
                cursor.execute(
                    """
                    SELECT
                        c.id,
                        c.status,
                        c.tracking_number,
                        c.order_number,
                        c.chat_history,
                        c.assigned_to,
                        c.last_interaction_date,
                        c.created_at,
                        i.id AS influencer_id,
                        i.name AS influencer_name,
                        i.email AS influencer_email,
                        i.phone AS influencer_phone,
                        i.tags AS influencer_tags,
                        i.shipping_address AS influencer_shipping_address,
                        i.social_links AS influencer_social_links,
                        i.avatar_blob AS influencer_avatar_blob,
                        p.id AS product_id,
                        p.asin AS product_asin,
                        p.name AS product_name
                    FROM collaborations c
                    JOIN influencers i ON c.influencer_id = i.id
                    JOIN products p ON c.product_id = p.id
                    ORDER BY c.last_interaction_date DESC NULLS LAST,
                             c.created_at DESC
                    """
                )
                rows = cursor.fetchall()
                return [dict(row) for row in rows]

    def find_collaboration(
        self, influencer_id: int, product_id: int
    ) -> dict[str, Any] | None:
        """Return an existing collaboration for the given pair, if any."""
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cursor:
                cursor.execute(
                    """
                    SELECT * FROM collaborations
                    WHERE influencer_id = %s AND product_id = %s
                    """,
                    (influencer_id, product_id),
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    def delete_collaboration(self, collaboration_id: int) -> bool:
        """Delete a collaboration and orphan influencer if no other collaborations remain."""
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cursor:
                cursor.execute(
                    "SELECT influencer_id FROM collaborations WHERE id = %s",
                    (collaboration_id,),
                )
                collab = cursor.fetchone()
                if not collab:
                    return False

                influencer_id = collab["influencer_id"]

                cursor.execute(
                    "DELETE FROM collaborations WHERE id = %s",
                    (collaboration_id,),
                )

                cursor.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM collaborations
                    WHERE influencer_id = %s
                    """,
                    (influencer_id,),
                )
                remaining = cursor.fetchone()
                if remaining and remaining["count"] == 0:
                    cursor.execute(
                        "DELETE FROM influencers WHERE id = %s",
                        (influencer_id,),
                    )

                return True

    def cleanup_orphan_influencers(self) -> int:
        """Delete influencers that are not linked to any collaboration records."""
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cursor:
                cursor.execute(
                    """
                    DELETE FROM influencers
                    WHERE id NOT IN (
                        SELECT influencer_id FROM collaborations
                    )
                    """
                )
                return cursor.rowcount

    def update_product_alias(self, product_id: int, new_name: str) -> bool:
        """Update the internal alias (name) of a product."""
        if not new_name.strip():
            return False
        with self._get_connection() as conn:
            with self._get_cursor(conn) as cursor:
                cursor.execute(
                    "UPDATE products SET name = %s WHERE id = %s",
                    (new_name.strip(), product_id),
                )
                return cursor.rowcount > 0

    @staticmethod
    def _format_timestamp(value: datetime | str | None) -> str | None:
        """Normalize datetime values to ISO-8601 strings for PostgreSQL storage."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="seconds")
        return value

    @staticmethod
    def _is_email_unique_violation(exc: psycopg2.IntegrityError) -> bool:
        """Detect unique-constraint failures on the influencers.email column."""
        if exc.pgcode == errorcodes.UNIQUE_VIOLATION:
            return True
        message = str(exc).lower()
        return "email" in message and "unique" in message

    @staticmethod
    def _find_conflicting_influencer_by_email(
        cursor: PgCursor,
        email: str,
        influencer_id: int,
    ) -> dict[str, Any] | None:
        """Return the influencer row that already owns the given email."""
        cursor.execute(
            """
            SELECT id, name FROM influencers
            WHERE email = %s AND id != %s
            """,
            (email, influencer_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
