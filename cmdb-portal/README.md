# CMDB Data Management Portal

Local Python portal for running and saving PostgreSQL CMDB data-quality reports.

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

Use the browser **Database Connection** form or edit `.env` with your PostgreSQL connection details.

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

## Report Packages

Use **Report Packages** in the browser to export saved reports as a JSON package or import reports from a package.

## Duplicate Cleanup SQL

Use **Duplicate Cleanup SQL** to generate a preview query and an update statement that marks duplicate rows with `markasdeleted = 1`.
The portal does not execute the update automatically.

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
