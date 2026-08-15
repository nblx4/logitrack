"""
database.py
------------
SQLite schema, connection helper, and seed data for LogiTrack.

Mirrors the ERD from the Systems Paradigms assignment (Assignment 2, Fig. 8):
customers, orders, order_items, products, inventory, warehouses, suppliers,
product_suppliers, payments, shipments, returns -- plus two tables that
operationalise the Event-Driven Microservices architecture chosen in Task 1:

  users   -> authentication + role (customer / warehouse / admin)
  events  -> the event log (OrderPlaced, PaymentConfirmed, InventoryReserved,
             ShipmentDispatched, OrderDelivered, ReorderNeeded, ...) that the
             admin dashboard reads from, exactly like the "Event Broker" in
             Figure 2 / Figure 17 of Assignment 2.
"""

import sqlite3
import os
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logitrack.db")

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL CHECK(role IN ('customer','warehouse','admin')),
    full_name       TEXT NOT NULL,
    email           TEXT,
    phone           TEXT,
    address         TEXT,
    warehouse_id    INTEGER REFERENCES warehouses(warehouse_id),
    is_active       INTEGER NOT NULL DEFAULT 1,
    register_date   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS warehouses (
    warehouse_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_name  TEXT NOT NULL,
    location        TEXT,
    city            TEXT,
    country         TEXT,
    warehouse_type  TEXT
);

CREATE TABLE IF NOT EXISTS products (
    product_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name    TEXT NOT NULL,
    sku             TEXT UNIQUE NOT NULL,
    category        TEXT,
    unit_price      REAL NOT NULL,
    weight          REAL,
    product_status  TEXT DEFAULT 'Active'
);

CREATE TABLE IF NOT EXISTS inventory (
    inventory_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id         INTEGER NOT NULL REFERENCES products(product_id),
    warehouse_id       INTEGER NOT NULL REFERENCES warehouses(warehouse_id),
    quantity_on_hand   INTEGER NOT NULL DEFAULT 0,
    reorder_level      INTEGER NOT NULL DEFAULT 10,
    last_updated       TEXT DEFAULT (datetime('now')),
    UNIQUE(product_id, warehouse_id)
);

CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_name   TEXT NOT NULL,
    contact_person  TEXT,
    email           TEXT,
    phone           TEXT,
    address         TEXT,
    country         TEXT
);

CREATE TABLE IF NOT EXISTS product_suppliers (
    product_id      INTEGER NOT NULL REFERENCES products(product_id),
    supplier_id     INTEGER NOT NULL REFERENCES suppliers(supplier_id),
    cost_price      REAL,
    lead_time_days  INTEGER,
    PRIMARY KEY (product_id, supplier_id)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL REFERENCES users(user_id),
    order_date      TEXT DEFAULT (datetime('now')),
    order_status    TEXT NOT NULL DEFAULT 'Pending Payment',
    total_amount    REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS order_items (
    order_item_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL REFERENCES orders(order_id),
    product_id      INTEGER NOT NULL REFERENCES products(product_id),
    quantity        INTEGER NOT NULL,
    unit_price      REAL NOT NULL,
    subtotal        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id             INTEGER NOT NULL REFERENCES orders(order_id),
    payment_method       TEXT,
    amount               REAL NOT NULL,
    payment_status       TEXT DEFAULT 'Confirmed',
    payment_date         TEXT DEFAULT (datetime('now')),
    transaction_reference TEXT
);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id                INTEGER NOT NULL REFERENCES orders(order_id),
    warehouse_id            INTEGER REFERENCES warehouses(warehouse_id),
    delivery_provider       TEXT,
    tracking_number         TEXT,
    ship_date               TEXT,
    expected_delivery_date  TEXT,
    delivery_status         TEXT DEFAULT 'Preparing'
);

CREATE TABLE IF NOT EXISTS returns (
    return_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL REFERENCES orders(order_id),
    product_id      INTEGER NOT NULL REFERENCES products(product_id),
    quantity        INTEGER NOT NULL DEFAULT 1,
    return_date     TEXT DEFAULT (datetime('now')),
    return_reason   TEXT,
    return_status   TEXT DEFAULT 'Requested',
    refund_amount   REAL
);

CREATE TABLE IF NOT EXISTS reservations (
    reservation_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL REFERENCES orders(order_id),
    product_id      INTEGER NOT NULL REFERENCES products(product_id),
    warehouse_id    INTEGER NOT NULL REFERENCES warehouses(warehouse_id),
    quantity        INTEGER NOT NULL,
    released        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type   TEXT NOT NULL,
    order_id     INTEGER,
    product_id   INTEGER,
    warehouse_id INTEGER,
    actor_role   TEXT,
    description  TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def log_event(conn, event_type, order_id=None, product_id=None,
              warehouse_id=None, actor_role=None, description=""):
    """Central place every part of the app publishes an event from --
    the same role the Event Broker plays in the Event-Driven Microservices
    architecture (Assignment 2, Figure 2 / Figure 17)."""
    conn.execute(
        """INSERT INTO events (event_type, order_id, product_id, warehouse_id,
                                actor_role, description)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (event_type, order_id, product_id, warehouse_id, actor_role, description),
    )


def _migrate(conn):
    """Add columns that were introduced after a person's database was first created,
    so existing data (and its logitrack.db file) is never wiped to pick up a fix."""
    existing_user_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "is_active" not in existing_user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")

    existing_return_cols = {r["name"] for r in conn.execute("PRAGMA table_info(returns)")}
    if "quantity" not in existing_return_cols:
        conn.execute("ALTER TABLE returns ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1")


def init_db(reset=False):
    first_time = reset or not os.path.exists(DB_PATH)
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = get_db()
    conn.executescript(SCHEMA)
    _migrate(conn)

    already_seeded = conn.execute(
        "SELECT COUNT(*) c FROM users"
    ).fetchone()["c"] > 0

    if not already_seeded:
        seed(conn)

    conn.commit()
    conn.close()


def seed(conn):
    # --- Warehouses -------------------------------------------------
    warehouses = [
        ("Klang Valley Fulfilment Centre", "Shah Alam Industrial Park", "Shah Alam", "Malaysia", "Fulfilment"),
        ("Penang Regional Hub", "Bayan Lepas Free Zone", "Penang", "Malaysia", "Distribution"),
    ]
    conn.executemany(
        "INSERT INTO warehouses (warehouse_name, location, city, country, warehouse_type) VALUES (?,?,?,?,?)",
        warehouses,
    )

    # --- Suppliers ----------------------------------------------------
    suppliers = [
        ("Global Gadgets Manufacturing", "Wei Ling Tan", "wei.tan@ggm.example", "+60123456001", "Shenzhen Industrial Zone", "China"),
        ("Nordic Home Supplies", "Erik Johansson", "erik@nordichome.example", "+4670001122", "Gothenburg", "Sweden"),
        ("Everwell Apparel Co.", "Aisha Rahman", "aisha@everwell.example", "+60123456002", "Kuala Lumpur", "Malaysia"),
    ]
    conn.executemany(
        "INSERT INTO suppliers (supplier_name, contact_person, email, phone, address, country) VALUES (?,?,?,?,?,?)",
        suppliers,
    )

    # --- Products -------------------------------------------------------
    products = [
        ("Wireless Noise-Cancelling Headphones", "WH-001", "Electronics", 349.00, 0.28, "Active"),
        ("Smart Fitness Watch", "SW-002", "Electronics", 279.00, 0.06, "Active"),
        ("Everyday Canvas Backpack", "BP-003", "Fashion", 129.00, 0.9, "Active"),
        ("Running Shoes - Trail Edition", "RS-004", "Fashion", 219.00, 0.7, "Active"),
        ("Insulated Steel Water Bottle 750ml", "WB-005", "Home & Living", 49.00, 0.4, "Active"),
        ("Portable Bluetooth Speaker", "BS-006", "Electronics", 159.00, 0.5, "Active"),
        ("Ceramic Non-Stick Cookware Set", "CW-007", "Home & Living", 389.00, 4.2, "Active"),
        ("Ergonomic Office Chair", "OC-008", "Home & Living", 599.00, 14.0, "Active"),
    ]
    conn.executemany(
        "INSERT INTO products (product_name, sku, category, unit_price, weight, product_status) VALUES (?,?,?,?,?,?)",
        products,
    )

    # --- Inventory (per product per warehouse) --------------------------
    # (product_id, warehouse_id, qty, reorder_level)
    inventory = [
        (1, 1, 150, 30), (1, 2, 40, 15),
        (2, 1, 60, 20),  (2, 2, 20, 10),
        (3, 1, 210, 40), (3, 2, 90, 20),
        (4, 1, 35, 30),  (4, 2, 10, 15),   # low stock on purpose
        (5, 1, 320, 50), (5, 2, 150, 30),
        (6, 1, 15, 20),  (6, 2, 8, 15),    # low stock on purpose
        (7, 1, 70, 15),  (7, 2, 25, 10),
        (8, 1, 22, 10),  (8, 2, 5, 8),
    ]
    conn.executemany(
        "INSERT INTO inventory (product_id, warehouse_id, quantity_on_hand, reorder_level) VALUES (?,?,?,?)",
        inventory,
    )

    # --- Product <-> Supplier links --------------------------------------
    product_suppliers = [
        (1, 1, 210.0, 18), (2, 1, 160.0, 18), (6, 1, 95.0, 15),
        (5, 2, 22.0, 10), (7, 2, 240.0, 21), (8, 2, 340.0, 25),
        (3, 3, 60.0, 7), (4, 3, 120.0, 7),
    ]
    conn.executemany(
        "INSERT INTO product_suppliers (product_id, supplier_id, cost_price, lead_time_days) VALUES (?,?,?,?)",
        product_suppliers,
    )

    # --- Users: 1 admin, 2 warehouse staff, 3 sample customers -----------
    def pw(raw):
        return generate_password_hash(raw)

    conn.execute(
        """INSERT INTO users (username, password_hash, role, full_name, email, phone, address)
           VALUES (?,?,?,?,?,?,?)""",
        ("admin", pw("admin123"), "admin", "System Administrator",
         "admin@logitrack.example", "+60123000000", "MSU Head Office"),
    )
    conn.execute(
        """INSERT INTO users (username, password_hash, role, full_name, email, phone, address, warehouse_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("warehouse1", pw("warehouse123"), "warehouse", "Kirtiroshni Sankaran",
         "kirtiroshni@logitrack.example", "+60123000001", "Shah Alam Industrial Park", 1),
    )
    conn.execute(
        """INSERT INTO users (username, password_hash, role, full_name, email, phone, address, warehouse_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("warehouse2", pw("warehouse123"), "warehouse", "Randa Alhomid",
         "randa@logitrack.example", "+60123000002", "Bayan Lepas Free Zone", 2),
    )

    customers = [
        ("aina", "Aina Rahman", "aina@example.com", "0123456789", "Shah Alam, Selangor"),
        ("daniel", "Daniel Lee", "daniel@example.com", "0134567890", "Petaling Jaya, Selangor"),
        ("nurnabila", "Nurnabila Abdul Rahman", "nurnabila@example.com", "0129998887", "Kuching, Sarawak"),
    ]
    for uname, name, email, phone, address in customers:
        conn.execute(
            """INSERT INTO users (username, password_hash, role, full_name, email, phone, address)
               VALUES (?,?,?,?,?,?,?)""",
            (uname, pw("customer123"), "customer", name, email, phone, address),
        )

    conn.commit()

    # --- A couple of historical orders so dashboards aren't empty --------
    now = datetime.now()
    cust = conn.execute("SELECT user_id FROM users WHERE username='aina'").fetchone()["user_id"]

    order_date = (now - timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "INSERT INTO orders (customer_id, order_date, order_status, total_amount) VALUES (?,?,?,?)",
        (cust, order_date, "Delivered", 349.00),
    )
    order_id = cur.lastrowid
    conn.execute(
        "INSERT INTO order_items (order_id, product_id, quantity, unit_price, subtotal) VALUES (?,?,?,?,?)",
        (order_id, 1, 1, 349.00, 349.00),
    )
    conn.execute(
        "INSERT INTO payments (order_id, payment_method, amount, payment_status, transaction_reference) VALUES (?,?,?,?,?)",
        (order_id, "Credit Card", 349.00, "Confirmed", "TXN-DEMO-0001"),
    )
    ship_date = (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    exp_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    conn.execute(
        """INSERT INTO shipments (order_id, warehouse_id, delivery_provider, tracking_number,
                                   ship_date, expected_delivery_date, delivery_status)
           VALUES (?,?,?,?,?,?,?)""",
        (order_id, 1, "J&T Express", "LT-100001-MY", ship_date, exp_date, "Delivered"),
    )
    for etype, offset, desc in [
        ("OrderPlaced", 6, "Order placed by customer via web portal."),
        ("PaymentConfirmed", 6, "Payment confirmed via Credit Card."),
        ("InventoryReserved", 6, "Stock reserved at Klang Valley Fulfilment Centre."),
        ("ShipmentDispatched", 5, "Shipment handed to J&T Express."),
        ("OrderDelivered", 2, "Package delivered to customer."),
    ]:
        conn.execute(
            "INSERT INTO events (event_type, order_id, actor_role, description, created_at) VALUES (?,?,?,?,?)",
            (etype, order_id, "system", desc, (now - timedelta(days=offset)).strftime("%Y-%m-%d %H:%M:%S")),
        )

    conn.commit()


if __name__ == "__main__":
    init_db(reset=True)
    print(f"Database initialised at {DB_PATH}")
