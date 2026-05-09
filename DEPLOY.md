# TriPoint CRM — Deployment Guide

## What's Included
- Full CRM replacing Salesforce ($960/month → $0-7/month)
- 7,926 cases, 4,602 contacts, 523 transactions, 157,613 activities imported from SF
- 8 hot leads from SMS campaign seeded
- User accounts: Warren (admin), Manny (agent)

## Local Development
```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:5000
# Login: warren@homesellct.com / tripoint2026
```

## Deploy to Render (Free → $7/month)

### Option A: One-click with render.yaml
1. Push this folder to a GitHub repo
2. Go to https://render.com → New → Blueprint
3. Connect your GitHub repo
4. Render reads render.yaml and creates the web service + PostgreSQL database
5. Done — your CRM is live at https://tripoint-crm.onrender.com

### Option B: Manual setup
1. Go to https://render.com → New → Web Service
2. Connect repo or upload
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Add PostgreSQL database (free tier)
6. Set environment variables:
   - `DATABASE_URL` → from the database
   - `SECRET_KEY` → generate a random string

## After Deployment
1. Login as Warren (admin)
2. Change default passwords immediately (Settings → add yourself with new password)
3. Import Salesforce data: upload sf_export/ folder to server and run:
   ```
   flask import-sf
   ```

## Default Logins
- Warren: warren@homesellct.com / tripoint2026
- Manny: manny@tripointrealestatect.com / tripoint2026

**Change these passwords immediately after first login.**

## Tech Stack
- Flask (Python web framework)
- SQLAlchemy (database ORM — works with SQLite and PostgreSQL)
- Bootstrap 5 (UI framework)
- Gunicorn (production server)

## Cost Comparison
| | Salesforce | TriPoint CRM |
|---|---|---|
| Monthly cost | $960 (6 Enterprise seats) | $0-7 (Render free/starter) |
| Users | 6 max | Unlimited |
| Features | Way more than needed | Exactly what you need |
| Data ownership | Locked in SF | Your database, your data |
