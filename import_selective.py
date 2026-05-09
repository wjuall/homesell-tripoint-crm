"""
Selective Salesforce import — only active opportunities and their linked contacts/cases.
Categorizes by RecordTypeId: 'Opportunity' type → Leads, everything else → Transactions.

Usage:
    DATABASE_URL=postgresql://... python import_selective.py /path/to/sf_export/
"""
import csv
import os
import sys
from datetime import datetime, date


# RecordTypeId mapping from SF export
RECORD_TYPES = {
    "012Hp000001yZ8zIAE": "Opportunity",        # → Lead
    "012Hp000001yZ94IAE": "Retail Listing",      # → Transaction
    "012Hp000001yZ99IAE": "Off Market Listing",  # → Transaction
    "012Hp000001yZ9EIAU": "Buyer Representation",# → Transaction
    "012Hp000001yZ9JIAU": "Purchase",            # → Transaction
    "012Hp000001yZ9OIAU": "Referral",            # → Transaction
    "012Hp000001yZ9TIAU": "Wholesale",           # → Transaction
    "012Hp000001ygrKIAQ": "Rehab Project",       # → Transaction
    "012WQ00000AdfrxYAB": "Surplus Funds",       # → Transaction
}

LEAD_RECORD_TYPE = "012Hp000001yZ8zIAE"

# SF stage → CRM lead status mapping
STAGE_TO_LEAD_STATUS = {
    "New": "New Case",
    "Working": "Working",
    "Appointment Set": "Appointment Set",
    "Offer Made": "Offer Made",
    "Follow Up on Offer": "Follow Up on Offer",
    "Long Term Follow Up": "Long Term Follow Up",
}


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


def run_selective_import(session, sf_dir="sf_export"):
    from models import Case, Contact, Transaction, Activity, User

    print("=" * 60)
    print("SELECTIVE SALESFORCE IMPORT")
    print("=" * 60)

    # ── Step 1: Read opportunities and filter ────────────────────────────
    opp_file = os.path.join(sf_dir, "Opportunity.csv")
    if not os.path.exists(opp_file):
        print("ERROR: Opportunity.csv not found!")
        return

    lead_opps = []       # RecordType = Opportunity → goes to Cases/Leads
    transaction_opps = [] # All other RecordTypes → goes to Transactions

    with open_csv(opp_file) as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip closed and Revert
            if row.get("IsClosed") == "1":
                continue
            if row.get("StageName") == "Revert":
                continue

            rt_id = row.get("RecordTypeId", "")
            # Trim to match our keys (SF IDs can be 15 or 18 chars)
            rt_match = None
            for key in RECORD_TYPES:
                if rt_id.startswith(key[:15]):
                    rt_match = key
                    break

            if rt_match == LEAD_RECORD_TYPE:
                lead_opps.append(row)
            else:
                transaction_opps.append(row)

    print(f"\nFiltered opportunities:")
    print(f"  Leads (Opportunity type): {len(lead_opps)}")
    print(f"  Transactions (other types): {len(transaction_opps)}")

    # Collect all AccountIds we care about (for linking contacts)
    relevant_account_ids = set()
    for row in lead_opps + transaction_opps:
        aid = row.get("AccountId", "")
        if aid:
            relevant_account_ids.add(aid)

    print(f"  Linked accounts: {len(relevant_account_ids)}")

    # ── Step 1b: Build lookup tables from Case.csv and Account.csv ───────
    # Docket + case_url from Case.csv (keyed by AccountId)
    sf_case_lookup = {}  # AccountId → {docket, case_url, sf_status}
    case_file = os.path.join(sf_dir, "Case.csv")
    if os.path.exists(case_file):
        with open_csv(case_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                acct = row.get("AccountId", "")
                docket = row.get("Docket_Number__c", "").strip()
                if acct and docket:
                    sf_case_lookup[acct] = {
                        "docket": docket,
                        "case_url": row.get("Case_Link__c", "").strip(),
                        "sf_status": row.get("Status", ""),
                        "sf_case_id": row.get("Id", ""),
                        "sale_date": row.get("Sale_Date__c", ""),
                    }
        print(f"  SF cases with docket numbers: {len(sf_case_lookup)}")

    # Town from Account.csv (BillingCity, keyed by AccountId)
    sf_town_lookup = {}  # AccountId → town
    account_file = os.path.join(sf_dir, "Account.csv")
    if os.path.exists(account_file):
        with open_csv(account_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                acct_id = row.get("Id", "")
                city = row.get("BillingCity", "").strip()
                if acct_id and city:
                    sf_town_lookup[acct_id] = city
        print(f"  SF accounts with town/city: {len(sf_town_lookup)}")

    # ── Step 2: Import leads as Cases ────────────────────────────────────
    print(f"\n── Importing {len(lead_opps)} leads as Cases ──")

    # First check if any cases with matching SF data already exist
    case_by_account = {}  # AccountId → case.id
    opp_to_case = {}      # Opp Id → case.id

    for row in lead_opps:
        account_id = row.get("AccountId", "")
        name = row.get("Name", "").strip()
        stage = row.get("StageName", "New")
        address = name

        # Get town from Account lookup
        town = sf_town_lookup.get(account_id, "")

        # Get docket + case_url from Case lookup
        sf_case = sf_case_lookup.get(account_id, {})
        docket = sf_case.get("docket")
        case_url = sf_case.get("case_url")
        sale_date_str = sf_case.get("sale_date", "")
        sf_case_id = sf_case.get("sf_case_id")

        status = STAGE_TO_LEAD_STATUS.get(stage, stage)

        # Check for existing
        existing = None
        if docket:
            existing = Case.query.filter_by(docket_number=docket).first()
        if not existing and account_id:
            existing = Case.query.filter_by(sf_account_id=account_id).first()

        if existing:
            # Backfill missing data on existing records
            if docket and not existing.docket_number:
                existing.docket_number = docket
            if case_url and not existing.case_url:
                existing.case_url = case_url
            if town and not existing.town:
                existing.town = town
            if sf_case_id and not existing.sf_case_id:
                existing.sf_case_id = sf_case_id
            if sale_date_str and not existing.sale_date:
                existing.sale_date = parse_date(sale_date_str)
            case_by_account[account_id] = existing.id
            opp_to_case[row.get("Id")] = existing.id
            continue

        case = Case(
            docket_number=docket,
            address=address or None,
            town=town or None,
            lead_type="Foreclosure",  # All current SF leads are foreclosure
            status=status,
            case_url=case_url or None,
            status_date=parse_date(row.get("LastModifiedDate")),
            sale_date=parse_date(sale_date_str) if sale_date_str else parse_date(row.get("CloseDate")),
            date_added=parse_date(row.get("CreatedDate")),
            source="salesforce",
            sf_case_id=sf_case_id,
            sf_account_id=account_id or None,
            notes=row.get("Description") or None,
        )
        session.add(case)
        session.flush()

        if account_id:
            case_by_account[account_id] = case.id
        opp_to_case[row.get("Id")] = case.id

    session.flush()
    print(f"  Cases created: {len(opp_to_case)}")

    # ── Step 3: Import transactions ──────────────────────────────────────
    print(f"\n── Importing {len(transaction_opps)} transactions ──")

    users = {u.email: u.id for u in User.query.all()}
    default_user = User.query.first()
    txn_count = 0

    for row in transaction_opps:
        sf_id = row.get("Id")
        existing = Transaction.query.filter_by(sf_opportunity_id=sf_id).first()
        if existing:
            continue

        rt_id = row.get("RecordTypeId", "")
        txn_type = "Retail Listing"  # default
        for key, name in RECORD_TYPES.items():
            if rt_id.startswith(key[:15]):
                txn_type = name
                break

        name = row.get("Name", "Untitled")
        account_id = row.get("AccountId", "")
        case_id = case_by_account.get(account_id)

        txn = Transaction(
            name=name,
            property_address=row.get("Property__c") or None,
            transaction_type=txn_type,
            stage=row.get("StageName", "New"),
            amount=float(row.get("Amount") or 0) or None,
            list_price=float(row.get("List_Price__c") or 0) or None,
            contract_price=float(row.get("Contract_Price__c") or 0) or None,
            close_date=parse_date(row.get("CloseDate")),
            is_closed=False,
            is_won=False,
            loss_reason=row.get("Loss_Reason__c") or None,
            case_id=case_id,
            assigned_to_id=default_user.id if default_user else None,
            created_at=parse_datetime(row.get("CreatedDate")),
            sf_opportunity_id=sf_id,
            notes=row.get("Description") or None,
        )
        session.add(txn)
        txn_count += 1

    session.flush()
    print(f"  Transactions created: {txn_count}")

    # ── Step 4: Import linked contacts ───────────────────────────────────
    contact_file = os.path.join(sf_dir, "Contact.csv")
    if os.path.exists(contact_file):
        print(f"\n── Importing contacts linked to active opportunities ──")
        imported = 0
        skipped = 0

        with open_csv(contact_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("IsDeleted") == "1":
                    continue

                sf_account = row.get("AccountId", "")
                # Only import contacts linked to our active opportunities
                if sf_account not in relevant_account_ids:
                    skipped += 1
                    continue

                case_id = case_by_account.get(sf_account)

                first = row.get("FirstName", "").strip()
                last = row.get("LastName", "").strip()
                name = f"{first} {last}".strip()
                if not name:
                    skipped += 1
                    continue

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

                if imported % 100 == 0:
                    session.flush()

        session.flush()
        print(f"  Contacts imported: {imported} (skipped {skipped} not linked)")

    # ── Step 5: Seed hot SMS leads ───────────────────────────────────────
    print(f"\n── Seeding hot SMS leads ──")
    seed_hot_leads(session)

    session.commit()
    print("\n" + "=" * 60)
    print("IMPORT COMPLETE!")
    print("=" * 60)


def seed_hot_leads(session):
    """Seed the 8 hot leads from SMS analysis."""
    from models import Case, Contact

    leads = [
        {
            "docket": "DBD-CV-25-6056384-S", "address": "Danbury Property",
            "town": "Danbury", "status": "Working",
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
            "town": "Simsbury", "status": "Working",
            "name": "Amyjean Silling", "phone": "8608059883",
            "response": "interested", "note": "SMS: 'What are your options?' then 'Hello?' — Waiting for reply",
        },
        {
            "docket": "HHD-CV-25-6208627-S", "address": "Farmington Property",
            "town": "Farmington", "status": "Working",
            "name": "Ronald Lee Monterosso", "phone": "5086856035",
            "response": "interested", "note": "SMS: Executrix seller, ready to sell by fiduciary deed",
        },
        {
            "docket": "AAN-CV-22-6046276-S", "address": "Ansonia Property",
            "town": "Ansonia", "status": "Working",
            "name": "Janessa Bennett", "phone": "2034144343",
            "response": "interested", "note": "SMS: Has competing investor offer at $375K as-is",
        },
        {
            "docket": "FBT-CV-25-6142236-S", "address": "Bridgeport Property",
            "town": "Bridgeport", "status": "Working",
            "name": "Erron Simmonds", "phone": "2035435269",
            "response": "interested", "note": "SMS: 'What options are you talking about?' — Asking for details",
        },
        {
            "docket": "HHD-CV-25-6210488-S", "address": "Hartford Property",
            "town": "Hartford", "status": "Working",
            "name": "Thanh Huyen Nguyen", "phone": "6175951680",
            "response": "interested", "note": "SMS: Replied 'Yes' to offer of help",
        },
        {
            "docket": "DBD-CV-25-6054847-S", "address": "Danbury Property",
            "town": "Danbury", "status": "Working",
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
                lead_type="Foreclosure",
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
    print(f"  Hot leads seeded: {created}")


if __name__ == "__main__":
    sf_dir = sys.argv[1] if len(sys.argv) > 1 else "sf_export"

    from app import app
    with app.app_context():
        from models import db
        run_selective_import(db.session, sf_dir)
