from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default="agent")  # admin, agent, va
    phone = db.Column(db.String(20))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Case(db.Model):
    """Lead — foreclosure case, multifamily, or other lead type."""
    __tablename__ = "cases"
    id = db.Column(db.Integer, primary_key=True)
    docket_number = db.Column(db.String(30), unique=True, nullable=True, index=True)
    lead_type = db.Column(db.String(40), default="Foreclosure", index=True)
    # Foreclosure, 2-4 Unit Multifamily, Commercial, Land, Short Sale, etc.
    address = db.Column(db.String(200))
    town = db.Column(db.String(60), index=True)
    county = db.Column(db.String(40))
    status = db.Column(db.String(40), default="New Case", index=True)
    status_date = db.Column(db.Date)
    sale_date = db.Column(db.Date)
    next_session_date = db.Column(db.Date)
    property_type = db.Column(db.String(30))
    case_url = db.Column(db.String(300))
    is_llc = db.Column(db.Boolean, default=False)
    date_added = db.Column(db.Date, default=date.today)
    date_closed = db.Column(db.Date)
    source = db.Column(db.String(30))
    notes = db.Column(db.Text)
    # SF import fields
    sf_case_id = db.Column(db.String(18))
    sf_account_id = db.Column(db.String(18))

    contacts = db.relationship("Contact", backref="case", lazy="dynamic")
    activities = db.relationship("Activity", backref="case", lazy="dynamic")


class Contact(db.Model):
    """Person associated with a foreclosure case or transaction."""
    __tablename__ = "contacts"
    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("cases.id"), index=True)
    first_name = db.Column(db.String(60))
    last_name = db.Column(db.String(60))
    name = db.Column(db.String(120))
    contact_type = db.Column(db.String(30))  # owner-occupant, spouse, attorney, etc.
    primary_phone = db.Column(db.String(15))
    secondary_phone = db.Column(db.String(15))
    additional_phones = db.Column(db.String(200))
    email = db.Column(db.String(120))
    mailing_address = db.Column(db.String(200))
    dnc = db.Column(db.Boolean, default=False)
    dnc_date = db.Column(db.DateTime)
    response_status = db.Column(db.String(30), index=True)
    # interested, not_interested, stop, wrong_number, deceased, needs_help
    salesforce_promoted = db.Column(db.Boolean, default=False)
    salesforce_date = db.Column(db.Date)
    date_added = db.Column(db.Date, default=date.today)
    skip_trace_date = db.Column(db.Date)
    skip_trace_source = db.Column(db.String(30))
    notes = db.Column(db.Text)
    # SF import
    sf_contact_id = db.Column(db.String(18))
    sf_account_id = db.Column(db.String(18))

    activities = db.relationship("Activity", backref="contact", lazy="dynamic")


class Transaction(db.Model):
    """Real estate transaction — listing, flip, purchase, etc."""
    __tablename__ = "transactions"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200))  # e.g. "123 Main St — Listing"
    property_address = db.Column(db.String(200))
    town = db.Column(db.String(60))
    transaction_type = db.Column(db.String(40), index=True)
    # Retail Listing, Off Market Listing, Wholesale, Rehab Project,
    # Purchase, Referral, Buyer Representation, Surplus Funds
    stage = db.Column(db.String(40), default="New")
    # Stages: New → Working → Appointment Set → Offer Made → Follow Up on Offer
    #       → Contract Signed → Under Construction → Active Listing → Closed Won / Closed Lost
    amount = db.Column(db.Float)
    list_price = db.Column(db.Float)
    contract_price = db.Column(db.Float)
    close_date = db.Column(db.Date)
    description = db.Column(db.Text)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    contact_id = db.Column(db.Integer, db.ForeignKey("contacts.id"))
    case_id = db.Column(db.Integer, db.ForeignKey("cases.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_closed = db.Column(db.Boolean, default=False)
    is_won = db.Column(db.Boolean, default=False)
    loss_reason = db.Column(db.String(100))
    notes = db.Column(db.Text)
    # SF import
    sf_opportunity_id = db.Column(db.String(18))

    assigned_to = db.relationship("User", backref="transactions")
    contact = db.relationship("Contact", backref="transactions")
    case = db.relationship("Case", backref="transactions")
    activities = db.relationship("Activity", backref="transaction", lazy="dynamic")


class Activity(db.Model):
    """Activity log — calls, SMS, emails, notes, letters, tasks."""
    __tablename__ = "activities"
    id = db.Column(db.Integer, primary_key=True)
    contact_id = db.Column(db.Integer, db.ForeignKey("contacts.id"), index=True)
    case_id = db.Column(db.Integer, db.ForeignKey("cases.id"), index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"))
    activity_type = db.Column(db.String(30))
    # call, sms, email, note, letter, skip_trace, task, meeting
    subject = db.Column(db.String(200))
    description = db.Column(db.Text)
    direction = db.Column(db.String(10))  # inbound, outbound, null for tasks/notes
    status = db.Column(db.String(20), default="completed")
    # completed, open, pending, missed
    activity_date = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    due_date = db.Column(db.Date)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    phone_used = db.Column(db.String(15))
    email_used = db.Column(db.String(120))
    # SF import
    sf_task_id = db.Column(db.String(18))

    created_by = db.relationship("User", backref="activities")


class Task(db.Model):
    """User task — follow-up, to-do, reminder."""
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    priority = db.Column(db.String(10), default="normal")  # high, normal, low
    status = db.Column(db.String(20), default="open", index=True)  # open, completed
    due_date = db.Column(db.Date, index=True)
    assigned_to_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    case_id = db.Column(db.Integer, db.ForeignKey("cases.id"))
    contact_id = db.Column(db.Integer, db.ForeignKey("contacts.id"))
    transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)

    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id], backref="assigned_tasks")
    created_by = db.relationship("User", foreign_keys=[created_by_id], backref="created_tasks")
    case = db.relationship("Case", backref="tasks")
    contact = db.relationship("Contact", backref="tasks")
    transaction = db.relationship("Transaction", backref="tasks")
