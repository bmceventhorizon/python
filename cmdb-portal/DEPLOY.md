# Deploying CMDB Data Management Portal

This portal is a local/internal Python app for running saved PostgreSQL reports against a BMC CMDB reporting database.

## Download Package

Distribute the portal as:

```text
cmdb-portal-install.zip
```

The zip contains the app source, install scripts, and documentation. It intentionally excludes local secrets and user-generated data.

To publish it on GitHub:

1. Create a GitHub release.
2. Attach `cmdb-portal-install.zip` as a release asset.
3. In the release notes, tell users to unzip the file, run `./install.sh`, then `./start.sh`.
4. Do not upload `.env`, `.venv/`, `.pycache/`, or private `reports.json` files.

Example release note:

```text
Download cmdb-portal-install.zip, unzip it, then run:

chmod +x install.sh start.sh
./install.sh
./start.sh

Open http://127.0.0.1:8000 and enter your read-only PostgreSQL connection details. Enter CMDB REST credentials only if you plan to apply duplicate-resolution changes.
```

## What To Share

Share these files:

```text
app.py
queries.py
requirements.txt
.env.example
.gitignore
README.md
DEPLOY.md
install.sh
start.sh
```

Do not share these files:

```text
.env
.venv/
.pycache/
__pycache__/
reports.json
```

`.env` contains database credentials. `reports.json` may contain internal SQL reports.

## Local Install

From the unpacked folder:

```bash
chmod +x install.sh start.sh
./install.sh
./start.sh
```

Open:

```text
http://127.0.0.1:8000
```

The portal runs on localhost only.

## Database Connection

You can configure the database in the browser under **Database Connection**, or edit `.env` directly:

```env
PGHOST=your_postgres_host
PGPORT=5432
PGDATABASE=your_database_name
PGUSER=your_read_only_user
PGPASSWORD=your_password
PGSSLMODE=prefer
PGCONNECT_TIMEOUT=5
CMDB_REST_BASE_URL=http://your_cmdb_host:8008
CMDB_REST_USERNAME=your_ar_user
CMDB_REST_PASSWORD=your_ar_password
CMDB_REST_NAMESPACE=BMC.CORE
CMDB_REST_CLASS=BMC_BaseElement
CMDB_REST_DELETE_OPTION=PURGE
PORT=8000
```

Use a read-only PostgreSQL user.

Use an AR user for CMDB REST operations. Do not use the PostgreSQL reporting user for REST login unless that is also a valid AR account.

## PostgreSQL Permissions

The database user needs `SELECT` access to the reporting tables used by your reports, for example:

```sql
GRANT CONNECT ON DATABASE your_database_name TO cmdb_dashboard_ro;
GRANT USAGE ON SCHEMA public TO cmdb_dashboard_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO cmdb_dashboard_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT ON TABLES TO cmdb_dashboard_ro;
```

Adjust schema names if your reporting database does not use `public`.

## Saved Reports

Saved reports are stored in:

```text
reports.json
```

That file is intentionally excluded from the install package by default. If you want to distribute starter reports, create a sanitized `reports.json` and include it intentionally.

## Query Results Export

After running a query, users can click **Export Spreadsheet** above the results table to download the current result set as an `.xlsx` file. The export reruns the selected query with the same filters before streaming the workbook to the browser.

Users can also click **Export PCT JSON** to create REST-ready payloads for `PCT:Product Catalog`. The JSON export reruns the selected query, keeps only rows where the seven Product Catalog fields are populated, and writes an `entries` array where each item is shaped like:

```json
{
  "values": {
    "Manufacturer": "HP",
    "Product Name": "LaserJet Enterprise 500 MFP M525f",
    "Product Categorization Tier 1": "Hardware",
    "Product Categorization Tier 2": "Printer",
    "Product Categorization Tier 3": "Multifunction Printer",
    "Model/Version": "M525f",
    "Market Version": "M525f"
  }
}
```

## Report Packages

Use **Report Packages** in the browser to distribute SQL reports separately from the app.

Export creates a JSON file named:

```text
cmdb-report-package.json
```

The package includes:

```text
report name
description
SQL
report metadata
```

The package does not include:

```text
database credentials
.env
saved connection settings
query results
```

To import a package:

1. Open the portal.
2. Find **Report Packages**.
3. Paste the package JSON into **Import Package JSON**.
4. Choose an import mode.
5. Click **Import Reports Package**.

Review SQL before importing packages from other people.

## ADDM Duplicate Resolution

The **ADDM Duplicate Resolution Preview** panel runs read-only SQL to find candidate duplicate CIs and show the evidence used for bulk selection:

```text
DatasetId
InstanceId
SerialNumber
ADDMIntegrationId
LastScanDate
MarkAsDeleted
relationship_count
resolution_status
```

Saved reports and duplicate previews remain read-only SQL. The portal does not run SQL update statements for cleanup.

When the admin confirms an apply action, selected duplicate CIs are updated through BMC CMDB REST API:

```text
Soft mark only
PATCH /api/cmdb/v1.0/instances/{datasetId}/{namespace}/{className}/{instanceId}
sets MarkAsDeleted = 1

Delete CMDB instance through REST
DELETE /api/cmdb/v1.0/instances/{datasetId}/{namespace}/{className}/{instanceId}?delete_option=PURGE
```

Use soft mark when you want the CI marked to be deleted later. Use hard delete only when you really want CMDB REST to purge the selected instance and your dataset allows it.

## AI Query Generator

The portal can generate editable SQL report drafts from a natural-language request.

Users can choose an AI engine in the browser under **AI Query Generator**.

Supported provider options:

```text
ChatGPT / OpenAI
Claude / Anthropic
Cursor
```

The portal can call OpenAI and Anthropic directly. Cursor is listed for clarity, but Cursor does not expose a separate general-purpose API endpoint for this portal; Cursor itself can use OpenAI and Anthropic keys inside the Cursor editor.

Set keys in the browser or in `.env`:

```env
AI_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-5.2
ANTHROPIC_API_KEY=your_anthropic_api_key
ANTHROPIC_MODEL=claude-sonnet-4-20250514
CURSOR_MODEL=auto
```

Anthropic keys can also be detected from `~/.bash_profile`, `~/.zprofile`, or `~/.zshrc` when exported as `ANTHROPIC_API_KEY` or `CLAUDE_API_KEY`.

Keys are stored locally in `.env` and are not included in the install package. Generated SQL should be reviewed before running or sharing.

## Internal Team Deployment

For a small internal team, the simplest supported pattern is:

1. Put the package on a shared drive or Git repo.
2. Each user installs it locally.
3. Each user enters their own read-only database credentials.
4. Users share sanitized SQL reports manually or by sharing a reviewed `reports.json`.

This app is not hardened for public or internet-facing deployment. It has no authentication layer, and it stores the database password locally in `.env`.

## Production Notes

Before hosting centrally, add:

- authentication
- authorization
- per-user saved reports
- stricter SQL execution controls
- HTTPS
- a proper WSGI/ASGI server instead of Python's built-in development server
- centralized secrets management
