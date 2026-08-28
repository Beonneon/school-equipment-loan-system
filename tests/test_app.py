from datetime import date, timedelta

import pytest

from app import create_app, get_db


@pytest.fixture()
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.db"),
        "SECRET_KEY": "testing",
    })
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client):
    with client.session_transaction() as session:
        return session["csrf_token"]


def login(client, username="student", password="Student123!"):
    client.get("/login")
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": csrf(client)},
        follow_redirects=True,
    )


def test_login_success_and_failure(client):
    client.get("/login")
    failed = client.post(
        "/login",
        data={"username": "student", "password": "wrong", "csrf_token": csrf(client)},
        follow_redirects=True,
    )
    assert b"Incorrect username or password" in failed.data
    success = login(client)
    assert b"Equipment catalogue" not in success.data
    assert b"Sample" in success.data


def test_unauthenticated_user_is_redirected(client):
    response = client.get("/equipment")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_health_endpoint_checks_database(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "healthy"}


def test_csrf_blocks_unprotected_post(client):
    client.get("/login")
    response = client.post("/login", data={"username": "student", "password": "Student123!"})
    assert response.status_code == 400


def test_borrow_approve_and_return_updates_stock(app, client):
    login(client)
    due = (date.today() + timedelta(days=7)).isoformat()
    response = client.post(
        "/equipment/1/request",
        data={"quantity": "2", "due_date": due, "pickup_time": "10:00", "purpose": "Media assessment", "csrf_token": csrf(client)},
        follow_redirects=True,
    )
    assert b"submitted for approval" in response.data
    client.post("/logout", data={"csrf_token": csrf(client)})
    login(client, "admin", "Admin123!")
    response = client.post(
        "/loans/1/decision",
        data={"decision": "approve", "admin_note": "Collect at lunch", "csrf_token": csrf(client)},
        follow_redirects=True,
    )
    assert b"Loan approved" in response.data
    with app.app_context():
        assert get_db().execute("SELECT available_quantity FROM equipment WHERE id=1").fetchone()[0] == 2
        assigned = get_db().execute(
            """SELECT au.asset_tag, au.status FROM loan_units lu
               JOIN asset_units au ON au.id=lu.unit_id WHERE lu.loan_id=1"""
        ).fetchall()
        assert len(assigned) == 2
        assert all(row["status"] == "on_loan" for row in assigned)
    client.post("/logout", data={"csrf_token": csrf(client)})
    login(client)
    dashboard = client.get("/dashboard")
    assert b"Mark returned" in dashboard.data
    response = client.post("/loans/1/return", data={"csrf_token": csrf(client)}, follow_redirects=True)
    assert b"stock restored" in response.data
    with app.app_context():
        assert get_db().execute("SELECT available_quantity FROM equipment WHERE id=1").fetchone()[0] == 4
        released = get_db().execute(
            """SELECT au.status, lu.released_at FROM loan_units lu
               JOIN asset_units au ON au.id=lu.unit_id WHERE lu.loan_id=1"""
        ).fetchall()
        assert all(row["status"] == "available" and row["released_at"] for row in released)


def test_admin_routes_forbid_borrower(client):
    login(client)
    assert client.get("/admin/audit").status_code == 403


def test_dashboard_renders_equipment_count_as_number(client):
    response = login(client, "admin", "Admin123!")
    assert b"<strong>4</strong>" in response.data
    assert b"built-in method items" not in response.data


def test_invalid_due_date_is_rejected(client):
    login(client)
    too_late = (date.today() + timedelta(days=31)).isoformat()
    response = client.post(
        "/equipment/1/request",
        data={"quantity": "1", "due_date": too_late, "pickup_time": "09:00", "purpose": "Class project", "csrf_token": csrf(client)},
        follow_redirects=True,
    )
    assert b"maximum of 30 days" in response.data


def test_admin_cannot_reduce_quantity_below_checked_out(app, client):
    login(client)
    due = (date.today() + timedelta(days=7)).isoformat()
    client.post("/equipment/1/request", data={"quantity": "2", "due_date": due, "pickup_time": "09:00", "purpose": "Media work", "csrf_token": csrf(client)})
    client.post("/logout", data={"csrf_token": csrf(client)})
    login(client, "admin", "Admin123!")
    client.post("/loans/1/decision", data={"decision": "approve", "admin_note": "", "csrf_token": csrf(client)})
    response = client.post(
        "/admin/equipment/1/edit",
        data={
            "asset_code": "CAM-001", "name": "Canon EOS 250D Camera", "category": "Cameras",
            "description": "Camera", "location": "Media Room A", "total_quantity": "1",
            "condition": "Excellent", "csrf_token": csrf(client),
        },
        follow_redirects=True,
    )
    assert b"currently on loan" in response.data


def test_borrower_can_register_with_strong_password(app, client):
    client.get("/register")
    response = client.post(
        "/register",
        data={
            "full_name": "New Borrower",
            "username": "new.borrower",
            "password": "StrongPass9!",
            "password_confirmation": "StrongPass9!",
            "accept_safety": "yes",
            "csrf_token": csrf(client),
        },
        follow_redirects=True,
    )
    assert b"Account created" in response.data
    with app.app_context():
        user = get_db().execute("SELECT * FROM users WHERE username='new.borrower'").fetchone()
        assert user["role"] == "borrower"
        assert user["password_hash"] != "StrongPass9!"


def test_registration_rejects_weak_password(client):
    client.get("/register")
    response = client.post(
        "/register",
        data={
            "full_name": "New Borrower", "username": "new.borrower", "password": "weak",
            "password_confirmation": "weak", "accept_safety": "yes", "csrf_token": csrf(client),
        },
        follow_redirects=True,
    )
    assert b"at least 10 characters" in response.data


def test_admin_can_add_and_remove_unused_category(app, client):
    login(client, "admin", "Admin123!")
    added = client.post(
        "/admin/categories",
        data={"name": "Art equipment", "csrf_token": csrf(client)},
        follow_redirects=True,
    )
    assert b"Category added" in added.data
    with app.app_context():
        category_id = get_db().execute("SELECT id FROM categories WHERE name='Art equipment'").fetchone()[0]
    removed = client.post(
        f"/admin/categories/{category_id}/delete",
        data={"csrf_token": csrf(client)},
        follow_redirects=True,
    )
    assert b"Category removed" in removed.data


def test_admin_cannot_remove_category_in_use(app, client):
    login(client, "admin", "Admin123!")
    with app.app_context():
        category_id = get_db().execute("SELECT id FROM categories WHERE name='Cameras'").fetchone()[0]
    response = client.post(
        f"/admin/categories/{category_id}/delete",
        data={"csrf_token": csrf(client)},
        follow_redirects=True,
    )
    assert b"before removing this category" in response.data


def test_every_seeded_physical_unit_has_unique_id(app):
    with app.app_context():
        db = get_db()
        total = db.execute("SELECT SUM(total_quantity) FROM equipment").fetchone()[0]
        units = db.execute("SELECT asset_tag FROM asset_units WHERE status!='retired'").fetchall()
        assert len(units) == total
        assert len({row["asset_tag"] for row in units}) == total


def test_admin_can_add_and_retire_available_physical_unit(app, client):
    login(client, "admin", "Admin123!")
    added = client.post(
        "/admin/equipment/1/units/add",
        data={"asset_tag": "CAMERA-LAB-99", "csrf_token": csrf(client)},
        follow_redirects=True,
    )
    assert b"Physical unit added" in added.data
    with app.app_context():
        db = get_db()
        unit = db.execute("SELECT * FROM asset_units WHERE asset_tag='CAMERA-LAB-99'").fetchone()
        assert db.execute("SELECT total_quantity FROM equipment WHERE id=1").fetchone()[0] == 5
        unit_id = unit["id"]
    retired = client.post(
        f"/admin/units/{unit_id}/retire",
        data={"csrf_token": csrf(client)},
        follow_redirects=True,
    )
    assert b"Physical unit retired" in retired.data
    with app.app_context():
        assert get_db().execute("SELECT total_quantity FROM equipment WHERE id=1").fetchone()[0] == 4
