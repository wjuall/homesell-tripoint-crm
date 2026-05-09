"""
TriPoint CRM — Foreclosure Pipeline & Transaction Management
"""
import os
from datetime import datetime, date, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    jsonify, abort,
)
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user,
)
from config import Config
from models import db, User, Case, Contact, Transaction, Activity, Task

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "info"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ── Helpers ──────────────────────────────────────────────────────────────────

PIPELINE_STATUSES = [
    "New Case", "In Mediation", "Mediation Failed", "Motion for Foreclosure",
    "Judgment of Foreclosure", "Sale Date Set", "Sale Date Extended",
    "Bankruptcy Stay Lifted", "In Bankruptcy", "Judgment Vacated",
    "Trial Date Set", "Surplus Funds", "On Hold",
]

LEAD_TYPES = [
    "Foreclosure", "2-4 Unit Multifamily", "Commercial", "Land",
    "Short Sale", "Probate", "Pre-Foreclosure", "Other",
]

TRANSACTION_TYPES = [
    "Retail Listing", "Off Market Listing", "Wholesale", "Rehab Project",
    "Purchase", "Referral", "Buyer Representation", "Surplus Funds",
]

TRANSACTION_STAGES = [
    "New", "Working", "Long Term Follow Up", "Appointment Set",
    "Offer Made", "Follow Up on Offer", "Contract Signed",
    "Under Construction", "Active Listing", "Listing Agreement Signed",
    "Sent to Attorneys", "Closed Won", "Closed Lost", "Revert",
]

RESPONSE_LABELS = {
    "interested": ("Interested", "success"),
    "needs_help": ("Needs Help", "warning"),
    "not_interested": ("Not Interested", "secondary"),
    "stop": ("DNC/Stop", "danger"),
    "wrong_number": ("Wrong Number", "dark"),
    "deceased": ("Deceased", "dark"),
    None: ("No Response", "light"),
    "": ("No Response", "light"),
}


@app.context_processor
def inject_globals():
    return {
        "now": datetime.utcnow(),
        "pipeline_statuses": PIPELINE_STATUSES,
        "lead_types_list": LEAD_TYPES,
        "transaction_types_list": TRANSACTION_TYPES,
        "transaction_stages": TRANSACTION_STAGES,
        "response_labels": RESPONSE_LABELS,
    }


# ── Auth ─────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ── Dashboard ────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    # Pipeline summary
    pipeline_counts = {}
    for status in PIPELINE_STATUSES:
        pipeline_counts[status] = Case.query.filter_by(status=status).filter(
            Case.date_closed.is_(None)
        ).count()

    total_active = sum(pipeline_counts.values())
    closed_count = Case.query.filter(Case.date_closed.isnot(None)).count()

    # Hot leads
    hot_leads = Contact.query.filter(
        Contact.response_status.in_(["interested", "needs_help"])
    ).filter_by(dnc=False).order_by(Contact.date_added.desc()).limit(10).all()

    # Recent activity
    recent = Activity.query.order_by(Activity.activity_date.desc()).limit(15).all()

    # Active transactions
    active_txns = Transaction.query.filter_by(is_closed=False).order_by(
        Transaction.updated_at.desc()
    ).limit(10).all()

    # Upcoming sale dates
    upcoming_sales = Case.query.filter(
        Case.sale_date >= date.today(),
        Case.date_closed.is_(None),
    ).order_by(Case.sale_date).limit(10).all()

    # Open tasks for current user
    my_tasks = Task.query.filter_by(
        assigned_to_id=current_user.id, status="open"
    ).order_by(Task.due_date.asc().nullslast()).limit(10).all()

    return render_template(
        "dashboard.html",
        pipeline_counts=pipeline_counts,
        total_active=total_active,
        closed_count=closed_count,
        hot_leads=hot_leads,
        recent=recent,
        active_txns=active_txns,
        upcoming_sales=upcoming_sales,
        my_tasks=my_tasks,
    )


# ── Pipeline ─────────────────────────────────────────────────────────────────

@app.route("/pipeline")
@login_required
def pipeline():
    status_filter = request.args.get("status", "")
    town_filter = request.args.get("town", "")
    lead_type_filter = request.args.get("lead_type", "")
    assignee_filter = request.args.get("assignee", "")
    search = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)

    query = Case.query.filter(Case.date_closed.is_(None))

    if status_filter:
        query = query.filter_by(status=status_filter)
    if town_filter:
        query = query.filter_by(town=town_filter)
    if lead_type_filter:
        query = query.filter_by(lead_type=lead_type_filter)
    if assignee_filter:
        if assignee_filter == "unassigned":
            query = query.filter(Case.assigned_to_id.is_(None))
        else:
            query = query.filter_by(assigned_to_id=int(assignee_filter))
    if search:
        query = query.filter(
            db.or_(
                Case.address.ilike(f"%{search}%"),
                Case.docket_number.ilike(f"%{search}%"),
                Case.town.ilike(f"%{search}%"),
            )
        )

    query = query.order_by(Case.sale_date.asc().nullslast(), Case.status_date.desc())
    cases = query.paginate(page=page, per_page=50, error_out=False)

    # Get distinct towns for filter
    towns = db.session.query(Case.town).filter(
        Case.date_closed.is_(None), Case.town.isnot(None)
    ).distinct().order_by(Case.town).all()
    towns = [t[0] for t in towns if t[0]]

    # Get distinct lead types for filter
    lead_types_in_use = db.session.query(Case.lead_type).filter(
        Case.date_closed.is_(None), Case.lead_type.isnot(None)
    ).distinct().order_by(Case.lead_type).all()
    lead_types_in_use = [lt[0] for lt in lead_types_in_use if lt[0]]

    # Pipeline counts for sidebar
    pipeline_counts = {}
    for status in PIPELINE_STATUSES:
        pipeline_counts[status] = Case.query.filter_by(status=status).filter(
            Case.date_closed.is_(None)
        ).count()

    users = User.query.filter_by(is_active=True).order_by(User.name).all()

    return render_template(
        "pipeline.html",
        cases=cases,
        towns=towns,
        lead_types=lead_types_in_use,
        pipeline_counts=pipeline_counts,
        users=users,
        status_filter=status_filter,
        town_filter=town_filter,
        lead_type_filter=lead_type_filter,
        assignee_filter=assignee_filter,
        search=search,
    )


@app.route("/case/<int:case_id>")
@login_required
def case_detail(case_id):
    case = db.session.get(Case, case_id) or abort(404)
    contacts = Contact.query.filter_by(case_id=case.id).all()
    activities = Activity.query.filter_by(case_id=case.id).order_by(
        Activity.activity_date.desc()
    ).limit(50).all()
    transactions = Transaction.query.filter_by(case_id=case.id).all()
    tasks = Task.query.filter_by(case_id=case.id, status="open").order_by(
        Task.due_date.asc().nullslast()
    ).all()
    users = User.query.filter_by(is_active=True).order_by(User.name).all()
    return render_template(
        "case_detail.html", case=case, contacts=contacts,
        activities=activities, transactions=transactions, tasks=tasks, users=users,
    )


@app.route("/case/<int:case_id>/update-status", methods=["POST"])
@login_required
def update_case_status(case_id):
    case = db.session.get(Case, case_id) or abort(404)
    new_status = request.form.get("status")
    if new_status and new_status != case.status:
        old = case.status
        case.status = new_status
        case.status_date = date.today()
        if new_status in ("Closed Won", "Closed Lost", "Dismissed"):
            case.date_closed = date.today()
        act = Activity(
            case_id=case.id, activity_type="note",
            subject=f"Status changed: {old} → {new_status}",
            created_by_id=current_user.id,
        )
        db.session.add(act)
        db.session.commit()
        flash(f"Status updated to {new_status}.", "success")
    return redirect(url_for("case_detail", case_id=case.id))


@app.route("/case/<int:case_id>/assign", methods=["POST"])
@login_required
def assign_case(case_id):
    case = db.session.get(Case, case_id) or abort(404)
    assignee_id = request.form.get("assigned_to_id")
    if assignee_id:
        user = db.session.get(User, int(assignee_id))
        case.assigned_to_id = user.id if user else None
        act = Activity(
            case_id=case.id, activity_type="note",
            subject=f"Lead assigned to {user.name}" if user else "Lead unassigned",
            created_by_id=current_user.id,
        )
        db.session.add(act)
    else:
        case.assigned_to_id = None
    db.session.commit()
    flash("Lead assignment updated.", "success")
    return redirect(url_for("case_detail", case_id=case.id))


@app.route("/case/<int:case_id>/convert", methods=["POST"])
@login_required
def convert_to_transaction(case_id):
    case = db.session.get(Case, case_id) or abort(404)
    txn_type = request.form.get("transaction_type", "Retail Listing")

    # Create transaction linked to this case
    txn = Transaction(
        name=f"{case.address or case.docket_number} — {txn_type}",
        property_address=case.address,
        town=case.town,
        transaction_type=txn_type,
        stage="New",
        case_id=case.id,
        assigned_to_id=current_user.id,
    )

    # Link to primary contact if one exists
    primary_contact = case.contacts.first()
    if primary_contact:
        txn.contact_id = primary_contact.id

    db.session.add(txn)

    # Log activity on the case
    act = Activity(
        case_id=case.id,
        activity_type="note",
        subject=f"Converted to {txn_type} transaction",
        created_by_id=current_user.id,
    )
    db.session.add(act)
    db.session.commit()

    flash(f"Transaction created: {txn.name}", "success")
    return redirect(url_for("transaction_detail", txn_id=txn.id))


@app.route("/case/<int:case_id>/notes", methods=["POST"])
@login_required
def update_case_notes(case_id):
    case = db.session.get(Case, case_id) or abort(404)
    case.notes = request.form.get("notes", "").strip() or None
    db.session.commit()
    flash("Notes saved.", "success")
    return redirect(url_for("case_detail", case_id=case.id))


@app.route("/case/<int:case_id>/edit", methods=["GET", "POST"])
@login_required
def edit_case(case_id):
    case = db.session.get(Case, case_id) or abort(404)
    if request.method == "POST":
        old_address = case.address
        old_status = case.status
        old_sale_date = case.sale_date

        case.address = request.form.get("address", "").strip() or case.address
        case.town = request.form.get("town", "").strip() or case.town
        case.docket_number = request.form.get("docket_number", "").strip() or case.docket_number
        case.county = request.form.get("county", "").strip() or None
        case.property_type = request.form.get("property_type", "").strip() or None
        case.case_url = request.form.get("case_url", "").strip() or None
        case.notes = request.form.get("notes", "").strip() or None
        case.lead_type = request.form.get("lead_type", case.lead_type)

        # Status change
        new_status = request.form.get("status", case.status)
        if new_status != old_status:
            case.status = new_status
            case.status_date = date.today()
            if new_status in ("Closed", "Dismissed"):
                case.date_closed = date.today()
            act = Activity(
                case_id=case.id, activity_type="note",
                subject=f"Status changed: {old_status} → {new_status}",
                created_by_id=current_user.id,
            )
            db.session.add(act)

        # Assignment
        assignee_id = request.form.get("assigned_to_id")
        case.assigned_to_id = int(assignee_id) if assignee_id else None

        # Sale date
        sale_date_str = request.form.get("sale_date", "").strip()
        if sale_date_str:
            new_sale_date = date.fromisoformat(sale_date_str)
            if new_sale_date != old_sale_date:
                case.sale_date = new_sale_date
                # Auto-set status if a valid future sale date is set
                if new_sale_date >= date.today() and case.status in ("New Case", "Motion for Foreclosure", "Judgment of Foreclosure"):
                    case.status = "Sale Date Set"
                    case.status_date = date.today()
                elif new_sale_date >= date.today() and old_sale_date and case.status == "Sale Date Set":
                    case.status = "Sale Date Extended"
                    case.status_date = date.today()
                act = Activity(
                    case_id=case.id, activity_type="note",
                    subject=f"Sale date {'updated' if old_sale_date else 'set'}: {new_sale_date.strftime('%b %d, %Y')}",
                    description=f"Previous: {old_sale_date.strftime('%b %d, %Y') if old_sale_date else 'None'}",
                    created_by_id=current_user.id,
                )
                db.session.add(act)
                if new_sale_date.weekday() != 5:
                    flash(f"Warning: {new_sale_date.strftime('%b %d, %Y')} is not a Saturday. CT foreclosure sales are always on Saturdays.", "warning")
        else:
            case.sale_date = None

        # Log address change
        if case.address != old_address and old_address:
            act = Activity(
                case_id=case.id, activity_type="note",
                subject=f"Address updated: {old_address} → {case.address}",
                created_by_id=current_user.id,
            )
            db.session.add(act)

        db.session.commit()
        flash("Lead updated.", "success")
        return redirect(url_for("case_detail", case_id=case.id))

    users = User.query.filter_by(is_active=True).order_by(User.name).all()
    return render_template("case_form.html", case=case, users=users)


@app.route("/case/<int:case_id>/log-activity", methods=["POST"])
@login_required
def log_case_activity(case_id):
    case = db.session.get(Case, case_id) or abort(404)
    act = Activity(
        case_id=case.id,
        activity_type=request.form.get("type", "note"),
        subject=request.form.get("subject", ""),
        description=request.form.get("description", ""),
        direction=request.form.get("direction") or None,
        status="completed",
        created_by_id=current_user.id,
    )
    db.session.add(act)
    db.session.commit()
    flash("Activity logged.", "success")
    return redirect(url_for("case_detail", case_id=case.id))


# ── Contacts ─────────────────────────────────────────────────────────────────

@app.route("/contacts")
@login_required
def contacts_list():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "")
    status_filter = request.args.get("status", "")

    query = Contact.query
    if search:
        query = query.filter(
            db.or_(
                Contact.name.ilike(f"%{search}%"),
                Contact.primary_phone.ilike(f"%{search}%"),
                Contact.email.ilike(f"%{search}%"),
            )
        )
    if status_filter:
        query = query.filter_by(response_status=status_filter)

    query = query.order_by(Contact.date_added.desc())
    contacts = query.paginate(page=page, per_page=50, error_out=False)

    return render_template(
        "contacts.html", contacts=contacts, search=search,
        status_filter=status_filter,
    )


@app.route("/contact/<int:contact_id>")
@login_required
def contact_detail(contact_id):
    contact = db.session.get(Contact, contact_id) or abort(404)
    activities = Activity.query.filter_by(contact_id=contact.id).order_by(
        Activity.activity_date.desc()
    ).limit(50).all()
    tasks = Task.query.filter_by(contact_id=contact.id, status="open").order_by(
        Task.due_date.asc().nullslast()
    ).all()
    users = User.query.filter_by(is_active=True).order_by(User.name).all()
    return render_template(
        "contact_detail.html", contact=contact, activities=activities,
        tasks=tasks, users=users,
    )


@app.route("/contact/<int:contact_id>/log-activity", methods=["POST"])
@login_required
def log_activity(contact_id):
    contact = db.session.get(Contact, contact_id) or abort(404)
    act = Activity(
        contact_id=contact.id,
        case_id=contact.case_id,
        activity_type=request.form.get("type", "note"),
        subject=request.form.get("subject", ""),
        description=request.form.get("description", ""),
        direction=request.form.get("direction") or None,
        status=request.form.get("status", "completed"),
        due_date=request.form.get("due_date") or None,
        created_by_id=current_user.id,
    )
    db.session.add(act)
    db.session.commit()
    flash("Activity logged.", "success")
    return redirect(url_for("contact_detail", contact_id=contact.id))


@app.route("/contact/<int:contact_id>/update-status", methods=["POST"])
@login_required
def update_contact_status(contact_id):
    contact = db.session.get(Contact, contact_id) or abort(404)
    new_status = request.form.get("response_status")
    contact.response_status = new_status
    if new_status == "stop":
        contact.dnc = True
        contact.dnc_date = datetime.utcnow()
    db.session.commit()
    flash(f"Contact status updated to {new_status}.", "success")
    return redirect(url_for("contact_detail", contact_id=contact.id))


@app.route("/contact/<int:contact_id>/notes", methods=["POST"])
@login_required
def update_contact_notes(contact_id):
    contact = db.session.get(Contact, contact_id) or abort(404)
    contact.notes = request.form.get("notes", "").strip() or None
    db.session.commit()
    flash("Notes saved.", "success")
    return redirect(url_for("contact_detail", contact_id=contact.id))


# ── Transactions ─────────────────────────────────────────────────────────────

@app.route("/transactions")
@login_required
def transactions_list():
    page = request.args.get("page", 1, type=int)
    show_closed = request.args.get("closed", "0") == "1"
    type_filter = request.args.get("type", "")

    query = Transaction.query
    if not show_closed:
        query = query.filter_by(is_closed=False)
    if type_filter:
        query = query.filter_by(transaction_type=type_filter)

    query = query.order_by(Transaction.updated_at.desc())
    txns = query.paginate(page=page, per_page=50, error_out=False)

    return render_template(
        "transactions.html", transactions=txns,
        show_closed=show_closed, type_filter=type_filter,
        transaction_types=TRANSACTION_TYPES,
    )


@app.route("/transaction/new", methods=["GET", "POST"])
@login_required
def new_transaction():
    if request.method == "POST":
        txn = Transaction(
            name=request.form.get("name"),
            property_address=request.form.get("property_address"),
            town=request.form.get("town"),
            transaction_type=request.form.get("transaction_type"),
            stage=request.form.get("stage", "New"),
            amount=float(request.form.get("amount") or 0) or None,
            list_price=float(request.form.get("list_price") or 0) or None,
            close_date=request.form.get("close_date") or None,
            assigned_to_id=request.form.get("assigned_to_id") or current_user.id,
            notes=request.form.get("notes"),
        )
        db.session.add(txn)
        db.session.commit()
        flash("Transaction created.", "success")
        return redirect(url_for("transaction_detail", txn_id=txn.id))
    users = User.query.filter_by(is_active=True).all()
    return render_template("transaction_form.html", txn=None, users=users, transaction_types=TRANSACTION_TYPES)


@app.route("/transaction/<int:txn_id>")
@login_required
def transaction_detail(txn_id):
    txn = db.session.get(Transaction, txn_id) or abort(404)
    activities = Activity.query.filter_by(transaction_id=txn.id).order_by(
        Activity.activity_date.desc()
    ).limit(50).all()
    tasks = Task.query.filter_by(transaction_id=txn.id, status="open").order_by(
        Task.due_date.asc().nullslast()
    ).all()
    users = User.query.filter_by(is_active=True).order_by(User.name).all()
    return render_template("transaction_detail.html", txn=txn, activities=activities,
                           tasks=tasks, users=users)


@app.route("/transaction/<int:txn_id>/notes", methods=["POST"])
@login_required
def update_transaction_notes(txn_id):
    txn = db.session.get(Transaction, txn_id) or abort(404)
    txn.notes = request.form.get("notes", "").strip() or None
    db.session.commit()
    flash("Notes saved.", "success")
    return redirect(url_for("transaction_detail", txn_id=txn.id))


@app.route("/transaction/<int:txn_id>/log-activity", methods=["POST"])
@login_required
def log_transaction_activity(txn_id):
    txn = db.session.get(Transaction, txn_id) or abort(404)
    act = Activity(
        transaction_id=txn.id,
        case_id=txn.case_id,
        activity_type=request.form.get("type", "note"),
        subject=request.form.get("subject", ""),
        description=request.form.get("description", ""),
        direction=request.form.get("direction") or None,
        status="completed",
        due_date=request.form.get("due_date") or None,
        created_by_id=current_user.id,
    )
    db.session.add(act)
    db.session.commit()
    flash("Activity logged.", "success")
    return redirect(url_for("transaction_detail", txn_id=txn.id))


@app.route("/transaction/<int:txn_id>/edit", methods=["GET", "POST"])
@login_required
def edit_transaction(txn_id):
    txn = db.session.get(Transaction, txn_id) or abort(404)
    if request.method == "POST":
        txn.name = request.form.get("name", txn.name)
        txn.property_address = request.form.get("property_address", txn.property_address)
        txn.town = request.form.get("town", txn.town)
        txn.transaction_type = request.form.get("transaction_type", txn.transaction_type)
        new_stage = request.form.get("stage", txn.stage)
        if new_stage != txn.stage:
            old_stage = txn.stage
            txn.stage = new_stage
            act = Activity(
                transaction_id=txn.id, activity_type="note",
                subject=f"Stage changed: {old_stage} → {new_stage}",
                created_by_id=current_user.id,
            )
            db.session.add(act)
        txn.amount = float(request.form.get("amount") or 0) or None
        txn.list_price = float(request.form.get("list_price") or 0) or None
        txn.contract_price = float(request.form.get("contract_price") or 0) or None
        txn.close_date = request.form.get("close_date") or None
        txn.assigned_to_id = request.form.get("assigned_to_id") or txn.assigned_to_id
        txn.notes = request.form.get("notes")
        if new_stage in ("Closed Won", "Closed Lost"):
            txn.is_closed = True
            txn.is_won = new_stage == "Closed Won"
        txn.updated_at = datetime.utcnow()
        db.session.commit()
        flash("Transaction updated.", "success")
        return redirect(url_for("transaction_detail", txn_id=txn.id))
    users = User.query.filter_by(is_active=True).all()
    return render_template("transaction_form.html", txn=txn, users=users, transaction_types=TRANSACTION_TYPES)


# ── Leads Board ──────────────────────────────────────────────────────────────

@app.route("/leads")
@login_required
def leads():
    interested = Contact.query.filter_by(
        response_status="interested", dnc=False
    ).order_by(Contact.date_added.desc()).all()

    needs_help = Contact.query.filter_by(
        response_status="needs_help", dnc=False
    ).order_by(Contact.date_added.desc()).all()

    return render_template("leads.html", interested=interested, needs_help=needs_help)


# ── Tasks ────────────────────────────────────────────────────────────────────

@app.route("/tasks")
@login_required
def tasks_list():
    show_completed = request.args.get("completed", "0") == "1"
    filter_user = request.args.get("user", "")

    query = Task.query
    if not show_completed:
        query = query.filter_by(status="open")
    if filter_user:
        query = query.filter_by(assigned_to_id=int(filter_user))

    query = query.order_by(
        Task.due_date.asc().nullslast(),
        Task.priority.desc(),
        Task.created_at.desc(),
    )
    tasks = query.all()
    users = User.query.filter_by(is_active=True).all()

    return render_template("tasks.html", tasks=tasks, users=users,
                           show_completed=show_completed, filter_user=filter_user)


@app.route("/tasks/new", methods=["POST"])
@login_required
def create_task():
    task = Task(
        title=request.form.get("title", "").strip(),
        description=request.form.get("description", "").strip() or None,
        priority=request.form.get("priority", "normal"),
        due_date=request.form.get("due_date") or None,
        assigned_to_id=int(request.form.get("assigned_to_id") or current_user.id),
        created_by_id=current_user.id,
        case_id=int(request.form.get("case_id")) if request.form.get("case_id") else None,
        contact_id=int(request.form.get("contact_id")) if request.form.get("contact_id") else None,
        transaction_id=int(request.form.get("transaction_id")) if request.form.get("transaction_id") else None,
    )
    db.session.add(task)
    db.session.commit()
    flash("Task created.", "success")

    # Redirect back to referring page if present
    next_url = request.form.get("next") or url_for("tasks_list")
    return redirect(next_url)


@app.route("/tasks/<int:task_id>/complete", methods=["POST"])
@login_required
def complete_task(task_id):
    task = db.session.get(Task, task_id) or abort(404)
    task.status = "completed"
    task.completed_at = datetime.utcnow()
    db.session.commit()
    flash("Task completed.", "success")

    next_url = request.form.get("next") or url_for("tasks_list")
    return redirect(next_url)


@app.route("/tasks/<int:task_id>/reopen", methods=["POST"])
@login_required
def reopen_task(task_id):
    task = db.session.get(Task, task_id) or abort(404)
    task.status = "open"
    task.completed_at = None
    db.session.commit()
    flash("Task reopened.", "success")
    return redirect(url_for("tasks_list", completed="1"))


# ── Settings / User Management ───────────────────────────────────────────────

@app.route("/settings")
@login_required
@admin_required
def settings():
    users = User.query.all()
    return render_template("settings.html", users=users)


@app.route("/settings/add-user", methods=["POST"])
@login_required
@admin_required
def add_user():
    email = request.form.get("email", "").strip().lower()
    if User.query.filter_by(email=email).first():
        flash("User with that email already exists.", "danger")
        return redirect(url_for("settings"))
    user = User(
        email=email,
        name=request.form.get("name", ""),
        role=request.form.get("role", "agent"),
        phone=request.form.get("phone"),
    )
    user.set_password(request.form.get("password", "changeme"))
    db.session.add(user)
    db.session.commit()
    flash(f"User {user.name} created.", "success")
    return redirect(url_for("settings"))


# ── API Endpoints ────────────────────────────────────────────────────────────

@app.route("/api/pipeline-summary")
@login_required
def api_pipeline_summary():
    counts = {}
    for status in PIPELINE_STATUSES:
        counts[status] = Case.query.filter_by(status=status).filter(
            Case.date_closed.is_(None)
        ).count()
    return jsonify(counts)


@app.route("/api/search")
@login_required
def api_search():
    q = request.args.get("q", "")
    if len(q) < 2:
        return jsonify([])
    results = []
    # Search contacts
    contacts = Contact.query.filter(
        db.or_(
            Contact.name.ilike(f"%{q}%"),
            Contact.primary_phone.ilike(f"%{q}%"),
        )
    ).limit(5).all()
    for c in contacts:
        results.append({
            "type": "contact", "id": c.id, "label": c.name or "Unknown",
            "sub": c.primary_phone or "",
            "url": url_for("contact_detail", contact_id=c.id),
        })
    # Search cases
    cases = Case.query.filter(
        db.or_(
            Case.address.ilike(f"%{q}%"),
            Case.docket_number.ilike(f"%{q}%"),
        )
    ).limit(5).all()
    for c in cases:
        results.append({
            "type": "case", "id": c.id, "label": c.address or c.docket_number,
            "sub": f"{c.town} — {c.status}",
            "url": url_for("case_detail", case_id=c.id),
        })
    return jsonify(results)


# ── Init DB & Seed ───────────────────────────────────────────────────────────

def init_db():
    """Create tables, run migrations, and seed admin user if needed."""
    db.create_all()

    # ── Schema migrations (add columns that db.create_all won't add) ─────
    from sqlalchemy import text, inspect
    with db.engine.connect() as conn:
        inspector = inspect(db.engine)

        # Add lead_type to cases if missing
        case_cols = [c["name"] for c in inspector.get_columns("cases")]
        if "lead_type" not in case_cols:
            conn.execute(text("ALTER TABLE cases ADD COLUMN lead_type VARCHAR(40) DEFAULT 'Foreclosure'"))
            conn.commit()

        # Add assigned_to_id to cases if missing
        if "assigned_to_id" not in case_cols:
            conn.execute(text("ALTER TABLE cases ADD COLUMN assigned_to_id INTEGER REFERENCES users(id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_cases_assigned_to_id ON cases (assigned_to_id)"))
            conn.commit()

        # Make docket_number nullable if it has a NOT NULL constraint
        # (already nullable in new schema, but existing DB may differ)

        # Add index on transaction_type if missing
        txn_cols = [c["name"] for c in inspector.get_columns("transactions")]
        # transaction_type column already exists, just may need wider varchar

    if not User.query.filter_by(email="warren@homesellct.com").first():
        admin = User(
            email="warren@homesellct.com",
            name="Warren Juall",
            role="admin",
            phone="2036318040",
        )
        admin.set_password("tripoint2026")
        db.session.add(admin)

    if not User.query.filter_by(email="manny@tripointrealestatect.com").first():
        manny = User(
            email="manny@tripointrealestatect.com",
            name="Manny Kavroudakis",
            role="agent",
            phone="2036803095",
        )
        manny.set_password("tripoint2026")
        db.session.add(manny)

    db.session.commit()


@app.cli.command("init-db")
def init_db_command():
    init_db()
    print("Database initialized.")


@app.cli.command("import-sf")
def import_sf_command():
    """Import Salesforce export data."""
    from import_salesforce import run_import
    run_import(db.session)


# ── One-time admin import route ──────────────────────────────────────────────

@app.route("/admin/import-sf", methods=["GET", "POST"])
@login_required
@admin_required
def admin_import_sf():
    """One-time import of Salesforce data. Admin only."""
    if request.method == "GET":
        case_count = Case.query.count()
        txn_count = Transaction.query.count()
        contact_count = Contact.query.count()
        return f"""
        <html><head><title>SF Import</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        </head><body class="bg-light p-5">
        <div class="container" style="max-width:600px;">
        <h2>Salesforce Import</h2>
        <div class="alert alert-info">
            <strong>Current data:</strong> {case_count} leads, {contact_count} contacts, {txn_count} transactions
        </div>
        <p>This will import:</p>
        <ul>
            <li><strong>144 leads</strong> (open SF Opportunities of type "Opportunity")</li>
            <li><strong>19 transactions</strong> (Retail Listing, Rehab Project, Wholesale, etc.)</li>
            <li><strong>~241 contacts</strong> linked to those opportunities</li>
            <li><strong>8 hot SMS leads</strong> from recent outreach</li>
        </ul>
        <form method="POST">
            <button type="submit" class="btn btn-primary btn-lg">Run Import</button>
            <a href="/" class="btn btn-outline-secondary btn-lg ms-2">Cancel</a>
        </form>
        </div></body></html>
        """

    # POST — run the import
    import io, sys
    from import_selective import run_selective_import

    sf_dir = os.path.join(os.path.dirname(__file__), "sf_data")
    output = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = output

    try:
        run_selective_import(db.session, sf_dir)
        sys.stdout = old_stdout
        log = output.getvalue()
        return f"""
        <html><head><title>Import Complete</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        </head><body class="bg-light p-5">
        <div class="container" style="max-width:700px;">
        <h2 class="text-success">Import Complete!</h2>
        <pre style="background:#1e293b;color:#e2e8f0;padding:20px;border-radius:8px;font-size:0.85rem;">{log}</pre>
        <a href="/" class="btn btn-primary btn-lg mt-3">Go to Dashboard</a>
        </div></body></html>
        """
    except Exception as e:
        sys.stdout = old_stdout
        log = output.getvalue()
        return f"""
        <html><head><title>Import Error</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        </head><body class="bg-light p-5">
        <div class="container" style="max-width:700px;">
        <h2 class="text-danger">Import Error</h2>
        <pre style="background:#1e293b;color:#e2e8f0;padding:20px;border-radius:8px;">{log}\n\nERROR: {e}</pre>
        <a href="/" class="btn btn-outline-secondary btn-lg mt-3">Back to Dashboard</a>
        </div></body></html>
        """


@app.route("/admin/import-tasks", methods=["GET", "POST"])
@login_required
@admin_required
def admin_import_tasks():
    """Import SF Task Report as activities/tasks. Admin only."""
    activity_count = Activity.query.count()
    task_count = Task.query.count()

    if request.method == "GET":
        return f"""
        <html><head><title>SF Task Import</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        </head><body class="bg-light p-5">
        <div class="container" style="max-width:600px;">
        <h2>Salesforce Task/Activity Import</h2>
        <div class="alert alert-info">
            <strong>Current data:</strong> {activity_count} activities, {task_count} tasks
        </div>
        <p>This will import activity history from the SF Task Report:</p>
        <ul>
            <li>Completed tasks → Activity records (calls, emails, follow-ups, etc.)</li>
            <li>Open tasks → CRM Tasks (assigned to Warren or Manny)</li>
            <li>Only imports records matching existing CRM leads/transactions</li>
            <li>Excludes Danica's records</li>
            <li>Skips duplicates if run again</li>
        </ul>
        <form method="POST">
            <button type="submit" class="btn btn-primary btn-lg">Run Task Import</button>
            <a href="/" class="btn btn-outline-secondary btn-lg ms-2">Cancel</a>
        </form>
        </div></body></html>
        """

    import io, sys
    from import_selective import run_task_import

    sf_dir = os.path.join(os.path.dirname(__file__), "sf_data")
    output = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = output

    try:
        run_task_import(db.session, sf_dir)
        sys.stdout = old_stdout
        log = output.getvalue()
        return f"""
        <html><head><title>Task Import Complete</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        </head><body class="bg-light p-5">
        <div class="container" style="max-width:700px;">
        <h2 class="text-success">Task Import Complete!</h2>
        <pre style="background:#1e293b;color:#e2e8f0;padding:20px;border-radius:8px;font-size:0.85rem;">{log}</pre>
        <a href="/" class="btn btn-primary btn-lg mt-3">Go to Dashboard</a>
        </div></body></html>
        """
    except Exception as e:
        sys.stdout = old_stdout
        log = output.getvalue()
        return f"""
        <html><head><title>Task Import Error</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
        </head><body class="bg-light p-5">
        <div class="container" style="max-width:700px;">
        <h2 class="text-danger">Task Import Error</h2>
        <pre style="background:#1e293b;color:#e2e8f0;padding:20px;border-radius:8px;">{log}\n\nERROR: {e}</pre>
        <a href="/" class="btn btn-outline-secondary btn-lg mt-3">Back to Dashboard</a>
        </div></body></html>
        """


@app.route("/admin/fix-sale-dates", methods=["GET", "POST"])
@login_required
@admin_required
def admin_fix_sale_dates():
    """Review and clean up bogus sale dates. Admin only."""
    # Gather stats
    cases_with_dates = Case.query.filter(
        Case.sale_date.isnot(None), Case.date_closed.is_(None)
    ).order_by(Case.sale_date).all()

    # Categorize
    non_saturday = []
    quarter_end = []
    valid_upcoming = []
    valid_past = []
    quarter_ends = {(3, 31), (6, 30), (9, 30), (12, 31)}

    for c in cases_with_dates:
        sd = c.sale_date
        is_qe = (sd.month, sd.day) in quarter_ends
        is_sat = sd.weekday() == 5

        if is_qe and not is_sat:
            quarter_end.append(c)
        elif not is_sat:
            non_saturday.append(c)
        elif sd >= date.today():
            valid_upcoming.append(c)
        else:
            valid_past.append(c)

    # Cases with status mismatch (have future sale date but not "Sale Date Set")
    mismatched = [c for c in valid_upcoming if c.status not in ("Sale Date Set", "Sale Date Extended")]

    if request.method == "GET":
        return render_template(
            "admin_fix_sale_dates.html",
            cases_with_dates=cases_with_dates,
            quarter_end=quarter_end,
            non_saturday=non_saturday,
            valid_upcoming=valid_upcoming,
            valid_past=valid_past,
            mismatched=mismatched,
        )

    # POST — apply fixes
    action = request.form.get("action", "")
    fixed = 0

    if action == "clear_bogus":
        # Clear quarter-end placeholder dates
        for c in quarter_end:
            c.sale_date = None
            fixed += 1

    elif action == "clear_non_saturday":
        # Clear all non-Saturday dates
        for c in non_saturday + quarter_end:
            c.sale_date = None
            fixed += 1

    elif action == "fix_statuses":
        # Update status for cases with valid future sale dates
        for c in mismatched:
            old = c.status
            c.status = "Sale Date Set"
            c.status_date = date.today()
            act = Activity(
                case_id=c.id, activity_type="note",
                subject=f"Status auto-updated: {old} → Sale Date Set (has sale date {c.sale_date.strftime('%b %d, %Y')})",
                created_by_id=current_user.id,
            )
            db.session.add(act)
            fixed += 1

    db.session.commit()
    flash(f"Fixed {fixed} records.", "success")
    return redirect(url_for("admin_fix_sale_dates"))


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
