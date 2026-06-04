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
- Normalization Candidates
- Normalization and Company Summary

All reports use CMDB REST endpoints under:

```text
/api/cmdb/v1.0/instances/{dataset}/{namespace}/{class}
```

No PostgreSQL or database connection is used.

## Add To Product Catalog

The **Normalization Candidates** report lists individual CIs from the selected datasets and classes that are not normalized, failed normalization, are awaiting approval, or changed after normalization.

Each result has an **Add to Product Catalog** button. After confirmation, the portal re-fetches the source CI, removes system-managed identifiers and status fields, and creates a copy in:

```text
BMC.AddToProductCatalog
```

The copy always uses the source CI's concrete class, such as `BMC_ComputerSystem`. The portal rejects `BMC_BaseElement` because it is not an appropriate class for the Product Catalog normalization action.

The copied attributes include Company, manufacturer, product categorization, model, and other non-system attributes available on the source CI. The next normalization job can use the target dataset entry to update the Product Catalog and normalize the source CI.

After creation, the portal reads the new concrete-class CI back from `BMC.AddToProductCatalog`. A green confirmation in the results section shows the new target InstanceId and confirms that the CI is eligible for the next normalization job. If the create request succeeds but the target CI cannot be verified, the portal shows a warning and does not claim normalization eligibility.
