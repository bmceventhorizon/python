# BMC Event Horizon Python Tools

## CMDB Data Management Portal

The CMDB portal is in:

```text
cmdb-portal/
```

Download package:

```text
cmdb-portal-install.zip
```

To install from source:

```bash
cd cmdb-portal
chmod +x install.sh start.sh
./install.sh
./start.sh
```

Open:

```text
http://127.0.0.1:8000
```

The portal runs read-only SQL reports against PostgreSQL, exports query results as `.xlsx` spreadsheets, and uses the BMC CMDB REST API for duplicate CI resolution. Saved reports are still restricted to read-only SQL. Duplicate apply actions can either set `MarkAsDeleted = 1` or, when explicitly selected, call CMDB REST hard delete with `delete_option=PURGE`.

Do not commit `.env`, `.venv/`, caches, or private `reports.json` files. The package excludes database credentials and local saved reports.
