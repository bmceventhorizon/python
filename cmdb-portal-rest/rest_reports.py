#!/usr/bin/env python3
import argparse
import csv
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from urllib.parse import quote, urlencode


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "rest_config.json")
DEFAULT_BASE_URL = "https://your-helix-restapi.example.com"
DEFAULT_NAMESPACE = "BMC.CORE"
DEFAULT_DATASET = "BMC.ASSET"
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

BASE_FIELDS = [
    "InstanceId",
    "DatasetId",
    "ClassId",
    "Name",
    "ShortDescription",
    "CreateDate",
    "ModifiedDate",
    "Submitter",
    "Site",
    "SerialNumber",
    "ManufacturerName",
    "Model",
    "Category",
    "Type",
    "Item",
    "NormalizationStatus",
    "ReconciliationIdentity",
    "MarkAsDeleted",
]


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as config_file:
        return json.load(config_file)


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as config_file:
        json.dump(config, config_file, indent=2, sort_keys=True)
        config_file.write("\n")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def prompt_value(label, current="", secret=False):
    suffix = f" [{current}]" if current and not secret else ""
    prompt = f"{label}{suffix}: "
    if secret:
        value = getpass.getpass(prompt)
    else:
        value = input(prompt).strip()
    return value or current


def configure(_args):
    current = load_config()
    base_url = prompt_value("CMDB REST Base URL", current.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
    username = prompt_value("Username", current.get("username", ""))
    password = prompt_value("Password", current.get("password", ""), secret=True)
    namespace = prompt_value("Namespace", current.get("namespace", DEFAULT_NAMESPACE))
    page_size = prompt_value("Page size", str(current.get("page_size", DEFAULT_PAGE_SIZE)))
    max_rows = prompt_value("Max rows per report", str(current.get("max_rows", DEFAULT_MAX_ROWS)))

    save_config(
        {
            "base_url": base_url,
            "username": username,
            "password": password,
            "namespace": namespace,
            "page_size": int(page_size or DEFAULT_PAGE_SIZE),
            "max_rows": int(max_rows or DEFAULT_MAX_ROWS),
        }
    )
    print(f"Saved REST config to {CONFIG_PATH}")


def checked_config():
    config = load_config()
    missing = [key for key in ("base_url", "username", "password") if not config.get(key)]
    if missing:
        raise RuntimeError(
            "Missing REST config: "
            + ", ".join(missing)
            + ". Run: python3 rest_reports.py configure"
        )
    config.setdefault("namespace", DEFAULT_NAMESPACE)
    config.setdefault("page_size", DEFAULT_PAGE_SIZE)
    config.setdefault("max_rows", DEFAULT_MAX_ROWS)
    return config


def login(config):
    data = urlencode({"username": config["username"], "password": config["password"]}).encode("utf-8")
    request = urllib.request.Request(
        f'{config["base_url"]}/api/jwt/login',
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            token = response.read().decode("utf-8").strip().strip('"')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Login failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Login failed: {exc.reason}") from exc
    if not token or token.startswith("<"):
        raise RuntimeError("Login did not return a JWT.")
    return token


def auth_headers(token):
    return {"Authorization": f"AR-JWT {token}", "Accept": "application/json"}


def class_url(config, dataset, class_name):
    parts = [
        config["base_url"],
        "api",
        "cmdb",
        "v1.0",
        "instances",
        quote(dataset, safe=""),
        quote(config["namespace"], safe=""),
        quote(class_name, safe=""),
    ]
    return "/".join(part.strip("/") for part in parts)


def parse_instances(payload):
    if isinstance(payload, dict):
        instances = payload.get("instances")
        if isinstance(instances, list):
            return instances
        if "attributes" in payload or "instance_id" in payload:
            return [payload]
    if isinstance(payload, list):
        return payload
    return []


def quote_qualification(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def or_qualification(field, values):
    values = [value for value in values if value]
    clauses = [f"'{field}'=\"{quote_qualification(value)}\"" for value in values]
    if not clauses:
        return ""
    return clauses[0] if len(clauses) == 1 else "(" + " OR ".join(clauses) + ")"


def get_instances(config, token, dataset, class_name, qualification="", attributes=None):
    rows = []
    offset = 0
    while len(rows) < int(config["max_rows"]):
        limit = min(int(config["page_size"]), int(config["max_rows"]) - len(rows))
        query = {"limit": str(limit), "offset": str(offset)}
        if qualification:
            query["qualification"] = qualification
        if attributes:
            query["attributes"] = ",".join(attributes)

        url = f"{class_url(config, dataset, class_name)}?{urlencode(query)}"
        request = urllib.request.Request(url, headers=auth_headers(token), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GET failed for {class_name}: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GET failed for {class_name}: {exc.reason}") from exc

        page = parse_instances(payload)
        for item in page:
            item["_dataset"] = dataset
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
    return rows


def norm_key(value):
    return "".join(char for char in str(value).lower() if char.isalnum())


def attrs(instance):
    value = instance.get("attributes", {})
    return value if isinstance(value, dict) else {}


def attr(instance, *names, default=""):
    values = attrs(instance)
    normalized = {norm_key(key): value for key, value in values.items()}
    for name in names:
        if name in values:
            return values[name]
        key = norm_key(name)
        if key in normalized:
            return normalized[key]
    return default


def instance_id(instance):
    return instance.get("instance_id") or attr(instance, "InstanceId", "instanceid")


def dataset_id(instance):
    return instance.get("dataset_id") or attr(instance, "DatasetId", "datasetid", default=instance.get("_dataset", ""))


def epoch_to_text(value):
    if value in ("", None):
        return ""
    try:
        import time

        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(float(value)))
    except (TypeError, ValueError):
        return value


def load_base(config, token, args):
    classids = args.classid or DEFAULT_CLASSES
    qualification = or_qualification("ClassId", classids)
    rows = []
    for dataset in args.dataset:
        rows.extend(
            get_instances(
                config,
                token,
                dataset,
                "BMC_BaseElement",
                qualification=qualification,
                attributes=BASE_FIELDS,
            )
        )
    selected = set(classids)
    return [row for row in rows if attr(row, "ClassId") in selected]


def load_relationships(config, token, datasets):
    rows = []
    for dataset in datasets:
        rows.extend(get_instances(config, token, dataset, "BMC_BaseRelationship"))
    return rows


def relationship_ids(relationship):
    source = attr(
        relationship,
        "Source.InstanceId",
        "SourceInstanceId",
        "Source_InstanceId",
        "source_instanceid",
        "sourceinstanceid",
    )
    destination = attr(
        relationship,
        "Destination.InstanceId",
        "DestinationInstanceId",
        "Destination_InstanceId",
        "destination_instanceid",
        "destinationinstanceid",
    )
    return str(source or ""), str(destination or "")


def report_ci_by_class(config, token, args):
    rows = load_base(config, token, args)
    counts = Counter((dataset_id(row), attr(row, "ClassId") or "(blank)") for row in rows)
    result = [(dataset, classid, count) for (dataset, classid), count in counts.items()]
    result.sort(key=lambda row: (-row[2], row[0], row[1]))
    return ["datasetid", "classid", "total_cis"], result


def report_ci_inventory(config, token, args):
    rows = []
    for item in load_base(config, token, args)[: args.limit]:
        rows.append(
            (
                epoch_to_text(attr(item, "CreateDate")),
                epoch_to_text(attr(item, "ModifiedDate")),
                attr(item, "ClassId"),
                instance_id(item),
                attr(item, "Name"),
                dataset_id(item),
                attr(item, "SerialNumber"),
                attr(item, "ManufacturerName"),
                attr(item, "Model"),
                attr(item, "Site"),
                attr(item, "Submitter"),
            )
        )
    return [
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
    ], rows


def report_duplicate_serials(config, token, args):
    groups = defaultdict(list)
    for item in load_base(config, token, args):
        serial = str(attr(item, "SerialNumber") or "").strip()
        if serial:
            groups[(dataset_id(item), serial)].append(item)
    rows = []
    for (dataset, serial), items in groups.items():
        if len(items) > 1:
            rows.append(
                (
                    dataset,
                    serial,
                    len(items),
                    ", ".join(str(instance_id(item)) for item in items),
                    ", ".join(str(attr(item, "Name")) for item in items),
                )
            )
    rows.sort(key=lambda row: (-row[2], row[0], row[1]))
    return ["datasetid", "serialnumber", "duplicate_count", "instanceids", "names"], rows[: args.limit]


def report_orphaned_cis(config, token, args):
    base_rows = load_base(config, token, args)
    relationships = load_relationships(config, token, args.dataset)
    related = set()
    for relationship in relationships:
        source, destination = relationship_ids(relationship)
        if source:
            related.add(source)
        if destination:
            related.add(destination)

    rows = []
    for item in base_rows:
        iid = str(instance_id(item))
        if iid not in related:
            rows.append(
                (
                    epoch_to_text(attr(item, "CreateDate")),
                    epoch_to_text(attr(item, "ModifiedDate")),
                    attr(item, "ClassId"),
                    iid,
                    attr(item, "Name"),
                    dataset_id(item),
                    attr(item, "Site"),
                    attr(item, "Submitter"),
                )
            )
        if len(rows) >= args.limit:
            break
    return ["datecreated", "datemodified", "classid", "instanceid", "ci_name", "datasetid", "site", "submitter"], rows


REPORTS = {
    "ci-by-class": report_ci_by_class,
    "ci-inventory": report_ci_inventory,
    "duplicate-serials": report_duplicate_serials,
    "orphaned-cis": report_orphaned_cis,
}


def print_table(columns, rows):
    widths = [len(column) for column in columns]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = min(80, max(widths[index], len(str(value))))
    print(" | ".join(column.ljust(widths[index]) for index, column in enumerate(columns)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(str(value).ljust(widths[index])[: widths[index]] for index, value in enumerate(row)))


def emit_result(columns, rows, output):
    if output == "json":
        print(json.dumps([dict(zip(columns, row)) for row in rows], indent=2, default=str))
    elif output == "csv":
        writer = csv.writer(sys.stdout)
        writer.writerow(columns)
        writer.writerows(rows)
    else:
        print_table(columns, rows)


def run_report(args):
    config = checked_config()
    token = login(config)
    columns, rows = REPORTS[args.report](config, token, args)
    emit_result(columns, rows, args.output)


def build_parser():
    parser = argparse.ArgumentParser(description="Run CMDB reports using only the BMC CMDB REST API.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure_parser = subparsers.add_parser("configure", help="Prompt for REST URL, username, and password.")
    configure_parser.set_defaults(func=configure)

    for name in REPORTS:
        report_parser = subparsers.add_parser(name, help=f"Run {name} report.")
        report_parser.add_argument("--dataset", action="append", default=None, help="Dataset ID. Repeat for multiple datasets.")
        report_parser.add_argument("--classid", action="append", default=None, help="ClassId. Repeat for multiple classes.")
        report_parser.add_argument("--limit", type=int, default=250, help="Maximum output rows.")
        report_parser.add_argument("--output", choices=("table", "json", "csv"), default="table")
        report_parser.set_defaults(func=run_report, report=name)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "dataset", None) is None:
        args.dataset = [DEFAULT_DATASET]
    try:
        args.func(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
