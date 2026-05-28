import html
import io
import json
import mimetypes
import os
import re
import shlex
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse
from xml.sax.saxutils import escape as xml_escape

from queries import DEFAULT_CLASSES, QUERIES


APP_TITLE = "CMDB Data Management Dashboard by BMC Helix Seal Team"
ENV_PATH = ".env"
REPORTS_PATH = "reports.json"
BUILTIN_PREFIX = "builtin:"
REPORT_PREFIX = "report:"
ASSETS_DIR = "assets"
DEFAULT_OPENAI_MODEL = "gpt-5.2"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
DEFAULT_CURSOR_MODEL = "auto"
DEFAULT_CMDB_REST_PORT = "8008"
DEFAULT_CMDB_NAMESPACE = "BMC.CORE"
DEFAULT_CMDB_CLASS = "BMC_BaseElement"
DEFAULT_CMDB_DELETE_OPTION = "PURGE"
AI_PROVIDERS = {
    "openai": "ChatGPT / OpenAI",
    "anthropic": "Claude / Anthropic",
    "cursor": "Cursor",
}
OPENAI_MODELS = [
    ("gpt-5.2-chat-latest", "ChatGPT 5.2"),
    ("gpt-5.2", "GPT-5.2"),
    ("gpt-5.2-pro", "GPT-5.2 Pro"),
    ("gpt-5.2-codex", "GPT-5.2 Codex"),
    ("gpt-5-mini", "GPT-5 Mini"),
    ("gpt-5-nano", "GPT-5 Nano"),
]
ANTHROPIC_MODELS = [
    ("claude-opus-4-1-20250805", "Claude Opus 4.1"),
    ("claude-opus-4-20250514", "Claude Opus 4"),
    ("claude-sonnet-4-20250514", "Claude Sonnet 4"),
    ("claude-3-7-sonnet-20250219", "Claude Sonnet 3.7"),
    ("claude-3-5-haiku-20241022", "Claude Haiku 3.5"),
]
CURSOR_MODELS = [
    ("auto", "Cursor Auto"),
    ("claude-4-sonnet", "Cursor Claude 4 Sonnet"),
    ("claude-4-opus", "Cursor Claude 4 Opus"),
]


def load_dotenv(path=ENV_PATH, override=False):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if override or key not in os.environ:
                os.environ[key] = value


def load_shell_profile_keys():
    paths = [
        os.path.expanduser("~/.bash_profile"),
        os.path.expanduser("~/.zprofile"),
        os.path.expanduser("~/.zshrc"),
    ]
    key_names = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_API_KEY"}
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as profile:
                for raw_line in profile:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    if line.startswith("export "):
                        line = line[len("export ") :].strip()
                    key, value = line.split("=", 1)
                    key = key.strip()
                    if key not in key_names or os.getenv(key):
                        continue
                    try:
                        parsed = shlex.split(value, posix=True)
                        value = parsed[0] if parsed else ""
                    except ValueError:
                        value = value.strip().strip("'\"")
                    if value:
                        os.environ[key] = value
        except OSError:
            continue


def save_db_settings(form, path=ENV_PATH):
    settings = {
        "PGHOST": form.get("pghost", ["localhost"])[0].strip(),
        "PGPORT": form.get("pgport", ["5432"])[0].strip(),
        "PGDATABASE": form.get("pgdatabase", [""])[0].strip(),
        "PGUSER": form.get("pguser", [""])[0].strip(),
        "PGPASSWORD": form.get("pgpassword", [""])[0],
        "PGSSLMODE": form.get("pgsslmode", ["prefer"])[0].strip() or "prefer",
        "PGCONNECT_TIMEOUT": form.get("pgconnect_timeout", ["5"])[0].strip() or "5",
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
        "OPENAI_MODEL": os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", os.getenv("CLAUDE_API_KEY", "")),
        "ANTHROPIC_MODEL": os.getenv("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL),
        "CURSOR_MODEL": os.getenv("CURSOR_MODEL", DEFAULT_CURSOR_MODEL),
        "AI_PROVIDER": os.getenv("AI_PROVIDER", "openai"),
        "CMDB_REST_BASE_URL": os.getenv(
            "CMDB_REST_BASE_URL",
            f"http://{form.get('pghost', ['localhost'])[0].strip()}:{DEFAULT_CMDB_REST_PORT}",
        ),
        "CMDB_REST_USERNAME": os.getenv("CMDB_REST_USERNAME", ""),
        "CMDB_REST_PASSWORD": os.getenv("CMDB_REST_PASSWORD", ""),
        "CMDB_REST_NAMESPACE": os.getenv("CMDB_REST_NAMESPACE", DEFAULT_CMDB_NAMESPACE),
        "CMDB_REST_CLASS": os.getenv("CMDB_REST_CLASS", DEFAULT_CMDB_CLASS),
        "CMDB_REST_DELETE_OPTION": os.getenv("CMDB_REST_DELETE_OPTION", DEFAULT_CMDB_DELETE_OPTION),
        "PORT": os.getenv("PORT", "8000"),
    }
    write_env_settings(settings, path)


def save_ai_settings(form, path=ENV_PATH):
    new_openai_key = form.get("openai_api_key", [""])[0]
    if not new_openai_key:
        new_openai_key = os.getenv("OPENAI_API_KEY", "")
    new_anthropic_key = form.get("anthropic_api_key", [""])[0]
    if not new_anthropic_key:
        new_anthropic_key = os.getenv("ANTHROPIC_API_KEY", os.getenv("CLAUDE_API_KEY", ""))
    provider = form.get("ai_provider", [os.getenv("AI_PROVIDER", "openai")])[0]
    if provider not in AI_PROVIDERS:
        provider = "openai"
    settings = {
        "PGHOST": os.getenv("PGHOST", "localhost"),
        "PGPORT": os.getenv("PGPORT", "5432"),
        "PGDATABASE": os.getenv("PGDATABASE", ""),
        "PGUSER": os.getenv("PGUSER", ""),
        "PGPASSWORD": os.getenv("PGPASSWORD", ""),
        "PGSSLMODE": os.getenv("PGSSLMODE", "prefer"),
        "PGCONNECT_TIMEOUT": os.getenv("PGCONNECT_TIMEOUT", "5"),
        "OPENAI_API_KEY": new_openai_key,
        "OPENAI_MODEL": form.get("openai_model", [os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)])[0].strip()
        or DEFAULT_OPENAI_MODEL,
        "ANTHROPIC_API_KEY": new_anthropic_key,
        "ANTHROPIC_MODEL": form.get(
            "anthropic_model", [os.getenv("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)]
        )[0].strip()
        or DEFAULT_ANTHROPIC_MODEL,
        "CURSOR_MODEL": form.get("cursor_model", [os.getenv("CURSOR_MODEL", DEFAULT_CURSOR_MODEL)])[0].strip()
        or DEFAULT_CURSOR_MODEL,
        "AI_PROVIDER": provider,
        "CMDB_REST_BASE_URL": os.getenv("CMDB_REST_BASE_URL", default_cmdb_rest_base_url()),
        "CMDB_REST_USERNAME": os.getenv("CMDB_REST_USERNAME", os.getenv("PGUSER", "")),
        "CMDB_REST_PASSWORD": os.getenv("CMDB_REST_PASSWORD", os.getenv("PGPASSWORD", "")),
        "CMDB_REST_NAMESPACE": os.getenv("CMDB_REST_NAMESPACE", DEFAULT_CMDB_NAMESPACE),
        "CMDB_REST_CLASS": os.getenv("CMDB_REST_CLASS", DEFAULT_CMDB_CLASS),
        "CMDB_REST_DELETE_OPTION": os.getenv("CMDB_REST_DELETE_OPTION", DEFAULT_CMDB_DELETE_OPTION),
        "PORT": os.getenv("PORT", "8000"),
    }
    write_env_settings(settings, path)


def default_cmdb_rest_base_url():
    host = os.getenv("PGHOST", "localhost")
    return f"http://{host}:{DEFAULT_CMDB_REST_PORT}"


def normalize_cmdb_delete_option(value):
    option = str(value or "").strip()
    if option in {"", "0", "1"}:
        return DEFAULT_CMDB_DELETE_OPTION
    if option.lower() in {"purge", "delete"}:
        return "PURGE"
    if option.lower() in {"mark_as_deleted", "mark-as-deleted", "markasdeleted"}:
        return "MARK_AS_DELETED"
    return option


def save_rest_settings(form, path=ENV_PATH):
    new_password = form.get("cmdb_rest_password", [""])[0]
    if not new_password:
        new_password = os.getenv("CMDB_REST_PASSWORD", os.getenv("PGPASSWORD", ""))
    settings = current_env_settings()
    settings.update(
        {
            "CMDB_REST_BASE_URL": form.get(
                "cmdb_rest_base_url", [os.getenv("CMDB_REST_BASE_URL", default_cmdb_rest_base_url())]
            )[0].strip().rstrip("/"),
            "CMDB_REST_USERNAME": form.get(
                "cmdb_rest_username", [os.getenv("CMDB_REST_USERNAME", os.getenv("PGUSER", ""))]
            )[0].strip(),
            "CMDB_REST_PASSWORD": new_password,
            "CMDB_REST_NAMESPACE": form.get(
                "cmdb_rest_namespace", [os.getenv("CMDB_REST_NAMESPACE", DEFAULT_CMDB_NAMESPACE)]
            )[0].strip()
            or DEFAULT_CMDB_NAMESPACE,
            "CMDB_REST_CLASS": form.get("cmdb_rest_class", [os.getenv("CMDB_REST_CLASS", DEFAULT_CMDB_CLASS)])[
                0
            ].strip()
            or DEFAULT_CMDB_CLASS,
            "CMDB_REST_DELETE_OPTION": normalize_cmdb_delete_option(
                form.get(
                    "cmdb_rest_delete_option", [os.getenv("CMDB_REST_DELETE_OPTION", DEFAULT_CMDB_DELETE_OPTION)]
                )[0]
            ),
        }
    )
    write_env_settings(settings, path)


def current_env_settings():
    return {
        "PGHOST": os.getenv("PGHOST", "localhost"),
        "PGPORT": os.getenv("PGPORT", "5432"),
        "PGDATABASE": os.getenv("PGDATABASE", ""),
        "PGUSER": os.getenv("PGUSER", ""),
        "PGPASSWORD": os.getenv("PGPASSWORD", ""),
        "PGSSLMODE": os.getenv("PGSSLMODE", "prefer"),
        "PGCONNECT_TIMEOUT": os.getenv("PGCONNECT_TIMEOUT", "5"),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
        "OPENAI_MODEL": os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", os.getenv("CLAUDE_API_KEY", "")),
        "ANTHROPIC_MODEL": os.getenv("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL),
        "CURSOR_MODEL": os.getenv("CURSOR_MODEL", DEFAULT_CURSOR_MODEL),
        "AI_PROVIDER": os.getenv("AI_PROVIDER", "openai"),
        "CMDB_REST_BASE_URL": os.getenv("CMDB_REST_BASE_URL", default_cmdb_rest_base_url()),
        "CMDB_REST_USERNAME": os.getenv("CMDB_REST_USERNAME", os.getenv("PGUSER", "")),
        "CMDB_REST_PASSWORD": os.getenv("CMDB_REST_PASSWORD", os.getenv("PGPASSWORD", "")),
        "CMDB_REST_NAMESPACE": os.getenv("CMDB_REST_NAMESPACE", DEFAULT_CMDB_NAMESPACE),
        "CMDB_REST_CLASS": os.getenv("CMDB_REST_CLASS", DEFAULT_CMDB_CLASS),
        "CMDB_REST_DELETE_OPTION": normalize_cmdb_delete_option(
            os.getenv("CMDB_REST_DELETE_OPTION", DEFAULT_CMDB_DELETE_OPTION)
        ),
        "PORT": os.getenv("PORT", "8000"),
    }


def write_env_settings(settings, path=ENV_PATH):
    lines = [
        "# Saved by the local CMDB portal.",
        f"PGHOST={settings['PGHOST']}",
        f"PGPORT={settings['PGPORT']}",
        f"PGDATABASE={settings['PGDATABASE']}",
        f"PGUSER={settings['PGUSER']}",
        f"PGPASSWORD={settings['PGPASSWORD']}",
        f"PGSSLMODE={settings['PGSSLMODE']}",
        f"PGCONNECT_TIMEOUT={settings['PGCONNECT_TIMEOUT']}",
        f"OPENAI_API_KEY={settings.get('OPENAI_API_KEY', '')}",
        f"OPENAI_MODEL={settings.get('OPENAI_MODEL', DEFAULT_OPENAI_MODEL)}",
        f"ANTHROPIC_API_KEY={settings.get('ANTHROPIC_API_KEY', '')}",
        f"ANTHROPIC_MODEL={settings.get('ANTHROPIC_MODEL', DEFAULT_ANTHROPIC_MODEL)}",
        f"CURSOR_MODEL={settings.get('CURSOR_MODEL', DEFAULT_CURSOR_MODEL)}",
        f"AI_PROVIDER={settings.get('AI_PROVIDER', 'openai')}",
        f"CMDB_REST_BASE_URL={settings.get('CMDB_REST_BASE_URL', default_cmdb_rest_base_url())}",
        f"CMDB_REST_USERNAME={settings.get('CMDB_REST_USERNAME', os.getenv('PGUSER', ''))}",
        f"CMDB_REST_PASSWORD={settings.get('CMDB_REST_PASSWORD', os.getenv('PGPASSWORD', ''))}",
        f"CMDB_REST_NAMESPACE={settings.get('CMDB_REST_NAMESPACE', DEFAULT_CMDB_NAMESPACE)}",
        f"CMDB_REST_CLASS={settings.get('CMDB_REST_CLASS', DEFAULT_CMDB_CLASS)}",
        f"CMDB_REST_DELETE_OPTION={normalize_cmdb_delete_option(settings.get('CMDB_REST_DELETE_OPTION', DEFAULT_CMDB_DELETE_OPTION))}",
        f"PORT={settings['PORT']}",
        "",
    ]
    with open(path, "w", encoding="utf-8") as env_file:
        env_file.write("\n".join(lines))
    for key, value in settings.items():
        os.environ[key] = value


def import_postgres_driver():
    try:
        import psycopg

        return "psycopg", psycopg
    except ModuleNotFoundError:
        pass

    try:
        import psycopg2
        import psycopg2.extras

        return "psycopg2", psycopg2
    except ModuleNotFoundError:
        return None, None


def db_config():
    return {
        "host": os.getenv("PGHOST", "localhost"),
        "port": int(os.getenv("PGPORT", "5432")),
        "dbname": os.getenv("PGDATABASE", ""),
        "user": os.getenv("PGUSER", ""),
        "password": os.getenv("PGPASSWORD", ""),
        "sslmode": os.getenv("PGSSLMODE", "prefer"),
        "connect_timeout": int(os.getenv("PGCONNECT_TIMEOUT", "5")),
    }


def checked_db_config():
    config = db_config()
    missing = [key for key in ("dbname", "user", "password") if not config[key]]
    if missing:
        raise RuntimeError(
            "Missing database settings: "
            + ", ".join(missing)
            + ". Save database connection details first."
        )
    return config


PLACEHOLDER_RE = re.compile(r"(?<!%)%(?:\([A-Za-z_][A-Za-z0-9_]*\))?[sbt]")


def sql_uses_placeholders(sql):
    return bool(PLACEHOLDER_RE.search(sql))


def execute_sql(sql, params=None):
    driver_name, driver = import_postgres_driver()
    if driver is None:
        raise RuntimeError(
            "No Postgres driver is installed. Run: python3 -m pip install -r requirements.txt"
        )

    config = checked_db_config()
    execute_params = params if params and sql_uses_placeholders(sql) else None
    if driver_name == "psycopg":
        with driver.connect(**config) as conn:
            with conn.cursor() as cur:
                if execute_params:
                    cur.execute(sql, execute_params)
                else:
                    cur.execute(sql)
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
    else:
        with driver.connect(**config) as conn:
            with conn.cursor() as cur:
                if execute_params:
                    cur.execute(sql, execute_params)
                else:
                    cur.execute(sql)
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
    return columns, rows


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
    header_cells = [xlsx_cell(1, index, column) for index, column in enumerate(columns)]
    sheet_rows.append(f'<row r="1">{"".join(header_cells)}</row>')
    for row_index, row in enumerate(rows, start=2):
        cells = [xlsx_cell(row_index, col_index, value) for col_index, value in enumerate(row)]
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    sheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    {''.join(sheet_rows)}
  </sheetData>
</worksheet>
"""
    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Results" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>
"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", content_types)
        workbook.writestr("_rels/.rels", root_rels)
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        workbook.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return output.getvalue()


def safe_filename(value, fallback="cmdb-query-results"):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip()).strip("-._")
    return cleaned[:80] or fallback


def cmdb_rest_config():
    return {
        "base_url": os.getenv("CMDB_REST_BASE_URL", default_cmdb_rest_base_url()).rstrip("/"),
        "username": os.getenv("CMDB_REST_USERNAME", os.getenv("PGUSER", "")),
        "password": os.getenv("CMDB_REST_PASSWORD", os.getenv("PGPASSWORD", "")),
        "namespace": os.getenv("CMDB_REST_NAMESPACE", DEFAULT_CMDB_NAMESPACE),
        "class_name": os.getenv("CMDB_REST_CLASS", DEFAULT_CMDB_CLASS),
        "delete_option": normalize_cmdb_delete_option(os.getenv("CMDB_REST_DELETE_OPTION", DEFAULT_CMDB_DELETE_OPTION)),
    }


def checked_cmdb_rest_config():
    config = cmdb_rest_config()
    missing = [key for key in ("base_url", "username", "password", "namespace", "class_name") if not config[key]]
    if missing:
        raise RuntimeError("Missing CMDB REST settings: " + ", ".join(missing))
    return config


def cmdb_rest_login(config):
    data = urlencode({"username": config["username"], "password": config["password"]}).encode("utf-8")
    req = urllib.request.Request(
        f'{config["base_url"]}/api/jwt/login',
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.read().decode("utf-8").strip().strip('"')
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"CMDB REST login failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"CMDB REST login failed: {exc.reason}") from exc


def cmdb_auth_headers(token):
    return {
        "Authorization": f"AR-JWT {token}",
        "Accept": "application/json",
    }


def cmdb_json_headers(token):
    headers = cmdb_auth_headers(token)
    headers["Content-Type"] = "application/json"
    return headers


def cmdb_instance_path(config, datasetid, instanceid=None):
    parts = [
        config["base_url"],
        "api",
        "cmdb",
        "v1.0",
        "instances",
        quote(str(datasetid), safe=""),
        quote(config["namespace"], safe=""),
        quote(config["class_name"], safe=""),
    ]
    if instanceid:
        parts.append(quote(str(instanceid), safe=""))
    return "/".join(part.strip("/") for part in parts)


def cmdb_lookup_url(config, datasetid, instanceid):
    qualification = f"'InstanceId'=\"{str(instanceid).replace(chr(34), chr(92) + chr(34))}\""
    query = urlencode(
        {
            "qualification": qualification,
            "limit": "2",
            "offset": "0",
            "attributes": "InstanceId,DatasetId,ClassId,Name,MarkAsDeleted",
        }
    )
    return f"{cmdb_instance_path(config, datasetid)}?{query}"


def cmdb_parse_instances(payload):
    if isinstance(payload, dict):
        instances = payload.get("instances")
        if isinstance(instances, list):
            return instances
        if "instance_id" in payload or "attributes" in payload:
            return [payload]
    if isinstance(payload, list):
        return payload
    return []


def cmdb_lookup_instance(config, token, datasetid, instanceid):
    url = cmdb_lookup_url(config, datasetid, instanceid)
    req = urllib.request.Request(url, headers=cmdb_auth_headers(token), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Lookup failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Lookup failed: {exc.reason}") from exc
    return url, cmdb_parse_instances(payload)


def cmdb_soft_mark_instance_deleted(config, token, datasetid, instanceid):
    url = cmdb_instance_path(config, datasetid, instanceid)
    body = {
        "instance_id": str(instanceid),
        "dataset_id": str(datasetid),
        "class_name_key": {
            "namespace": config["namespace"],
            "name": config["class_name"],
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
        raise RuntimeError(f"Soft mark-as-deleted failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Soft mark-as-deleted failed: {exc.reason}") from exc


def cmdb_delete_instance(config, token, datasetid, instanceid):
    query = urlencode({"delete_option": config["delete_option"]})
    url = f"{cmdb_instance_path(config, datasetid, instanceid)}?{query}"
    req = urllib.request.Request(url, headers=cmdb_auth_headers(token), method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            detail = response.read().decode("utf-8", errors="replace")
            return response.status, url, detail
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Delete failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Delete failed: {exc.reason}") from exc


def rows_as_dicts(columns, rows):
    return [dict(zip(columns, row)) for row in rows]


def apply_addm_duplicate_resolution(form):
    if form.get("confirm_cmdb_rest_apply", [""])[0] != "1":
        raise RuntimeError("Confirm REST apply before updating duplicate CIs.")
    rest_action = form.get("cmdb_rest_duplicate_action", ["soft_mark_deleted"])[0]
    if rest_action not in {"soft_mark_deleted", "delete_instance"}:
        rest_action = "soft_mark_deleted"
    preview_sql = build_addm_duplicate_resolution_sql(form)
    columns, rows = execute_sql(preview_sql)
    candidates = [
        row
        for row in rows_as_dicts(columns, rows)
        if str(row.get("resolution_status", "")) == "Selected For Deletion"
        and str(row.get("markasdeleted", "0")) != "1"
    ]
    if not candidates:
        return preview_sql, (
            ["status", "detail"],
            [("No selected active duplicate rows were found for the current rule.", "No REST calls were made.")],
        )

    config = checked_cmdb_rest_config()
    token = cmdb_rest_login(config)
    result_rows = []
    for row in candidates:
        datasetid = row.get("datasetid")
        instanceid = row.get("instanceid")
        try:
            lookup_url, matches = cmdb_lookup_instance(config, token, datasetid, instanceid)
            if len(matches) != 1:
                result_rows.append(
                    (
                        datasetid,
                        instanceid,
                        row.get("serialnumber"),
                        row.get("addmintegrationid"),
                        len(matches),
                        "Skipped",
                        lookup_url,
                        "Expected exactly one CMDB REST lookup match.",
                    )
                )
                continue
            if rest_action == "delete_instance":
                status_code, update_url, detail = cmdb_delete_instance(config, token, datasetid, instanceid)
                action_status = f"Deleted ({status_code})"
            else:
                status_code, update_url, detail = cmdb_soft_mark_instance_deleted(
                    config, token, datasetid, instanceid
                )
                action_status = f"MarkAsDeleted set to 1 ({status_code})"
            result_rows.append(
                (
                    datasetid,
                    instanceid,
                    row.get("serialnumber"),
                    row.get("addmintegrationid"),
                    len(matches),
                    action_status,
                    update_url,
                    detail[:500],
                )
            )
        except Exception as exc:
            result_rows.append(
                (
                    datasetid,
                    instanceid,
                    row.get("serialnumber"),
                    row.get("addmintegrationid"),
                    "",
                    "Failed",
                    "",
                    str(exc),
                )
            )
    return preview_sql, (
        [
            "datasetid",
            "instanceid",
            "serialnumber",
            "addmintegrationid",
            "lookup_matches",
            "status",
            "rest_url",
            "detail",
        ],
        result_rows,
    )


def dataset_options():
    _, rows = execute_sql(
        """
SELECT DISTINCT
    coredatasetid
FROM bmc_core_config_bmc_dataset
WHERE coredatasetid IS NOT NULL
  AND datasettype = 0
ORDER BY coredatasetid;
"""
    )
    return [str(row[0]) for row in rows if row and row[0]]


def connection_form_values():
    config = db_config()
    return {
        "pghost": config["host"],
        "pgport": config["port"],
        "pgdatabase": config["dbname"],
        "pguser": config["user"],
        "pgpassword": config["password"],
        "pgsslmode": config["sslmode"],
        "pgconnect_timeout": config["connect_timeout"],
    }


def ai_form_values():
    openai_key = os.getenv("OPENAI_API_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", os.getenv("CLAUDE_API_KEY", ""))
    return {
        "ai_provider": os.getenv("AI_PROVIDER", "openai"),
        "openai_api_key": openai_key,
        "openai_key_saved": bool(openai_key),
        "openai_model": os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        "anthropic_api_key": anthropic_key,
        "anthropic_key_saved": bool(anthropic_key),
        "anthropic_model": os.getenv("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL),
        "cursor_model": os.getenv("CURSOR_MODEL", DEFAULT_CURSOR_MODEL),
    }


def rest_form_values():
    password = os.getenv("CMDB_REST_PASSWORD", os.getenv("PGPASSWORD", ""))
    return {
        "cmdb_rest_base_url": os.getenv("CMDB_REST_BASE_URL", default_cmdb_rest_base_url()),
        "cmdb_rest_username": os.getenv("CMDB_REST_USERNAME", os.getenv("PGUSER", "")),
        "cmdb_rest_password_saved": bool(password),
        "cmdb_rest_namespace": os.getenv("CMDB_REST_NAMESPACE", DEFAULT_CMDB_NAMESPACE),
        "cmdb_rest_class": os.getenv("CMDB_REST_CLASS", DEFAULT_CMDB_CLASS),
        "cmdb_rest_delete_option": normalize_cmdb_delete_option(
            os.getenv("CMDB_REST_DELETE_OPTION", DEFAULT_CMDB_DELETE_OPTION)
        ),
    }


def load_reports(path=REPORTS_PATH):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as reports_file:
        data = json.load(reports_file)
    if not isinstance(data, list):
        return []
    return data


def save_reports(reports, path=REPORTS_PATH):
    with open(path, "w", encoding="utf-8") as reports_file:
        json.dump(reports, reports_file, indent=2, sort_keys=True)
        reports_file.write("\n")


def query_catalog():
    catalog = {}
    for key, meta in QUERIES.items():
        catalog[f"{BUILTIN_PREFIX}{key}"] = {
            "id": key,
            "source": "builtin",
            "name": meta["name"],
            "description": meta["description"],
            "sql": meta["sql"],
        }
    for report in load_reports():
        report_id = report.get("id", "")
        if not report_id:
            continue
        catalog[f"{REPORT_PREFIX}{report_id}"] = {
            "id": report_id,
            "source": "report",
            "name": report.get("name", "Untitled Report"),
            "description": report.get("description", ""),
            "sql": report.get("sql", ""),
        }
    return catalog


def selected_query_key(raw_key):
    catalog = query_catalog()
    if raw_key in catalog:
        return raw_key
    old_builtin_key = f"{BUILTIN_PREFIX}{raw_key}"
    if old_builtin_key in catalog:
        return old_builtin_key
    return f"{BUILTIN_PREFIX}ci_by_class"


def save_report(form):
    report_id = form.get("report_id", [""])[0].strip()
    if form.get("save_as_new", [""])[0] == "1":
        report_id = ""
    name = form.get("report_name", [""])[0].strip() or "Untitled Report"
    description = form.get("report_description", [""])[0].strip()
    sql = form.get("report_sql", [""])[0].strip()
    if not sql:
        raise RuntimeError("Report SQL cannot be empty.")
    validate_report_sql(sql)

    reports = load_reports()
    now = int(time.time())
    if not report_id:
        report_id = uuid.uuid4().hex[:12]
        reports.append(
            {
                "id": report_id,
                "name": name,
                "description": description,
                "sql": sql,
                "created_at": now,
                "updated_at": now,
            }
        )
    else:
        for report in reports:
            if report.get("id") == report_id:
                report["name"] = name
                report["description"] = description
                report["sql"] = sql
                report["updated_at"] = now
                break
        else:
            reports.append(
                {
                    "id": report_id,
                    "name": name,
                    "description": description,
                    "sql": sql,
                    "created_at": now,
                    "updated_at": now,
                }
            )
    save_reports(reports)
    return report_id


def save_generated_report(name, description, sql):
    validate_report_sql(sql)
    reports = load_reports()
    now = int(time.time())
    report_id = uuid.uuid4().hex[:12]
    reports.append(
        {
            "id": report_id,
            "name": name,
            "description": description,
            "sql": sql,
            "created_at": now,
            "updated_at": now,
        }
    )
    save_reports(reports)
    return report_id


def generate_report_with_ai(form):
    save_ai_settings(form)
    provider = os.getenv("AI_PROVIDER", "openai")
    request_text = form.get("ai_query_request", [""])[0].strip()
    if not request_text:
        raise RuntimeError("Describe the query you want to create.")

    if provider == "openai":
        sql = generate_sql_with_openai(request_text)
    elif provider == "anthropic":
        sql = generate_sql_with_anthropic(request_text)
    elif provider == "cursor":
        raise RuntimeError(
            "Cursor does not expose a separate general-purpose API for this portal. "
            "Use ChatGPT / OpenAI or Claude / Anthropic here. Cursor can still use those keys inside Cursor itself."
        )
    else:
        raise RuntimeError("Unknown AI provider.")

    sql = strip_markdown_fence(sql.strip())
    validate_report_sql(sql)
    name = "AI Generated Query"
    if len(request_text) <= 60:
        name = request_text
    else:
        name = request_text[:57].rstrip() + "..."
    description = f"Generated by {AI_PROVIDERS.get(provider, provider)} from request:\n" + request_text
    return save_generated_report(name, description, sql)


def sql_generation_instructions():
    return """
You generate safe read-only PostgreSQL SQL for a BMC CMDB reporting portal.
Return only one SQL query. Do not use Markdown fences. Do not explain.
Use lowercase unquoted table and column names unless the user explicitly gives exact identifiers.
Never generate INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT, or REVOKE.
Prefer these portal parameters when useful:
- %(datasetids)s for multiple dataset IDs with = ANY(%(datasetids)s)
- %(datasetid)s for the first selected dataset only
- %(classids)s for class lists with = ANY(%(classids)s)
- %(hours)s for a recent-hours numeric parameter
- %(limit)s for row limits
Known CMDB tables include bmc_core_bmc_baseelement, bmc_core_bmc_baserelationship,
ast_baseelement, ast_assetpeople, bmc_core_bmc_person, ctm_people,
ctm_supportgroupassocpeopleloo, ctm_all_supportgroups, ast_attributes,
ast_contract_ciassociations, and bmc_core_config_bmc_dataset.
"""


def generate_sql_with_openai(request_text):
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OpenAI API key is required for ChatGPT / OpenAI.")

    model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    input_text = f"What query would you like to create?\n\n{request_text}"
    payload = {
        "model": model,
        "instructions": sql_generation_instructions(),
        "input": input_text,
        "store": False,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI request failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc

    return extract_response_text(data)


def generate_sql_with_anthropic(request_text):
    api_key = os.getenv("ANTHROPIC_API_KEY", os.getenv("CLAUDE_API_KEY", ""))
    if not api_key:
        raise RuntimeError("Anthropic API key is required for Claude / Anthropic.")

    model = os.getenv("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)
    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": sql_generation_instructions(),
        "messages": [
            {
                "role": "user",
                "content": f"What query would you like to create?\n\n{request_text}",
            }
        ],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Anthropic request failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Anthropic request failed: {exc.reason}") from exc

    return extract_anthropic_text(data)


def extract_response_text(data):
    if data.get("output_text"):
        return data["output_text"]
    chunks = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                chunks.append(text)
    return "\n".join(chunks)


def extract_anthropic_text(data):
    chunks = []
    for content in data.get("content", []):
        if content.get("type") == "text" and content.get("text"):
            chunks.append(content["text"])
    return "\n".join(chunks)


def strip_markdown_fence(text):
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def delete_report(form):
    report_id = form.get("report_id", [""])[0].strip()
    if not report_id:
        return
    reports = [report for report in load_reports() if report.get("id") != report_id]
    save_reports(reports)


def report_package_payload():
    return {
        "package_type": "cmdb_portal_reports",
        "version": 1,
        "exported_at": int(time.time()),
        "reports": load_reports(),
    }


def import_report_package(form):
    raw_package = form.get("package_json", [""])[0].strip()
    if not raw_package:
        raise RuntimeError("Paste a report package JSON document before importing.")

    package = json.loads(raw_package)
    if package.get("package_type") != "cmdb_portal_reports":
        raise RuntimeError("This is not a CMDB portal report package.")

    imported_reports = package.get("reports", [])
    if not isinstance(imported_reports, list):
        raise RuntimeError("Package reports must be a list.")

    existing = {report.get("id"): report for report in load_reports() if report.get("id")}
    now = int(time.time())
    imported_count = 0
    for report in imported_reports:
        name = str(report.get("name", "")).strip() or "Untitled Report"
        description = str(report.get("description", "")).strip()
        sql = str(report.get("sql", "")).strip()
        if not sql:
            continue
        validate_report_sql(sql)

        report_id = str(report.get("id", "")).strip() or uuid.uuid4().hex[:12]
        if form.get("import_mode", ["merge"])[0] == "copy" or report_id in existing:
            report_id = uuid.uuid4().hex[:12]

        existing[report_id] = {
            "id": report_id,
            "name": name,
            "description": description,
            "sql": sql,
            "created_at": int(report.get("created_at") or now),
            "updated_at": now,
        }
        imported_count += 1

    save_reports(list(existing.values()))
    return imported_count


def validate_report_sql(sql):
    normalized = sql.strip().lower()
    if not normalized.startswith(("select", "with")):
        raise RuntimeError("Reports must start with SELECT or WITH.")
    forbidden = (
        "alter ",
        "create ",
        "delete ",
        "drop ",
        "grant ",
        "insert ",
        "revoke ",
        "truncate ",
        "update ",
    )
    padded = f" {normalized} "
    for keyword in forbidden:
        if keyword in padded:
            raise RuntimeError("Reports can only run read-only SQL.")


def validate_identifier(value, label):
    cleaned = value.strip().lower()
    if not cleaned:
        raise RuntimeError(f"{label} is required.")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_")
    if any(char not in allowed for char in cleaned):
        raise RuntimeError(f"{label} must use lowercase letters, numbers, and underscores only.")
    return cleaned


def int_form_value(form, field, default, minimum=1, maximum=100000):
    raw_value = form.get(field, [str(default)])[0] or str(default)
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{field} must be a number.") from exc
    return max(minimum, min(maximum, value))


def duplicate_group_columns(form):
    group_mode = form.get("addm_group_mode", ["dataset_serial"])[0]
    if group_mode == "dataset_addm":
        return ["datasetid", "addmintegrationid"]
    if group_mode == "dataset_serial_addm":
        return ["datasetid", "serialnumber", "addmintegrationid"]
    return ["datasetid", "serialnumber"]


def build_addm_duplicate_resolution_sql(form):
    table_name = validate_identifier(form.get("addm_table", ["bmc_core_bmc_baseelement"])[0], "Table name")
    relationship_table = validate_identifier(
        form.get("addm_relationship_table", ["bmc_core_bmc_baserelationship"])[0], "Relationship table"
    )
    id_column = validate_identifier(form.get("addm_id_column", ["instanceid"])[0], "Instance ID column")
    dataset_column = validate_identifier(form.get("addm_dataset_column", ["datasetid"])[0], "Dataset column")
    class_column = validate_identifier(form.get("addm_class_column", ["classid"])[0], "Class ID column")
    name_column = validate_identifier(form.get("addm_name_column", ["name"])[0], "Name column")
    serial_column = validate_identifier(form.get("addm_serial_column", ["serialnumber"])[0], "Serial number column")
    addm_column = validate_identifier(
        form.get("addm_integration_column", ["addmintegrationid"])[0], "ADDM integration column"
    )
    created_column = validate_identifier(form.get("addm_created_column", ["createdate"])[0], "Created date column")
    modified_column = validate_identifier(form.get("addm_modified_column", ["modifieddate"])[0], "Modified date column")
    lastscan_column = validate_identifier(form.get("addm_lastscan_column", ["lastscandate"])[0], "Last scan date column")
    deleted_column = validate_identifier(form.get("addm_deleted_column", ["markasdeleted"])[0], "Mark-as-deleted column")
    source_column = validate_identifier(form.get("addm_source_column", ["datasetid"])[0], "Discovery source column")
    limit = int_form_value(form, "addm_limit", 500)

    group_columns = duplicate_group_columns(form)
    partition_cols = ", ".join(group_columns)
    raw_blank_addm = f"(ci.{addm_column} IS NULL OR trim(ci.{addm_column}::text) = '')"
    ranked_blank_addm = "(addmintegrationid IS NULL OR trim(addmintegrationid::text) = '')"
    rule = form.get("addm_selection_rule", ["mark_newest_lastscan"])[0]

    if rule == "mark_oldest_lastscan":
        rank_order = "lastscandate ASC NULLS LAST, createdate ASC NULLS LAST, instanceid"
        selected_condition = "delete_rank = 1"
    elif rule == "mark_blank_addm":
        rank_order = f"CASE WHEN {ranked_blank_addm} THEN 0 ELSE 1 END, lastscandate DESC NULLS LAST, instanceid"
        selected_condition = "addm_integration_status = 'Blank ADDMIntegrationId'"
    elif rule == "mark_fewest_relationships":
        rank_order = "relationship_count ASC, lastscandate DESC NULLS LAST, instanceid"
        selected_condition = "delete_rank = 1"
    elif rule == "mark_all_except_oldest_created":
        rank_order = "createdate ASC NULLS LAST, lastscandate ASC NULLS LAST, instanceid"
        selected_condition = "delete_rank > 1"
    elif rule == "mark_all_except_most_relationships":
        rank_order = "relationship_count DESC, createdate ASC NULLS LAST, instanceid"
        selected_condition = "delete_rank > 1"
    else:
        rank_order = "lastscandate DESC NULLS LAST, createdate DESC NULLS LAST, instanceid"
        selected_condition = "delete_rank = 1"

    return f"""WITH relationship_counts AS (
    SELECT
        instanceid,
        count(*) AS relationship_count
    FROM (
        SELECT
            source_instanceid AS instanceid
        FROM {relationship_table}
        WHERE source_instanceid IS NOT NULL

        UNION ALL

        SELECT
            destination_instanceid AS instanceid
        FROM {relationship_table}
        WHERE destination_instanceid IS NOT NULL
    ) rel
    GROUP BY instanceid
),
base AS (
    SELECT
        ci.{dataset_column} AS datasetid,
        ci.{class_column} AS classid,
        ci.{id_column} AS instanceid,
        ci.{name_column} AS name,
        ci.{serial_column} AS serialnumber,
        ci.{addm_column} AS addmintegrationid,
        ci.{source_column} AS discovery_source,
        to_timestamp(ci.{created_column}) AS createdate,
        to_timestamp(ci.{modified_column}) AS modifieddate,
        to_timestamp(ci.{lastscan_column}) AS lastscandate,
        coalesce(ci.{deleted_column}, 0) AS markasdeleted,
        coalesce(rc.relationship_count, 0) AS relationship_count,
        CASE
            WHEN {raw_blank_addm} THEN 'Blank ADDMIntegrationId'
            ELSE 'Has ADDMIntegrationId'
        END AS addm_integration_status
    FROM {table_name} ci
    LEFT JOIN relationship_counts rc
        ON rc.instanceid = ci.{id_column}
),
ranked AS (
    SELECT
        *,
        count(*) OVER (
            PARTITION BY {partition_cols}
        ) AS group_total,
        sum(CASE WHEN markasdeleted <> 1 THEN 1 ELSE 0 END) OVER (
            PARTITION BY {partition_cols}
        ) AS active_group_total,
        row_number() OVER (
            PARTITION BY {partition_cols}
            ORDER BY
                CASE WHEN markasdeleted = 1 THEN 1 ELSE 0 END,
                {rank_order}
        ) AS delete_rank
    FROM base
    WHERE {" AND ".join(f"{column} IS NOT NULL AND trim({column}::text) <> ''" for column in group_columns)}
),
duplicate_groups AS (
    SELECT *
    FROM ranked
    WHERE group_total > 1
)
SELECT
    datasetid,
    classid,
    instanceid,
    name,
    serialnumber,
    addmintegrationid,
    discovery_source,
    createdate,
    modifieddate,
    lastscandate,
    markasdeleted,
    relationship_count,
    group_total,
    active_group_total,
    delete_rank,
    CASE
        WHEN markasdeleted = 1 THEN 'Already Marked Deleted'
        WHEN active_group_total <= 1 THEN 'Keep / Only Active CI'
        WHEN {selected_condition} THEN 'Selected For Deletion'
        ELSE 'Keep / Review'
    END AS resolution_status
FROM duplicate_groups
ORDER BY {partition_cols}, resolution_status DESC, delete_rank
LIMIT {limit};"""


def query_params(form):
    selected_datasetids = [item.strip() for item in form.get("datasetids", []) if item.strip()]
    if not selected_datasetids:
        legacy_datasetid = form.get("datasetid", ["BMC.ASSET"])[0]
        selected_datasetids = [item.strip() for item in legacy_datasetid.splitlines() if item.strip()]
    if not selected_datasetids:
        selected_datasetids = ["BMC.ASSET"]

    classids = form.get("classids", [""])[0]
    selected_classes = [item.strip() for item in classids.splitlines() if item.strip()]
    if not selected_classes:
        selected_classes = DEFAULT_CLASSES

    return {
        "datasetid": selected_datasetids[0],
        "datasetids": selected_datasetids,
        "hours": int(form.get("hours", ["24"])[0] or "24"),
        "limit": int(form.get("limit", ["250"])[0] or "250"),
        "classids": selected_classes,
    }


def run_query(query_key, params):
    meta = query_catalog()[query_key]
    sql = meta["sql"]
    if meta["source"] == "report":
        validate_report_sql(sql)

    return execute_sql(sql, params)


def esc(value):
    if value is None:
        return ""
    return html.escape(str(value))


def render_layout(body):
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{APP_TITLE}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --line: #d9dee7;
      --text: #1f2933;
      --muted: #5b6776;
      --accent: #146c94;
      --accent-dark: #0f536f;
      --danger: #a23b3b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      background: #14213d;
      color: white;
      padding: 18px 24px;
    }}
    header h1 {{
      display: flex;
      align-items: center;
      gap: 12px;
      margin: 0;
      font-size: 22px;
      font-weight: 650;
      letter-spacing: 0;
    }}
    .header-logo {{
      display: block;
      height: 28px;
      width: auto;
      flex: 0 0 auto;
    }}
    main {{
      width: 100%;
      max-width: none;
      margin: 0;
      padding: 18px 22px;
    }}
    .page-stack {{
      display: grid;
      gap: 18px;
    }}
    .cockpit-deck {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 14px;
      align-items: start;
    }}
    .query-panel {{
      grid-column: span 2;
    }}
    .results-panel {{
      width: 100%;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      min-width: 0;
    }}
    .panel h2 {{
      margin: 0;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      font-size: 15px;
    }}
    .panel-body {{ padding: 16px; }}
    .stack {{
      display: grid;
      gap: 18px;
    }}
    label {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      margin: 14px 0 6px;
      text-transform: uppercase;
    }}
    select, input, textarea {{
      width: 100%;
      border: 1px solid #c9d1dc;
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
      background: white;
      color: var(--text);
    }}
    input[type="checkbox"] {{
      width: auto;
      margin-right: 8px;
    }}
    .saved-query-select {{
      min-height: 54px;
      border: 0;
      background: var(--accent);
      color: white;
      font-size: 16px;
      font-weight: 800;
      cursor: pointer;
      box-shadow: inset 0 -1px 0 rgba(0, 0, 0, 0.18);
    }}
    .saved-query-select:hover {{
      background: var(--accent-dark);
    }}
    .saved-query-label {{
      color: var(--text);
      font-family: Tahoma, Geneva, sans-serif;
      font-size: 18px;
      font-weight: 800;
      text-transform: none;
    }}
    textarea {{
      min-height: 190px;
      resize: vertical;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
    }}
    button {{
      width: 100%;
      margin-top: 16px;
      border: 0;
      border-radius: 6px;
      padding: 10px 12px;
      color: white;
      background: var(--accent);
      font-weight: 700;
      cursor: pointer;
    }}
    button:hover {{ background: var(--accent-dark); }}
    .run-query-button {{
      min-height: 72px;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 12px;
      font-size: 18px;
      letter-spacing: 0;
      background: #05070c;
      border: 1px solid #1e293b;
      box-shadow: 0 8px 18px rgba(5, 7, 12, 0.22);
    }}
    .run-query-button:hover {{
      background: #111827;
    }}
    .run-query-icon {{
      width: 92px;
      height: 38px;
      flex: 0 0 auto;
      object-fit: contain;
      border-radius: 4px;
    }}
    button.secondary {{
      background: #4f5f6f;
    }}
    button.secondary:hover {{
      background: #3e4b58;
    }}
    button.danger {{
      background: #a23b3b;
    }}
    button.danger:hover {{
      background: #832f2f;
    }}
    button:disabled {{
      background: #9aa6b2;
      color: #eef2f7;
      cursor: not-allowed;
      box-shadow: none;
    }}
    button:disabled:hover {{
      background: #9aa6b2;
    }}
    .actions {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }}
    .query-fields {{
      display: grid;
      grid-template-columns: minmax(220px, 1fr) minmax(240px, 1.2fr);
      gap: 14px;
      align-items: start;
    }}
    .query-small-fields {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }}
    .query-wide {{
      grid-column: 1 / -1;
    }}
    .top-query-sql {{
      height: 210px;
      margin: 0;
    }}
    .hint {{
      color: var(--muted);
      font-size: 12px;
      margin: 8px 0 0;
    }}
    .status {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      color: var(--muted);
      margin-bottom: 12px;
    }}
    .pill {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 9px;
      background: #fbfcfd;
    }}
    .export-pill {{
      color: var(--accent);
      font-weight: 700;
      text-decoration: none;
    }}
    .export-pill:hover {{
      border-color: var(--accent);
      background: #eef8fb;
    }}
    .error {{
      border: 1px solid #e5b4b4;
      background: #fff7f7;
      color: var(--danger);
      border-radius: 8px;
      padding: 12px;
      white-space: pre-wrap;
      overflow: auto;
    }}
    .success {{
      border: 1px solid #a8d5b8;
      background: #f3fbf6;
      color: #23643b;
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 14px;
    }}
    .key-status {{
      border-radius: 6px;
      padding: 8px 10px;
      margin-top: 8px;
      font-size: 12px;
      font-weight: 700;
    }}
    .key-status.found {{
      background: #f3fbf6;
      color: #23643b;
      border: 1px solid #a8d5b8;
    }}
    .key-status.missing {{
      background: #fff7f7;
      color: var(--danger);
      border: 1px solid #e5b4b4;
    }}
    .provider-group.hidden,
    .key-status.hidden {{
      display: none;
    }}
    .sql {{
      background: #101820;
      color: #eef6ff;
      border-radius: 8px;
      padding: 13px;
      overflow: auto;
      font-size: 12px;
      line-height: 1.45;
      height: 260px;
      max-height: none;
      min-height: 120px;
      resize: vertical;
    }}
    details.sql-details {{
      border-bottom: 1px solid var(--line);
    }}
    details.sql-details summary {{
      cursor: pointer;
      font-weight: 700;
      padding: 14px 16px;
      user-select: none;
    }}
    details.sql-details[open] summary {{
      border-bottom: 1px solid var(--line);
    }}
    .report-sql {{
      min-height: 300px;
    }}
    .report-description {{
      min-height: 110px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 13px;
    }}
    .dataset-select {{
      min-height: 142px;
    }}
    .ai-request {{
      min-height: 150px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 13px;
    }}
    .table-wrap {{
      height: 720px;
      min-height: 240px;
      max-height: none;
      overflow-x: auto;
      overflow-y: auto;
      border-top: 1px solid var(--line);
      width: 100%;
      resize: vertical;
    }}
    table {{
      width: max-content;
      min-width: 100%;
      border-collapse: collapse;
      background: white;
      font-size: 13px;
    }}
    th, td {{
      border-bottom: 1px solid #e6ebf1;
      padding: 8px 10px;
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }}
    th {{
      position: sticky;
      top: 0;
      background: #eef2f7;
      z-index: 1;
      font-weight: 700;
      user-select: none;
    }}
    th.resizable {{
      position: sticky;
      padding-right: 18px;
    }}
    .col-resizer {{
      position: absolute;
      top: 0;
      right: 0;
      width: 8px;
      height: 100%;
      cursor: col-resize;
      touch-action: none;
    }}
    .col-resizer::after {{
      content: "";
      position: absolute;
      top: 25%;
      right: 3px;
      width: 1px;
      height: 50%;
      background: #aeb8c5;
    }}
    body.resizing-columns {{
      cursor: col-resize;
      user-select: none;
    }}
    td {{ color: #263442; }}
    td {{
      max-width: 420px;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .empty {{
      color: var(--muted);
      padding: 24px;
      text-align: center;
    }}
    @media (max-width: 860px) {{
      .query-panel {{ grid-column: auto; }}
      .query-fields {{ grid-template-columns: 1fr; }}
      .query-small-fields {{ grid-template-columns: 1fr; }}
      .query-wide {{ grid-column: auto; }}
      main {{ padding: 14px; }}
      th, td {{ white-space: normal; }}
    }}
  </style>
  <script>
    function setColumnWidth(table, index, width) {{
      const safeWidth = Math.max(60, width);
      for (const row of table.rows) {{
        const cell = row.cells[index];
        if (!cell) continue;
        cell.style.width = safeWidth + "px";
        cell.style.minWidth = safeWidth + "px";
        cell.style.maxWidth = safeWidth + "px";
      }}
    }}

    function installColumnResizers() {{
      for (const table of document.querySelectorAll("table.resizable-table")) {{
        const headers = table.querySelectorAll("thead th");
        headers.forEach((header, index) => {{
          if (header.querySelector(".col-resizer")) return;
          header.classList.add("resizable");
          const resizer = document.createElement("span");
          resizer.className = "col-resizer";
          resizer.title = "Drag to resize column";
          header.appendChild(resizer);

          resizer.addEventListener("mousedown", (event) => {{
            event.preventDefault();
            const startX = event.clientX;
            const startWidth = header.getBoundingClientRect().width;
            document.body.classList.add("resizing-columns");

            const onMove = (moveEvent) => {{
              setColumnWidth(table, index, startWidth + moveEvent.clientX - startX);
            }};
            const onUp = () => {{
              document.body.classList.remove("resizing-columns");
              document.removeEventListener("mousemove", onMove);
              document.removeEventListener("mouseup", onUp);
            }};

            document.addEventListener("mousemove", onMove);
            document.addEventListener("mouseup", onUp);
          }});
        }});
      }}
    }}

    function installAiProviderControls() {{
      const provider = document.querySelector("#ai_provider");
      if (!provider) return;
      const groups = document.querySelectorAll("[data-provider-group]");
      const statuses = document.querySelectorAll("[data-key-status]");
      const update = () => {{
        const selected = provider.value;
        groups.forEach((group) => {{
          group.classList.toggle("hidden", group.dataset.providerGroup !== selected);
        }});
        statuses.forEach((status) => {{
          status.classList.toggle("hidden", status.dataset.keyStatus !== selected);
        }});
      }};
      provider.addEventListener("change", update);
      update();
    }}

    window.addEventListener("DOMContentLoaded", () => {{
      installColumnResizers();
      installAiProviderControls();
    }});
  </script>
</head>
<body>
  <header><h1><img class="header-logo" src="/assets/2024-bmc-helix-reversed.png" alt="BMC Helix">{APP_TITLE}</h1></header>
  <main>{body}</main>
</body>
</html>"""


def render_connection_form(message=""):
    values = connection_form_values()
    message_html = f'<div class="success">{esc(message)}</div>' if message else ""
    return f"""
<section class="panel">
  <h2>Database Connection</h2>
  <div class="panel-body">
    {message_html}
    <form method="post" action="/connection">
      <label for="pghost">Host</label>
      <input id="pghost" name="pghost" value="{esc(values["pghost"])}">

      <label for="pgport">Port</label>
      <input id="pgport" name="pgport" type="number" value="{esc(values["pgport"])}">

      <label for="pgdatabase">Database</label>
      <input id="pgdatabase" name="pgdatabase" value="{esc(values["pgdatabase"])}">

      <label for="pguser">User</label>
      <input id="pguser" name="pguser" value="{esc(values["pguser"])}">

      <label for="pgpassword">Password</label>
      <input id="pgpassword" name="pgpassword" type="password" value="{esc(values["pgpassword"])}">

      <label for="pgsslmode">SSL Mode</label>
      <select id="pgsslmode" name="pgsslmode">
        {render_ssl_options(values["pgsslmode"])}
      </select>

      <label for="pgconnect_timeout">Timeout, Seconds</label>
      <input id="pgconnect_timeout" name="pgconnect_timeout" type="number" min="1" value="{esc(values["pgconnect_timeout"])}">

      <button type="submit">Save Connection</button>
      <p class="hint">Saved locally to {esc(os.path.abspath(ENV_PATH))}</p>
    </form>
  </div>
</section>
"""


def render_delete_option_select(selected):
    selected = normalize_cmdb_delete_option(selected)
    options = [
        ("PURGE", "PURGE - physically delete the instance"),
        ("MARK_AS_DELETED", "MARK_AS_DELETED - mark only"),
    ]
    option_html = [
        f'<option value="{esc(value)}" {"selected" if value == selected else ""}>{esc(label)}</option>'
        for value, label in options
    ]
    if selected and selected not in {value for value, _ in options}:
        option_html.append(f'<option value="{esc(selected)}" selected>Custom ({esc(selected)})</option>')
    return "\n".join(option_html)


def render_rest_settings_form(message=""):
    values = rest_form_values()
    password_placeholder = (
        "Saved password is set. Enter a new password to replace it."
        if values["cmdb_rest_password_saved"]
        else "AR password"
    )
    message_html = f'<div class="success">{esc(message)}</div>' if message else ""
    return f"""
<section class="panel">
  <h2>CMDB REST Connection</h2>
  <div class="panel-body">
    {message_html}
    <form method="post" action="/rest-settings">
      <label for="cmdb_rest_base_url">CMDB REST Base URL</label>
      <input id="cmdb_rest_base_url" name="cmdb_rest_base_url" value="{esc(values["cmdb_rest_base_url"])}">

      <label for="cmdb_rest_username">AR User</label>
      <input id="cmdb_rest_username" name="cmdb_rest_username" value="{esc(values["cmdb_rest_username"])}">

      <label for="cmdb_rest_password">AR Password</label>
      <input id="cmdb_rest_password" name="cmdb_rest_password" type="password" placeholder="{esc(password_placeholder)}">

      <label for="cmdb_rest_namespace">CMDB Namespace</label>
      <input id="cmdb_rest_namespace" name="cmdb_rest_namespace" value="{esc(values["cmdb_rest_namespace"])}">

      <label for="cmdb_rest_class">CMDB Class</label>
      <input id="cmdb_rest_class" name="cmdb_rest_class" value="{esc(values["cmdb_rest_class"])}">

      <label for="cmdb_rest_delete_option">Delete Option For Hard Delete</label>
      <select id="cmdb_rest_delete_option" name="cmdb_rest_delete_option">
        {render_delete_option_select(values["cmdb_rest_delete_option"])}
      </select>

      <button type="submit">Save CMDB REST Connection</button>
      <p class="hint">Saved locally to {esc(os.path.abspath(ENV_PATH))}. This is used only for CMDB REST login and duplicate apply.</p>
    </form>
  </div>
</section>
"""


def render_ssl_options(selected):
    options = ["prefer", "require", "disable", "allow", "verify-ca", "verify-full"]
    return "\n".join(
        f'<option value="{esc(option)}" {"selected" if option == selected else ""}>{esc(option)}</option>'
        for option in options
    )


def render_query_form(selected_key, params):
    catalog = query_catalog()
    meta = catalog[selected_key]
    options = "\n".join(
        f'<option value="{esc(key)}" {"selected" if key == selected_key else ""}>{esc(meta["name"])}</option>'
        for key, meta in catalog.items()
    )
    dataset_select, dataset_hint = render_dataset_select(params["datasetids"])
    classes = "\n".join(params["classids"])
    return f"""
<section class="panel query-panel">
  <h2>Query</h2>
  <div class="panel-body">
    <form method="get" action="/">
      <div class="query-fields">
        <div>
          <label class="saved-query-label" for="query">Saved Query List</label>
          <select class="saved-query-select" id="query" name="query">{options}</select>
          <p class="hint">{esc(meta["description"])}</p>
        </div>

        <div>
          <label for="datasetids">Datasets</label>
          {dataset_select}
          {dataset_hint}
        </div>

        <div class="query-small-fields">
          <div>
            <label for="hours">Recent Window, Hours</label>
            <input id="hours" name="hours" type="number" min="1" value="{esc(params["hours"])}">
          </div>

          <div>
            <label for="limit">Row Limit</label>
            <input id="limit" name="limit" type="number" min="1" max="100000" value="{esc(params["limit"])}">
          </div>
        </div>

        <div class="query-wide">
          <label for="classids">Class IDs</label>
          <textarea id="classids" name="classids">{esc(classes)}</textarea>
        </div>

        <div class="query-wide">
          <label>SQL Query</label>
          <pre class="sql top-query-sql">{esc(meta["sql"].strip())}</pre>
        </div>
      </div>
      <button class="run-query-button" type="submit">
        <img class="run-query-icon" src="/assets/blackholesurfer-logo.jpg" alt="">
        <span>Run Query</span>
      </button>
    </form>
  </div>
</section>
"""


def render_dataset_select(selected_datasetids):
    selected = set(selected_datasetids)
    hint = '<p class="hint">Hold Command or Shift to select multiple datasets.</p>'
    try:
        options = dataset_options()
    except Exception as exc:
        options = list(selected_datasetids or ["BMC.ASSET"])
        hint = f'<p class="hint">Dataset list unavailable: {esc(exc)}</p>'

    for datasetid in selected:
        if datasetid not in options:
            options.append(datasetid)

    if not options:
        options = ["BMC.ASSET"]

    option_html = "\n".join(
        f'<option value="{esc(datasetid)}" {"selected" if datasetid in selected else ""}>{esc(datasetid)}</option>'
        for datasetid in options
    )
    return (
        f'<select class="dataset-select" id="datasetids" name="datasetids" multiple size="7">{option_html}</select>',
        hint,
    )


def render_report_form(selected_key):
    catalog = query_catalog()
    meta = catalog[selected_key]
    report_id = meta["id"] if meta["source"] == "report" else ""
    delete_button = ""
    if meta["source"] == "report":
        delete_button = f"""
    <form method="post" action="/reports/delete">
      <input type="hidden" name="report_id" value="{esc(report_id)}">
      <button class="danger" type="submit">Delete Report</button>
    </form>
"""
    return f"""
<section class="panel">
  <h2>Report Editor</h2>
  <div class="panel-body">
    <form method="post" action="/reports/save">
      <input type="hidden" name="report_id" value="{esc(report_id)}">

      <label for="report_name">Report Name</label>
      <input id="report_name" name="report_name" value="{esc(meta["name"])}">

      <label for="report_description">Description</label>
      <textarea class="report-description" id="report_description" name="report_description">{esc(meta["description"])}</textarea>

      <label for="report_sql">SQL</label>
      <textarea class="report-sql" id="report_sql" name="report_sql">{esc(meta["sql"].strip())}</textarea>

      <div class="actions">
        <button type="submit">Save Report</button>
        <button class="secondary" type="submit" name="save_as_new" value="1">Save As New</button>
      </div>
    </form>
    {delete_button}
    <p class="hint">Saved locally to {esc(os.path.abspath(REPORTS_PATH))}</p>
  </div>
</section>
"""


def render_ai_generator_form():
    values = ai_form_values()
    provider_options = "\n".join(
        f'<option value="{esc(key)}" {"selected" if key == values["ai_provider"] else ""}>{esc(label)}</option>'
        for key, label in AI_PROVIDERS.items()
    )
    openai_key_placeholder = (
        "Saved key is set. Enter a new key to replace it." if values["openai_key_saved"] else "sk-..."
    )
    anthropic_key_placeholder = (
        "Saved or shell profile key is set. Enter a new key to replace it."
        if values["anthropic_key_saved"]
        else "sk-ant-..."
    )
    return f"""
<section class="panel">
  <h2>AI Query Generator</h2>
  <div class="panel-body">
    <form method="post" action="/ai/generate">
      <label for="ai_provider">AI Engine</label>
      <select id="ai_provider" name="ai_provider">
        {provider_options}
      </select>

      {render_key_status("openai", values["openai_key_saved"], "OpenAI key found and applied.", "OpenAI key not found.")}
      {render_key_status("anthropic", values["anthropic_key_saved"], "Anthropic key found and applied.", "Anthropic key not found.")}
      {render_key_status("cursor", False, "Cursor key found and applied.", "Cursor cannot be called directly from this portal.")}

      <div class="provider-group" data-provider-group="openai">
        <label for="openai_api_key">OpenAI API Key</label>
        <input id="openai_api_key" name="openai_api_key" type="password" placeholder="{esc(openai_key_placeholder)}">

        <label for="openai_model">AI Model</label>
        <select id="openai_model" name="openai_model">
          {render_model_options(OPENAI_MODELS, values["openai_model"])}
        </select>
      </div>

      <div class="provider-group" data-provider-group="anthropic">
        <label for="anthropic_api_key">Anthropic API Key</label>
        <input id="anthropic_api_key" name="anthropic_api_key" type="password" placeholder="{esc(anthropic_key_placeholder)}">

        <label for="anthropic_model">AI Model</label>
        <select id="anthropic_model" name="anthropic_model">
          {render_model_options(ANTHROPIC_MODELS, values["anthropic_model"])}
        </select>
      </div>

      <div class="provider-group" data-provider-group="cursor">
        <label for="cursor_model">AI Model</label>
        <select id="cursor_model" name="cursor_model">
          {render_model_options(CURSOR_MODELS, values["cursor_model"])}
        </select>
      </div>

      <label for="ai_query_request">What query would you like to create?</label>
      <textarea class="ai-request" id="ai_query_request" name="ai_query_request"></textarea>

      <button type="submit">Generate Query</button>
      <p class="hint">Keys are saved locally to {esc(os.path.abspath(ENV_PATH))}. Claude keys are also detected from ~/.bash_profile, ~/.zprofile, or ~/.zshrc when present. Cursor is listed for clarity, but this portal cannot call Cursor directly.</p>
    </form>
  </div>
</section>
"""


def render_key_status(provider, found, found_text, missing_text):
    class_name = "found" if found else "missing"
    text = found_text if found else missing_text
    return f'<div class="key-status {class_name}" data-key-status="{esc(provider)}">{esc(text)}</div>'


def render_model_options(models, selected):
    values = [value for value, _ in models]
    option_html = []
    for value, label in models:
        option_html.append(
            f'<option value="{esc(value)}" {"selected" if value == selected else ""}>{esc(label)} ({esc(value)})</option>'
        )
    if selected and selected not in values:
        option_html.append(f'<option value="{esc(selected)}" selected>Custom ({esc(selected)})</option>')
    return "\n".join(option_html)


def render_package_form():
    return f"""
<section class="panel">
  <h2>Report Packages</h2>
  <div class="panel-body">
    <form method="get" action="/reports/export">
      <button type="submit">Export Reports Package</button>
    </form>

    <form method="post" action="/reports/import">
      <label for="package_json">Import Package JSON</label>
      <textarea class="report-description" id="package_json" name="package_json"></textarea>

      <label for="import_mode">Import Mode</label>
      <select id="import_mode" name="import_mode">
        <option value="merge">Merge and copy conflicts</option>
        <option value="copy">Always import as new copies</option>
      </select>

      <button type="submit">Import Reports Package</button>
    </form>
    <p class="hint">Packages contain report names, descriptions, and SQL only. They do not contain database credentials.</p>
  </div>
</section>
"""


def render_addm_duplicate_resolution_form(preview_sql=""):
    sql_html = ""
    if preview_sql:
        sql_html = f"""
    <label>Preview SQL</label>
    <textarea class="report-sql" readonly>{esc(preview_sql)}</textarea>
"""
    return f"""
<section class="panel">
  <h2>ADDM Duplicate Resolution Preview</h2>
  <div class="panel-body">
    <form method="post" action="/duplicates/addm-preview">
      <label for="addm_group_mode">Duplicate Grouping</label>
      <select id="addm_group_mode" name="addm_group_mode">
        <option value="dataset_serial">Dataset + SerialNumber</option>
        <option value="dataset_addm">Dataset + ADDMIntegrationId</option>
        <option value="dataset_serial_addm">Dataset + SerialNumber + ADDMIntegrationId</option>
      </select>

      <label for="addm_selection_rule">Bulk Selection Rule</label>
      <select id="addm_selection_rule" name="addm_selection_rule">
        <option value="mark_newest_lastscan">Mark newest LastScanDate in each group</option>
        <option value="mark_oldest_lastscan">Mark oldest LastScanDate in each group</option>
        <option value="mark_blank_addm">Mark active records with blank ADDMIntegrationId</option>
        <option value="mark_fewest_relationships">Mark record with fewest relationships</option>
        <option value="mark_all_except_oldest_created">Mark all except oldest CreatedDate</option>
        <option value="mark_all_except_most_relationships">Mark all except most relationships</option>
      </select>

      <label for="addm_limit">Preview Row Limit</label>
      <input id="addm_limit" name="addm_limit" type="number" min="1" max="100000" value="500">

      <details class="sql-details">
        <summary>Column Mapping</summary>
        <div class="panel-body">
          <label for="addm_table">CI Table</label>
          <input id="addm_table" name="addm_table" value="bmc_core_bmc_baseelement">

          <label for="addm_relationship_table">Relationship Table</label>
          <input id="addm_relationship_table" name="addm_relationship_table" value="bmc_core_bmc_baserelationship">

          <label for="addm_id_column">Instance ID Column</label>
          <input id="addm_id_column" name="addm_id_column" value="instanceid">

          <label for="addm_dataset_column">Dataset Column</label>
          <input id="addm_dataset_column" name="addm_dataset_column" value="datasetid">

          <label for="addm_class_column">Class ID Column</label>
          <input id="addm_class_column" name="addm_class_column" value="classid">

          <label for="addm_name_column">Name Column</label>
          <input id="addm_name_column" name="addm_name_column" value="name">

          <label for="addm_serial_column">SerialNumber Column</label>
          <input id="addm_serial_column" name="addm_serial_column" value="serialnumber">

          <label for="addm_integration_column">ADDMIntegrationId Column</label>
          <input id="addm_integration_column" name="addm_integration_column" value="addmintegrationid">

          <label for="addm_source_column">Discovery Source Column</label>
          <input id="addm_source_column" name="addm_source_column" value="datasetid">

          <label for="addm_created_column">CreatedDate Column</label>
          <input id="addm_created_column" name="addm_created_column" value="createdate">

          <label for="addm_modified_column">ModifiedDate Column</label>
          <input id="addm_modified_column" name="addm_modified_column" value="modifieddate">

          <label for="addm_lastscan_column">LastScanDate Column</label>
          <input id="addm_lastscan_column" name="addm_lastscan_column" value="lastscandate">

          <label for="addm_deleted_column">MarkAsDeleted Column</label>
          <input id="addm_deleted_column" name="addm_deleted_column" value="markasdeleted">
        </div>
      </details>

      <button type="submit">Preview Bulk Selection</button>
      <p class="hint">Preview only. Updates will be applied through BMC CMDB REST API, not SQL.</p>

      <label for="cmdb_rest_duplicate_action">CMDB REST Apply Action</label>
      <select id="cmdb_rest_duplicate_action" name="cmdb_rest_duplicate_action">
        <option value="soft_mark_deleted">Soft mark only: set MarkAsDeleted to 1</option>
        <option value="delete_instance">Delete CMDB instance through REST</option>
      </select>
      <p class="hint">Soft mark uses PATCH to update MarkAsDeleted. Delete uses the CMDB REST DELETE endpoint and the configured delete option.</p>

      <label>
        <input type="checkbox" name="confirm_cmdb_rest_apply" value="1">
        Confirm REST apply for rows marked Selected For Deletion
      </label>
      <button class="danger" type="submit" formaction="/duplicates/addm-apply">Apply Selected Updates Through CMDB REST API</button>
      <p class="hint">Apply uses the CMDB REST Connection panel credentials plus each selected row's DatasetId and InstanceId. SQL remains read-only for discovery and preview.</p>
    </form>
    {sql_html}
  </div>
</section>
"""


def render_table(columns, rows):
    if not rows:
        return '<div class="empty">No rows returned.</div>'

    head = "".join(f"<th>{esc(col)}</th>" for col in columns)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{esc(value)}</td>" for value in row)
        body_rows.append(f"<tr>{cells}</tr>")
    return f"""
<div class="table-wrap">
  <table class="resizable-table">
    <thead><tr>{head}</tr></thead>
    <tbody>{''.join(body_rows)}</tbody>
  </table>
</div>
"""


def result_export_query(query_key, params):
    query = {
        "query": query_key,
        "datasetids": params["datasetids"],
        "hours": params["hours"],
        "limit": params["limit"],
        "classids": "\n".join(params["classids"]),
    }
    return urlencode(query, doseq=True)


def render_page(
    query_key,
    params,
    error=None,
    result=None,
    message="",
    addm_preview_sql="",
    result_title=None,
    result_description=None,
    result_sql=None,
):
    catalog = query_catalog()
    meta = catalog[query_key]
    display_title = result_title or meta["name"]
    config = db_config()
    db_label = f'{config["user"]}@{config["host"]}:{config["port"]}/{config["dbname"] or "(no database)"}'

    result_html = ""
    if error:
        result_html = f'<div class="panel-body"><div class="error">{esc(error)}</div></div>'
    elif result:
        columns, rows = result
        export_href = f"/results/export?{result_export_query(query_key, params)}"
        result_html = (
            f'<div class="panel-body"><div class="status">'
            f'<span class="pill">{esc(len(rows))} rows</span>'
            f'<span class="pill">{esc(db_label)}</span>'
            f'<a class="pill export-pill" href="{esc(export_href)}">Export Spreadsheet</a>'
            f"</div></div>"
            + render_table(columns, rows)
        )
    else:
        result_html = '<div class="empty">Choose a query and run it.</div>'

    connection_message = message if not message.startswith("CMDB REST") else ""
    rest_message = message if message.startswith("CMDB REST") else ""
    body = f"""
<div class="page-stack">
  <div class="cockpit-deck">
    {render_query_form(query_key, params)}
    {render_connection_form(connection_message)}
    {render_rest_settings_form(rest_message)}
    {render_report_form(query_key)}
    {render_ai_generator_form()}
    {render_addm_duplicate_resolution_form(addm_preview_sql)}
    {render_package_form()}
  </div>
  <section class="panel results-panel">
    <h2>{esc(display_title)} Results</h2>
    {result_html}
  </section>
</div>
"""
    return render_layout(body)


class DashboardHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = parse_qs(body)

        if parsed.path == "/connection":
            save_db_settings(form)
            self.send_response(303)
            self.send_header("Location", "/?saved=connection")
            self.end_headers()
            return

        if parsed.path == "/rest-settings":
            save_rest_settings(form)
            self.send_response(303)
            self.send_header("Location", "/?saved=rest")
            self.end_headers()
            return

        if parsed.path == "/reports/save":
            try:
                report_id = save_report(form)
                params = {"query": f"{REPORT_PREFIX}{report_id}", "saved": "report"}
            except Exception as exc:
                params = {"saved": "report_error", "error": str(exc)}
            self.send_response(303)
            self.send_header("Location", "/?" + urlencode(params))
            self.end_headers()
            return

        if parsed.path == "/reports/delete":
            delete_report(form)
            self.send_response(303)
            self.send_header("Location", "/?saved=report_deleted")
            self.end_headers()
            return

        if parsed.path == "/reports/import":
            try:
                imported_count = import_report_package(form)
                params = {"saved": "package_imported", "count": str(imported_count)}
            except Exception as exc:
                params = {"saved": "report_error", "error": str(exc)}
            self.send_response(303)
            self.send_header("Location", "/?" + urlencode(params))
            self.end_headers()
            return

        if parsed.path == "/ai/generate":
            try:
                report_id = generate_report_with_ai(form)
                params = {"query": f"{REPORT_PREFIX}{report_id}", "saved": "ai_generated"}
            except Exception as exc:
                params = {"saved": "report_error", "error": str(exc)}
            self.send_response(303)
            self.send_header("Location", "/?" + urlencode(params))
            self.end_headers()
            return

        if parsed.path == "/duplicates/addm-preview":
            query_key = selected_query_key(f"{BUILTIN_PREFIX}ci_by_class")
            params = query_params({})
            error = None
            result = None
            message = "ADDM duplicate resolution preview generated. No CMDB rows were updated."
            preview_sql = ""
            try:
                preview_sql = build_addm_duplicate_resolution_sql(form)
                result = execute_sql(preview_sql)
            except Exception as exc:
                error = str(exc)
            page = render_page(
                query_key,
                params,
                error=error,
                result=result,
                message=message,
                addm_preview_sql=preview_sql,
                result_title="ADDM Duplicate Resolution Preview",
                result_description="Bulk duplicate selection preview with MarkAsDeleted visibility. Preview only.",
                result_sql=preview_sql,
            )
            encoded = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return

        if parsed.path == "/duplicates/addm-apply":
            query_key = selected_query_key(f"{BUILTIN_PREFIX}ci_by_class")
            params = query_params({})
            error = None
            result = None
            message = "CMDB REST duplicate resolution completed. Review each row for success or failure."
            preview_sql = ""
            try:
                preview_sql, result = apply_addm_duplicate_resolution(form)
            except Exception as exc:
                error = str(exc)
            page = render_page(
                query_key,
                params,
                error=error,
                result=result,
                message=message,
                addm_preview_sql=preview_sql,
                result_title="CMDB REST Duplicate Resolution",
                result_description="Bulk duplicate resolution results from CMDB REST API.",
                result_sql=preview_sql,
            )
            encoded = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/assets/"):
            self.serve_asset(parsed.path)
            return

        if parsed.path == "/results/export":
            form = parse_qs(parsed.query)
            query_key = selected_query_key(form.get("query", [f"{BUILTIN_PREFIX}ci_by_class"])[0])
            params = query_params(form)
            try:
                columns, rows = run_query(query_key, params)
                report_name = query_catalog()[query_key]["name"]
                payload = build_xlsx(columns, rows)
                filename = f"{safe_filename(report_name)}-results.xlsx"
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as exc:
                payload = str(exc).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            return

        if parsed.path == "/reports/export":
            payload = json.dumps(report_package_payload(), indent=2, sort_keys=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="cmdb-report-package.json"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        form = parse_qs(parsed.query)
        query_key = selected_query_key(form.get("query", [f"{BUILTIN_PREFIX}ci_by_class"])[0])

        params = query_params(form)
        error = None
        result = None
        try:
            result = run_query(query_key, params)
        except Exception as exc:
            error = str(exc)

        message = ""
        if form.get("saved", [""])[0] == "connection":
            message = "Connection settings saved. Run a query to test them."
        elif form.get("saved", [""])[0] == "rest":
            message = "CMDB REST connection settings saved."
        elif form.get("saved", [""])[0] == "report":
            message = "Report saved."
        elif form.get("saved", [""])[0] == "report_deleted":
            message = "Report deleted."
        elif form.get("saved", [""])[0] == "report_error":
            error = form.get("error", ["Unable to save report."])[0]
        elif form.get("saved", [""])[0] == "package_imported":
            message = f"Imported {form.get('count', ['0'])[0]} report(s)."
        elif form.get("saved", [""])[0] == "ai_generated":
            message = "AI generated a saved report draft. Review it before running."

        page = render_page(query_key, params, error=error, result=result, message=message)
        encoded = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def serve_asset(self, path):
        asset_name = os.path.basename(path)
        asset_path = os.path.join(ASSETS_DIR, asset_name)
        if not os.path.exists(asset_path):
            self.send_response(404)
            self.end_headers()
            return
        content_type = mimetypes.guess_type(asset_path)[0] or "application/octet-stream"
        with open(asset_path, "rb") as asset_file:
            data = asset_file.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def main():
    load_dotenv(override=True)
    load_shell_profile_keys()
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"{APP_TITLE} running at http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
