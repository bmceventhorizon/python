DEFAULT_CLASSES = [
    "BMC_PhysicalLink",
    "BMC_NetworkAddress",
    "BMC_Protocol",
    "BMC_TopologicalLink",
    "BMC_ProtocolEndpoint",
    "BMC_ConnectivityCollection",
    "BMC_Database",
    "BMC_Tag",
    "BMC_HardwareSystemComponent",
    "BMC_LogicalSystemComponent",
    "BMC_ApplicationSystem",
    "BMC_ComputerSystem",
    "BMC_OperatingSystem",
    "BMC_Product",
    "BMC_Printer",
    "BMC_Mainframe",
    "BMC_SoftwareServer",
    "BMC_BusinessService",
    "BMC_ApplicationService",
]


QUERIES = {
    "ci_by_class": {
        "name": "Total CIs by Class",
        "description": "Counts active asset dataset CIs grouped by classid.",
        "sql": """
SELECT
    be.classid,
    COUNT(*) AS total_cis
FROM bmc_core_bmc_baseelement be
WHERE be.datasetid = ANY(%(datasetids)s)
GROUP BY be.classid
ORDER BY total_cis DESC;
""",
    },
    "orphaned_cis": {
        "name": "Orphaned CIs",
        "description": "Finds CIs with no relationship where the CI is either source or destination.",
        "sql": """
SELECT
    to_timestamp(be.createdate) AS datecreated,
    to_timestamp(be.modifieddate) AS datemodified,
    be.classid,
    be.instanceid,
    be.name AS ci_name,
    be.datasetid,
    be.site,
    be.submitter
FROM bmc_core_bmc_baseelement be
WHERE be.datasetid = ANY(%(datasetids)s)
  AND be.classid = ANY(%(classids)s)
  AND NOT EXISTS (
      SELECT 1
      FROM bmc_core_bmc_baserelationship br
      WHERE br.datasetid = be.datasetid
        AND (
            br.source_instanceid = be.instanceid
            OR br.destination_instanceid = be.instanceid
        )
  )
ORDER BY be.modifieddate DESC
LIMIT %(limit)s;
""",
    },
    "relationship_summary": {
        "name": "Relationship Data Quality Summary",
        "description": "Summarizes total CIs, orphaned CIs, and business service relationship coverage.",
        "sql": """
WITH visible_cis AS (
    SELECT
        be.instanceid,
        be.classid
    FROM bmc_core_bmc_baseelement be
    WHERE be.datasetid = ANY(%(datasetids)s)
      AND be.classid = ANY(%(classids)s)
      AND be.modifieddate >= EXTRACT(EPOCH FROM NOW() - (%(hours)s * INTERVAL '1 hour'))
),
ci_relationships AS (
    SELECT
        x.instanceid,
        MAX(CASE WHEN x.hasimpact = 1 THEN 1 ELSE 0 END) AS has_impact,
        COUNT(*) AS relationship_count
    FROM (
        SELECT
            br.source_instanceid AS instanceid,
            br.hasimpact
        FROM bmc_core_bmc_baserelationship br
        WHERE br.datasetid = ANY(%(datasetids)s)

        UNION ALL

        SELECT
            br.destination_instanceid AS instanceid,
            br.hasimpact
        FROM bmc_core_bmc_baserelationship br
        WHERE br.datasetid = ANY(%(datasetids)s)
    ) x
    GROUP BY x.instanceid
)
SELECT
    COUNT(vc.instanceid) AS total_cis,
    CASE
        WHEN COUNT(vc.instanceid) = 0 THEN 0
        ELSE SUM(CASE WHEN cr.has_impact = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(vc.instanceid)
    END AS proportion_with_impact,
    SUM(CASE WHEN cr.relationship_count IS NULL THEN 1 ELSE 0 END) AS orphaned_cis,
    SUM(CASE WHEN vc.classid = 'BMC_BusinessService' THEN 1 ELSE 0 END) AS business_services,
    SUM(
        CASE
            WHEN vc.classid = 'BMC_BusinessService'
             AND cr.relationship_count > 0
            THEN 1 ELSE 0
        END
    ) AS business_services_with_relationships
FROM visible_cis vc
LEFT JOIN ci_relationships cr
    ON cr.instanceid = vc.instanceid;
""",
    },
    "assets_with_people": {
        "name": "Assets With People",
        "description": "Counts CIs with people relationships modified in the selected recent window.",
        "sql": """
SELECT
    COUNT(DISTINCT be.reconciliation_identity) AS assetswithpeople
FROM ast_baseelement be
JOIN ast_assetpeople ap
    ON ap.assetinstanceid = be.reconciliation_identity
WHERE be.data_set_id = ANY(%(datasetids)s)
  AND be.class_id = ANY(%(classids)s)
  AND (
      be.modified_date >= EXTRACT(EPOCH FROM NOW() - (%(hours)s * INTERVAL '1 hour'))
      OR ap.modified_date >= EXTRACT(EPOCH FROM NOW() - (%(hours)s * INTERVAL '1 hour'))
  );
""",
    },
    "cis_missing_people": {
        "name": "CIs Missing People",
        "description": "Lists CIs that do not have an AST asset people row.",
        "sql": """
SELECT
    be.class_id,
    be.reconciliation_identity,
    be.name,
    be.data_set_id,
    be.modified_date
FROM ast_baseelement be
WHERE be.data_set_id = ANY(%(datasetids)s)
  AND be.class_id = ANY(%(classids)s)
  AND NOT EXISTS (
      SELECT 1
      FROM ast_assetpeople ap
      WHERE ap.assetinstanceid = be.reconciliation_identity
  )
ORDER BY be.modified_date DESC
LIMIT %(limit)s;
""",
    },
}
