# LogiTrack — Decentralised E-Commerce Logistics System

A working reference implementation of the system described in the *Systems
Paradigms* Assignment 2 report: three role-based accounts (**customer**,
**warehouse**, **admin**) on top of a **SQLite** database, built with
**Flask** + server-rendered HTML (no build step, no Node required).

Every state change — an order placed, a payment confirmed, stock reserved,
a shipment dispatched, a delivery confirmed, stock received, a return
processed — is written to an `events` table and streamed to the admin
**Event Log**, mirroring the Event-Driven Microservices architecture chosen
in Task 1 of the report (Order Service → Event Broker → Inventory /
Fulfilment / Notification / Returns services).

## What each account can do

| Role | Capabilities |
|---|---|
| **Customer** | Register/login, browse the catalogue, add to cart, check out (simulated instant payment), view order history, track shipments on a live event timeline, request returns on delivered items. |
| **Warehouse** | View the pick-and-pack queue, mark orders picked/packed/dispatched (creates a shipment + tracking number), confirm delivery, view/receive inventory (restock from a supplier), approve or reject return requests. |
| **Admin** | System-wide KPI dashboard, full event log (filterable by event type), inventory across all warehouses, reports (order status mix, 14-day order volume, top products, low-stock report, returns summary), user directory. |

## 1. Prerequisites

- Python 3.9 or newer
- VS Code with the Python extension (recommended, not required)

No separate database server is needed — SQLite stores everything in a
single file, `logitrack.db`, created automatically the first time you run
the app.

## 2. Setup (in VS Code's integrated terminal)

```bash
# 1. Open this folder in VS Code:  File → Open Folder… → logitrack

# 2. (Recommended) create a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Initialise the SQLite database (creates logitrack.db and seed data)
python database.py

# 5. Run the app
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

To reset the database at any point (wipes all orders/events and reloads
the seed data), run `python database.py` again — it only re-seeds if
`logitrack.db` doesn't already exist. To force a full reset:

```python
# from a Python shell, or add reset=True temporarily in app.py's __main__
import database
database.init_db(reset=True)
```

## 3. Demo accounts

| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `admin123` |
| Warehouse (Klang Valley Fulfilment Centre) | `warehouse1` | `warehouse123` |
| Warehouse (Penang Regional Hub) | `warehouse2` | `warehouse123` |
| Customer | `aina` | `customer123` |
| Customer | `daniel` | `customer123` |
| Customer | `nurnabila` | `customer123` |

New customers can also self-register from the login screen.

## 4. How the order lifecycle maps to the report

1. **Customer places order** → `OrderPlaced` event.
2. **Payment is confirmed** (simulated instant gateway) → `PaymentConfirmed`
   event, matching Section 3.2 of Assignment 2's sequence diagram.
3. **Inventory Service reserves stock** across warehouses → `InventoryReserved`
   events per line item; if a warehouse's stock falls to/below its reorder
   level, a `ReorderNeeded` event fires automatically (the "B1: Inventory
   Replenishment Loop" from Assignment 1).
4. If stock can't fully cover the order, it's flagged **Insufficient Stock**
   and a `BackorderFlagged` event is logged (this is the policy-resistance
   scenario discussed in Assignment 2, Section 4.1).
5. **Warehouse staff** pick, pack and dispatch the order → `ShipmentDispatched`
   event with a generated tracking number and carrier.
6. **Warehouse staff** confirm delivery → `OrderDelivered` event.
7. **Customer** can request a **return** on a delivered item → `ReturnRequested`;
   warehouse approves/rejects → `ReturnProcessed`, and approved returns are
   added back into inventory.

The **admin Event Log** (`/admin/events`) is the single place all of this is
observable in real time — the "Independent Databases, Audit Logs &
Monitoring Systems" layer from Figure 3 of Assignment 2.

## 5. Project structure

```
logitrack/
├── app.py              # Flask routes: auth + customer/warehouse/admin blueVprints
├── database.py          # SQLite schema, seed data, event-logging helper
├── requirements.txt
├── logitrack.db          # created on first run (SQLite file)
├── static/
│   └── style.css
└── templates/
    ├── base.html
    ├── _macros.html
    ├── auth/            # login, register
    ├── customer/        # dashboard, products, cart, orders, order_detail
    ├── warehouse/        # dashboard, orders, order_detail, inventory, returns
    └── admin/            # dashboard, events, inventory, reports, users
```

## 6. Notes / things you can extend

- Payment is simulated (no real gateway) — it's confirmed instantly on
  checkout, as noted on the cart page.
- Inventory reservation is a simple greedy allocator across warehouses,
  ordered by whichever warehouse has the most stock.
- The `events` table is intentionally generic (`event_type`, `order_id`,
  `product_id`, `warehouse_id`, `actor_role`, `description`) so new event
  types can be added without a schema change — the same flexibility the
  Event Broker gives the microservices architecture in the report.
