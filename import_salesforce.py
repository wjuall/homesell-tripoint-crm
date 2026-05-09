"""
Import Salesforce export data into TriPoint CRM.

Usage:
    flask import-sf          # Run from Flask CLI
    python import_salesforce.py /path/to/sf_export/   # Standalone
"""
import csv
import os
import sys
from datetime import datetime, date


def open_csv(path):
    return open(path, encoding="latin-1")


def parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_datetime(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def run_import(session, sf_dir="sf_export"):
    from models import Case, Contact, Transaction, Activity, User

    print("Starting Salesforce import...")

    # ── 1. Import Cases ──────────────────────────────────────────────────
    case_file = os.path.join(sf_dir, "Case.csv")
    if os.path.exists(case_file):
        imported = 0
        skipped = 0
        with open_csv(case_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                docket = row.get("Docket_Number__c", "").strip()
                if not docket:
                    skipped += 1
                    continue

                existing = Case.query.filter_by(docket_number=docket).first()
                if existing:
                    # Update SF references
                    existing.sf_case_id = row.get("Id")
                    existing.sf_account_id = row.get("AccountId")
                    if not existing.case_url and row.get("Case_Link__c"):
                        existing.case_url = row["Case_Link__c"]
                    skipped += 1
                    continue

                # Map SF status to our statuses
                sf_status = row.get("Status", "New Case")
                status_map = {
                    "Judgment of Foreclosure": "Judgment of Foreclosure",
                    "Sale Date Extended": "Sale Date Extended",
                    "Motion for Foreclosure": "Motion for Foreclosure",
                    "In Mediation": "In Mediation",
                    "Mediation Failed": "Mediation Failed",
                    "In Bankruptcy": "In Bankruptcy",
                    "Bankruptcy Stay Lifted": "Bankruptcy Stay Lifted",
                    "Surplus Funds": "Surplus Funds",
                    "On Hold": "On Hold",
                    "Closed": "Closed",
                }
                status = status_map.get(sf_status, sf_status)

                # Get address from Account if linked
                address = row.get("Subject", "")
                if not address:
                    address = row.get("Property__c", "")

                case = Case(
                    docket_number=docket,
                    address=address or None,
                    status=status,
                    status_date=parse_date(row.get("LastModifiedDate")),
                    sale_date=parse_date(row.get("Sale_Date__c")),
                    case_url=row.get("Case_Link__c") or None,
                    date_added=parse_date(row.get("Case_Open_Date__c")) or parse_date(row.get("CreatedDate")),
                    date_closed=parse_date(row.get("Case_Closed_Date__c")) or (
                        parse_date(row.get("ClosedDate")) if row.get("IsClosed") == "1" else None
                    ),
                    source="salesforce",
                    sf_case_id=row.get("Id"),
                    sf_account_id=row.get("AccountId"),
                )
                session.add(case)
                imported += 1

                if imported % 500 == 0:
                    session.flush()
                    print(f"  Cases: {imported} imported...")

        session.flush()
        print(f"  Cases: {imported} imported, {skipped} skipped (existing/no docket)")

    # ── 2. Import Contacts ───────────────────────────────────────────────
    contact_file = os.path.join(sf_dir, "Contact.csv")
    if os.path.exists(contact_file):
        # Build case lookup by SF Account ID
        case_lookup = {}
        for c in Case.query.filter(Case.sf_account_id.isnot(None)).all():
            case_lookup[c.sf_account_id] = c.id

        imported = 0
        skipped = 0
        with open_csv(contact_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("IsDeleted") == "1":
                    skipped += 1
                    continue

                sf_account = row.get("AccountId", "")
                case_id = case_lookup.get(sf_account)

                first = row.get("FirstName", "").strip()
                last = row.get("LastName", "").strip()
                name = f"{first} {last}".strip()
                if not name:
                    skipped += 1
                    continue

                # Check for existing by SF ID
                sf_id = row.get("Id")
                existing = Contact.query.filter_by(sf_contact_id=sf_id).first()
                if existing:
                    skipped += 1
                    continue

                contact = Contact(
                    case_id=case_id,
                    first_name=first or None,
                    last_name=last or None,
                    name=name,
                    contact_type=row.get("Contact_Type__c") or None,
                    primary_phone=row.get("Phone", "").replace("-", "").replace("(", "").replace(")", "").replace(" ", "").strip() or None,
                    secondary_phone=row.get("Phone_2__c", "").replace("-", "").replace("(", "").replace(")", "").replace(" ", "").strip() or None,
                    email=row.get("Email") or None,
                    mailing_address=(
                        f"{row.get('mail_address__c', '')} {row.get('mail_city__c', '')} {row.get('mail_state__c', '')} {row.get('mail_zip__c', '')}".strip()
                        or None
                    ),
                    date_added=parse_date(row.get("CreatedDate")),
                    sf_contact_id=sf_id,
                    sf_account_id=sf_account or None,
                )
                session.add(contact)
                imported += 1

                if imported % 500 == 0:
                    session.flush()
                    print(f"  Contacts: {imported} imported...")

        session.flush()
        print(f"  Contacts: {imported} imported, {skipped} skipped")

    # ── 3. Import Opportunities as Transactions ──────────────────────────
    opp_file = os.path.join(sf_dir, "Opportunity.csv")
    if os.path.exists(opp_file):
        # Get user lookup
        users = {u.email: u.id for u in User.query.all()}
        default_user = User.query.first()

        imported = 0
        with open_csv(opp_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("IsDeleted") == "1":
                    continue

                sf_id = row.get("Id")
                existing = Transaction.query.filter_by(sf_opportunity_id=sf_id).first()
                if existing:
                    continue

                # Map SF stage
                stage = row.get("StageName", "New")
                stage_map = {
                    "Closed Lost": "Closed Lost",
                    "Closed Won": "Closed Won",
                    "Long Term Follow Up": "Long Term Follow Up",
                }
                stage = stage_map.get(stage, stage)

                # Determine type from name/stage
                name = row.get("Name", "")
                txn_type = "foreclosure"
                name_lower = name.lower()
                if "listing" in name_lower or stage == "Active Listing":
                    txn_type = "listing"
                elif "flip" in name_lower or stage == "Under Construction":
                    txn_type = "flip"
                elif "purchase" in name_lower:
                    txn_type = "purchase"
                elif "referral" in name_lower:
                    txn_type = "referral"

                # Link to case via account
                case_id = None
                account_id = row.get("AccountId")
                if account_id:
                    case = Case.query.filter_by(sf_account_id=account_id).first()
                    if case:
                        case_id = case.id

                txn = Transaction(
                    name=name or "Untitled",
                    property_address=row.get("Property__c") or None,
                    transaction_type=txn_type,
                    stage=stage,
                    amount=float(row.get("Amount") or 0) or None,
                    list_price=float(row.get("List_Price__c") or 0) or None,
                    contract_price=float(row.get("Contract_Price__c") or 0) or None,
                    close_date=parse_date(row.get("CloseDate")),
                    is_closed=row.get("IsClosed") == "1",
                    is_won=row.get("IsWon") == "1",
                    loss_reason=row.get("Loss_Reason__c") or None,
                    case_id=case_id,
                    assigned_to_id=default_user.id if default_user else None,
                    created_at=parse_datetime(row.get("CreatedDate")),
                    sf_opportunity_id=sf_id,
                    notes=row.get("Description") or None,
                )
                session.add(txn)
                imported += 1

        session.flush()
        print(f"  Transactions: {imported} imported from Opportunities")

    # ── 4. Import Tasks as Activities ────────────────────────────────────
    task_file = os.path.join(sf_dir, "Task.csv")
    if os.path.exists(task_file):
        # Build contact lookup by SF ID
        contact_lookup = {}
        for c in Contact.query.filter(Contact.sf_contact_id.isnot(None)).all():
            contact_lookup[c.sf_contact_id] = c.id

        # Build case lookup by SF Account ID
        case_by_account = {}
        for c in Case.query.filter(Case.sf_account_id.isnot(None)).all():
            case_by_account[c.sf_account_id] = c.id

        imported = 0
        skipped = 0
        batch_size = 2000

        with open_csv(task_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("IsDeleted") == "1":
                    skipped += 1
                    continue

                subject = row.get("Subject", "").strip()
                if not subject:
                    skipped += 1
                    continue

                # Map subject to activity type
                subj_lower = subject.lower()
                if "call" in subj_lower:
                    act_type = "call"
                    direction = "outbound" if "outgoing" in subj_lower else (
                        "inbound" if "incoming" in subj_lower or "missed" in subj_lower else None
                    )
                elif "sms" in subj_lower:
                    act_type = "sms"
                    direction = "outbound" if "outgoing" in subj_lower else (
                        "inbound" if "incoming" in subj_lower else None
                    )
                elif "email" in subj_lower:
                    act_type = "email"
                    direction = "outbound"
                elif "letter" in subj_lower:
                    act_type = "letter"
                    direction = "outbound"
                elif "skip trace" in subj_lower:
                    act_type = "skip_trace"
                    direction = None
                else:
                    act_type = "task"
                    direction = None

                # Link to contact and case
                who_id = row.get("WhoId", "")
                contact_id = contact_lookup.get(who_id)
                account_id = row.get("AccountId", "")
                case_id = case_by_account.get(account_id)

                status = "completed" if row.get("IsClosed") == "1" else "open"

                act = Activity(
                    contact_id=contact_id,
                    case_id=case_id,
                    activity_type=act_type,
                    subject=subject,
                    description=row.get("Description") or None,
                    direction=direction,
                    status=status,
                    activity_date=parse_datetime(row.get("CreatedDate")),
                    due_date=parse_date(row.get("ActivityDate")),
                    sf_task_id=row.get("Id"),
                )
                session.add(act)
                imported += 1

                if imported % batch_size == 0:
                    session.flush()
                    print(f"  Activities: {imported} imported...")

        session.flush()
        print(f"  Activities: {imported} imported, {skipped} skipped")

    session.commit()
    print("Salesforce import complete!")


def seed_hot_leads(session):
    """Seed the hot leads from today's SMS analysis."""
    from models import Case, Contact

    leads = [
        {
            "docket": "DBD-CV-25-6056384-S", "address": "Danbury Property",
            "town": "Danbury", "status": "Motion for Foreclosure",
            "name": "Braulio Duran", "phone": "2033000243",
            "response": "interested", "note": "SMS: 'Hey manny can I call you?' — Hot lead, wants to talk",
        },
        {
            "docket": "MMX-CV-18-6022282-S", "address": "East Haddam Property",
            "town": "East Haddam", "status": "Sale Date Set",
            "name": "Priscilla Lafountain", "phone": "8602156515",
            "response": "interested", "note": "SMS: 'Please get in touch' — Sale date approaching",
        },
        {
            "docket": "HHD-CV23-6163403-S", "address": "Simsbury Property",
            "town": "Simsbury", "status": "In Mediation",
            "name": "Amyjean Silling", "phone": "8608059883",
            "response": "interested", "note": "SMS: 'What are your options?' then 'Hello?' — Waiting for reply",
        },
        {
            "docket": "HHD-CV-25-6208627-S", "address": "Farmington Property",
            "town": "Farmington", "status": "New Case",
            "name": "Ronald Lee Monterosso", "phone": "5086856035",
            "response": "interested", "note": "SMS: Executrix seller, ready to sell by fiduciary deed",
        },
        {
            "docket": "AAN-CV-22-6046276-S", "address": "Ansonia Property",
            "town": "Ansonia", "status": "New Case",
            "name": "Janessa Bennett", "phone": "2034144343",
            "response": "interested", "note": "SMS: Has competing investor offer at $375K as-is",
        },
        {
            "docket": "FBT-CV-25-6142236-S", "address": "Bridgeport Property",
            "town": "Bridgeport", "status": "New Case",
            "name": "Erron Simmonds", "phone": "2035435269",
            "response": "interested", "note": "SMS: 'What options are you talking about?' — Asking for details",
        },
        {
            "docket": "HHD-CV-25-6210488-S", "address": "Hartford Property",
            "town": "Hartford", "status": "Judgment of Foreclosure",
            "name": "Thanh Huyen Nguyen", "phone": "6175951680",
            "response": "interested", "note": "SMS: Replied 'Yes' to offer of help",
        },
        {
            "docket": "DBD-CV-25-6054847-S", "address": "Danbury Property",
            "town": "Danbury", "status": "New Case",
            "name": "Samih Bajramaj", "phone": "6466708228",
            "response": "interested", "note": "SMS: Replied 'Yes' to offer of help",
        },
    ]

    created = 0
    for lead in leads:
        existing = Case.query.filter_by(docket_number=lead["docket"]).first()
        if not existing:
            case = Case(
                docket_number=lead["docket"],
                address=lead["address"],
                town=lead["town"],
                status=lead["status"],
                status_date=date.today(),
                date_added=date.today(),
                source="sms_outreach",
            )
            session.add(case)
            session.flush()
            case_id = case.id
        else:
            case_id = existing.id

        existing_contact = Contact.query.filter_by(primary_phone=lead["phone"]).first()
        if not existing_contact:
            parts = lead["name"].rsplit(" ", 1)
            contact = Contact(
                case_id=case_id,
                name=lead["name"],
                first_name=parts[0] if len(parts) > 1 else lead["name"],
                last_name=parts[-1] if len(parts) > 1 else "",
                primary_phone=lead["phone"],
                response_status=lead["response"],
                date_added=date.today(),
                notes=lead["note"],
            )
            session.add(contact)
            created += 1

    session.commit()
    print(f"  Hot leads: {created} seeded")


if __name__ == "__main__":
    sf_dir = sys.argv[1] if len(sys.argv) > 1 else "sf_export"

    from app import app
    with app.app_context():
        from models import db
        run_import(db.session, sf_dir)
        seed_hot_leads(db.session)
