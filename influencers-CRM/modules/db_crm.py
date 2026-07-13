"""SQLite database layer for the Amazon influencer private-domain CRM system."""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Optional

# ---------------------------------------------------------------------------
# Database Configuration
# ---------------------------------------------------------------------------
# 自动检测是否在 Render 云端环境运行 (Render 会自动注入 RENDER=true 环境变量)
if "RENDER" in os.environ:
    # Render 的持久化磁盘挂载路径 (请确保在 Render 后台挂载了该目录)
    DB_DIR = Path("/opt/render/project/src/data")
    DB_DIR.mkdir(parents=True, exist_ok=True)  # 确保目录存在
    DB_FILE = str(DB_DIR / "influencer_crm.db")
else:
    # 本地运行时的默认路径
    DB_FILE = "influencer_crm.db"

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
    """Singleton manager for the influencer CRM SQLite database."""

    _instance: Optional[CRMDatabaseManager] = None
    _init_lock = threading.Lock()

    def __new__(
        cls, db_path: str | Path | None = None
    ) -> CRMDatabaseManager:
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance

    def __init__(self, db_path: str | Path | None = None) -> None:
        if self._initialized:
            return
        self.db_path = Path(db_path) if db_path else Path(DB_FILE)
        self._conn_lock = threading.Lock()
        self._initialize_database()
        self._initialized = True

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a thread-local connection with foreign keys enabled."""
        with self._conn_lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _initialize_database(self) -> None:
        """Create core tables if they do not exist."""
        with self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asin TEXT UNIQUE NOT NULL,          -- Amazon ASIN
                    name TEXT NOT NULL,                 -- Internal product alias
                    price REAL,                         -- Product price
                    selling_points TEXT,                -- Five bullet points & key selling points
                    pros_cons TEXT,                     -- Pros/cons for AI complaint handling
                    negotiation_strategy TEXT,          -- Negotiation baseline (commission, coupons)
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS influencers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,                 -- Influencer display name
                    email TEXT UNIQUE,                  -- Primary unique identifier
                    social_links TEXT,                  -- JSON string: YouTube/X/TikTok URLs
                    shipping_address TEXT,              -- Full shipping address
                    phone TEXT,                         -- Contact phone number
                    tags TEXT,                          -- Comma-separated tags
                    avatar_blob BLOB,                   -- Profile avatar binary data
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS collaborations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    influencer_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT '待沟通',  -- Status machine
                    tracking_number TEXT,               -- Logistics tracking number
                    order_number TEXT,                  -- Sample/order reference number
                    chat_history TEXT,                  -- Full message/email history for AI context
                    assigned_to TEXT,                   -- Owner (e.g. "运营A")
                    last_interaction_date TIMESTAMP,    -- Last communication timestamp
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (influencer_id)
                        REFERENCES influencers(id) ON DELETE CASCADE,
                    FOREIGN KEY (product_id)
                        REFERENCES products(id) ON DELETE CASCADE
                );
                """
            )
            self._migrate_schema(conn)

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        """Add new columns to existing databases without breaking legacy data."""
        influencer_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(influencers)").fetchall()
        }
        if "avatar_blob" not in influencer_cols:
            conn.execute("ALTER TABLE influencers ADD COLUMN avatar_blob BLOB")

        collaboration_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(collaborations)").fetchall()
        }
        if "order_number" not in collaboration_cols:
            conn.execute("ALTER TABLE collaborations ADD COLUMN order_number TEXT")

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
            cursor = conn.execute(
                """
                INSERT INTO products (
                    asin, name, price, selling_points,
                    pros_cons, negotiation_strategy
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (asin, name, price, selling_points, pros_cons, negotiation_strategy),
            )
            return int(cursor.lastrowid)

    def get_all_products(self) -> list[dict[str, Any]]:
        """Return all products ordered by creation time (newest first)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM products ORDER BY created_at DESC"
            ).fetchall()
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
                try:
                    conn.execute(
                        """
                        UPDATE influencers
                        SET name = ?,
                            email = ?,
                            social_links = ?,
                            shipping_address = ?,
                            phone = ?,
                            tags = ?,
                            avatar_blob = COALESCE(?, avatar_blob)
                        WHERE id = ?
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
                except sqlite3.IntegrityError as exc:
                    if "influencers.email" in str(exc):
                        conflicting = conn.execute(
                            """
                            SELECT id, name FROM influencers
                            WHERE email = ? AND id != ?
                            """,
                            (email, influencer_id),
                        ).fetchone()
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
                existing = conn.execute(
                    "SELECT id FROM influencers WHERE email = ?",
                    (email,),
                ).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE influencers
                        SET name = ?,
                            social_links = ?,
                            shipping_address = ?,
                            phone = ?,
                            tags = ?,
                            avatar_blob = COALESCE(?, avatar_blob)
                        WHERE id = ?
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
            cursor = conn.execute(
                """
                INSERT INTO influencers (
                    name, email, social_links,
                    shipping_address, phone, tags, avatar_blob
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (name, email, social_links, shipping_address, phone, tags, avatar_blob),
            )
            return int(cursor.lastrowid)

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
            cursor = conn.execute(
                """
                INSERT INTO collaborations (
                    influencer_id, product_id, status,
                    tracking_number, chat_history,
                    assigned_to, last_interaction_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
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
            return int(cursor.lastrowid)

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
            cursor = conn.execute(
                """
                UPDATE collaborations
                SET status = ?,
                    tracking_number = COALESCE(?, tracking_number),
                    order_number = COALESCE(?, order_number),
                    chat_history = COALESCE(?, chat_history),
                    assigned_to = COALESCE(?, assigned_to),
                    last_interaction_date = COALESCE(?, last_interaction_date)
                WHERE id = ?
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
            rows = conn.execute(
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
            ).fetchall()
            return [dict(row) for row in rows]

    def find_collaboration(
        self, influencer_id: int, product_id: int
    ) -> dict[str, Any] | None:
        """Return an existing collaboration for the given pair, if any."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM collaborations
                WHERE influencer_id = ? AND product_id = ?
                """,
                (influencer_id, product_id),
            ).fetchone()
            return dict(row) if row else None

    def delete_collaboration(self, collaboration_id: int) -> bool:
        """Delete a collaboration and orphan influencer if no other collaborations remain."""
        with self._get_connection() as conn:
            collab = conn.execute(
                "SELECT influencer_id FROM collaborations WHERE id = ?",
                (collaboration_id,),
            ).fetchone()
            if not collab:
                return False

            influencer_id = collab["influencer_id"]

            conn.execute(
                "DELETE FROM collaborations WHERE id = ?",
                (collaboration_id,),
            )

            remaining = conn.execute(
                "SELECT COUNT(*) AS count FROM collaborations WHERE influencer_id = ?",
                (influencer_id,),
            ).fetchone()
            if remaining["count"] == 0:
                conn.execute(
                    "DELETE FROM influencers WHERE id = ?",
                    (influencer_id,),
                )

            return True

    def cleanup_orphan_influencers(self) -> int:
        """Delete influencers that are not linked to any collaboration records."""
        with self._get_connection() as conn:
            cursor = conn.execute(
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
            cursor = conn.execute(
                "UPDATE products SET name = ? WHERE id = ?",
                (new_name.strip(), product_id),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _format_timestamp(value: datetime | str | None) -> str | None:
        """Normalize datetime values to ISO-8601 strings for SQLite storage."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat(sep=" ", timespec="seconds")
        return value
