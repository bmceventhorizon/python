import html
import io
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse
from xml.sax.saxutils import escape as xml_escape


APP_TITLE = "CMDB REST Data Management Dashboard by BMC Helix Seal Team"
ENV_PATH = ".env"
ASSETS_DIR = "assets"
DEFAULT_BASE_URL = "https://your-helix-restapi.example.com"
DEFAULT_NAMESPACE = "BMC.CORE"
DEFAULT_DATASETS = ["BMC.ASSET"]
DEFAULT_PAGE_SIZE = 500
DEFAULT_MAX_ROWS = 5000
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


REPORTS = {
    "ci_by_class": {
        "name": "Total CIs by Class",
        "description": "Counts CMDB CIs grouped by ClassId.",
        "definition": "GET /api/cmdb/v1.0/instances/{dataset}/BMC.CORE/BMC_BaseElement",
    },
    "ci_inventory": {
        "name": "CI Inventory",
        "description": "Lists CMDB CIs from BMC_BaseElement.",
        "definition": "GET /api/cmdb/v1.0/instances/{dataset}/BMC.CORE/BMC_BaseElement",
    },
    "duplicate_serials": {
        "name": "Duplicate Serial Numbers",
        "description": "Finds duplicate nonblank SerialNumber values within each dataset.",
        "definition": "GET BMC_BaseElement, then group SerialNumber values locally.",
    },
    "orphaned_cis": {
        "name": "Orphaned CIs",
        "description": "Finds CIs that do not appear as either side of a BaseRelationship.",
        "definition": "GET BMC_BaseElement + GET BMC_BaseRelationship, then compare InstanceId values locally.",
    },
    "relationship_summary": {
        "name": "Relationship Data Quality Summary",
        "description": "Summarizes relationship coverage for the selected CIs.",
        "definition": "GET BMC_BaseElement + GET BMC_BaseRelationship, then calculate coverage locally.",
    },
    "normalization_summary": {
        "name": "Normalization Summary",
        "description": "Groups CIs by NormalizationStatus and product categorization fields.",
        "definition": "GET BMC_BaseElement, then group normalization fields locally.",
    },
}


def load_dotenv(path=ENV_PATH, override=False):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if override or key.strip() not in os.environ:
                os.environ[key.strip()] = value.strip().strip("'\"")


def write_env(settings, path=ENV_PATH):
    lines = [
        "# Saved by the local CMDB REST-only portal.",
        f"CMDB_REST_BASE_URL={settings['base_url']}",
        f"CMDB_REST_USERNAME={settings['username']}",
        f"CMDB_REST_PASSWORD={settings['password']}",
        f"CMDB_REST_NAMESPACE={settings['namespace']}",
        f"CMDB_REST_PAGE_SIZE={settings['page_size']}",
        f"CMDB_REST_MAX_ROWS={settings['max_rows']}",
        f"PORT={settings['port']}",
        "",
    ]
    with open(path, "w", encoding="utf-8") as env_file:
        env_file.write("\n".join(lines))
    os.environ.update(
        {
            "CMDB_REST_BASE_URL": settings["base_url"],
            "CMDB_REST_USERNAME": settings["username"],
            "CMDB_REST_PASSWORD": settings["password"],
            "CMDB_REST_NAMESPACE": settings["namespace"],
            "CMDB_REST_PAGE_SIZE": str(settings["page_size"]),
            "CMDB_REST_MAX_ROWS": str(settings["max_rows"]),
            "PORT": str(settings["port"]),
        }
    )


def int_env(name, default, minimum=1, maximum=100000):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def rest_config():
    return {
        "base_url": os.getenv("CMDB_REST_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        "username": os.getenv("CMDB_REST_USERNAME", ""),
        "password": os.getenv("CMDB_REST_PASSWORD", ""),
        "namespace": os.getenv("CMDB_REST_NAMESPACE", DEFAULT_NAMESPACE),
        "page_size": int_env("CMDB_REST_PAGE_SIZE", DEFAULT_PAGE_SIZE),
        "max_rows": int_env("CMDB_REST_MAX_ROWS", DEFAULT_MAX_ROWS),
        "port": int_env("PORT", 8010, minimum=1, maximum=65535),
    }


def checked_rest_config():
    config = rest_config()
    missing = [key for key in ("base_url", "username", "password", "namespace") if not config[key]]
    if missing:
        raise RuntimeError("Missing CMDB REST settings: " + ", ".join(missing))
    return config


def save_rest_settings(form):
    current = rest_config()
    password = form.get("password", [""])[0] or current["password"]
    settings = {
        "base_url": form.get("base_url", [current["base_url"]])[0].strip().rstrip("/") or DEFAULT_BASE_URL,
        "username": form.get("username", [current["username"]])[0].strip(),
        "password": password,
        "namespace": form.get("namespace", [current["namespace"]])[0].strip() or DEFAULT_NAMESPACE,
        "page_size": int(form.get("page_size", [str(current["page_size"])])[0] or DEFAULT_PAGE_SIZE),
        "max_rows": int(form.get("max_rows", [str(current["max_rows"])])[0] or DEFAULT_MAX_ROWS),
        "port": int(form.get("port", [str(current["port"])])[0] or current["port"]),
    }
    write_env(settings)


def cmdb_login(config):
    data = urlencode({"username": config["username"], "password": config["password"]}).encode("utf-8")
    req = urllib.request.Request(
        f'{config["base_url"]}/api/jwt/login',
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            token = response.read().decode("utf-8").strip().strip('"')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"CMDB REST login failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"CMDB REST login failed: {exc.reason}") from exc
    if not token or token.startswith("<"):
        raise RuntimeError("CMDB REST login did not return a JWT.")
    return token


def auth_headers(token):
    return {"Authorization": f"AR-JWT {token}", "Accept": "application/json"}


def class_path(config, datasetid, class_name, namespace=None):
    namespace = namespace or config["namespace"]
    parts = [
        config["base_url"],
        "api",
        "cmdb",
        "v1.0",
        "instances",
        quote(str(datasetid), safe=""),
        quote(namespace, safe=""),
        quote(class_name, safe=""),
    ]
    return "/".join(part.strip("/") for part in parts)


def parse_instances(payload):
    if isinstance(payload, dict):
        instances = payload.get("instances")
        if isinstance(instances, list):
            return instances
        if "instance_id" in payload or "attributes" in payload:
            return [payload]
    if isinstance(payload, list):
        return payload
    return []


def parse_datasets(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("datasets", "items", "data", "entries"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    if "id" in payload or "dataset_type" in payload:
        return [payload]
    return []


def cmdb_quote(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def or_qualification(field_name, values):
    clean_values = [str(value).strip() for value in values if str(value).strip()]
    if not clean_values:
        return ""
    clauses = [f"'{field_name}'=\"{cmdb_quote(value)}\"" for value in clean_values]
    return clauses[0] if len(clauses) == 1 else "(" + " OR ".join(clauses) + ")"


def classid_values(values):
    return sorted({str(value).strip().upper() for value in values if str(value).strip()})


def get_instances(config, token, datasetid, class_name, qualification="", attributes=None, limit=None, offset=0, namespace=None):
    query = {
        "limit": str(limit or config["page_size"]),
        "offset": str(offset),
    }
    if qualification:
        query["qualification"] = qualification
    if attributes:
        query["attributes"] = ",".join(attributes)
    url = f"{class_path(config, datasetid, class_name, namespace=namespace)}?{urlencode(query)}"
    req = urllib.request.Request(url, headers=auth_headers(token), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"CMDB REST request failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"CMDB REST request failed: {exc.reason}") from exc
    return parse_instances(payload), url


def fetch_all_instances(config, token, datasetids, class_name, qualification="", attributes=None, namespace=None):
    instances = []
    urls = []
    for datasetid in datasetids:
        offset = 0
        while len(instances) < config["max_rows"]:
            remaining = config["max_rows"] - len(instances)
            limit = min(config["page_size"], remaining)
            page, url = get_instances(
                config,
                token,
                datasetid,
                class_name,
                qualification=qualification,
                attributes=attributes,
                limit=limit,
                offset=offset,
                namespace=namespace,
            )
            urls.append(url)
            for instance in page:
                instance["_portal_datasetid"] = datasetid
            instances.extend(page)
            if len(page) < limit:
                break
            offset += limit
    return instances, urls


def normalize_key(value):
    return "".join(char for char in str(value).lower() if char.isalnum())


def attributes(instance):
    value = instance.get("attributes", {}) if isinstance(instance, dict) else {}
    return value if isinstance(value, dict) else {}


def pick_attr(instance, *names, default=""):
    attrs = attributes(instance)
    normalized = {normalize_key(key): value for key, value in attrs.items()}
    for name in names:
        if name in attrs:
            return attrs[name]
        key = normalize_key(name)
        if key in normalized:
            return normalized[key]
    return default


def instance_value(instance, *names, default=""):
    for name in names:
        if name in instance:
            return instance[name]
    return pick_attr(instance, *names, default=default)


def instance_id(instance):
    return instance_value(instance, "instance_id", "InstanceId", "instanceid")


def dataset_id(instance):
    return instance_value(
        instance,
        "dataset_id",
        "DatasetId",
        "datasetid",
        default=instance.get("_portal_datasetid", ""),
    )


def row_value(instance, field_name):
    if field_name == "DatasetId":
        return dataset_id(instance)
    if field_name == "InstanceId":
        return instance_id(instance)
    return pick_attr(instance, field_name, field_name.lower(), field_name.replace("_", ""))


def epoch_to_utc(value):
    if value in (None, ""):
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(float(value)))
    except (TypeError, ValueError):
        return value


def truthy(value):
    return str(value).strip().lower() in {"1", "true", "yes"}


def regular_dataset_type(value):
    normalized = str(value).strip().lower()
    return normalized in {"0", "regular"}


def fetch_regular_datasets():
    config = checked_rest_config()
    token = cmdb_login(config)
    datasets = []
    offset = 0
    while len(datasets) < config["max_rows"]:
        limit = min(config["page_size"], config["max_rows"] - len(datasets))
        query = urlencode(
            {
                "offset": str(offset),
                "limit": str(limit),
                "return_overlay_datasets": "false",
                "sort": "id",
            }
        )
        url = f'{config["base_url"]}/api/cmdb/v1.0/datasets?{query}'
        req = urllib.request.Request(url, headers=auth_headers(token), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"CMDB dataset lookup failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"CMDB dataset lookup failed: {exc.reason}") from exc
        page = parse_datasets(payload)
        datasets.extend(page)
        if len(page) < limit:
            break
        offset += limit

    datasetids = []
    for dataset in datasets:
        dataset_type = dataset.get("dataset_type") or dataset.get("datasetType") or dataset.get("type")
        datasetid_value = str(dataset.get("id") or dataset.get("name") or "").strip()
        if datasetid_value and regular_dataset_type(dataset_type):
            datasetids.append(datasetid_value)
    return sorted(set(datasetids))


BASE_FIELDS = [
    "InstanceId",
    "DatasetId",
    "ClassId",
    "Name",
    "ShortDescription",
    "Description",
    "CreateDate",
    "ModifiedDate",
    "LastScanDate",
    "Submitter",
    "Site",
    "SerialNumber",
    "ManufacturerName",
    "Model",
    "Category",
    "Type",
    "Item",
    "NormalizationStatus",
    "MarkAsDeleted",
    "ReconciliationIdentity",
]


def timestamp_sort_value(value):
    if value in (None, ""):
        return -1
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    text = str(value)
    for fmt in ("%Y-%m-%dT%H:%M:%S.000%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            return time.mktime(time.strptime(text.replace("+0000", "+0000"), fmt))
        except ValueError:
            continue
    return -1


def mark_as_deleted_value(value):
    return str(value).strip().lower() in {"1", "true", "yes"}


def newest_duplicate_candidates(params):
    config, token, instances = fetch_base(params)
    groups = {}
    for instance in instances:
        serial = str(row_value(instance, "SerialNumber") or "").strip()
        if not serial:
            continue
        groups.setdefault((dataset_id(instance), serial), []).append(instance)

    candidates = []
    for (dataset, serial), group in groups.items():
        if len(group) <= 1:
            continue
        active_group = [
            instance
            for instance in group
            if not mark_as_deleted_value(row_value(instance, "MarkAsDeleted"))
        ]
        if len(active_group) <= 1:
            continue
        active_group.sort(
            key=lambda instance: (
                timestamp_sort_value(row_value(instance, "LastScanDate")),
                timestamp_sort_value(row_value(instance, "CreateDate")),
                str(instance_id(instance)),
            ),
            reverse=True,
        )
        selected = active_group[0]
        candidates.append((selected, len(group), len(active_group)))
    return config, token, candidates


def duplicate_resolution_preview(params):
    _, _, candidates = newest_duplicate_candidates(params)
    rows = []
    for instance, group_total, active_group_total in candidates[: params["limit"]]:
        rows.append(
            (
                dataset_id(instance),
                row_value(instance, "ClassId"),
                instance_id(instance),
                row_value(instance, "Name"),
                row_value(instance, "SerialNumber"),
                epoch_to_utc(row_value(instance, "LastScanDate")),
                epoch_to_utc(row_value(instance, "CreateDate")),
                row_value(instance, "MarkAsDeleted"),
                group_total,
                active_group_total,
                "Selected For MarkAsDeleted",
            )
        )
    return [
        "datasetid",
        "classid",
        "instanceid",
        "ci_name",
        "serialnumber",
        "lastscandate",
        "createdate",
        "markasdeleted",
        "group_total",
        "active_group_total",
        "resolution_status",
    ], rows


def cmdb_json_headers(token):
    headers = auth_headers(token)
    headers["Content-Type"] = "application/json"
    return headers


def cmdb_mark_instance_deleted(config, token, instance):
    dataset = dataset_id(instance)
    iid = instance_id(instance)
    url = f"{class_path(config, dataset, 'BMC_BaseElement')}/{quote(str(iid), safe='')}"
    body = {
        "instance_id": str(iid),
        "dataset_id": str(dataset),
        "class_name_key": {
            "namespace": config["namespace"],
            "name": "BMC_BaseElement",
        },
        "attributes": {
            "MarkAsDeleted": 1,
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=cmdb_json_headers(token),
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            detail = response.read().decode("utf-8", errors="replace")
            return response.status, url, detail
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"MarkAsDeleted update failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"MarkAsDeleted update failed: {exc.reason}") from exc


def apply_duplicate_resolution(params):
    config, token, candidates = newest_duplicate_candidates(params)
    rows = []
    for instance, group_total, active_group_total in candidates[: params["limit"]]:
        try:
            status, url, detail = cmdb_mark_instance_deleted(config, token, instance)
            action_status = f"MarkAsDeleted set to Yes ({status})"
        except Exception as exc:
            url = ""
            detail = str(exc)
            action_status = "Failed"
        rows.append(
            (
                dataset_id(instance),
                row_value(instance, "ClassId"),
                instance_id(instance),
                row_value(instance, "Name"),
                row_value(instance, "SerialNumber"),
                group_total,
                active_group_total,
                action_status,
                url,
                detail[:500],
            )
        )
    return [
        "datasetid",
        "classid",
        "instanceid",
        "ci_name",
        "serialnumber",
        "group_total",
        "active_group_total",
        "status",
        "rest_url",
        "detail",
    ], rows


def fetch_base(params):
    config = checked_rest_config()
    token = cmdb_login(config)
    selected_classids = classid_values(params["classids"])
    qualification = or_qualification("ClassId", selected_classids)
    instances, _ = fetch_all_instances(
        config,
        token,
        params["datasetids"],
        "BMC_BaseElement",
        qualification=qualification,
        attributes=BASE_FIELDS,
    )
    selected = set(selected_classids)
    if selected:
        instances = [instance for instance in instances if str(row_value(instance, "ClassId")).upper() in selected]
    return config, token, instances


def fetch_relationships(config, token, datasetids):
    instances, _ = fetch_all_instances(config, token, datasetids, "BMC_BaseRelationship")
    return instances


def relationship_ids(relationship):
    source = pick_attr(
        relationship,
        "Source.InstanceId",
        "SourceInstanceId",
        "Source_InstanceId",
        "source_instanceid",
        "sourceinstanceid",
    )
    destination = pick_attr(
        relationship,
        "Destination.InstanceId",
        "DestinationInstanceId",
        "Destination_InstanceId",
        "destination_instanceid",
        "destinationinstanceid",
    )
    return str(source or ""), str(destination or "")


def report_ci_by_class(params):
    _, _, instances = fetch_base(params)
    counts = {}
    for instance in instances:
        key = (dataset_id(instance), row_value(instance, "ClassId") or "(blank)")
        counts[key] = counts.get(key, 0) + 1
    rows = [(dataset, classid, count) for (dataset, classid), count in counts.items()]
    rows.sort(key=lambda row: (-row[2], row[0], row[1]))
    return ["datasetid", "classid", "total_cis"], rows


def report_ci_inventory(params):
    _, _, instances = fetch_base(params)
    rows = []
    for instance in instances[: params["limit"]]:
        rows.append(
            (
                epoch_to_utc(row_value(instance, "CreateDate")),
                epoch_to_utc(row_value(instance, "ModifiedDate")),
                row_value(instance, "ClassId"),
                instance_id(instance),
                row_value(instance, "Name"),
                dataset_id(instance),
                row_value(instance, "SerialNumber"),
                row_value(instance, "ManufacturerName"),
                row_value(instance, "Model"),
                row_value(instance, "Site"),
                row_value(instance, "Submitter"),
            )
        )
    return (
        [
            "datecreated",
            "datemodified",
            "classid",
            "instanceid",
            "ci_name",
            "datasetid",
            "serialnumber",
            "manufacturername",
            "model",
            "site",
            "submitter",
        ],
        rows,
    )


def report_duplicate_serials(params):
    _, _, instances = fetch_base(params)
    groups = {}
    for instance in instances:
        serial = str(row_value(instance, "SerialNumber") or "").strip()
        if not serial:
            continue
        key = (dataset_id(instance), serial)
        groups.setdefault(key, []).append(instance)
    rows = []
    for (dataset, serial), group in groups.items():
        if len(group) <= 1:
            continue
        rows.append(
            (
                dataset,
                serial,
                len(group),
                ", ".join(str(instance_id(item)) for item in group),
                ", ".join(str(row_value(item, "Name")) for item in group),
            )
        )
    rows.sort(key=lambda row: (-row[2], row[0], row[1]))
    return ["datasetid", "serialnumber", "duplicate_count", "instanceids", "names"], rows[: params["limit"]]


def report_orphaned_cis(params):
    config, token, instances = fetch_base(params)
    relationships = fetch_relationships(config, token, params["datasetids"])
    related_ids = set()
    for relationship in relationships:
        source, destination = relationship_ids(relationship)
        if source:
            related_ids.add(source)
        if destination:
            related_ids.add(destination)
    rows = []
    for instance in instances:
        iid = str(instance_id(instance))
        if iid in related_ids:
            continue
        rows.append(
            (
                epoch_to_utc(row_value(instance, "CreateDate")),
                epoch_to_utc(row_value(instance, "ModifiedDate")),
                row_value(instance, "ClassId"),
                iid,
                row_value(instance, "Name"),
                dataset_id(instance),
                row_value(instance, "Site"),
                row_value(instance, "Submitter"),
            )
        )
        if len(rows) >= params["limit"]:
            break
    return ["datecreated", "datemodified", "classid", "instanceid", "ci_name", "datasetid", "site", "submitter"], rows


def report_relationship_summary(params):
    config, token, instances = fetch_base(params)
    relationships = fetch_relationships(config, token, params["datasetids"])
    relationship_counts = {}
    impact_ids = set()
    for relationship in relationships:
        source, destination = relationship_ids(relationship)
        has_impact = truthy(pick_attr(relationship, "HasImpact", "hasimpact"))
        for iid in (source, destination):
            if not iid:
                continue
            relationship_counts[iid] = relationship_counts.get(iid, 0) + 1
            if has_impact:
                impact_ids.add(iid)
    total = len(instances)
    orphaned = 0
    services = 0
    services_with_relationships = 0
    for instance in instances:
        iid = str(instance_id(instance))
        classid = str(row_value(instance, "ClassId"))
        relationship_count = relationship_counts.get(iid, 0)
        if relationship_count == 0:
            orphaned += 1
        if classid == "BMC_BusinessService":
            services += 1
            if relationship_count > 0:
                services_with_relationships += 1
    impact_percent = (len(impact_ids) * 100.0 / total) if total else 0
    return (
        ["total_cis", "proportion_with_impact", "orphaned_cis", "business_services", "business_services_with_relationships"],
        [(total, round(impact_percent, 2), orphaned, services, services_with_relationships)],
    )


def report_normalization_summary(params):
    _, _, instances = fetch_base(params)
    labels = {
        "10": "Other",
        "20": "Not Normalized",
        "30": "Not Applicable for Normalization",
        "40": "Normalization Failed",
        "50": "Normalized but Not Approved",
        "60": "Normalized and Approved",
        "70": "Modified after last Normalization",
    }
    counts = {}
    for instance in instances:
        status = str(row_value(instance, "NormalizationStatus") or "")
        key = (
            labels.get(status, status or "(blank)"),
            row_value(instance, "ClassId"),
            row_value(instance, "Category"),
            row_value(instance, "Type"),
            row_value(instance, "Item"),
            row_value(instance, "Model"),
            row_value(instance, "ManufacturerName"),
        )
        counts[key] = counts.get(key, 0) + 1
    rows = [(count, *key) for key, count in counts.items()]
    rows.sort(key=lambda row: (-row[0],) + tuple(str(value) for value in row[1:]))
    return [
        "record_count",
        "normalizationstatus",
        "classid",
        "category",
        "type",
        "item",
        "model",
        "manufacturername",
    ], rows[: params["limit"]]


REPORT_RUNNERS = {
    "ci_by_class": report_ci_by_class,
    "ci_inventory": report_ci_inventory,
    "duplicate_serials": report_duplicate_serials,
    "orphaned_cis": report_orphaned_cis,
    "relationship_summary": report_relationship_summary,
    "normalization_summary": report_normalization_summary,
}


def query_params(form):
    dataset_values = form.get("datasetids", [])
    datasetids = []
    for value in dataset_values:
        datasetids.extend(item.strip() for item in value.splitlines() if item.strip())
    if not datasetids:
        datasetids = DEFAULT_DATASETS
    class_text = form.get("classids", ["\n".join(DEFAULT_CLASSES)])[0]
    classids = [item.strip() for item in class_text.splitlines() if item.strip()]
    return {
        "datasetids": datasetids,
        "classids": classids,
        "limit": max(1, min(100000, int(form.get("limit", ["250"])[0] or "250"))),
    }


def selected_report(raw):
    return raw if raw in REPORTS else "ci_by_class"


def run_report(report_key, params):
    return REPORT_RUNNERS[report_key](params)


def esc(value):
    return html.escape("" if value is None else str(value))


def column_letter(index):
    index += 1
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def xlsx_cell(row_number, column_index, value):
    reference = f"{column_letter(column_index)}{row_number}"
    if value is None:
        return f'<c r="{reference}"/>'
    if isinstance(value, bool):
        return f'<c r="{reference}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{reference}"><v>{value}</v></c>'
    text = xml_escape(str(value), {'"': "&quot;"})
    preserve = ' xml:space="preserve"' if text.strip() != text else ""
    return f'<c r="{reference}" t="inlineStr"><is><t{preserve}>{text}</t></is></c>'


def build_xlsx(columns, rows):
    sheet_rows = []
    sheet_rows.append(f'<row r="1">{"".join(xlsx_cell(1, index, column) for index, column in enumerate(columns))}</row>')
    for row_number, row in enumerate(rows, start=2):
        sheet_rows.append(
            f'<row r="{row_number}">{"".join(xlsx_cell(row_number, index, value) for index, value in enumerate(row))}</row>'
        )
    sheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{''.join(sheet_rows)}</sheetData></worksheet>
"""
    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Results" sheetId="1" r:id="rId1"/></sheets></workbook>
"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>
"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>
"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", content_types)
        workbook.writestr("_rels/.rels", root_rels)
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        workbook.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return output.getvalue()


def safe_filename(value):
    cleaned = "".join(char if char.isalnum() or char in "._-" else "-" for char in str(value)).strip("-._")
    return cleaned[:80] or "cmdb-rest-results"


def export_query(report_key, params):
    return urlencode(
        {
            "report": report_key,
            "datasetids": "\n".join(params["datasetids"]),
            "classids": "\n".join(params["classids"]),
            "limit": params["limit"],
        }
    )


def render_layout(body):
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{APP_TITLE}</title>
  <style>
    :root {{
      --bg:#f6f7f9; --panel:#fff; --line:#d9dee7; --text:#1f2933;
      --muted:#5b6776; --accent:#146c94; --accent-dark:#0f536f; --danger:#a23b3b;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    header {{ background:#14213d; color:white; padding:18px 24px; }}
    .header-bar {{ display:flex; align-items:center; justify-content:space-between; gap:16px; }}
    header h1 {{ display:flex; align-items:center; gap:12px; margin:0; font-size:22px; letter-spacing:0; }}
    .header-logo {{ height:28px; width:auto; }}
    .portal-switch {{ display:inline-flex; align-items:center; justify-content:center; min-height:36px; padding:7px 12px; border:1px solid rgba(255,255,255,.4); border-radius:6px; color:white; font-weight:700; text-decoration:none; white-space:nowrap; }}
    .portal-switch:hover {{ background:rgba(255,255,255,.12); }}
    main {{ padding:18px 22px; }}
    .page-stack {{ display:grid; gap:18px; }}
    .deck {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:14px; align-items:start; }}
    .query-panel {{ grid-column:span 2; }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; min-width:0; }}
    .panel h2 {{ margin:0; padding:14px 16px; border-bottom:1px solid var(--line); font-size:15px; }}
    .panel-body {{ padding:16px; }}
    label {{ display:block; color:var(--muted); font-size:12px; font-weight:650; margin:14px 0 6px; text-transform:uppercase; }}
    select,input,textarea {{ width:100%; border:1px solid #c9d1dc; border-radius:6px; padding:9px 10px; font:inherit; background:white; color:var(--text); }}
    input[type="checkbox"] {{ width:auto; margin-right:8px; }}
    textarea {{ min-height:160px; resize:vertical; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }}
    button {{ width:100%; margin-top:16px; border:0; border-radius:6px; padding:10px 12px; color:white; background:var(--accent); font-weight:700; cursor:pointer; }}
    button:hover {{ background:var(--accent-dark); }}
    button.secondary {{ background:#4f5f6f; }}
    button.secondary:hover {{ background:#3e4b58; }}
    button.danger {{ background:#a23b3b; }}
    button.danger:hover {{ background:#832f2f; }}
    .actions {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
    .run-button {{ min-height:72px; display:flex; align-items:center; justify-content:center; gap:12px; font-size:18px; background:#05070c; border:1px solid #1e293b; }}
    .run-button img {{ width:92px; height:38px; object-fit:contain; border-radius:4px; }}
    .query-fields {{ display:grid; grid-template-columns:minmax(220px,1fr) minmax(240px,1.2fr); gap:14px; align-items:start; }}
    .query-wide {{ grid-column:1 / -1; }}
    .sql {{ background:#101820; color:#eef6ff; border-radius:8px; padding:13px; overflow:auto; font-size:12px; height:180px; }}
    .hint {{ color:var(--muted); font-size:12px; margin:8px 0 0; }}
    .status {{ display:flex; gap:10px; flex-wrap:wrap; color:var(--muted); margin-bottom:12px; }}
    .pill {{ border:1px solid var(--line); border-radius:999px; padding:4px 9px; background:#fbfcfd; }}
    .export-pill {{ color:var(--accent); font-weight:700; text-decoration:none; }}
    .error {{ border:1px solid #e5b4b4; background:#fff7f7; color:var(--danger); border-radius:8px; padding:12px; white-space:pre-wrap; overflow:auto; }}
    .success {{ border:1px solid #a8d5b8; background:#f3fbf6; color:#23643b; border-radius:8px; padding:12px; margin-bottom:14px; }}
    .table-wrap {{ height:680px; min-height:240px; overflow:auto; border-top:1px solid var(--line); resize:vertical; }}
    table {{ width:max-content; min-width:100%; border-collapse:collapse; background:white; font-size:13px; }}
    th,td {{ border-bottom:1px solid #e6ebf1; padding:8px 10px; text-align:left; vertical-align:top; white-space:nowrap; max-width:420px; overflow:hidden; text-overflow:ellipsis; }}
    th {{ position:sticky; top:0; background:#eef2f7; z-index:1; font-weight:700; }}
    .empty {{ color:var(--muted); padding:24px; text-align:center; }}
    @media (max-width:860px) {{ .query-panel {{ grid-column:auto; }} .query-fields {{ grid-template-columns:1fr; }} .header-bar {{ align-items:flex-start; flex-direction:column; }} main {{ padding:14px; }} th,td {{ white-space:normal; }} }}
  </style>
</head>
<body>
  <header>
    <div class="header-bar">
      <h1><img class="header-logo" src="/assets/2024-bmc-helix-reversed.png" alt="BMC Helix">{APP_TITLE}</h1>
      <a class="portal-switch" href="{esc(os.getenv("CMDB_SQL_PORTAL_URL", "http://127.0.0.1:8000"))}">SQL Portal</a>
    </div>
  </header>
  <main>{body}</main>
</body>
</html>"""


def render_settings(message=""):
    config = rest_config()
    password_placeholder = "Saved password is set. Enter a new password to replace it." if config["password"] else "AR password"
    message_html = f'<div class="success">{esc(message)}</div>' if message else ""
    return f"""
<section class="panel">
  <h2>CMDB REST Connection</h2>
  <div class="panel-body">
    {message_html}
    <form method="post" action="/settings">
      <label for="base_url">CMDB REST Base URL</label>
      <input id="base_url" name="base_url" value="{esc(config["base_url"])}">
      <label for="username">AR User</label>
      <input id="username" name="username" value="{esc(config["username"])}">
      <label for="password">AR Password</label>
      <input id="password" name="password" type="password" placeholder="{esc(password_placeholder)}">
      <label for="namespace">CMDB Namespace</label>
      <input id="namespace" name="namespace" value="{esc(config["namespace"])}">
      <label for="page_size">Page Size</label>
      <input id="page_size" name="page_size" type="number" min="1" max="10000" value="{esc(config["page_size"])}">
      <label for="max_rows">Max Rows Per Run</label>
      <input id="max_rows" name="max_rows" type="number" min="1" max="100000" value="{esc(config["max_rows"])}">
      <label for="port">Portal Port</label>
      <input id="port" name="port" type="number" min="1" max="65535" value="{esc(config["port"])}">
      <button type="submit">Save REST Connection</button>
      <p class="hint">Saved locally to {esc(os.path.abspath(ENV_PATH))}. No database settings are used.</p>
    </form>
  </div>
</section>"""


def render_dataset_select(selected_datasetids):
    selected = set(selected_datasetids)
    hint = '<p class="hint">Regular datasets loaded from /api/cmdb/v1.0/datasets.</p>'
    try:
        options = fetch_regular_datasets()
    except Exception as exc:
        options = list(selected_datasetids or DEFAULT_DATASETS)
        hint = f'<p class="hint">Dataset list unavailable: {esc(exc)}</p>'

    for datasetid in selected:
        if datasetid not in options:
            options.append(datasetid)
    if not options:
        options = DEFAULT_DATASETS

    option_html = "\n".join(
        f'<option value="{esc(datasetid)}" {"selected" if datasetid in selected else ""}>{esc(datasetid)}</option>'
        for datasetid in options
    )
    return (
        f'<select id="datasetids" name="datasetids" multiple size="{esc(min(max(len(options), 3), 8))}">{option_html}</select>',
        hint,
    )


def render_query_form(report_key, params):
    report = REPORTS[report_key]
    dataset_select, dataset_hint = render_dataset_select(params["datasetids"])
    definition = report["definition"].replace("{dataset}", params["datasetids"][0] if params["datasetids"] else DEFAULT_DATASETS[0])
    options = "\n".join(
        f'<option value="{esc(key)}" {"selected" if key == report_key else ""}>{esc(meta["name"])}</option>'
        for key, meta in REPORTS.items()
    )
    return f"""
<section class="panel query-panel">
  <h2>REST Reports</h2>
  <div class="panel-body">
    <form method="post" action="/run">
      <div class="query-fields">
        <div>
          <label for="report">Report</label>
          <select id="report" name="report">{options}</select>
          <p class="hint">{esc(report["description"])}</p>
        </div>
        <div>
          <label for="datasetids">Datasets</label>
          {dataset_select}
          {dataset_hint}
        </div>
        <div>
          <label for="limit">Row Limit</label>
          <input id="limit" name="limit" type="number" min="1" max="100000" value="{esc(params["limit"])}">
        </div>
        <div class="query-wide">
          <label for="classids">Class IDs</label>
          <textarea id="classids" name="classids">{esc(chr(10).join(params["classids"]))}</textarea>
        </div>
        <div class="query-wide">
          <label>REST Definition</label>
          <pre class="sql">{esc(definition)}</pre>
        </div>
      </div>
      <button class="run-button" type="submit">
        <img src="/assets/blackholesurfer-logo.jpg" alt="">
        <span>Run REST Report</span>
      </button>
      <div class="actions">
        <button class="secondary" type="submit" formaction="/duplicates/preview">Preview New Duplicate Resolution</button>
        <button class="danger" type="submit" formaction="/duplicates/apply">Apply MarkAsDeleted = Yes</button>
      </div>
      <label>
        <input type="checkbox" name="confirm_duplicate_apply" value="1">
        Confirm applying MarkAsDeleted = Yes to newest duplicate candidates
      </label>
      <p class="hint">Preview selects the newest active CI in each duplicate SerialNumber group, matching the database portal's default newest-duplicate method. Apply uses only the CMDB REST API.</p>
    </form>
  </div>
</section>"""


def render_table(columns, rows):
    if not rows:
        return '<div class="empty">No rows returned.</div>'
    head = "".join(f"<th>{esc(column)}</th>" for column in columns)
    body = "".join("<tr>" + "".join(f"<td>{esc(value)}</td>" for value in row) + "</tr>" for row in rows)
    return f"""<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"""


def render_page(report_key, params, result=None, error=None, message="", result_title=None):
    config = rest_config()
    result_html = ""
    if error:
        result_html = f'<div class="panel-body"><div class="error">{esc(error)}</div></div>'
    elif result:
        columns, rows = result
        href = f"/results/export?{export_query(report_key, params)}"
        result_html = (
            f'<div class="panel-body"><div class="status">'
            f'<span class="pill">{esc(len(rows))} rows</span>'
            f'<span class="pill">CMDB REST {esc(config["base_url"])}</span>'
            f'<a class="pill export-pill" href="{esc(href)}">Export Spreadsheet</a>'
            f"</div></div>"
            + render_table(columns, rows)
        )
    else:
        result_html = '<div class="empty">Choose a REST report and run it.</div>'
    body = f"""
<div class="page-stack">
  <div class="deck">
    {render_query_form(report_key, params)}
    {render_settings(message)}
  </div>
  <section class="panel">
    <h2>{esc(result_title or REPORTS[report_key]["name"])} Results</h2>
    {result_html}
  </section>
</div>"""
    return render_layout(body)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        if parsed.path == "/run":
            report_key = selected_report(form.get("report", ["ci_by_class"])[0])
            params = query_params(form)
            error = None
            result = None
            try:
                result = run_report(report_key, params)
            except Exception as exc:
                error = str(exc)
            payload = render_page(report_key, params, result=result, error=error).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if parsed.path == "/duplicates/preview":
            report_key = selected_report(form.get("report", ["duplicate_serials"])[0])
            params = query_params(form)
            error = None
            result = None
            try:
                result = duplicate_resolution_preview(params)
            except Exception as exc:
                error = str(exc)
            payload = render_page(
                report_key,
                params,
                result=result,
                error=error,
                result_title="New Duplicate Resolution Preview",
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if parsed.path == "/duplicates/apply":
            report_key = selected_report(form.get("report", ["duplicate_serials"])[0])
            params = query_params(form)
            error = None
            result = None
            if form.get("confirm_duplicate_apply", [""])[0] != "1":
                error = "Confirm applying MarkAsDeleted = Yes before running duplicate resolution."
            else:
                try:
                    result = apply_duplicate_resolution(params)
                except Exception as exc:
                    error = str(exc)
            payload = render_page(
                report_key,
                params,
                result=result,
                error=error,
                result_title="MarkAsDeleted Apply",
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if parsed.path == "/settings":
            try:
                save_rest_settings(form)
                location = "/?saved=settings"
            except Exception as exc:
                location = "/?" + urlencode({"error": str(exc)})
            self.send_response(303)
            self.send_header("Location", location)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/assets/"):
            self.serve_asset(parsed.path)
            return
        form = parse_qs(parsed.query)
        report_key = selected_report(form.get("report", ["ci_by_class"])[0])
        params = query_params(form)
        if parsed.path == "/results/export":
            try:
                columns, rows = run_report(report_key, params)
                payload = build_xlsx(columns, rows)
                filename = f"{safe_filename(REPORTS[report_key]['name'])}-results.xlsx"
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as exc:
                self.send_text(str(exc), status=500)
            return
        if parsed.path != "/":
            self.send_response(404)
            self.end_headers()
            return

        error = form.get("error", [""])[0]
        message = "CMDB REST connection settings saved." if form.get("saved", [""])[0] == "settings" else ""
        result = None
        if not error:
            try:
                result = run_report(report_key, params)
            except Exception as exc:
                error = str(exc)
        payload = render_page(report_key, params, result=result, error=error, message=message).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def serve_asset(self, path):
        asset_name = os.path.basename(path)
        asset_path = os.path.join(ASSETS_DIR, asset_name)
        if not os.path.exists(asset_path):
            self.send_response(404)
            self.end_headers()
            return
        content_type = mimetypes.guess_type(asset_path)[0] or "application/octet-stream"
        with open(asset_path, "rb") as asset_file:
            payload = asset_file.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_text(self, text, status=200):
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def main():
    load_dotenv(override=True)
    port = rest_config()["port"]
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"{APP_TITLE} running at http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
