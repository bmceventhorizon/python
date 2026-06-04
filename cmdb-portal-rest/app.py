import html
import io
import json
import mimetypes
import os
import re
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
PRODUCT_CATALOG_DATASET = "BMC.AddToProductCatalog"
BASE_ELEMENT_CLASS = "BMC_BaseElement"
SYSTEM_MANAGED_ATTRIBUTE_KEYS = {
    "attributedatasourcelist",
    "classid",
    "cmdbrowlevelsecurity",
    "cmdbrowlevelsecurityparent",
    "cmdbwritesecurity",
    "cmdbwritesecurityparent",
    "compareactioncode",
    "createdate",
    "datasetid",
    "deleteinstancetrigger",
    "dsouniqueid",
    "failedautomaticidentification",
    "instanceid",
    "lastrejobrunid",
    "lastupdateddatasetid",
    "lastmodifiedby",
    "markasdeleted",
    "modifieddate",
    "normalizationstatus",
    "reconciliationidentity",
    "requestid",
    "requestidentifier",
    "readsecurity",
    "referenceinstance",
    "relleadclassid",
    "relleadinstanceid",
    "rowlevelsecurity",
    "submitter",
    "tokenid",
    "writesecurity",
}
SYSTEM_MANAGED_ATTRIBUTE_PREFIXES = ("reconciliation", "z1d", "zcmdbeng", "ztmp")
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
    "normalization_candidates": {
        "name": "Normalization Candidates",
        "description": "Lists individual CIs from the selected classes that can be copied into BMC.AddToProductCatalog for normalization.",
        "definition": "GET each selected CMDB class, then copy a selected CI to BMC.AddToProductCatalog through POST /api/cmdb/v1.0/instances.",
    },
    "normalization_company_summary": {
        "name": "Normalization and Company Summary",
        "description": "Groups all BaseElement CIs in the selected datasets and matches CI companies to COM:Company.",
        "definition": "GET BMC_BaseElement + GET /api/arsys/v1/entry/COM:Company, then match and group locally.",
    },
    "computer_system_attribute_sources": {
        "name": "Computer System Attribute Sources",
        "description": "Shows the source dataset for the Name attribute and translates AttributeDataSourceList field IDs.",
        "definition": "GET /api/cmdb/v1.0/instances/{dataset}/BMC.CORE/BMC_ComputerSystem + GET /api/arsys/v1.0/fields/BMC.CORE:BMC_ComputerSystem",
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


def instance_path(config, datasetid, namespace, class_name, instanceid):
    return f"{class_path(config, datasetid, class_name, namespace=namespace)}/{quote(str(instanceid), safe='')}"


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


def parse_entries(payload):
    if isinstance(payload, dict):
        entries = payload.get("entries")
        if isinstance(entries, list):
            return entries
        if isinstance(payload.get("values"), dict):
            return [payload]
    if isinstance(payload, list):
        return payload
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


def fetch_instance(config, token, datasetid, namespace, class_name, instanceid):
    url = instance_path(config, datasetid, namespace, class_name, instanceid)
    req = urllib.request.Request(url, headers=auth_headers(token), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"CMDB source CI lookup failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"CMDB source CI lookup failed: {exc.reason}") from exc
    instances = parse_instances(payload)
    if len(instances) != 1:
        raise RuntimeError(f"Expected one source CI but found {len(instances)}.")
    return instances[0], url


def fetch_all_ar_entries(config, token, form_name, fields=None):
    entries = []
    offset = 0
    while len(entries) < config["max_rows"]:
        remaining = config["max_rows"] - len(entries)
        limit = min(config["page_size"], remaining)
        query = {
            "limit": str(limit),
            "offset": str(offset),
        }
        if fields:
            query["fields"] = f"values({','.join(fields)})"
        url = f'{config["base_url"]}/api/arsys/v1/entry/{quote(form_name, safe="")}?{urlencode(query)}'
        req = urllib.request.Request(url, headers=auth_headers(token), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"AR REST request failed for {form_name}: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"AR REST request failed for {form_name}: {exc.reason}") from exc
        page = parse_entries(payload)
        entries.extend(page)
        if len(page) < limit:
            break
        offset += limit
    return entries


def fetch_ar_field_map(config, token, form_name):
    url = f'{config["base_url"]}/api/arsys/v1.0/fields/{quote(form_name, safe="")}/'
    req = urllib.request.Request(url, headers=auth_headers(token), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AR REST field metadata request failed for {form_name}: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"AR REST field metadata request failed for {form_name}: {exc.reason}") from exc

    if isinstance(payload, dict):
        field_items = payload.get("fields") or payload.get("items") or payload.get("data") or []
    elif isinstance(payload, list):
        field_items = payload
    else:
        field_items = []

    field_map = {}
    for field in field_items:
        if not isinstance(field, dict):
            continue
        field_id = field.get("id") or field.get("fieldId") or field.get("field_id") or field.get("fieldid")
        field_name = (
            field.get("name")
            or field.get("fieldName")
            or field.get("field_name")
            or field.get("fieldname")
        )
        if field_id not in (None, "") and field_name:
            field_map[str(field_id)] = str(field_name)
    if not field_map:
        raise RuntimeError(f"AR REST field metadata for {form_name} did not contain field IDs and names.")
    return field_map


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


def class_name_key(instance):
    value = instance.get("class_name_key", {}) if isinstance(instance, dict) else {}
    return value if isinstance(value, dict) else {}


def instance_namespace(instance, default="BMC.CORE"):
    return str(class_name_key(instance).get("namespace") or default)


def instance_class_name(instance):
    return str(class_name_key(instance).get("name") or row_value(instance, "ClassId") or "BMC_BaseElement")


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
    "Company",
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


def product_catalog_copy_attributes(instance):
    copied = {}
    for name, value in attributes(instance).items():
        normalized_name = normalize_key(name)
        if normalized_name in SYSTEM_MANAGED_ATTRIBUTE_KEYS or normalized_name.startswith(
            SYSTEM_MANAGED_ATTRIBUTE_PREFIXES
        ):
            continue
        if value is None:
            continue
        copied[name] = value
    return copied


def product_catalog_create_payload(instance, config):
    source_class = instance_class_name(instance)
    if normalize_key(source_class) == normalize_key(BASE_ELEMENT_CLASS):
        raise RuntimeError(
            "Cannot create a Product Catalog copy as BMC_BaseElement. Select the CI's concrete class."
        )
    copied_attributes = product_catalog_copy_attributes(instance)
    if not copied_attributes.get("Name") and row_value(instance, "Name"):
        copied_attributes["Name"] = row_value(instance, "Name")
    if not copied_attributes:
        raise RuntimeError("The source CI has no copyable attributes.")
    return {
        "instances": [
            {
                "class_name_key": {
                    "namespace": instance_namespace(instance, config["namespace"]),
                    "name": source_class,
                },
                "dataset_id": PRODUCT_CATALOG_DATASET,
                "attributes": copied_attributes,
            }
        ]
    }


def created_instance_reference(detail, source, config):
    try:
        payload = json.loads(detail)
    except (TypeError, ValueError):
        return None
    created_instances = parse_instances(payload)
    created = created_instances[0] if created_instances else payload if isinstance(payload, dict) else {}
    created_id = instance_id(created)
    if not created_id and isinstance(payload, dict):
        instance_ids = payload.get("instance_ids") or payload.get("instanceIds")
        if isinstance(instance_ids, list) and instance_ids:
            created_id = instance_ids[0]
    if not created_id:
        return None
    created_key = class_name_key(created)
    created_class = created_key.get("name") or row_value(created, "ClassId") or instance_class_name(source)
    return {
        "datasetid": str(dataset_id(created) or PRODUCT_CATALOG_DATASET),
        "namespace": instance_namespace(created, instance_namespace(source, config["namespace"])),
        "class_name": str(created_class),
        "instanceid": str(created_id),
    }


def verify_created_instance(config, token, reference):
    last_error = ""
    for attempt in range(3):
        if attempt:
            time.sleep(attempt * 0.5)
        try:
            instance, url = fetch_instance(
                config,
                token,
                reference["datasetid"],
                reference["namespace"],
                reference["class_name"],
                reference["instanceid"],
            )
            return instance, url, ""
        except Exception as exc:
            last_error = str(exc)
    return None, "", last_error


def create_product_catalog_copy(form):
    if form.get("confirm_product_catalog_add", [""])[0] != "1":
        raise RuntimeError("Confirm adding the CI to BMC.AddToProductCatalog.")
    source_dataset = form.get("source_datasetid", [""])[0].strip()
    source_namespace = form.get("source_namespace", ["BMC.CORE"])[0].strip() or "BMC.CORE"
    source_class = form.get("source_class_name", ["BMC_BaseElement"])[0].strip() or "BMC_BaseElement"
    source_instanceid = form.get("source_instanceid", [""])[0].strip()
    if not source_dataset or not source_instanceid:
        raise RuntimeError("Source DatasetId and InstanceId are required.")
    if source_dataset.lower() == PRODUCT_CATALOG_DATASET.lower():
        raise RuntimeError(f"{PRODUCT_CATALOG_DATASET} cannot be used as its own source dataset.")
    if normalize_key(source_class) == normalize_key(BASE_ELEMENT_CLASS):
        raise RuntimeError(
            "BMC_BaseElement is not a valid source class for this action. Select the CI's concrete class."
        )

    config = checked_rest_config()
    token = cmdb_login(config)
    source, source_url = fetch_instance(
        config,
        token,
        source_dataset,
        source_namespace,
        source_class,
        source_instanceid,
    )
    payload = product_catalog_create_payload(source, config)
    url = f'{config["base_url"]}/api/cmdb/v1.0/instances'
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=cmdb_json_headers(token),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            detail = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Create CI failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Create CI failed: {exc.reason}") from exc
    created_reference = created_instance_reference(detail, source, config)
    verified_instance = None
    verification_url = ""
    verification_error = ""
    if created_reference:
        verified_instance, verification_url, verification_error = verify_created_instance(
            config,
            token,
            created_reference,
        )
    else:
        verification_error = "Create response did not include a target InstanceId."
    return {
        "status": status,
        "source_url": source_url,
        "create_url": url,
        "source_name": row_value(source, "Name"),
        "source_class": instance_class_name(source),
        "attribute_count": len(payload["instances"][0]["attributes"]),
        "detail": detail,
        "created_reference": created_reference,
        "verified": verified_instance is not None,
        "verification_url": verification_url,
        "verification_error": verification_error,
    }


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


def fetch_all_base_elements(params):
    config = checked_rest_config()
    token = cmdb_login(config)
    instances, _ = fetch_all_instances(
        config,
        token,
        params["datasetids"],
        "BMC_BaseElement",
        attributes=BASE_FIELDS,
    )
    return config, token, instances


def fetch_selected_class_instances(params):
    config = checked_rest_config()
    token = cmdb_login(config)
    class_names = [str(value).strip() for value in params["classids"] if str(value).strip()]
    class_names = [
        class_name for class_name in class_names if normalize_key(class_name) != normalize_key(BASE_ELEMENT_CLASS)
    ]
    if not class_names:
        raise RuntimeError("Normalization Candidates requires at least one concrete Class ID other than BMC_BaseElement.")
    instances = []
    seen = set()
    for class_name in class_names:
        if len(instances) >= config["max_rows"]:
            break
        class_instances, _ = fetch_all_instances(
            config,
            token,
            params["datasetids"],
            class_name,
            attributes=BASE_FIELDS,
        )
        for instance in class_instances:
            key = (dataset_id(instance), instance_id(instance))
            if key in seen:
                continue
            seen.add(key)
            instances.append(instance)
            if len(instances) >= config["max_rows"]:
                break
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


def normalization_status_label(value):
    labels = {
        "10": "Other",
        "20": "Not Normalized",
        "30": "Not Applicable for Normalization",
        "40": "Normalization Failed",
        "50": "Normalized but Not Approved",
        "60": "Normalized and Approved",
        "70": "Modified after last Normalization",
    }
    status = str(value or "")
    if status in labels:
        return labels[status]
    display_labels = {normalize_key(label): label for label in labels.values()}
    return display_labels.get(normalize_key(status))


def report_normalization_summary(params):
    _, _, instances = fetch_base(params)
    counts = {}
    for instance in instances:
        status = str(row_value(instance, "NormalizationStatus") or "")
        key = (
            dataset_id(instance),
            normalization_status_label(status) or status or "(blank)",
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
        "datasetid",
        "normalizationstatus",
        "classid",
        "category",
        "type",
        "item",
        "model",
        "manufacturername",
    ], rows[: params["limit"]]


def report_normalization_candidates(params):
    _, _, instances = fetch_base(params)
    candidate_labels = {
        "Not Normalized",
        "Normalization Failed",
        "Normalized but Not Approved",
        "Modified after last Normalization",
    }
    rows = []
    for instance in instances:
        if str(dataset_id(instance)).lower() == PRODUCT_CATALOG_DATASET.lower():
            continue
        if normalize_key(instance_class_name(instance)) == normalize_key(BASE_ELEMENT_CLASS):
            continue
        status = normalization_status_label(row_value(instance, "NormalizationStatus"))
        if status not in candidate_labels:
            continue
        rows.append(
            (
                dataset_id(instance),
                instance_namespace(instance),
                instance_class_name(instance),
                instance_id(instance),
                row_value(instance, "Name"),
                status,
                row_value(instance, "Category"),
                row_value(instance, "Type"),
                row_value(instance, "Item"),
                row_value(instance, "Model"),
                row_value(instance, "ManufacturerName"),
                row_value(instance, "Company"),
            )
        )
    rows.sort(key=lambda row: (str(row[5]), str(row[4]), str(row[0]), str(row[3])))
    return [
        "datasetid",
        "namespace",
        "class_name",
        "instanceid",
        "name",
        "normalizationstatus",
        "category",
        "type",
        "item",
        "model",
        "manufacturername",
        "company",
    ], rows[: params["limit"]]


def entry_values(entry):
    values = entry.get("values", {}) if isinstance(entry, dict) else {}
    return values if isinstance(values, dict) else {}


def entry_value(entry, *names, default=""):
    values = entry_values(entry)
    normalized = {normalize_key(key): value for key, value in values.items()}
    for name in names:
        if name in values:
            return values[name]
        key = normalize_key(name)
        if key in normalized:
            return normalized[key]
    return default


def company_match(companies, company):
    company_lower = str(company or "").lower()
    matches = []
    for entry in companies:
        description = entry_value(entry, "Description")
        if not str(description or "").strip():
            continue
        company_name = entry_value(entry, "Company")
        company_name_lower = str(company_name or "").lower()
        description_lower = str(description or "").lower()
        if (
            company_name_lower == company_lower
            or company_lower in description_lower
            or company_name_lower in company_lower
        ):
            if company_name_lower == company_lower:
                rank = 0
            elif company_lower in description_lower:
                rank = 1
            else:
                rank = 2
            matches.append((rank, len(str(description)), entry))
    if not matches:
        return None
    matches.sort(key=lambda match: (match[0], match[1]))
    return matches[0][2]


def report_normalization_company_summary(params):
    config, token, instances = fetch_all_base_elements(params)
    company_form = os.getenv("CMDB_REST_COMPANY_FORM", "COM:Company")
    companies = fetch_all_ar_entries(
        config,
        token,
        company_form,
        fields=["Company", "Description", "Company Type"],
    )
    counts = {}
    match_cache = {}
    for instance in instances:
        status = str(row_value(instance, "NormalizationStatus") or "")
        source_company = pick_attr(instance, "Company", "company", default=None)
        display_company = "- Global -" if source_company == "BMC Software" else source_company
        cache_key = str(display_company or "").lower()
        if cache_key not in match_cache:
            match_cache[cache_key] = company_match(companies, display_company)
        match = match_cache[cache_key]
        manufacturer = pick_attr(instance, "ManufacturerName", "manufacturername", default=None)
        key = (
            normalization_status_label(status),
            row_value(instance, "ClassId"),
            row_value(instance, "Category"),
            row_value(instance, "Type"),
            row_value(instance, "Item"),
            row_value(instance, "Model"),
            "BMC_UNKNOWN" if manufacturer is None else manufacturer,
            display_company,
            entry_value(match, "Description") if match else None,
            entry_value(match, "Company Type", "CompanyType", "company_type") if match else None,
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
        "company",
        "com_company",
        "companytype",
    ], rows[: params["limit"]]


def attribute_data_source_segments(value):
    return [segment for segment in str(value or "").split("/") if segment]


def segment_field_names(segment, field_map):
    field_ids = re.findall(r"(?<!\d)(\d+)(?!\d)", segment)
    return sorted({field_map[field_id] for field_id in field_ids if field_id in field_map})


def translate_attribute_data_source_list(value, field_map):
    translated = []
    for segment in attribute_data_source_segments(value):
        names = segment_field_names(segment, field_map)
        if not names:
            translated.append(segment)
            continue
        prefix, separator, _ = segment.partition(":")
        translated.append(f"{prefix}: {', '.join(names)}" if separator else ", ".join(names))
    return " / ".join(translated)


def report_computer_system_attribute_sources(params):
    config = checked_rest_config()
    token = cmdb_login(config)
    field_map = fetch_ar_field_map(config, token, "BMC.CORE:BMC_ComputerSystem")
    name_field_ids = {field_id for field_id, field_name in field_map.items() if field_name == "Name"}
    instances, _ = fetch_all_instances(
        config,
        token,
        params["datasetids"],
        "BMC_ComputerSystem",
        attributes=["Name", "DatasetId", "AttributeDataSourceList"],
    )
    rows = []
    for instance in instances:
        source_list = row_value(instance, "AttributeDataSourceList")
        if source_list in (None, ""):
            continue
        name_source = ""
        for segment in attribute_data_source_segments(source_list):
            segment_ids = set(re.findall(r"(?<!\d)(\d+)(?!\d)", segment))
            if name_field_ids.intersection(segment_ids) and ":" in segment:
                name_source = segment.split(":", 1)[0]
                break
        rows.append(
            (
                name_source,
                row_value(instance, "Name"),
                dataset_id(instance),
                source_list,
                translate_attribute_data_source_list(source_list, field_map),
            )
        )
    rows.sort(key=lambda row: str(row[1] or "").lower())
    return [
        "list",
        "ci_name",
        "dataset",
        "attributedatasourcelist",
        "Precedence Contest Winner",
    ], rows[: params["limit"]]


REPORT_RUNNERS = {
    "ci_by_class": report_ci_by_class,
    "ci_inventory": report_ci_inventory,
    "duplicate_serials": report_duplicate_serials,
    "orphaned_cis": report_orphaned_cis,
    "relationship_summary": report_relationship_summary,
    "normalization_summary": report_normalization_summary,
    "normalization_candidates": report_normalization_candidates,
    "normalization_company_summary": report_normalization_company_summary,
    "computer_system_attribute_sources": report_computer_system_attribute_sources,
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
    .inline-action {{ margin:0; }}
    .inline-action button {{ width:auto; min-width:150px; margin:0; padding:7px 10px; white-space:nowrap; }}
    .actions {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
    .run-button {{ min-height:72px; display:flex; align-items:center; justify-content:center; gap:12px; font-size:18px; background:#05070c; border:1px solid #1e293b; }}
    .run-button img {{ width:92px; height:38px; object-fit:contain; border-radius:4px; }}
    .run-button[aria-busy="true"] {{ cursor:wait; opacity:.88; }}
    .spinner {{ display:none; width:22px; height:22px; border:3px solid rgba(255,255,255,.35); border-top-color:white; border-radius:50%; animation:spin .75s linear infinite; }}
    .run-button[aria-busy="true"] .spinner {{ display:block; }}
    .loading-bar {{ position:fixed; inset:0 0 auto 0; z-index:20; height:4px; overflow:hidden; background:rgba(20,108,148,.2); opacity:0; pointer-events:none; transition:opacity .15s ease; }}
    .loading-bar::after {{ content:""; display:block; width:35%; height:100%; background:var(--accent); animation:loading-slide 1.1s ease-in-out infinite; }}
    body.report-running .loading-bar {{ opacity:1; }}
    @keyframes spin {{ to {{ transform:rotate(360deg); }} }}
    @keyframes loading-slide {{ from {{ transform:translateX(-110%); }} to {{ transform:translateX(320%); }} }}
    .query-fields {{ display:grid; grid-template-columns:minmax(220px,1fr) minmax(240px,1.2fr); gap:14px; align-items:start; }}
    .compact-field {{ max-width:180px; }}
    .query-wide {{ grid-column:1 / -1; }}
    .sql {{ background:#101820; color:#eef6ff; border-radius:8px; padding:13px; overflow:auto; font-size:12px; height:180px; }}
    .hint {{ color:var(--muted); font-size:12px; margin:8px 0 0; }}
    .status {{ display:flex; gap:10px; flex-wrap:wrap; color:var(--muted); margin-bottom:12px; }}
    .pill {{ border:1px solid var(--line); border-radius:999px; padding:4px 9px; background:#fbfcfd; }}
    .export-pill {{ color:var(--accent); font-weight:700; text-decoration:none; }}
    .error {{ border:1px solid #e5b4b4; background:#fff7f7; color:var(--danger); border-radius:8px; padding:12px; white-space:pre-wrap; overflow:auto; }}
    .success {{ border:1px solid #a8d5b8; background:#f3fbf6; color:#23643b; border-radius:8px; padding:12px; margin-bottom:14px; }}
    .warning {{ border:1px solid #e1c36a; background:#fffaf0; color:#725510; border-radius:8px; padding:12px; margin-bottom:14px; }}
    .table-wrap {{ height:680px; min-height:240px; overflow:auto; border-top:1px solid var(--line); resize:vertical; }}
    table {{ width:max-content; min-width:100%; border-collapse:collapse; background:white; font-size:13px; }}
    th,td {{ border-bottom:1px solid #e6ebf1; padding:8px 10px; text-align:left; vertical-align:top; white-space:nowrap; max-width:420px; overflow:hidden; text-overflow:ellipsis; }}
    th {{ position:sticky; top:0; background:#eef2f7; z-index:1; font-weight:700; }}
    .sort-button {{ display:flex; align-items:center; gap:6px; width:100%; min-height:0; margin:0; padding:0; border:0; border-radius:0; background:transparent; color:inherit; font:inherit; text-align:left; }}
    .sort-button:hover {{ background:transparent; color:var(--accent-dark); }}
    .sort-indicator {{ color:var(--muted); font-size:11px; min-width:10px; }}
    .column-resizer {{ position:absolute; top:0; right:-4px; width:9px; height:100%; z-index:3; cursor:col-resize; user-select:none; touch-action:none; }}
    .column-resizer::after {{ content:""; position:absolute; top:20%; bottom:20%; left:4px; width:1px; background:#aeb8c5; }}
    .column-resizer:hover::after, body.column-resizing .column-resizer::after {{ background:var(--accent); width:2px; }}
    body.column-resizing {{ cursor:col-resize; user-select:none; }}
    .filter-row th {{ background:#e4eaf3; padding:4px 6px; position:sticky; top:37px; z-index:1; }}
    .filter-row input, .filter-row select {{ width:100%; box-sizing:border-box; padding:3px 6px; font-size:12px; background:#fff; border:1px solid #c8d0dc; border-radius:3px; color:#1a202c; }}
    .filter-row input:focus, .filter-row select:focus {{ outline:none; border-color:#5a7fc9; }}
    .empty {{ color:var(--muted); padding:24px; text-align:center; }}
    @media (max-width:860px) {{ .query-panel {{ grid-column:auto; }} .query-fields {{ grid-template-columns:1fr; }} .header-bar {{ align-items:flex-start; flex-direction:column; }} main {{ padding:14px; }} th,td {{ white-space:normal; }} }}
  </style>
</head>
<body>
  <div class="loading-bar" aria-hidden="true"></div>
  <header>
    <div class="header-bar">
      <h1><img class="header-logo" src="/assets/2024-bmc-helix-reversed.png" alt="BMC Helix">{APP_TITLE}</h1>
      <a class="portal-switch" href="{esc(os.getenv("CMDB_SQL_PORTAL_URL", "http://127.0.0.1:8000"))}">SQL Portal</a>
    </div>
  </header>
  <main>{body}</main>
  <script>
    function sortableValue(cell) {{
      const value = cell.textContent.trim();
      const number = Number(value.replace(/,/g, ""));
      return value !== "" && Number.isFinite(number)
        ? {{ kind: "number", value: number }}
        : {{ kind: "text", value: value.toLocaleLowerCase() }};
    }}

    document.querySelectorAll("table[data-sortable='true']").forEach((table) => {{
      table.querySelectorAll("th[data-sort-index]").forEach((header) => {{
        const button = header.querySelector(".sort-button");
        if (!button) return;
        button.addEventListener("click", () => {{
          const index = Number(header.dataset.sortIndex);
          const direction = header.getAttribute("aria-sort") === "ascending" ? "descending" : "ascending";
          const rows = Array.from(table.tBodies[0].rows);
          rows.sort((left, right) => {{
            const a = sortableValue(left.cells[index]);
            const b = sortableValue(right.cells[index]);
            const comparison = a.kind === "number" && b.kind === "number"
              ? a.value - b.value
              : String(a.value).localeCompare(String(b.value), undefined, {{ numeric: true, sensitivity: "base" }});
            return direction === "ascending" ? comparison : -comparison;
          }});
          rows.forEach((row) => table.tBodies[0].appendChild(row));
          table.querySelectorAll("th[data-sort-index]").forEach((other) => {{
            other.removeAttribute("aria-sort");
            const indicator = other.querySelector(".sort-indicator");
            if (indicator) indicator.textContent = "";
          }});
          header.setAttribute("aria-sort", direction);
          header.querySelector(".sort-indicator").textContent = direction === "ascending" ? "ASC" : "DESC";
        }});
      }});
    }});

    document.querySelectorAll("table[data-sortable='true']").forEach((table) => {{
      table.querySelectorAll("th[data-sort-index]").forEach((header) => {{
        const index = Number(header.dataset.sortIndex);
        const resizer = document.createElement("span");
        resizer.className = "column-resizer";
        resizer.title = "Resize column";
        resizer.setAttribute("aria-hidden", "true");
        resizer.addEventListener("mousedown", (event) => {{
          event.preventDefault();
          event.stopPropagation();
          const startX = event.clientX;
          const startWidth = header.getBoundingClientRect().width;
          const cells = Array.from(table.rows).map(row => row.cells[index]).filter(Boolean);
          cells.forEach(cell => {{
            const width = cell.getBoundingClientRect().width;
            cell.style.width = `${{width}}px`;
            cell.style.minWidth = `${{width}}px`;
            cell.style.maxWidth = `${{width}}px`;
          }});
          document.body.classList.add("column-resizing");
          const move = (moveEvent) => {{
            const width = Math.max(70, startWidth + moveEvent.clientX - startX);
            cells.forEach(cell => {{
              cell.style.width = `${{width}}px`;
              cell.style.minWidth = `${{width}}px`;
              cell.style.maxWidth = `${{width}}px`;
            }});
          }};
          const stop = () => {{
            document.body.classList.remove("column-resizing");
            document.removeEventListener("mousemove", move);
            document.removeEventListener("mouseup", stop);
          }};
          document.addEventListener("mousemove", move);
          document.addEventListener("mouseup", stop);
        }});
        header.appendChild(resizer);
      }});
    }});

    document.querySelectorAll("table[data-sortable='true']").forEach((table) => {{
      table.querySelectorAll(".filter-row select[data-filter-col]").forEach(sel => {{
        const col = Number(sel.dataset.filterCol);
        const seen = new Set();
        Array.from(table.tBodies[0].rows).forEach(row => {{
          const cell = row.cells[col];
          if (cell) {{ const v = cell.textContent.trim(); if (v) seen.add(v); }}
        }});
        Array.from(seen).sort((a, b) => a.localeCompare(b, undefined, {{ sensitivity: "base" }}))
          .forEach(v => {{ const opt = document.createElement("option"); opt.value = v; opt.textContent = v; sel.appendChild(opt); }});
      }});
      const controls = table.querySelectorAll(".filter-row input[data-filter-col], .filter-row select[data-filter-col]");
      if (!controls.length) return;
      const applyFilters = () => {{
        const filters = Array.from(controls)
          .map(el => ({{ col: Number(el.dataset.filterCol), value: el.value.trim().toLowerCase(), exact: el.tagName === "SELECT" }}))
          .filter(f => f.value);
        Array.from(table.tBodies[0].rows).forEach(row => {{
          row.style.display = filters.every(f => {{
            const cell = row.cells[f.col];
            if (!cell) return true;
            const text = cell.textContent.trim().toLowerCase();
            return f.exact ? text === f.value : text.includes(f.value);
          }}) ? "" : "none";
        }});
      }};
      controls.forEach(el => el.addEventListener(el.tagName === "SELECT" ? "change" : "input", applyFilters));
    }});

    document.querySelectorAll("form").forEach((form) => {{
      form.addEventListener("submit", (e) => {{
        const submitter = e.submitter;
        if (!submitter) return;
        if (submitter.id === "run-report-button") return;
        submitter.disabled = true;
        const span = submitter.querySelector("span") || submitter;
        const original = span.textContent;
        span.textContent = "Running…";
        submitter.classList.add("running");
        const cleanup = () => {{
          submitter.disabled = false;
          span.textContent = original;
          submitter.classList.remove("running");
        }};
        window.addEventListener("pageshow", cleanup, {{ once: true }});
        setTimeout(cleanup, 60000);
      }});
    }});

    const reportForm = document.getElementById("report-form");
    const runReportButton = document.getElementById("run-report-button");
    if (reportForm && runReportButton) {{
      reportForm.addEventListener("submit", (event) => {{
        if (event.submitter !== runReportButton) return;
        document.body.classList.add("report-running");
        runReportButton.setAttribute("aria-busy", "true");
        runReportButton.disabled = true;
        const label = runReportButton.querySelector(".run-label");
        if (label) label.textContent = "Running REST Report...";
        const cleanup = () => {{
          document.body.classList.remove("report-running");
          runReportButton.removeAttribute("aria-busy");
          runReportButton.disabled = false;
          if (label) label.textContent = "Run REST Report";
        }};
        window.addEventListener("pageshow", cleanup, {{ once: true }});
        setTimeout(cleanup, 60000);
      }});
    }}
  </script>
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
    <form id="report-form" method="post" action="/run">
      <div class="query-fields">
        <div>
          <label for="report">Report</label>
          <select id="report" name="report">{options}</select>
          <p class="hint">{esc(report["description"])}</p>
          <div class="compact-field">
            <label for="limit">Row Limit</label>
            <input id="limit" name="limit" type="number" min="1" max="100000" value="{esc(params["limit"])}">
          </div>
        </div>
        <div>
          <label for="datasetids">Datasets</label>
          {dataset_select}
          {dataset_hint}
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
      <button class="run-button" id="run-report-button" type="submit">
        <img src="/assets/blackholesurfer-logo.jpg" alt="">
        <span class="spinner" aria-hidden="true"></span>
        <span class="run-label">Run REST Report</span>
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


def hidden_input(name, value):
    return f'<input type="hidden" name="{esc(name)}" value="{esc(value)}">'


def render_product_catalog_action(columns, row, report_key, params):
    values = dict(zip(columns, row))
    hidden = "".join(
        [
            hidden_input("report", report_key),
            hidden_input("datasetids", "\n".join(params["datasetids"])),
            hidden_input("classids", "\n".join(params["classids"])),
            hidden_input("limit", params["limit"]),
            hidden_input("source_datasetid", values.get("datasetid", "")),
            hidden_input("source_namespace", values.get("namespace", "BMC.CORE")),
            hidden_input("source_class_name", values.get("class_name", "BMC_BaseElement")),
            hidden_input("source_instanceid", values.get("instanceid", "")),
            hidden_input("confirm_product_catalog_add", "1"),
        ]
    )
    return (
        '<form class="inline-action" method="post" action="/product-catalog/add">'
        f"{hidden}"
        '<button type="submit" onclick="return confirm(\'Create a copy of this CI in BMC.AddToProductCatalog?\')">'
        "Add to Product Catalog"
        "</button></form>"
    )


def render_table(columns, rows, report_key=None, params=None):
    if not rows:
        return '<div class="empty">No rows returned.</div>'
    include_product_action = report_key == "normalization_candidates" and params is not None
    display_columns = (["action"] if include_product_action else []) + list(columns)
    head = "".join(
        (
            f'<th data-sort-index="{index}"><button class="sort-button" type="button">'
            f'<span>{esc(column)}</span><span class="sort-indicator" aria-hidden="true"></span>'
            "</button></th>"
        )
        if column != "action"
        else "<th>action</th>"
        for index, column in enumerate(display_columns)
    )
    body_rows = []
    for row in rows:
        action = (
            f"<td>{render_product_catalog_action(columns, row, report_key, params)}</td>"
            if include_product_action
            else ""
        )
        body_rows.append("<tr>" + action + "".join(f"<td>{esc(value)}</td>" for value in row) + "</tr>")
    body = "".join(body_rows)
    dropdown_cols = {"normalizationstatus", "classid", "manufacturername", "dataset", "datasetid"}
    filter_cells = "".join(
        "<th></th>" if column == "action"
        else f'<th><select data-filter-col="{index}"><option value="">All</option></select></th>'
        if column.lower() in dropdown_cols
        else f'<th><input type="text" placeholder="Filter…" data-filter-col="{index}" autocomplete="off"></th>'
        for index, column in enumerate(display_columns)
    )
    filter_row = f'<tr class="filter-row">{filter_cells}</tr>'
    return f"""<div class="table-wrap"><table data-sortable="true"><thead><tr>{head}</tr>{filter_row}</thead><tbody>{body}</tbody></table></div>"""


def render_page(
    report_key,
    params,
    result=None,
    error=None,
    message="",
    result_title=None,
    result_message="",
    result_message_kind="success",
):
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
            + render_table(columns, rows, report_key=report_key, params=params)
        )
    else:
        result_html = '<div class="empty">Choose a REST report and run it.</div>'
    if result_message:
        banner_class = "success" if result_message_kind == "success" else "warning"
        result_html = f'<div class="panel-body"><div class="{banner_class}">{esc(result_message)}</div></div>' + result_html
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
        if parsed.path == "/product-catalog/add":
            report_key = selected_report(form.get("report", ["normalization_candidates"])[0])
            params = query_params(form)
            error = None
            result = None
            result_message = ""
            result_message_kind = "success"
            try:
                create_result = create_product_catalog_copy(form)
                created_reference = create_result["created_reference"]
                if create_result["verified"]:
                    result_message = (
                        f'Confirmed: "{create_result["source_name"]}" was added to {PRODUCT_CATALOG_DATASET} '
                        f'as {created_reference["class_name"]} InstanceId {created_reference["instanceid"]}. '
                        "The CI is eligible for the next normalization job."
                    )
                else:
                    result_message_kind = "warning"
                    target_id = (
                        f' Target InstanceId: {created_reference["instanceid"]}.'
                        if created_reference
                        else ""
                    )
                    result_message = (
                        f'Create accepted for "{create_result["source_name"]}" (HTTP {create_result["status"]}), '
                        f"but the portal could not verify the copy in {PRODUCT_CATALOG_DATASET}.{target_id} "
                        f'Eligibility is not yet confirmed. Verification detail: {create_result["verification_error"]}'
                    )
                result = run_report(report_key, params)
            except Exception as exc:
                error = str(exc)
            payload = render_page(
                report_key,
                params,
                result=result,
                error=error,
                result_message=result_message,
                result_message_kind=result_message_kind,
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
        if parsed.path == "/datasets":
            try:
                payload = json.dumps({"datasets": fetch_regular_datasets()}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as exc:
                self.send_text(str(exc), status=500)
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
