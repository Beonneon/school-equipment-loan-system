from __future__ import annotations

import hmac
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config


EQUIPMENT_IMAGES = {
    "CAM-001": "images/camera.webp",
    "LAP-014": "images/laptop.webp",
    "SPT-008": "images/basketballs.webp",
    "MUS-003": "images/guitar.webp",
}
LOAN_TIMES = [f"{hour:02d}:00" for hour in range(8, 18)]


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'borrower')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS equipment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_code TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL,
    total_quantity INTEGER NOT NULL CHECK (total_quantity >= 0),
    available_quantity INTEGER NOT NULL CHECK (
        available_quantity >= 0 AND available_quantity <= total_quantity
    ),
    condition TEXT NOT NULL CHECK (condition IN ('Excellent', 'Good', 'Fair', 'Maintenance')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    equipment_id INTEGER NOT NULL REFERENCES equipment(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    purpose TEXT NOT NULL,
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at TEXT,
    due_date TEXT NOT NULL,
    pickup_time TEXT NOT NULL DEFAULT '09:00',
    returned_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'approved', 'denied', 'returned', 'cancelled')
    ),
    admin_note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS asset_units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id),
    asset_tag TEXT NOT NULL UNIQUE COLLATE NOCASE,
    condition TEXT NOT NULL CHECK (condition IN ('Excellent', 'Good', 'Fair', 'Maintenance')),
    status TEXT NOT NULL DEFAULT 'available' CHECK (status IN ('available', 'on_loan', 'retired')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS loan_units (
    loan_id INTEGER NOT NULL REFERENCES loans(id),
    unit_id INTEGER NOT NULL REFERENCES asset_units(id),
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    released_at TEXT,
    PRIMARY KEY (loan_id, unit_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_equipment_category ON equipment(category);
CREATE INDEX IF NOT EXISTS idx_loans_user ON loans(user_id);
CREATE INDEX IF NOT EXISTS idx_loans_status ON loans(status);
CREATE INDEX IF NOT EXISTS idx_asset_units_equipment ON asset_units(equipment_id);
CREATE INDEX IF NOT EXISTS idx_asset_units_status ON asset_units(status);
"""


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    if os.environ.get("APP_ENV") == "production" and app.config["SECRET_KEY"] == "development-only-change-me":
        raise RuntimeError("SECRET_KEY must be set for production deployment.")

    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)

    app.teardown_appcontext(close_db)
    app.jinja_env.globals.update(
        today=date.today,
        max_due_date=lambda: date.today() + timedelta(days=30),
        loan_times=LOAN_TIMES,
        equipment_image=lambda asset_code: EQUIPMENT_IMAGES.get(asset_code),
        is_overdue=is_overdue,
    )

    @app.before_request
    def load_user() -> None:
        user_id = session.get("user_id")
        g.user = (
            get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if user_id
            else None
        )
        if request.method == "POST":
            submitted = request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not expected or not hmac.compare_digest(submitted, expected):
                abort(400, "Invalid or expired form token. Please refresh and try again.")

    @app.context_processor
    def inject_csrf() -> dict[str, str]:
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(24)
        return {"csrf_token": session["csrf_token"]}

    register_routes(app)

    with app.app_context():
        init_db()

    return app


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_database())
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def current_database() -> str:
    from flask import current_app

    return current_app.config["DATABASE"]


def close_db(_: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.executescript(SCHEMA)
    loan_columns = {row["name"] for row in db.execute("PRAGMA table_info(loans)").fetchall()}
    if "pickup_time" not in loan_columns:
        db.execute("ALTER TABLE loans ADD COLUMN pickup_time TEXT NOT NULL DEFAULT '09:00'")
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        db.executemany(
            "INSERT INTO users (username, full_name, password_hash, role) VALUES (?, ?, ?, ?)",
            [
                ("admin", "Equipment Coordinator", generate_password_hash("Admin123!"), "admin"),
                ("student", "Sample Student", generate_password_hash("Student123!"), "borrower"),
            ],
        )
    credential_overrides = {
        "admin": os.environ.get("ADMIN_PASSWORD"),
        "student": os.environ.get("BORROWER_PASSWORD"),
    }
    for username, password in credential_overrides.items():
        if password:
            db.execute(
                "UPDATE users SET password_hash=? WHERE username=? COLLATE NOCASE",
                (generate_password_hash(password), username),
            )
    if db.execute("SELECT COUNT(*) FROM equipment").fetchone()[0] == 0:
        db.executemany(
            """INSERT INTO equipment
               (asset_code, name, category, description, location, total_quantity,
                available_quantity, condition)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("CAM-001", "Canon EOS 250D Camera", "Cameras", "DSLR camera with kit lens and battery.", "Media Room A", 4, 4, "Excellent"),
                ("LAP-014", "Dell Latitude Laptop", "Laptops", "Windows laptop with charger and protective case.", "ICT Store", 12, 12, "Good"),
                ("SPT-008", "Basketball Set", "Sports", "Set of six indoor/outdoor basketballs.", "Sports Storeroom", 5, 5, "Good"),
                ("MUS-003", "Yamaha Acoustic Guitar", "Instruments", "Full-size acoustic guitar with case.", "Music Room 2", 6, 6, "Fair"),
            ],
        )
    db.execute("INSERT OR IGNORE INTO categories (name) SELECT DISTINCT category FROM equipment")
    for item in db.execute("SELECT * FROM equipment").fetchall():
        sync_asset_units(item["id"], item["asset_code"], item["total_quantity"], item["condition"])
    active_loans = db.execute(
        """SELECT l.* FROM loans l
           WHERE l.status='approved' AND NOT EXISTS (
               SELECT 1 FROM loan_units lu WHERE lu.loan_id=l.id AND lu.released_at IS NULL
           ) ORDER BY l.approved_at, l.id"""
    ).fetchall()
    for loan in active_loans:
        units = db.execute(
            "SELECT id FROM asset_units WHERE equipment_id=? AND status='available' ORDER BY asset_tag LIMIT ?",
            (loan["equipment_id"], loan["quantity"]),
        ).fetchall()
        for unit in units:
            db.execute("UPDATE asset_units SET status='on_loan' WHERE id=?", (unit["id"],))
            db.execute("INSERT OR IGNORE INTO loan_units (loan_id, unit_id) VALUES (?, ?)", (loan["id"], unit["id"]))
    refresh_equipment_availability()
    db.commit()


def next_asset_tag(asset_code: str) -> str:
    db = get_db()
    prefix = f"{asset_code}-U"
    existing = {
        row["asset_tag"].upper()
        for row in db.execute("SELECT asset_tag FROM asset_units WHERE asset_tag LIKE ?", (f"{prefix}%",)).fetchall()
    }
    number = 1
    while f"{prefix}{number:03d}".upper() in existing:
        number += 1
    return f"{prefix}{number:03d}"


def sync_asset_units(equipment_id: int, asset_code: str, target_total: int, condition: str) -> str | None:
    db = get_db()
    active_units = db.execute(
        "SELECT * FROM asset_units WHERE equipment_id=? AND status!='retired' ORDER BY id", (equipment_id,)
    ).fetchall()
    difference = target_total - len(active_units)
    if difference > 0:
        for _ in range(difference):
            db.execute(
                "INSERT INTO asset_units (equipment_id, asset_tag, condition) VALUES (?, ?, ?)",
                (equipment_id, next_asset_tag(asset_code), condition),
            )
    elif difference < 0:
        available = db.execute(
            "SELECT id FROM asset_units WHERE equipment_id=? AND status='available' ORDER BY id DESC LIMIT ?",
            (equipment_id, -difference),
        ).fetchall()
        if len(available) < -difference:
            return "Reduce or return assigned units before lowering the total quantity."
        for unit in available:
            db.execute("UPDATE asset_units SET status='retired' WHERE id=?", (unit["id"],))
    return None


def refresh_equipment_availability(equipment_id: int | None = None) -> None:
    db = get_db()
    if equipment_id is None:
        items = db.execute("SELECT id FROM equipment").fetchall()
    else:
        items = [{"id": equipment_id}]
    for item in items:
        available = db.execute(
            "SELECT COUNT(*) FROM asset_units WHERE equipment_id=? AND status='available'", (item["id"],)
        ).fetchone()[0]
        total = db.execute(
            "SELECT COUNT(*) FROM asset_units WHERE equipment_id=? AND status!='retired'", (item["id"],)
        ).fetchone()[0]
        db.execute(
            "UPDATE equipment SET total_quantity=?, available_quantity=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (total, available, item["id"]),
        )


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(**kwargs: Any) -> Any:
        if g.user is None:
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(**kwargs)

    return wrapped


def admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    @login_required
    def wrapped(**kwargs: Any) -> Any:
        if g.user["role"] != "admin":
            abort(403)
        return view(**kwargs)

    return wrapped


def audit(action: str, details: str, user_id: int | None = None) -> None:
    uid = user_id if user_id is not None else (g.user["id"] if g.user else None)
    get_db().execute(
        "INSERT INTO audit_log (user_id, action, details) VALUES (?, ?, ?)",
        (uid, action, details[:500]),
    )


def is_overdue(loan: sqlite3.Row) -> bool:
    return loan["status"] == "approved" and date.fromisoformat(loan["due_date"]) < date.today()


def parse_due_date(raw: str) -> date:
    try:
        due = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("Choose a valid due date.") from exc
    if due < date.today():
        raise ValueError("The due date cannot be in the past.")
    if due > date.today() + timedelta(days=30):
        raise ValueError("Loans can be requested for a maximum of 30 days.")
    return due


def validate_password(password: str) -> str | None:
    if len(password) < 10:
        return "Password must contain at least 10 characters."
    if not re.search(r"[A-Z]", password):
        return "Password must include an uppercase letter."
    if not re.search(r"[a-z]", password):
        return "Password must include a lowercase letter."
    if not re.search(r"\d", password):
        return "Password must include a number."
    if not re.search(r"[^A-Za-z0-9]", password):
        return "Password must include a symbol."
    return None


def register_routes(app: Flask) -> None:
    @app.get("/")
    def index() -> Any:
        return redirect(url_for("dashboard" if g.user else "login"))

    @app.get("/health")
    def health() -> tuple[dict[str, str], int]:
        get_db().execute("SELECT 1").fetchone()
        return {"status": "healthy"}, 200

    @app.route("/login", methods=("GET", "POST"))
    def login() -> Any:
        if g.user:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = get_db().execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
            ).fetchone()
            if user is None or not check_password_hash(user["password_hash"], password):
                flash("Incorrect username or password.", "error")
            else:
                session.clear()
                session["user_id"] = user["id"]
                session["csrf_token"] = secrets.token_hex(24)
                audit("login", f"{user['username']} signed in", user["id"])
                get_db().commit()
                return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.route("/register", methods=("GET", "POST"))
    def register() -> Any:
        if g.user:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            confirmation = request.form.get("password_confirmation", "")
            school_code = request.form.get("school_code", "")
            error = None
            if not (2 <= len(full_name) <= 80):
                error = "Full name must be between 2 and 80 characters."
            elif not re.fullmatch(r"[A-Za-z0-9._-]{3,30}", username):
                error = "Username must be 3-30 letters, numbers, dots, hyphens or underscores."
            elif password != confirmation:
                error = "Passwords do not match."
            elif validate_password(password):
                error = validate_password(password)
            elif not hmac.compare_digest(school_code, app.config["REGISTRATION_CODE"]):
                error = "The school registration code is incorrect. Ask the equipment coordinator for the current code."
            if error:
                flash(error, "error")
            else:
                db = get_db()
                try:
                    cursor = db.execute(
                        "INSERT INTO users (username, full_name, password_hash, role) VALUES (?, ?, ?, 'borrower')",
                        (username, full_name, generate_password_hash(password)),
                    )
                    audit("account_registered", f"Borrower account @{username} registered", cursor.lastrowid)
                    db.commit()
                except sqlite3.IntegrityError:
                    db.rollback()
                    flash("That username is already registered.", "error")
                else:
                    flash("Account created. You can now sign in.", "success")
                    return redirect(url_for("login"))
        return render_template("register.html")

    @app.post("/logout")
    @login_required
    def logout() -> Any:
        audit("logout", f"{g.user['username']} signed out")
        get_db().commit()
        session.clear()
        return redirect(url_for("login"))

    @app.get("/dashboard")
    @login_required
    def dashboard() -> Any:
        db = get_db()
        counts = {
            "items": db.execute("SELECT COUNT(*) FROM equipment WHERE active = 1").fetchone()[0],
            "available": db.execute("SELECT COALESCE(SUM(available_quantity), 0) FROM equipment WHERE active = 1").fetchone()[0],
        }
        if g.user["role"] == "admin":
            counts["pending"] = db.execute("SELECT COUNT(*) FROM loans WHERE status = 'pending'").fetchone()[0]
            counts["overdue"] = db.execute(
                "SELECT COUNT(*) FROM loans WHERE status = 'approved' AND due_date < ?",
                (date.today().isoformat(),),
            ).fetchone()[0]
            recent = db.execute(
                """SELECT l.*, u.full_name, e.name AS equipment_name, e.asset_code,
                   (SELECT GROUP_CONCAT(au.asset_tag, ', ') FROM loan_units lu
                    JOIN asset_units au ON au.id=lu.unit_id WHERE lu.loan_id=l.id) AS asset_tags
                   FROM loans l JOIN users u ON u.id=l.user_id
                   JOIN equipment e ON e.id=l.equipment_id
                   ORDER BY l.requested_at DESC LIMIT 8"""
            ).fetchall()
        else:
            counts["active"] = db.execute(
                "SELECT COUNT(*) FROM loans WHERE user_id = ? AND status IN ('pending','approved')",
                (g.user["id"],),
            ).fetchone()[0]
            counts["overdue"] = db.execute(
                "SELECT COUNT(*) FROM loans WHERE user_id = ? AND status = 'approved' AND due_date < ?",
                (g.user["id"], date.today().isoformat()),
            ).fetchone()[0]
            recent = db.execute(
                """SELECT l.*, u.full_name, e.name AS equipment_name, e.asset_code,
                   (SELECT GROUP_CONCAT(au.asset_tag, ', ') FROM loan_units lu
                    JOIN asset_units au ON au.id=lu.unit_id WHERE lu.loan_id=l.id) AS asset_tags
                   FROM loans l JOIN users u ON u.id=l.user_id
                   JOIN equipment e ON e.id=l.equipment_id
                   WHERE l.user_id=? ORDER BY l.requested_at DESC LIMIT 8""",
                (g.user["id"],),
            ).fetchall()
        return render_template("dashboard.html", counts=counts, loans=recent)

    @app.get("/equipment")
    @login_required
    def equipment_list() -> Any:
        query = request.args.get("q", "").strip()
        category = request.args.get("category", "").strip()
        sql = "SELECT * FROM equipment WHERE active = 1"
        params: list[Any] = []
        if query:
            sql += " AND (name LIKE ? OR asset_code LIKE ? OR description LIKE ?)"
            term = f"%{query}%"
            params.extend([term, term, term])
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY category, name"
        items = get_db().execute(sql, params).fetchall()
        categories = get_db().execute("SELECT name AS category FROM categories ORDER BY name").fetchall()
        return render_template("equipment.html", items=items, categories=categories, query=query, selected_category=category)

    @app.post("/equipment/<int:equipment_id>/request")
    @login_required
    def request_loan(equipment_id: int) -> Any:
        db = get_db()
        item = db.execute("SELECT * FROM equipment WHERE id=? AND active=1", (equipment_id,)).fetchone()
        if item is None:
            abort(404)
        try:
            quantity = int(request.form.get("quantity", "1"))
            due = parse_due_date(request.form.get("due_date", ""))
            pickup_time = request.form.get("pickup_time", "")
            purpose = request.form.get("purpose", "").strip()
            if quantity < 1 or quantity > item["available_quantity"]:
                raise ValueError("Requested quantity is not currently available.")
            if len(purpose) < 5 or len(purpose) > 250:
                raise ValueError("Purpose must be between 5 and 250 characters.")
            if pickup_time not in LOAN_TIMES:
                raise ValueError("Choose a pickup time between 8:00 and 17:00 on the hour.")
        except (ValueError, TypeError) as exc:
            flash(str(exc), "error")
            return redirect(url_for("equipment_list"))
        db.execute(
            """INSERT INTO loans (user_id, equipment_id, quantity, purpose, due_date, pickup_time)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (g.user["id"], equipment_id, quantity, purpose, due.isoformat(), pickup_time),
        )
        audit("loan_requested", f"Requested {quantity} x {item['asset_code']} at {pickup_time} until {due.isoformat()}")
        db.commit()
        flash("Loan request submitted for approval.", "success")
        return redirect(url_for("loans"))

    @app.get("/loans")
    @login_required
    def loans() -> Any:
        db = get_db()
        sql = """SELECT l.*, u.full_name, u.username, e.name AS equipment_name, e.asset_code,
                 (SELECT GROUP_CONCAT(au.asset_tag, ', ') FROM loan_units lu
                  JOIN asset_units au ON au.id=lu.unit_id WHERE lu.loan_id=l.id) AS asset_tags
                 FROM loans l JOIN users u ON u.id=l.user_id
                 JOIN equipment e ON e.id=l.equipment_id"""
        params: tuple[Any, ...] = ()
        if g.user["role"] != "admin":
            sql += " WHERE l.user_id = ?"
            params = (g.user["id"],)
        sql += " ORDER BY CASE l.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, l.requested_at DESC"
        return render_template("loans.html", loans=db.execute(sql, params).fetchall())

    @app.post("/loans/<int:loan_id>/decision")
    @admin_required
    def loan_decision(loan_id: int) -> Any:
        decision = request.form.get("decision")
        note = request.form.get("admin_note", "").strip()[:250]
        if decision not in {"approve", "deny"}:
            abort(400)
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        loan = db.execute(
            """SELECT l.*, e.available_quantity, e.asset_code
               FROM loans l JOIN equipment e ON e.id=l.equipment_id WHERE l.id=?""",
            (loan_id,),
        ).fetchone()
        if loan is None:
            abort(404)
        if loan["status"] != "pending":
            flash("That request has already been reviewed.", "warning")
        elif decision == "approve" and loan["quantity"] > loan["available_quantity"]:
            flash("Not enough stock is available to approve this request.", "error")
        elif decision == "approve":
            units = db.execute(
                "SELECT id, asset_tag FROM asset_units WHERE equipment_id=? AND status='available' ORDER BY asset_tag LIMIT ?",
                (loan["equipment_id"], loan["quantity"]),
            ).fetchall()
            if len(units) != loan["quantity"]:
                db.rollback()
                flash("Not enough individually tagged units are available.", "error")
                return redirect(url_for("loans"))
            for unit in units:
                db.execute("UPDATE asset_units SET status='on_loan' WHERE id=?", (unit["id"],))
                db.execute("INSERT INTO loan_units (loan_id, unit_id) VALUES (?, ?)", (loan_id, unit["id"]))
            db.execute(
                "UPDATE loans SET status='approved', approved_at=CURRENT_TIMESTAMP, admin_note=? WHERE id=?",
                (note, loan_id),
            )
            refresh_equipment_availability(loan["equipment_id"])
            tags = ", ".join(unit["asset_tag"] for unit in units)
            audit("loan_approved", f"Approved loan #{loan_id} for units {tags}")
            flash("Loan approved and stock updated.", "success")
        else:
            db.execute("UPDATE loans SET status='denied', admin_note=? WHERE id=?", (note, loan_id))
            audit("loan_denied", f"Denied loan #{loan_id} for {loan['asset_code']}")
            flash("Loan request denied.", "success")
        db.commit()
        return redirect(url_for("loans"))

    @app.post("/loans/<int:loan_id>/cancel")
    @login_required
    def cancel_loan(loan_id: int) -> Any:
        db = get_db()
        loan = db.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
        if loan is None:
            abort(404)
        if loan["user_id"] != g.user["id"] and g.user["role"] != "admin":
            abort(403)
        if loan["status"] != "pending":
            flash("Only pending requests can be cancelled.", "warning")
        else:
            db.execute("UPDATE loans SET status='cancelled' WHERE id=?", (loan_id,))
            audit("loan_cancelled", f"Cancelled loan #{loan_id}")
            db.commit()
            flash("Request cancelled.", "success")
        return redirect(url_for("loans"))

    @app.post("/loans/<int:loan_id>/return")
    @login_required
    def return_loan(loan_id: int) -> Any:
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        loan = db.execute(
            "SELECT l.*, e.asset_code FROM loans l JOIN equipment e ON e.id=l.equipment_id WHERE l.id=?",
            (loan_id,),
        ).fetchone()
        if loan is None:
            abort(404)
        if loan["user_id"] != g.user["id"] and g.user["role"] != "admin":
            abort(403)
        if loan["status"] != "approved":
            flash("Only approved, active loans can be returned.", "warning")
        else:
            db.execute(
                "UPDATE loans SET status='returned', returned_at=CURRENT_TIMESTAMP WHERE id=?", (loan_id,)
            )
            units = db.execute(
                """SELECT au.id, au.asset_tag FROM loan_units lu JOIN asset_units au ON au.id=lu.unit_id
                   WHERE lu.loan_id=? AND lu.released_at IS NULL""",
                (loan_id,),
            ).fetchall()
            for unit in units:
                db.execute("UPDATE asset_units SET status='available' WHERE id=?", (unit["id"],))
            db.execute("UPDATE loan_units SET released_at=CURRENT_TIMESTAMP WHERE loan_id=? AND released_at IS NULL", (loan_id,))
            refresh_equipment_availability(loan["equipment_id"])
            tags = ", ".join(unit["asset_tag"] for unit in units)
            audit("equipment_returned", f"Returned loan #{loan_id}: {tags or loan['asset_code']}")
            db.commit()
            flash("Equipment returned and stock restored.", "success")
        return redirect(url_for("loans"))

    @app.route("/admin/equipment/new", methods=("GET", "POST"))
    @admin_required
    def equipment_new() -> Any:
        categories = get_db().execute("SELECT name FROM categories ORDER BY name").fetchall()
        if request.method == "POST":
            error = save_equipment(None)
            if error is None:
                flash("Equipment added.", "success")
                return redirect(url_for("equipment_list"))
            flash(error, "error")
        return render_template("equipment_form.html", item=None, categories=categories)

    @app.route("/admin/equipment/<int:equipment_id>/edit", methods=("GET", "POST"))
    @admin_required
    def equipment_edit(equipment_id: int) -> Any:
        categories = get_db().execute("SELECT name FROM categories ORDER BY name").fetchall()
        item = get_db().execute("SELECT * FROM equipment WHERE id=?", (equipment_id,)).fetchone()
        if item is None:
            abort(404)
        if request.method == "POST":
            error = save_equipment(equipment_id)
            if error is None:
                flash("Equipment updated.", "success")
                return redirect(url_for("equipment_list"))
            flash(error, "error")
        return render_template("equipment_form.html", item=item, categories=categories)

    @app.get("/admin/equipment/<int:equipment_id>/units")
    @admin_required
    def equipment_units(equipment_id: int) -> Any:
        db = get_db()
        item = db.execute("SELECT * FROM equipment WHERE id=?", (equipment_id,)).fetchone()
        if item is None:
            abort(404)
        units = db.execute(
            """SELECT au.*,
               (SELECT u.full_name FROM loan_units lu JOIN loans l ON l.id=lu.loan_id
                JOIN users u ON u.id=l.user_id WHERE lu.unit_id=au.id
                AND lu.released_at IS NULL AND l.status='approved' LIMIT 1) AS borrower,
               (SELECT l.id FROM loan_units lu JOIN loans l ON l.id=lu.loan_id
                WHERE lu.unit_id=au.id AND lu.released_at IS NULL AND l.status='approved' LIMIT 1) AS loan_id
               FROM asset_units au WHERE au.equipment_id=? ORDER BY au.status='retired', au.asset_tag""",
            (equipment_id,),
        ).fetchall()
        return render_template("equipment_units.html", item=item, units=units)

    @app.post("/admin/equipment/<int:equipment_id>/units/add")
    @admin_required
    def equipment_unit_add(equipment_id: int) -> Any:
        db = get_db()
        item = db.execute("SELECT * FROM equipment WHERE id=?", (equipment_id,)).fetchone()
        if item is None:
            abort(404)
        asset_tag = request.form.get("asset_tag", "").strip().upper() or next_asset_tag(item["asset_code"])
        if not re.fullmatch(r"[A-Z0-9._-]{3,40}", asset_tag):
            flash("Unit ID must be 3-40 letters, numbers, dots, hyphens or underscores.", "error")
        else:
            try:
                db.execute(
                    "INSERT INTO asset_units (equipment_id, asset_tag, condition) VALUES (?, ?, ?)",
                    (equipment_id, asset_tag, item["condition"]),
                )
                refresh_equipment_availability(equipment_id)
                audit("asset_unit_created", f"Added physical unit {asset_tag} to {item['asset_code']}")
                db.commit()
                flash("Physical unit added.", "success")
            except sqlite3.IntegrityError:
                db.rollback()
                flash("That unit ID is already in use.", "error")
        return redirect(url_for("equipment_units", equipment_id=equipment_id))

    @app.post("/admin/units/<int:unit_id>/retire")
    @admin_required
    def equipment_unit_retire(unit_id: int) -> Any:
        db = get_db()
        unit = db.execute("SELECT * FROM asset_units WHERE id=?", (unit_id,)).fetchone()
        if unit is None:
            abort(404)
        if unit["status"] != "available":
            flash("Only an available unit can be retired.", "warning")
        else:
            db.execute("UPDATE asset_units SET status='retired' WHERE id=?", (unit_id,))
            refresh_equipment_availability(unit["equipment_id"])
            audit("asset_unit_retired", f"Retired physical unit {unit['asset_tag']}")
            db.commit()
            flash("Physical unit retired and stock updated.", "success")
        return redirect(url_for("equipment_units", equipment_id=unit["equipment_id"]))

    @app.route("/admin/categories", methods=("GET", "POST"))
    @admin_required
    def category_manager() -> Any:
        db = get_db()
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not (2 <= len(name) <= 50):
                flash("Category name must be between 2 and 50 characters.", "error")
            elif not re.fullmatch(r"[A-Za-z0-9 &/'-]+", name):
                flash("Category name contains unsupported characters.", "error")
            else:
                try:
                    db.execute("INSERT INTO categories (name) VALUES (?)", (name,))
                    audit("category_created", f"Created category: {name}")
                    db.commit()
                    flash("Category added.", "success")
                except sqlite3.IntegrityError:
                    db.rollback()
                    flash("That category already exists.", "warning")
        categories = db.execute(
            """SELECT c.*, COUNT(e.id) AS equipment_count FROM categories c
               LEFT JOIN equipment e ON e.category = c.name AND e.active=1
               GROUP BY c.id ORDER BY c.name"""
        ).fetchall()
        return render_template("categories.html", categories=categories)

    @app.post("/admin/categories/<int:category_id>/delete")
    @admin_required
    def category_delete(category_id: int) -> Any:
        db = get_db()
        category = db.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
        if category is None:
            abort(404)
        count = db.execute(
            "SELECT COUNT(*) FROM equipment WHERE category=? AND active=1", (category["name"],)
        ).fetchone()[0]
        if count:
            flash(f"Move or edit the {count} active equipment item(s) before removing this category.", "warning")
        else:
            db.execute("DELETE FROM categories WHERE id=?", (category_id,))
            audit("category_deleted", f"Removed category: {category['name']}")
            db.commit()
            flash("Category removed.", "success")
        return redirect(url_for("category_manager"))

    @app.get("/admin/audit")
    @admin_required
    def audit_log() -> Any:
        rows = get_db().execute(
            """SELECT a.*, u.full_name FROM audit_log a
               LEFT JOIN users u ON u.id=a.user_id ORDER BY a.created_at DESC LIMIT 200"""
        ).fetchall()
        return render_template("audit.html", rows=rows)

    @app.get("/help")
    @login_required
    def help_page() -> Any:
        return render_template("help.html")


def save_equipment(equipment_id: int | None) -> str | None:
    db = get_db()
    code = request.form.get("asset_code", "").strip().upper()
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    description = request.form.get("description", "").strip()
    location = request.form.get("location", "").strip()
    condition = request.form.get("condition", "")
    try:
        total = int(request.form.get("total_quantity", "0"))
    except ValueError:
        return "Quantity must be a whole number."
    if not (2 <= len(code) <= 20) or not all(c.isalnum() or c in "-_" for c in code):
        return "Asset code must be 2-20 letters, numbers, hyphens or underscores."
    if min(len(name), len(category), len(location)) < 2:
        return "Name, category and location must each contain at least 2 characters."
    if db.execute("SELECT 1 FROM categories WHERE name=? COLLATE NOCASE", (category,)).fetchone() is None:
        return "Choose a category from the managed list."
    if total < 0 or total > 999:
        return "Total quantity must be between 0 and 999."
    if condition not in {"Excellent", "Good", "Fair", "Maintenance"}:
        return "Choose a valid condition."
    try:
        if equipment_id is None:
            cursor = db.execute(
                """INSERT INTO equipment
                   (asset_code,name,category,description,location,total_quantity,available_quantity,condition)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (code, name, category, description[:500], location, total, total, condition),
            )
            error = sync_asset_units(cursor.lastrowid, code, total, condition)
            if error:
                db.rollback()
                return error
            refresh_equipment_availability(cursor.lastrowid)
            audit("equipment_created", f"Created {code}: {name}")
        else:
            current = db.execute("SELECT * FROM equipment WHERE id=?", (equipment_id,)).fetchone()
            checked_out = current["total_quantity"] - current["available_quantity"]
            if total < checked_out:
                return f"Total cannot be below {checked_out}; that many units are currently on loan."
            db.execute(
                """UPDATE equipment SET asset_code=?,name=?,category=?,description=?,location=?,
                   total_quantity=?,available_quantity=?,condition=?,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (code, name, category, description[:500], location, total, total - checked_out, condition, equipment_id),
            )
            error = sync_asset_units(equipment_id, code, total, condition)
            if error:
                db.rollback()
                return error
            refresh_equipment_availability(equipment_id)
            audit("equipment_updated", f"Updated {code}: {name}")
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return "That asset code is already in use."
    return None


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
