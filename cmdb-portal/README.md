# CMDB Data Management Portal

Local Python portal for running and saving PostgreSQL CMDB data-quality reports, previewing duplicate CIs, and resolving selected duplicates through BMC CMDB REST API.

## Download

Use the install package:

```text
cmdb-portal-install.zip
```

After downloading, unzip it and open the `cmdb-portal` folder.

## Setup

```bash
chmod +x install.sh start.sh
./install.sh
```

Use the browser **Database Connection** form or edit `.env` with your PostgreSQL connection details. Use **CMDB REST Connection** for the AR user, password, CMDB REST base URL, namespace, class, and hard-delete option.

## Run

```bash
./start.sh
```

Open:

```text
http://127.0.0.1:8000
```

## Included Queries

- Total CIs by Class
- Orphaned CIs
- Relationship Data Quality Summary
- Assets With People
- CIs Missing People

Query SQL lives in `queries.py`.

## Saved Reports

Use the Report Editor in the browser to save custom read-only SQL reports.
Reports are stored locally in `reports.json`.
Saved reports must remain read-only SQL. Duplicate updates are applied through CMDB REST, not SQL updates.

## Report Packages

Use **Report Packages** in the browser to export saved reports as a JSON package or import reports from a package.

## ADDM Duplicate Resolution

Use **ADDM Duplicate Resolution Preview** to bulk select duplicate CIs with rules such as newest `LastScanDate`, blank `ADDMIntegrationId`, fewest relationships, or all except oldest `CreatedDate`.

The preview report includes fields such as `DatasetId`, `InstanceId`, `SerialNumber`, `ADDMIntegrationId`, `LastScanDate`, relationship count, and `MarkAsDeleted`.

Apply actions use BMC CMDB REST API:

- **Soft mark only** uses `PATCH` to set `MarkAsDeleted = 1`.
- **Delete CMDB instance through REST** uses `DELETE` with the configured hard-delete option. The default is `PURGE`.

The portal does not run SQL updates for duplicate cleanup.

## AI Query Generator

Use **AI Query Generator** to enter an OpenAI API key, describe the query you want, and create an editable saved report draft.
Supported provider options:

- ChatGPT / OpenAI
- Claude / Anthropic
- Cursor, shown for clarity, but Cursor does not expose a separate API endpoint for this portal

The model dropdown includes common API model IDs such as `gpt-5.2-chat-latest`, `gpt-5.2`, `claude-opus-4-1-20250805`, and `claude-sonnet-4-20250514`.

API keys are stored locally in `.env`. Anthropic keys can also be detected from `~/.bash_profile`, `~/.zprofile`, or `~/.zshrc`.

## Deployment

See `DEPLOY.md`.
