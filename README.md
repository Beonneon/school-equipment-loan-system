# EquipTrack - School Equipment Loan System

EquipTrack is a secure, responsive web application for managing the borrowing and return of school cameras, laptops, sports equipment and instruments. It gives borrowers a searchable catalogue and loan history while giving equipment coordinators stock control, approvals, overdue visibility and an audit log.

## Features

- Secure sign-in with hashed passwords and role-based access control
- Searchable equipment catalogue with category filters and live stock counts
- Loan requests with quantity, purpose and a maximum 30-day due date
- Coordinator approval/denial workflow with optional notes
- Transaction-safe stock updates on approval and return
- Pending cancellation, return history and automatic overdue highlighting
- Add/edit inventory with safeguards for equipment currently checked out
- Audit log for sign-ins, equipment changes, approvals, denials and returns
- Responsive, accessible interface and integrated user guide
- Automated tests for authentication, permissions, CSRF, validation and stock integrity

## Quick start

Requirements: Python 3.11 or newer.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000>.

Demo accounts:

| Role | Username | Password |
|---|---|---|
| Coordinator | `admin` | `Admin123!` |
| Borrower | `student` | `Student123!` |

The SQLite database is created automatically at `instance/equipment.db` with four sample equipment records. In a deployed environment, set a strong random `SECRET_KEY` environment variable and replace the demo credentials.

## Test

```powershell
pytest -q
```

The test suite creates an isolated temporary database and does not alter local development data.

## Architecture

```text
Browser
  |  HTTPS forms + CSRF token
Flask routes / role checks
  |  parameterised SQL + transactions
SQLite database
  |- users
  |- equipment
  |- loans
  `- audit_log
```

The application uses server-rendered Jinja templates so it remains simple to deploy and easy to inspect. SQLite provides relational constraints and atomic inventory transactions without requiring a separate database server for a school-scale project.

## Security and privacy

- Passwords use Werkzeug's salted password hashing; plain-text passwords are never stored.
- Every state-changing form is protected by a session-specific CSRF token.
- Coordinator-only routes enforce server-side role checks.
- SQL statements use placeholders to prevent injection.
- Session cookies are HTTP-only and use `SameSite=Lax`.
- Inputs are length-, range- and choice-validated on the server.
- Only account identity and loan-related information required by the system is stored.
- The audit log supports accountability without collecting location or sensitive personal profiles.

For production, use HTTPS, set `SECRET_KEY`, remove demo credentials, establish backup/retention rules, and connect authentication to the school's approved identity provider.

## Project files

- `app.py` - application factory, schema, validation and routes
- `config.py` - runtime configuration
- `templates/` - accessible server-rendered pages
- `static/` - responsive design and interactions
- `tests/` - automated workflow and security tests
- `docs/TESTING.md` - test cases and browser QA evidence

## AI-use acknowledgement

Generative AI was used with human oversight to help plan, implement, test, debug and document this project. The assessment brief explicitly permits specified AI use with acknowledgement. The student remains responsible for understanding the code, checking the results and explaining the system in the demonstration.

