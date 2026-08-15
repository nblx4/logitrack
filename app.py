"""
LogiTrack - Decentralised E-Commerce Logistics System
=======================================================
A working reference implementation of the architecture proposed in the
Systems Paradigms Assignment 2 report: three role-based accounts (customer,
warehouse, admin) sitting on top of a SQLite database, with every state
change published to an `events` table the same way the report's chosen
Event-Driven Microservices Architecture publishes OrderPlaced,
PaymentConfirmed, InventoryReserved, ShipmentDispatched and OrderDelivered
events (see Assignment 2, Figure 2 & Figure 17).

Run with:  python app.py
Then open: http://127.0.0.1:5000
"""

import random
import string
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for,
                    session, flash, g)
from werkzeug.security import generate_password_hash, check_password_hash

import database as db

app = Flask(__name__)
app.secret_key = "logitrack-dev-secret-change-me"

CARRIERS = ["J&T Express", "Pos Laju", "DHL Express", "City-Link Express", "Ninja Van"]


# ----------------------------------------------------------------------
# Bootstrapping / request lifecycle
# ----------------------------------------------------------------------
@app.before_request
def load_logged_in_user():
    g.db = db.get_db()
    user_id = session.get("user_id")
    g.user = None
    if user_id:
        g.user = g.db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if g.user and not g.user["is_active"]:
            session.clear()
            g.user = None


@app.teardown_appcontext
def close_db(exception=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@app.context_processor
def inject_globals():
    return {"current_user": g.get("user"), "now": datetime.now()}


def login_required(role=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            if role and g.user["role"] != role:
                flash("You don't have access to that area.", "error")
                return redirect(url_for("home"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def gen_tracking_number(order_id):
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    return f"LT-{order_id:05d}-{suffix}"


def gen_transaction_ref():
    return "TXN-" + "".join(random.choices(string.digits, k=10))


# ----------------------------------------------------------------------
# Home / Auth
# ----------------------------------------------------------------------
@app.route("/")
def home():
    if not g.user:
        return redirect(url_for("login"))
    return redirect(url_for(f"{g.user['role']}_dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("home"))
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = g.db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            if not user["is_active"]:
                flash("This account has been deactivated. Contact an administrator.", "error")
                return render_template("auth/login.html")
            session.clear()
            session["user_id"] = user["user_id"]
            flash(f"Welcome back, {user['full_name']}.", "success")
            return redirect(url_for(f"{user['role']}_dashboard"))
        flash("Incorrect username or password.", "error")
    return render_template("auth/login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("home"))
    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        username = request.form["username"].strip()
        email = request.form["email"].strip()
        phone = request.form["phone"].strip()
        address = request.form["address"].strip()
        password = request.form["password"]

        error = None
        if not (full_name and username and password):
            error = "Name, username and password are required."
        elif g.db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            error = "That username is already taken."

        if error:
            flash(error, "error")
        else:
            g.db.execute(
                """INSERT INTO users (username, password_hash, role, full_name, email, phone, address)
                   VALUES (?,?,?,?,?,?,?)""",
                (username, generate_password_hash(password), "customer",
                 full_name, email, phone, address),
            )
            g.db.commit()
            flash("Account created. You can log in now.", "success")
            return redirect(url_for("login"))
    return render_template("auth/register.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You've been logged out.", "success")
    return redirect(url_for("login"))


# ========================================================================
# CUSTOMER
# ========================================================================
@app.route("/customer")
@login_required("customer")
def customer_dashboard():
    orders = g.db.execute(
        """SELECT * FROM orders WHERE customer_id=? ORDER BY order_date DESC LIMIT 5""",
        (g.user["user_id"],),
    ).fetchall()
    stats = g.db.execute(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN order_status NOT IN ('Delivered','Cancelled') THEN 1 ELSE 0 END) active,
                  SUM(CASE WHEN order_status='Delivered' THEN 1 ELSE 0 END) delivered
           FROM orders WHERE customer_id=?""",
        (g.user["user_id"],),
    ).fetchone()
    featured = g.db.execute(
        """SELECT p.*, COALESCE(SUM(i.quantity_on_hand),0) stock
           FROM products p LEFT JOIN inventory i ON i.product_id=p.product_id
           WHERE p.product_status='Active' GROUP BY p.product_id LIMIT 4"""
    ).fetchall()
    return render_template("customer/dashboard.html", orders=orders, stats=stats, featured=featured)


@app.route("/customer/track", methods=["GET"])
@login_required("customer")
def customer_track_shipment():
    tracking_number = request.args.get("tracking_number", "").strip()
    if not tracking_number:
        flash("Enter a tracking number to search.", "error")
        return redirect(url_for("customer_dashboard"))

    shipment = g.db.execute(
        """SELECT s.* FROM shipments s JOIN orders o ON o.order_id = s.order_id
           WHERE s.tracking_number = ? AND o.customer_id = ?""",
        (tracking_number, g.user["user_id"]),
    ).fetchone()
    if not shipment:
        flash(f"No shipment found on your account with tracking number '{tracking_number}'.", "error")
        return redirect(url_for("customer_dashboard"))
    return redirect(url_for("customer_order_detail", order_id=shipment["order_id"]))


@app.route("/customer/products")
@login_required("customer")
def customer_products():
    q = request.args.get("q", "").strip()
    sql = """SELECT p.*, COALESCE(SUM(i.quantity_on_hand),0) stock
              FROM products p LEFT JOIN inventory i ON i.product_id = p.product_id
              WHERE p.product_status='Active' """
    params = []
    if q:
        sql += " AND (p.product_name LIKE ? OR p.category LIKE ?) "
        params += [f"%{q}%", f"%{q}%"]
    sql += " GROUP BY p.product_id ORDER BY p.category, p.product_name"
    products = g.db.execute(sql, params).fetchall()
    cart = session.get("cart", {})
    return render_template("customer/products.html", products=products, q=q, cart_count=sum(cart.values()))


@app.route("/customer/cart/add/<int:product_id>", methods=["POST"])
@login_required("customer")
def cart_add(product_id):
    qty = max(1, int(request.form.get("quantity", 1)))
    cart = session.get("cart", {})
    cart[str(product_id)] = cart.get(str(product_id), 0) + qty
    session["cart"] = cart
    flash("Added to cart.", "success")
    return redirect(request.referrer or url_for("customer_products"))


@app.route("/customer/cart/remove/<int:product_id>", methods=["POST"])
@login_required("customer")
def cart_remove(product_id):
    cart = session.get("cart", {})
    cart.pop(str(product_id), None)
    session["cart"] = cart
    return redirect(url_for("customer_cart"))


@app.route("/customer/cart")
@login_required("customer")
def customer_cart():
    cart = session.get("cart", {})
    items, total = [], 0.0
    for pid, qty in cart.items():
        p = g.db.execute("SELECT * FROM products WHERE product_id=?", (pid,)).fetchone()
        if not p:
            continue
        stock = g.db.execute(
            "SELECT COALESCE(SUM(quantity_on_hand),0) s FROM inventory WHERE product_id=?", (pid,)
        ).fetchone()["s"]
        subtotal = p["unit_price"] * qty
        total += subtotal
        items.append({"product": p, "qty": qty, "subtotal": subtotal, "stock": stock})
    return render_template("customer/cart.html", items=items, total=total)


@app.route("/customer/checkout", methods=["POST"])
@login_required("customer")
def customer_checkout():
    cart = session.get("cart", {})
    if not cart:
        flash("Your cart is empty.", "error")
        return redirect(url_for("customer_products"))

    conn = g.db
    total = 0.0
    line_items = []
    for pid, qty in cart.items():
        p = conn.execute("SELECT * FROM products WHERE product_id=?", (pid,)).fetchone()
        if not p:
            continue
        subtotal = p["unit_price"] * qty
        total += subtotal
        line_items.append((int(pid), qty, p["unit_price"], subtotal))

    # 1. ORDER PLACED --------------------------------------------------
    cur = conn.execute(
        "INSERT INTO orders (customer_id, order_status, total_amount) VALUES (?,?,?)",
        (g.user["user_id"], "Pending Payment", total),
    )
    order_id = cur.lastrowid
    for pid, qty, price, subtotal in line_items:
        conn.execute(
            "INSERT INTO order_items (order_id, product_id, quantity, unit_price, subtotal) VALUES (?,?,?,?,?)",
            (order_id, pid, qty, price, subtotal),
        )
    db.log_event(conn, "OrderPlaced", order_id=order_id, actor_role="customer",
                 description=f"Customer {g.user['full_name']} placed order #{order_id} "
                             f"({len(line_items)} item(s), RM {total:,.2f}).")

    # 2. PAYMENT CONFIRMED (simulated instant gateway) ------------------
    conn.execute(
        """INSERT INTO payments (order_id, payment_method, amount, payment_status, transaction_reference)
           VALUES (?,?,?,?,?)""",
        (order_id, request.form.get("payment_method", "Credit Card"), total, "Confirmed", gen_transaction_ref()),
    )
    db.log_event(conn, "PaymentConfirmed", order_id=order_id, actor_role="system",
                 description=f"Payment of RM {total:,.2f} confirmed for order #{order_id}.")

    # 3. INVENTORY RESERVATION ------------------------------------------
    fully_reserved = True
    for pid, qty, price, subtotal in line_items:
        remaining = qty
        rows = conn.execute(
            "SELECT * FROM inventory WHERE product_id=? AND quantity_on_hand > 0 ORDER BY quantity_on_hand DESC",
            (pid,),
        ).fetchall()
        for row in rows:
            if remaining <= 0:
                break
            take = min(remaining, row["quantity_on_hand"])
            new_qty = row["quantity_on_hand"] - take
            conn.execute(
                "UPDATE inventory SET quantity_on_hand=?, last_updated=datetime('now') WHERE inventory_id=?",
                (new_qty, row["inventory_id"]),
            )
            remaining -= take
            conn.execute(
                "INSERT INTO reservations (order_id, product_id, warehouse_id, quantity) VALUES (?,?,?,?)",
                (order_id, pid, row["warehouse_id"], take),
            )
            db.log_event(conn, "InventoryReserved", order_id=order_id, product_id=pid,
                         warehouse_id=row["warehouse_id"], actor_role="system",
                         description=f"Reserved {take} unit(s) of product #{pid} at warehouse "
                                     f"#{row['warehouse_id']} for order #{order_id}.")
            if new_qty <= row["reorder_level"]:
                db.log_event(conn, "ReorderNeeded", product_id=pid, warehouse_id=row["warehouse_id"],
                             actor_role="system",
                             description=f"Stock for product #{pid} at warehouse #{row['warehouse_id']} "
                                         f"fell to {new_qty} units (reorder level {row['reorder_level']}).")
        if remaining > 0:
            fully_reserved = False
            db.log_event(conn, "BackorderFlagged", order_id=order_id, product_id=pid, actor_role="system",
                         description=f"Insufficient stock for product #{pid} on order #{order_id}: "
                                     f"{remaining} unit(s) short.")

    order_status = "Processing" if fully_reserved else "Insufficient Stock"
    conn.execute("UPDATE orders SET order_status=? WHERE order_id=?", (order_status, order_id))
    conn.commit()

    session["cart"] = {}
    flash(f"Order #{order_id} placed successfully!", "success")
    return redirect(url_for("customer_order_detail", order_id=order_id))


@app.route("/customer/orders")
@login_required("customer")
def customer_orders():
    orders = g.db.execute(
        "SELECT * FROM orders WHERE customer_id=? ORDER BY order_date DESC", (g.user["user_id"],)
    ).fetchall()
    return render_template("customer/orders.html", orders=orders)


@app.route("/customer/orders/<int:order_id>")
@login_required("customer")
def customer_order_detail(order_id):
    order = g.db.execute(
        "SELECT * FROM orders WHERE order_id=? AND customer_id=?", (order_id, g.user["user_id"])
    ).fetchone()
    if not order:
        flash("Order not found.", "error")
        return redirect(url_for("customer_orders"))
    items = g.db.execute(
        """SELECT oi.*, p.product_name, p.sku FROM order_items oi
           JOIN products p ON p.product_id=oi.product_id WHERE oi.order_id=?""",
        (order_id,),
    ).fetchall()
    shipment = g.db.execute(
        "SELECT s.*, w.warehouse_name FROM shipments s LEFT JOIN warehouses w ON w.warehouse_id=s.warehouse_id "
        "WHERE order_id=?", (order_id,)
    ).fetchone()
    timeline = g.db.execute(
        "SELECT * FROM events WHERE order_id=? ORDER BY created_at ASC, event_id ASC", (order_id,)
    ).fetchall()
    returns = g.db.execute(
        "SELECT r.*, p.product_name FROM returns r JOIN products p ON p.product_id=r.product_id "
        "WHERE r.order_id=?", (order_id,)
    ).fetchall()
    returned_qty = {}
    for r in returns:
        if r["return_status"] != "Rejected":
            returned_qty[r["product_id"]] = returned_qty.get(r["product_id"], 0) + r["quantity"]
    remaining_returnable = {
        it["product_id"]: it["quantity"] - returned_qty.get(it["product_id"], 0) for it in items
    }
    return render_template("customer/order_detail.html", order=order, items=items, shipment=shipment,
                           timeline=timeline, returns=returns, remaining_returnable=remaining_returnable)


@app.route("/customer/orders/<int:order_id>/return", methods=["POST"])
@login_required("customer")
def customer_request_return(order_id):
    order = g.db.execute(
        "SELECT * FROM orders WHERE order_id=? AND customer_id=?", (order_id, g.user["user_id"])
    ).fetchone()
    if not order or order["order_status"] != "Delivered":
        flash("Returns can only be requested for delivered orders.", "error")
        return redirect(url_for("customer_order_detail", order_id=order_id))

    product_id = int(request.form["product_id"])
    reason = request.form.get("reason", "").strip() or "No reason provided"
    item = g.db.execute(
        "SELECT * FROM order_items WHERE order_id=? AND product_id=?", (order_id, product_id)
    ).fetchone()
    if not item:
        flash("That item isn't part of this order.", "error")
        return redirect(url_for("customer_order_detail", order_id=order_id))

    # A customer may return anywhere from 1 up to the quantity they actually bought,
    # minus whatever has already been requested/refunded for this line.
    already_returned = g.db.execute(
        "SELECT COALESCE(SUM(quantity),0) c FROM returns WHERE order_id=? AND product_id=? "
        "AND return_status != 'Rejected'", (order_id, product_id)
    ).fetchone()["c"]
    max_returnable = item["quantity"] - already_returned
    if max_returnable <= 0:
        flash("This item has already been fully returned.", "error")
        return redirect(url_for("customer_order_detail", order_id=order_id))

    quantity = int(request.form.get("quantity", max_returnable) or max_returnable)
    quantity = max(1, min(quantity, max_returnable))
    refund_amount = round(item["unit_price"] * quantity, 2)

    g.db.execute(
        """INSERT INTO returns (order_id, product_id, quantity, return_reason, return_status, refund_amount)
           VALUES (?,?,?,?,?,?)""",
        (order_id, product_id, quantity, reason, "Requested", refund_amount),
    )
    db.log_event(g.db, "ReturnRequested", order_id=order_id, product_id=product_id, actor_role="customer",
                 description=f"Customer requested a return of {quantity} unit(s) of product #{product_id} "
                             f"on order #{order_id}: {reason}")
    g.db.commit()
    flash("Return request submitted. The warehouse team will review it.", "success")
    return redirect(url_for("customer_order_detail", order_id=order_id))


@app.route("/customer/orders/<int:order_id>/cancel", methods=["POST"])
@login_required("customer")
def customer_cancel_order(order_id):
    order = g.db.execute(
        "SELECT * FROM orders WHERE order_id=? AND customer_id=?", (order_id, g.user["user_id"])
    ).fetchone()
    if not order or order["order_status"] not in ("Pending Payment", "Processing", "Insufficient Stock"):
        flash("This order can no longer be cancelled.", "error")
        return redirect(url_for("customer_order_detail", order_id=order_id))

    # Release any reserved stock back to the warehouses it was taken from
    # (Assignment 2, Fig. 13: "payment failed -> order cancelled" / cancel option).
    reservations = g.db.execute(
        "SELECT * FROM reservations WHERE order_id=? AND released=0", (order_id,)
    ).fetchall()
    for r in reservations:
        g.db.execute(
            "UPDATE inventory SET quantity_on_hand = quantity_on_hand + ?, last_updated=datetime('now') "
            "WHERE product_id=? AND warehouse_id=?",
            (r["quantity"], r["product_id"], r["warehouse_id"]),
        )
        g.db.execute("UPDATE reservations SET released=1 WHERE reservation_id=?", (r["reservation_id"],))
        db.log_event(g.db, "InventoryReleased", order_id=order_id, product_id=r["product_id"],
                     warehouse_id=r["warehouse_id"], actor_role="customer",
                     description=f"Released {r['quantity']} unit(s) of product #{r['product_id']} back to "
                                 f"warehouse #{r['warehouse_id']} after order #{order_id} was cancelled.")

    g.db.execute("UPDATE orders SET order_status='Cancelled' WHERE order_id=?", (order_id,))
    db.log_event(g.db, "OrderCancelled", order_id=order_id, actor_role="customer",
                 description=f"Customer {g.user['full_name']} cancelled order #{order_id}.")
    g.db.commit()
    flash(f"Order #{order_id} has been cancelled.", "success")
    return redirect(url_for("customer_order_detail", order_id=order_id))


# ========================================================================
# WAREHOUSE
# ========================================================================
@app.route("/warehouse")
@login_required("warehouse")
def warehouse_dashboard():
    wid = g.user["warehouse_id"]
    pending = g.db.execute(
        "SELECT COUNT(*) c FROM orders WHERE order_status='Processing'"
    ).fetchone()["c"]
    shipped_today = g.db.execute(
        "SELECT COUNT(*) c FROM shipments WHERE warehouse_id=? AND date(ship_date)=date('now')", (wid,)
    ).fetchone()["c"]
    low_stock = g.db.execute(
        "SELECT COUNT(*) c FROM inventory WHERE warehouse_id=? AND quantity_on_hand <= reorder_level", (wid,)
    ).fetchone()["c"]
    pending_returns = g.db.execute(
        "SELECT COUNT(*) c FROM returns WHERE return_status='Requested'"
    ).fetchone()["c"]
    failed_deliveries = g.db.execute(
        "SELECT COUNT(*) c FROM shipments WHERE warehouse_id=? AND delivery_status='Delivery Failed'", (wid,)
    ).fetchone()["c"]
    queue = g.db.execute(
        """SELECT o.*, u.full_name customer_name FROM orders o
           JOIN users u ON u.user_id=o.customer_id
           WHERE o.order_status='Processing' ORDER BY o.order_date ASC LIMIT 6"""
    ).fetchall()
    low_items = g.db.execute(
        """SELECT i.*, p.product_name, p.sku FROM inventory i JOIN products p ON p.product_id=i.product_id
           WHERE i.warehouse_id=? AND i.quantity_on_hand <= i.reorder_level ORDER BY i.quantity_on_hand ASC LIMIT 6""",
        (wid,),
    ).fetchall()
    warehouse = g.db.execute("SELECT * FROM warehouses WHERE warehouse_id=?", (wid,)).fetchone()
    return render_template("warehouse/dashboard.html", pending=pending, shipped_today=shipped_today,
                           low_stock=low_stock, pending_returns=pending_returns, failed_deliveries=failed_deliveries,
                           queue=queue, low_items=low_items, warehouse=warehouse)


@app.route("/warehouse/orders")
@login_required("warehouse")
def warehouse_orders():
    status_filter = request.args.get("status", "Processing")
    if status_filter == "All":
        rows = g.db.execute(
            """SELECT o.*, u.full_name customer_name FROM orders o
               JOIN users u ON u.user_id=o.customer_id ORDER BY o.order_date DESC"""
        ).fetchall()
    else:
        rows = g.db.execute(
            """SELECT o.*, u.full_name customer_name FROM orders o
               JOIN users u ON u.user_id=o.customer_id WHERE o.order_status=? ORDER BY o.order_date ASC""",
            (status_filter,),
        ).fetchall()
    return render_template("warehouse/orders.html", orders=rows, status_filter=status_filter)


@app.route("/warehouse/orders/<int:order_id>")
@login_required("warehouse")
def warehouse_order_detail(order_id):
    order = g.db.execute(
        """SELECT o.*, u.full_name customer_name, u.address FROM orders o
           JOIN users u ON u.user_id=o.customer_id WHERE o.order_id=?""", (order_id,)
    ).fetchone()
    if not order:
        flash("Order not found.", "error")
        return redirect(url_for("warehouse_orders"))
    items = g.db.execute(
        """SELECT oi.*, p.product_name, p.sku FROM order_items oi
           JOIN products p ON p.product_id=oi.product_id WHERE oi.order_id=?""", (order_id,)
    ).fetchall()
    shipment = g.db.execute(
        "SELECT * FROM shipments WHERE order_id=?", (order_id,)
    ).fetchone()
    timeline = g.db.execute(
        "SELECT * FROM events WHERE order_id=? ORDER BY created_at ASC, event_id ASC", (order_id,)
    ).fetchall()
    return render_template("warehouse/order_detail.html", order=order, items=items, shipment=shipment,
                           timeline=timeline, carriers=CARRIERS)


@app.route("/warehouse/orders/<int:order_id>/ship", methods=["POST"])
@login_required("warehouse")
def warehouse_ship_order(order_id):
    order = g.db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if not order or order["order_status"] != "Processing":
        flash("Only orders in 'Processing' can be shipped.", "error")
        return redirect(url_for("warehouse_order_detail", order_id=order_id))

    carrier = request.form.get("delivery_provider", CARRIERS[0])
    tracking = gen_tracking_number(order_id)
    ship_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    expected = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")

    g.db.execute(
        """INSERT INTO shipments (order_id, warehouse_id, delivery_provider, tracking_number,
                                   ship_date, expected_delivery_date, delivery_status)
           VALUES (?,?,?,?,?,?,?)""",
        (order_id, g.user["warehouse_id"], carrier, tracking, ship_date, expected, "In Transit"),
    )
    g.db.execute("UPDATE orders SET order_status='Shipped' WHERE order_id=?", (order_id,))
    db.log_event(g.db, "ShipmentDispatched", order_id=order_id, warehouse_id=g.user["warehouse_id"],
                 actor_role="warehouse",
                 description=f"Order #{order_id} picked, packed and dispatched via {carrier} "
                             f"(tracking {tracking}) by {g.user['full_name']}.")
    g.db.commit()
    flash(f"Order #{order_id} marked as shipped ({tracking}).", "success")
    return redirect(url_for("warehouse_order_detail", order_id=order_id))


@app.route("/warehouse/orders/<int:order_id>/deliver", methods=["POST"])
@login_required("warehouse")
def warehouse_deliver_order(order_id):
    order = g.db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if not order or order["order_status"] != "Shipped":
        flash("Only shipped orders can be marked as delivered.", "error")
        return redirect(url_for("warehouse_order_detail", order_id=order_id))

    g.db.execute("UPDATE shipments SET delivery_status='Delivered' WHERE order_id=?", (order_id,))
    g.db.execute("UPDATE orders SET order_status='Delivered' WHERE order_id=?", (order_id,))
    db.log_event(g.db, "OrderDelivered", order_id=order_id, warehouse_id=g.user["warehouse_id"],
                 actor_role="warehouse",
                 description=f"Order #{order_id} confirmed delivered to customer.")
    g.db.commit()
    flash(f"Order #{order_id} marked as delivered.", "success")
    return redirect(url_for("warehouse_order_detail", order_id=order_id))


@app.route("/warehouse/orders/<int:order_id>/delivery-failed", methods=["POST"])
@login_required("warehouse")
def warehouse_delivery_failed(order_id):
    # Assignment 2, Fig. 13: "Delivery successful? No -> Return/reattempt delivery"
    order = g.db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if not order or order["order_status"] != "Shipped":
        flash("Only shipped orders can be marked as a failed delivery.", "error")
        return redirect(url_for("warehouse_order_detail", order_id=order_id))

    reason = request.form.get("reason", "").strip() or "Recipient unavailable"
    g.db.execute("UPDATE shipments SET delivery_status='Delivery Failed' WHERE order_id=?", (order_id,))
    g.db.execute("UPDATE orders SET order_status='Delivery Failed' WHERE order_id=?", (order_id,))
    db.log_event(g.db, "DeliveryFailed", order_id=order_id, warehouse_id=g.user["warehouse_id"],
                 actor_role="warehouse",
                 description=f"Delivery attempt for order #{order_id} failed: {reason}.")
    g.db.commit()
    flash(f"Order #{order_id} marked as a failed delivery attempt.", "success")
    return redirect(url_for("warehouse_order_detail", order_id=order_id))


@app.route("/warehouse/orders/<int:order_id>/reattempt", methods=["POST"])
@login_required("warehouse")
def warehouse_reattempt_delivery(order_id):
    order = g.db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if not order or order["order_status"] != "Delivery Failed":
        flash("Only failed deliveries can be reattempted.", "error")
        return redirect(url_for("warehouse_order_detail", order_id=order_id))

    g.db.execute("UPDATE shipments SET delivery_status='In Transit' WHERE order_id=?", (order_id,))
    g.db.execute("UPDATE orders SET order_status='Shipped' WHERE order_id=?", (order_id,))
    db.log_event(g.db, "DeliveryReattempted", order_id=order_id, warehouse_id=g.user["warehouse_id"],
                 actor_role="warehouse",
                 description=f"Redelivery of order #{order_id} scheduled by {g.user['full_name']}.")
    g.db.commit()
    flash(f"Order #{order_id} re-queued for delivery.", "success")
    return redirect(url_for("warehouse_order_detail", order_id=order_id))


@app.route("/warehouse/inventory")
@login_required("warehouse")
def warehouse_inventory():
    wid = g.user["warehouse_id"]
    items = g.db.execute(
        """SELECT i.*, p.product_name, p.sku, p.category FROM inventory i
           JOIN products p ON p.product_id=i.product_id
           WHERE i.warehouse_id=? ORDER BY p.category, p.product_name""",
        (wid,),
    ).fetchall()
    warehouse = g.db.execute("SELECT * FROM warehouses WHERE warehouse_id=?", (wid,)).fetchone()
    other_warehouses = g.db.execute(
        "SELECT * FROM warehouses WHERE warehouse_id != ? ORDER BY warehouse_name", (wid,)
    ).fetchall()
    suppliers = g.db.execute("SELECT * FROM suppliers ORDER BY supplier_name").fetchall()
    # Surfaces the Product<->Supplier relationship from the ERD: cost price and lead
    # time drive the "physical delay" discussed in Assignment 2, Section 3.3.1.
    supplier_links = g.db.execute(
        """SELECT ps.*, p.product_name, p.sku, s.supplier_name, s.country FROM product_suppliers ps
           JOIN products p ON p.product_id=ps.product_id
           JOIN suppliers s ON s.supplier_id=ps.supplier_id
           WHERE ps.product_id IN (SELECT product_id FROM inventory WHERE warehouse_id=?)
           ORDER BY ps.lead_time_days DESC""",
        (wid,),
    ).fetchall()
    return render_template("warehouse/inventory.html", items=items, warehouse=warehouse,
                           other_warehouses=other_warehouses, suppliers=suppliers, supplier_links=supplier_links)


@app.route("/warehouse/inventory/receive", methods=["POST"])
@login_required("warehouse")
def warehouse_receive_stock():
    wid = g.user["warehouse_id"]
    product_id = int(request.form["product_id"])
    quantity = max(1, int(request.form["quantity"]))
    supplier_id = request.form.get("supplier_id") or None

    row = g.db.execute(
        "SELECT * FROM inventory WHERE product_id=? AND warehouse_id=?", (product_id, wid)
    ).fetchone()
    if row:
        g.db.execute(
            "UPDATE inventory SET quantity_on_hand=quantity_on_hand+?, last_updated=datetime('now') WHERE inventory_id=?",
            (quantity, row["inventory_id"]),
        )
    else:
        g.db.execute(
            "INSERT INTO inventory (product_id, warehouse_id, quantity_on_hand, reorder_level) VALUES (?,?,?,?)",
            (product_id, wid, quantity, 10),
        )
    supplier_note = ""
    if supplier_id:
        supplier = g.db.execute("SELECT supplier_name FROM suppliers WHERE supplier_id=?", (supplier_id,)).fetchone()
        supplier_note = f" from {supplier['supplier_name']}" if supplier else ""
    db.log_event(g.db, "StockReceived", product_id=product_id, warehouse_id=wid, actor_role="warehouse",
                 description=f"Received {quantity} unit(s) of product #{product_id}{supplier_note} "
                             f"(logged by {g.user['full_name']}).")
    g.db.commit()
    flash("Stock received and inventory updated.", "success")
    return redirect(url_for("warehouse_inventory"))


@app.route("/warehouse/inventory/damage", methods=["POST"])
@login_required("warehouse")
def warehouse_report_damaged():
    # Assignment 2, Fig. 4: "damaged goods" outflow from the Inventory stock.
    wid = g.user["warehouse_id"]
    product_id = int(request.form["product_id"])
    quantity = max(1, int(request.form["quantity"]))
    reason = request.form.get("reason", "").strip() or "Damaged in handling"

    row = g.db.execute(
        "SELECT * FROM inventory WHERE product_id=? AND warehouse_id=?", (product_id, wid)
    ).fetchone()
    if not row or row["quantity_on_hand"] < quantity:
        flash("Not enough stock on hand to write off that quantity.", "error")
        return redirect(url_for("warehouse_inventory"))

    g.db.execute(
        "UPDATE inventory SET quantity_on_hand=quantity_on_hand-?, last_updated=datetime('now') WHERE inventory_id=?",
        (quantity, row["inventory_id"]),
    )
    db.log_event(g.db, "DamagedGoodsRemoved", product_id=product_id, warehouse_id=wid, actor_role="warehouse",
                 description=f"Wrote off {quantity} unit(s) of product #{product_id} as damaged/expired: "
                             f"{reason} (logged by {g.user['full_name']}).")
    g.db.commit()
    flash("Damaged stock written off and inventory updated.", "success")
    return redirect(url_for("warehouse_inventory"))


@app.route("/warehouse/inventory/transfer", methods=["POST"])
@login_required("warehouse")
def warehouse_transfer_stock():
    # Assignment 2, Fig. 4: "goods transferred from/to other distribution centers".
    source_wid = g.user["warehouse_id"]
    product_id = int(request.form["product_id"])
    dest_wid = int(request.form["dest_warehouse_id"])
    quantity = max(1, int(request.form["quantity"]))

    if dest_wid == source_wid:
        flash("Choose a different destination warehouse.", "error")
        return redirect(url_for("warehouse_inventory"))

    source = g.db.execute(
        "SELECT * FROM inventory WHERE product_id=? AND warehouse_id=?", (product_id, source_wid)
    ).fetchone()
    if not source or source["quantity_on_hand"] < quantity:
        flash("Not enough stock on hand to transfer that quantity.", "error")
        return redirect(url_for("warehouse_inventory"))

    g.db.execute(
        "UPDATE inventory SET quantity_on_hand=quantity_on_hand-?, last_updated=datetime('now') WHERE inventory_id=?",
        (quantity, source["inventory_id"]),
    )
    dest = g.db.execute(
        "SELECT * FROM inventory WHERE product_id=? AND warehouse_id=?", (product_id, dest_wid)
    ).fetchone()
    if dest:
        g.db.execute(
            "UPDATE inventory SET quantity_on_hand=quantity_on_hand+?, last_updated=datetime('now') WHERE inventory_id=?",
            (quantity, dest["inventory_id"]),
        )
    else:
        g.db.execute(
            "INSERT INTO inventory (product_id, warehouse_id, quantity_on_hand, reorder_level) VALUES (?,?,?,?)",
            (product_id, dest_wid, quantity, source["reorder_level"]),
        )
    dest_name = g.db.execute("SELECT warehouse_name FROM warehouses WHERE warehouse_id=?", (dest_wid,)).fetchone()
    db.log_event(g.db, "StockTransferred", product_id=product_id, warehouse_id=source_wid, actor_role="warehouse",
                 description=f"Transferred {quantity} unit(s) of product #{product_id} to "
                             f"{dest_name['warehouse_name'] if dest_name else 'warehouse #' + str(dest_wid)} "
                             f"(logged by {g.user['full_name']}).")
    g.db.commit()
    flash("Stock transferred to the destination warehouse.", "success")
    return redirect(url_for("warehouse_inventory"))


@app.route("/warehouse/returns")
@login_required("warehouse")
def warehouse_returns():
    rows = g.db.execute(
        """SELECT r.*, p.product_name, o.customer_id, u.full_name customer_name
           FROM returns r
           JOIN products p ON p.product_id=r.product_id
           JOIN orders o ON o.order_id=r.order_id
           JOIN users u ON u.user_id=o.customer_id
           ORDER BY r.return_date DESC"""
    ).fetchall()
    return render_template("warehouse/returns.html", returns=rows)


@app.route("/warehouse/returns/<int:return_id>/process", methods=["POST"])
@login_required("warehouse")
def warehouse_process_return(return_id):
    decision = request.form.get("decision")  # 'Approved' or 'Rejected'
    ret = g.db.execute("SELECT * FROM returns WHERE return_id=?", (return_id,)).fetchone()
    if not ret:
        flash("Return not found.", "error")
        return redirect(url_for("warehouse_returns"))

    new_status = "Refunded" if decision == "Approved" else "Rejected"
    g.db.execute("UPDATE returns SET return_status=? WHERE return_id=?", (new_status, return_id))

    if decision == "Approved":
        wid = g.user["warehouse_id"]
        qty = ret["quantity"] or 1
        row = g.db.execute(
            "SELECT * FROM inventory WHERE product_id=? AND warehouse_id=?", (ret["product_id"], wid)
        ).fetchone()
        if row:
            g.db.execute(
                "UPDATE inventory SET quantity_on_hand=quantity_on_hand+?, last_updated=datetime('now') WHERE inventory_id=?",
                (qty, row["inventory_id"]),
            )
        else:
            g.db.execute(
                "INSERT INTO inventory (product_id, warehouse_id, quantity_on_hand, reorder_level) VALUES (?,?,?,?)",
                (ret["product_id"], wid, qty, 10),
            )

    db.log_event(g.db, "ReturnProcessed", order_id=ret["order_id"], product_id=ret["product_id"],
                 warehouse_id=g.user["warehouse_id"], actor_role="warehouse",
                 description=f"Return #{return_id} for order #{ret['order_id']} marked as {new_status} "
                             f"by {g.user['full_name']}.")
    g.db.commit()
    flash(f"Return #{return_id} {new_status.lower()}.", "success")
    return redirect(url_for("warehouse_returns"))


# ========================================================================
# ADMIN
# ========================================================================
@app.route("/admin")
@login_required("admin")
def admin_dashboard():
    kpis = g.db.execute(
        """SELECT COUNT(*) total_orders,
                  COALESCE(SUM(total_amount),0) revenue,
                  SUM(CASE WHEN order_status='Processing' THEN 1 ELSE 0 END) processing,
                  SUM(CASE WHEN order_status='Shipped' THEN 1 ELSE 0 END) shipped,
                  SUM(CASE WHEN order_status='Delivered' THEN 1 ELSE 0 END) delivered,
                  SUM(CASE WHEN order_status='Insufficient Stock' THEN 1 ELSE 0 END) backorder
           FROM orders"""
    ).fetchone()
    low_stock = g.db.execute(
        "SELECT COUNT(*) c FROM inventory WHERE quantity_on_hand <= reorder_level"
    ).fetchone()["c"]
    customers = g.db.execute("SELECT COUNT(*) c FROM users WHERE role='customer'").fetchone()["c"]
    recent_events = g.db.execute(
        "SELECT * FROM events ORDER BY created_at DESC, event_id DESC LIMIT 8"
    ).fetchall()
    return render_template("admin/dashboard.html", kpis=kpis, low_stock=low_stock,
                           customers=customers, recent_events=recent_events)


@app.route("/admin/events")
@login_required("admin")
def admin_events():
    event_type = request.args.get("type", "")
    sql = "SELECT * FROM events"
    params = []
    if event_type:
        sql += " WHERE event_type=?"
        params.append(event_type)
    sql += " ORDER BY created_at DESC, event_id DESC LIMIT 300"
    events = g.db.execute(sql, params).fetchall()
    event_types = [r["event_type"] for r in g.db.execute(
        "SELECT DISTINCT event_type FROM events ORDER BY event_type"
    ).fetchall()]
    return render_template("admin/events.html", events=events, event_types=event_types, selected=event_type)


@app.route("/admin/inventory")
@login_required("admin")
def admin_inventory():
    items = g.db.execute(
        """SELECT i.*, p.product_name, p.sku, p.category, w.warehouse_name FROM inventory i
           JOIN products p ON p.product_id=i.product_id
           JOIN warehouses w ON w.warehouse_id=i.warehouse_id
           ORDER BY p.category, p.product_name, w.warehouse_name"""
    ).fetchall()
    return render_template("admin/inventory.html", items=items)


@app.route("/admin/products")
@login_required("admin")
def admin_products():
    products = g.db.execute(
        """SELECT p.*, COALESCE(SUM(i.quantity_on_hand),0) total_stock FROM products p
           LEFT JOIN inventory i ON i.product_id = p.product_id
           GROUP BY p.product_id ORDER BY p.category, p.product_name"""
    ).fetchall()
    return render_template("admin/products.html", products=products)


@app.route("/admin/products/add", methods=["POST"])
@login_required("admin")
def admin_add_product():
    name = request.form.get("product_name", "").strip()
    sku = request.form.get("sku", "").strip().upper()
    category = request.form.get("category", "").strip()
    try:
        price = float(request.form.get("unit_price", 0))
        weight = float(request.form.get("weight") or 0) or None
    except ValueError:
        flash("Price and weight must be numbers.", "error")
        return redirect(url_for("admin_products"))

    if not name or not sku or price <= 0:
        flash("Product name, SKU and a price greater than 0 are required.", "error")
        return redirect(url_for("admin_products"))

    existing = g.db.execute("SELECT 1 FROM products WHERE sku=?", (sku,)).fetchone()
    if existing:
        flash(f"SKU '{sku}' is already in use.", "error")
        return redirect(url_for("admin_products"))

    g.db.execute(
        "INSERT INTO products (product_name, sku, category, unit_price, weight, product_status) VALUES (?,?,?,?,?,?)",
        (name, sku, category, price, weight, "Active"),
    )
    db.log_event(g.db, "ProductAdded", actor_role="admin",
                 description=f"{g.user['full_name']} added product '{name}' ({sku}) at RM {price:.2f}.")
    g.db.commit()
    flash(f"Product '{name}' added.", "success")
    return redirect(url_for("admin_products"))


@app.route("/admin/products/<int:product_id>/toggle", methods=["POST"])
@login_required("admin")
def admin_toggle_product(product_id):
    product = g.db.execute("SELECT * FROM products WHERE product_id=?", (product_id,)).fetchone()
    if not product:
        flash("Product not found.", "error")
        return redirect(url_for("admin_products"))

    new_status = "Inactive" if product["product_status"] == "Active" else "Active"
    g.db.execute("UPDATE products SET product_status=? WHERE product_id=?", (new_status, product_id))
    db.log_event(g.db, "ProductStatusChanged", product_id=product_id, actor_role="admin",
                 description=f"{g.user['full_name']} set '{product['product_name']}' to {new_status}.")
    g.db.commit()
    flash(f"'{product['product_name']}' is now {new_status.lower()}.", "success")
    return redirect(url_for("admin_products"))


@app.route("/admin/reports")
@login_required("admin")
def admin_reports():
    by_status = g.db.execute(
        "SELECT order_status, COUNT(*) c, COALESCE(SUM(total_amount),0) total FROM orders GROUP BY order_status"
    ).fetchall()
    top_products = g.db.execute(
        """SELECT p.product_name, p.sku, SUM(oi.quantity) units_sold, SUM(oi.subtotal) revenue
           FROM order_items oi JOIN products p ON p.product_id=oi.product_id
           GROUP BY oi.product_id ORDER BY units_sold DESC LIMIT 6"""
    ).fetchall()
    low_stock = g.db.execute(
        """SELECT p.product_name, p.sku, w.warehouse_name, i.quantity_on_hand, i.reorder_level
           FROM inventory i JOIN products p ON p.product_id=i.product_id
           JOIN warehouses w ON w.warehouse_id=i.warehouse_id
           WHERE i.quantity_on_hand <= i.reorder_level ORDER BY i.quantity_on_hand ASC"""
    ).fetchall()
    daily_orders = g.db.execute(
        """SELECT date(order_date) d, COUNT(*) c, COALESCE(SUM(total_amount),0) revenue
           FROM orders WHERE order_date >= datetime('now','-14 days')
           GROUP BY date(order_date) ORDER BY d ASC"""
    ).fetchall()
    max_orders = max([r["c"] for r in daily_orders], default=1)
    returns_summary = g.db.execute(
        "SELECT return_status, COUNT(*) c FROM returns GROUP BY return_status"
    ).fetchall()
    return render_template("admin/reports.html", by_status=by_status, top_products=top_products,
                           low_stock=low_stock, daily_orders=daily_orders, max_orders=max_orders,
                           returns_summary=returns_summary)


@app.route("/admin/users")
@login_required("admin")
def admin_users():
    customers = g.db.execute(
        "SELECT * FROM users WHERE role='customer' ORDER BY register_date DESC"
    ).fetchall()
    staff = g.db.execute(
        """SELECT u.*, w.warehouse_name FROM users u LEFT JOIN warehouses w ON w.warehouse_id=u.warehouse_id
           WHERE u.role IN ('warehouse','admin') ORDER BY u.role, u.full_name"""
    ).fetchall()
    return render_template("admin/users.html", customers=customers, staff=staff)


@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@login_required("admin")
def admin_toggle_user(user_id):
    target = g.db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not target:
        flash("User not found.", "error")
        return redirect(url_for("admin_users"))
    if target["user_id"] == g.user["user_id"]:
        flash("You can't deactivate your own account.", "error")
        return redirect(url_for("admin_users"))

    new_status = 0 if target["is_active"] else 1
    g.db.execute("UPDATE users SET is_active=? WHERE user_id=?", (new_status, user_id))
    db.log_event(g.db, "UserActivated" if new_status else "UserDeactivated", actor_role="admin",
                 description=f"{g.user['full_name']} {'reactivated' if new_status else 'deactivated'} "
                             f"{target['role']} account '{target['username']}' ({target['full_name']}).")
    g.db.commit()
    flash(f"{target['full_name']} has been {'reactivated' if new_status else 'deactivated'}.", "success")
    return redirect(url_for("admin_users"))


if __name__ == "__main__":
    db.init_db()
    app.run(debug=True)
