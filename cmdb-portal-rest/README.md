# CMDB REST Data Management Portal

REST-only CMDB reporting portal for environments where database access is not available.

## Run

```bash
cd cmdb-portal-rest
PORT=8010 python3 app.py
```

Open:

```text
http://127.0.0.1:8010
```

## Reports

- Total CIs by Class
- CI Inventory
- Duplicate Serial Numbers
- Orphaned CIs
- Relationship Data Quality Summary
- Normalization Summary
- Normalization and Company Summary

All reports use CMDB REST endpoints under:

```text
/api/cmdb/v1.0/instances/{dataset}/{namespace}/{class}
```

No PostgreSQL or database connection is used.
