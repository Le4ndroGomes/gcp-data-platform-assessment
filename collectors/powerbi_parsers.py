"""Parsers de artefatos Power BI para o modo offline e para análise de M/DAX.

Formatos suportados:
  - Resultado do Scanner API exportado (JSON com `workspaces`)
  - PBIP: <nome>.SemanticModel/definition/**/*.tmdl (TMDL) ou model.bim (TMSL)
          <nome>.Report/definition/pages/** (PBIR) ou report.json (legado)
  - PBIT: zip com DataModelSchema (JSON UTF-16) e Report/Layout
  - model.bim avulso
Limitação conhecida: .pbix não é suportado (DataModel é binário comprimido);
salve como PBIP ou exporte PBIT.
"""
from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from pathlib import Path

from analyzers.sql_complexity import analyze_text

# ------------------------------------------------------------------- M
SOURCE_FUNCTIONS = {
    "GoogleBigQuery.Database": "bigquery", "GoogleBigQueryAad.Database": "bigquery",
    "Sql.Database": "sqlserver", "Sql.Databases": "sqlserver", "Oracle.Database": "oracle",
    "PostgreSQL.Database": "postgresql", "MySQL.Database": "mysql", "Odbc.DataSource": "odbc", "Odbc.Query": "odbc",
    "OleDb.DataSource": "oledb", "Web.Contents": "web/api", "Web.Page": "web/api", "OData.Feed": "odata",
    "SharePoint.Files": "sharepoint", "SharePoint.Contents": "sharepoint", "SharePoint.Tables": "sharepoint",
    "Excel.Workbook": "excel", "Csv.Document": "csv", "File.Contents": "file", "Folder.Files": "folder",
    "Json.Document": "json", "Xml.Tables": "xml", "Snowflake.Databases": "snowflake",
    "Databricks.Catalogs": "databricks", "DatabricksMultiCloud.Catalogs": "databricks",
    "AzureStorage.Blobs": "azure_storage", "AzureStorage.DataLake": "azure_storage", "Salesforce.Data": "salesforce",
    "Salesforce.Reports": "salesforce", "AnalysisServices.Database": "analysis_services",
    "PowerPlatform.Dataflows": "powerbi_dataflow", "PowerBI.Dataflows": "powerbi_dataflow",
    "SapHana.Database": "sap_hana", "SapBusinessWarehouse.Cubes": "sap_bw", "Teradata.Database": "teradata",
    "AmazonRedshift.Database": "redshift", "Spark.Tables": "spark", "GoogleAnalytics.Accounts": "google_analytics",
    "CommonDataService.Database": "dataverse", "Dataverse.Contents": "dataverse", "Lakehouse.Contents": "fabric",
    "Fabric.Warehouse": "fabric", "Sql.DataWarehouse": "sqlserver",
}
HEAVY_STEPS = {"Table.NestedJoin", "Table.Join", "Table.FuzzyNestedJoin", "Table.Group", "Table.Pivot", "Table.Unpivot",
               "Table.UnpivotOtherColumns", "Table.Buffer", "Table.Combine", "Table.AddColumn", "List.Generate",
               "List.Accumulate", "Table.ExpandTableColumn", "Table.Distinct", "Table.Sort", "Table.TransformColumns"}
_SOURCE_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(SOURCE_FUNCTIONS, key=len, reverse=True)) + r")\s*\(")
_NAV_RE = re.compile(r'\{\s*\[\s*Name\s*=\s*("(?:[^"]|"")*"|#"(?:[^"]|"")*"|[A-Za-z_][\w.]*)\s*(?:,\s*Kind\s*=\s*"(\w+)"\s*)?\]\s*\}')
_STEP_RE = re.compile(r"^\s*(#\"(?:[^\"]|\"\")+\"|[A-Za-z_][\w.]*)\s*=", re.M)
_FUNC_RE = re.compile(r"\b((?:Table|List|Text|Date|DateTime|Number|Record)\.[A-Za-z]+)\s*\(")
_NATIVE_RE = re.compile(r'(?:Value\.NativeQuery\s*\([^,]+,|\[\s*Query\s*=)\s*"((?:[^"]|"")*)"', re.S)
_BILLING_RE = re.compile(r'BillingProject\s*=\s*"([^"]+)"')


def _m_unquote(token: str, params: dict) -> str | None:
    token = token.strip()
    if token.startswith('#"'):
        name = token[2:-1].replace('""', '"')
        return params.get(name, name)
    if token.startswith('"'):
        return token[1:-1].replace('""', '"')
    return params.get(token)  # identificador -> parâmetro


def _m_string(s: str) -> str:
    return s.replace('""', '"').replace("#(lf)", "\n").replace("#(cr)", "").replace("#(tab)", "\t")


def parameters_from_expressions(expressions: list[dict]) -> dict:
    """Parâmetros do Power Query: `"valor" meta [IsParameterQuery=true, ...]`."""
    out = {}
    for e in expressions or []:
        expr = e.get("expression")
        if isinstance(expr, list):
            expr = "\n".join(expr)
        m = re.match(r'\s*"((?:[^"]|"")*)"\s+meta\s*\[', expr or "")
        if m and e.get("name"):
            out[e["name"]] = m.group(1).replace('""', '"')
    return out


def parse_m(expression: str | list | None, params: dict | None = None) -> dict:
    if isinstance(expression, list):
        expression = "\n".join(expression)
    expr = expression or ""
    params = params or {}
    connectors = [m.group(1) for m in _SOURCE_RE.finditer(expr)]
    source_types = sorted({SOURCE_FUNCTIONS[c] for c in connectors})
    bq_refs = []
    if any(SOURCE_FUNCTIONS[c] == "bigquery" for c in connectors):
        project = dataset = None
        for tok, kind in _NAV_RE.findall(expr):
            val = _m_unquote(tok, params)
            if val is None:
                continue
            if not kind:
                project = val
            elif kind == "Schema":
                dataset = val
            elif kind in ("Table", "View", "Function"):
                bq_refs.append(".".join(x for x in [project, dataset, val] if x))
    natives = []
    for m in _NATIVE_RE.finditer(expr):
        sql = _m_string(m.group(1))
        a = analyze_text(sql)
        natives.append({"complexity": a["complexity"], "score": a["score"], "anti_patterns": a["anti_patterns"],
                        "referenced_tables": a["referenced_tables"], "chars": len(sql)})
    funcs = Counter(f for f in _FUNC_RE.findall(expr))
    heavy = {k: v for k, v in funcs.items() if k in HEAVY_STEPS}
    steps = len(_STEP_RE.findall(expr))
    return {"connectors": sorted(set(connectors)), "source_types": source_types, "bigquery_refs": sorted(set(bq_refs)),
            "billing_project": (_BILLING_RE.search(expr).group(1) if _BILLING_RE.search(expr) else None),
            "native_queries": natives, "steps": steps, "heavy_steps": heavy,
            "transform_functions": sum(funcs.values()), "chars": len(expr)}


# ------------------------------------------------------------------- DAX
DAX_ITERATORS = re.compile(r"\b(SUMX|AVERAGEX|MINX|MAXX|COUNTX|RANKX|CONCATENATEX|PRODUCTX|FILTER|ADDCOLUMNS|GENERATE)\s*\(", re.I)
DAX_TIME = re.compile(r"\b(DATEADD|SAMEPERIODLASTYEAR|TOTALYTD|TOTALMTD|TOTALQTD|DATESYTD|DATESMTD|PARALLELPERIOD|"
                      r"DATESINPERIOD|DATESBETWEEN|PREVIOUSMONTH|PREVIOUSYEAR)\s*\(", re.I)


def dax_metrics(expr: str | list | None) -> dict:
    if isinstance(expr, list):
        expr = "\n".join(expr)
    e = expr or ""
    calc = len(re.findall(r"\bCALCULATE(TABLE)?\s*\(", e, re.I))
    it = len(DAX_ITERATORS.findall(e))
    ti = len(DAX_TIME.findall(e))
    other = len(re.findall(r"\b(USERELATIONSHIP|CROSSFILTER|TREATAS|SUMMARIZECOLUMNS|SELECTEDVALUE|SWITCH|LOOKUPVALUE|"
                           r"EARLIER|PATH|ALLEXCEPT|ALLSELECTED|KEEPFILTERS)\s*\(", e, re.I))
    vars_ = len(re.findall(r"\bVAR\b", e, re.I))
    score = calc * 2 + it * 3 + ti * 2 + other * 2 + vars_ * 0.5 + len(e) / 400
    cls = "Baixa" if score < 6 else "Média" if score < 15 else "Alta" if score < 35 else "Muito alta"
    return {"chars": len(e), "calculate": calc, "iterators": it, "time_intelligence": ti, "advanced_functions": other,
            "variables": vars_, "score": round(score, 1), "complexity": cls,
            "uses_userprincipalname": bool(re.search(r"USERPRINCIPALNAME|USERNAME\s*\(", e, re.I))}


# ------------------------------------------------------------------ TMDL
def _tmdl_name(text: str) -> str:
    text = text.strip()
    if text.startswith("'"):
        m = re.match(r"'((?:[^']|'')*)'", text)
        return m.group(1).replace("''", "'") if m else text
    return text.split()[0] if text else text


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip("\t"))


def parse_tmdl_file(text: str) -> list[dict]:
    """Retorna objetos de topo (table/relationship/role/expression) com filhos e propriedades."""
    lines = text.splitlines()
    objs: list[dict] = []
    i = 0
    stack: list[dict] = []
    obj_re = re.compile(r"^(table|relationship|role|expression|measure|column|partition|calculationGroup|calculationItem|"
                        r"hierarchy|level|tablePermission|cultureInfo|perspective|dataSource|model|database|annotation|"
                        r"extendedProperty|ref|calculationExpression)\b\s*(.*)$")
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.strip().startswith("///"):
            i += 1
            continue
        ind = _indent(raw)
        body = raw.strip()
        m = obj_re.match(body)
        if m:
            kind, rest = m.group(1), m.group(2)
            name_part, _, expr = rest.partition("=")
            obj = {"kind": kind, "name": _tmdl_name(name_part) if name_part.strip() else "", "props": {}, "children": [],
                   "expression": None, "indent": ind}
            expr = expr.strip()
            if "=" in rest:
                if expr and expr != "```":
                    obj["expression"] = expr
                    i += 1
                    # continuação multilinha (linhas mais indentadas que as propriedades)
                    cont, i = _collect(lines, i, ind + 2)
                    if cont:
                        obj["expression"] += "\n" + cont
                else:
                    cont, i = _collect(lines, i + 1, ind + 2, fenced=(expr == "```"))
                    obj["expression"] = cont
            else:
                i += 1
            while stack and stack[-1]["indent"] >= ind:
                stack.pop()
            (stack[-1]["children"] if stack else objs).append(obj)
            stack.append(obj)
            continue
        # propriedade
        if stack:
            owner = stack[-1]
            while stack and owner["indent"] >= ind and len(stack) > 1:
                stack.pop()
                owner = stack[-1]
            if ":" in body and not body.endswith("="):
                k, _, v = body.partition(":")
                owner["props"][k.strip()] = v.strip()
                i += 1
            elif body.endswith("=") or re.match(r"^\w+\s*=", body):
                k, _, v = body.partition("=")
                v = v.strip()
                if v and v != "```":
                    owner["props"][k.strip()] = v
                    i += 1
                else:
                    cont, i = _collect(lines, i + 1, ind + 1, fenced=(v == "```"))
                    owner["props"][k.strip()] = cont
            else:
                i += 1
        else:
            i += 1
    return objs


def _collect(lines: list[str], i: int, min_indent: int, fenced: bool = False) -> tuple[str, int]:
    out = []
    while i < len(lines):
        ln = lines[i]
        if fenced:
            if ln.strip() == "```":
                return "\n".join(out), i + 1
            out.append(ln.strip("\t"))
            i += 1
            continue
        if ln.strip() and _indent(ln) < min_indent:
            break
        out.append(ln[min_indent:] if ln.strip() else "")
        i += 1
    return "\n".join(out).strip(), i


def model_from_tmdl(definition_dir: Path) -> dict:
    tables, relationships, roles, expressions = [], [], [], []
    for f in sorted(definition_dir.rglob("*.tmdl")):
        for o in parse_tmdl_file(f.read_text(encoding="utf-8-sig")):
            if o["kind"] == "table":
                t = {"name": o["name"], "columns": [], "measures": [], "partitions": []}
                for c in o["children"]:
                    if c["kind"] == "column":
                        t["columns"].append({"name": c["name"], "dataType": c["props"].get("dataType"),
                                             "type": "calculated" if c["expression"] else "data",
                                             "expression": c["expression"]})
                    elif c["kind"] == "measure":
                        t["measures"].append({"name": c["name"], "expression": c["expression"]})
                    elif c["kind"] == "partition":
                        t["partitions"].append({"name": c["name"], "mode": c["props"].get("mode", "import"),
                                                "source": {"type": c["expression"] or "m",
                                                           "expression": c["props"].get("source")}})
                tables.append(t)
            elif o["kind"] == "relationship":
                fc, tc = o["props"].get("fromColumn", ""), o["props"].get("toColumn", "")
                relationships.append({"name": o["name"], "fromTable": fc.rsplit(".", 1)[0], "fromColumn": fc.rsplit(".", 1)[-1],
                                      "toTable": tc.rsplit(".", 1)[0], "toColumn": tc.rsplit(".", 1)[-1],
                                      "crossFilteringBehavior": o["props"].get("crossFilteringBehavior", "oneDirection"),
                                      "isActive": o["props"].get("isActive", "true") != "false"})
            elif o["kind"] == "role":
                roles.append({"name": o["name"], "modelPermission": o["props"].get("modelPermission"),
                              "tablePermissions": [{"name": c["name"], "filterExpression": c["expression"]}
                                                   for c in o["children"] if c["kind"] == "tablePermission"]})
            elif o["kind"] == "expression":
                expressions.append({"name": o["name"], "expression": o["expression"]})
    return {"tables": tables, "relationships": relationships, "roles": roles, "expressions": expressions}


def model_from_bim(data: dict) -> dict:
    m = data.get("model", data)
    return {"tables": m.get("tables") or [], "relationships": m.get("relationships") or [], "roles": m.get("roles") or [],
            "expressions": m.get("expressions") or [], "dataSources": m.get("dataSources") or []}


def report_stats_pbir(report_dir: Path) -> dict:
    pages = list(report_dir.glob("definition/pages/*/page.json"))
    visuals = list(report_dir.glob("definition/pages/*/visuals/*/visual.json"))
    if pages:
        return {"pages": len(pages), "visuals": len(visuals), "format": "PBIR"}
    legacy = report_dir / "report.json"
    if legacy.exists():
        return report_stats_layout(json.loads(legacy.read_text(encoding="utf-8-sig")))
    return {"pages": None, "visuals": None, "format": "desconhecido"}


def report_stats_layout(layout: dict) -> dict:
    sections = layout.get("sections") or []
    return {"pages": len(sections), "visuals": sum(len(s.get("visualContainers") or []) for s in sections),
            "format": "PBIR-Legacy"}


def _read_utf16_json(raw: bytes) -> dict:
    for enc in ("utf-16-le", "utf-16", "utf-8-sig"):
        try:
            return json.loads(raw.decode(enc).lstrip("﻿"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise ValueError("Não foi possível decodificar o JSON do PBIT")


def load_offline(path: str | Path) -> tuple[list[dict], list[str]]:
    """Varre uma pasta/arquivo e retorna (artefatos normalizados, avisos)."""
    root = Path(path)
    artifacts, warnings = [], []
    files = [root] if root.is_file() else list(root.rglob("*"))
    seen_models: set[Path] = set()
    for f in files:
        try:
            if f.is_dir() and f.name.endswith(".SemanticModel"):
                d = f / "definition"
                if d.is_dir():
                    model = model_from_tmdl(d)
                elif (f / "model.bim").exists():
                    model = model_from_bim(json.loads((f / "model.bim").read_text(encoding="utf-8-sig")))
                else:
                    warnings.append(f"{f.name}: SemanticModel sem definition/ ou model.bim")
                    continue
                seen_models.add(f)
                rep_dir = f.with_name(f.name.replace(".SemanticModel", ".Report"))
                artifacts.append({"kind": "pbip", "name": f.name.replace(".SemanticModel", ""), "model": model,
                                  "report": report_stats_pbir(rep_dir) if rep_dir.exists() else None, "path": f.name})
            elif f.is_file() and f.suffix.lower() == ".pbit":
                with zipfile.ZipFile(f) as z:
                    model = model_from_bim(_read_utf16_json(z.read("DataModelSchema")))
                    report = None
                    if "Report/Layout" in z.namelist():
                        report = report_stats_layout(_read_utf16_json(z.read("Report/Layout")))
                artifacts.append({"kind": "pbit", "name": f.stem, "model": model, "report": report, "path": f.name})
            elif f.is_file() and f.name == "model.bim" and f.parent not in seen_models:
                artifacts.append({"kind": "bim", "name": f.parent.name, "model": model_from_bim(json.loads(
                    f.read_text(encoding="utf-8-sig"))), "report": None, "path": f.name})
            elif f.is_file() and f.suffix.lower() == ".json" and "scan" in f.name.lower():
                data = json.loads(f.read_text(encoding="utf-8-sig"))
                if "workspaces" in data:
                    artifacts.append({"kind": "scanner", "name": f.stem, "scan": data, "path": f.name})
            elif f.is_file() and f.suffix.lower() == ".pbix":
                warnings.append(f"{f.name}: .pbix não suportado (modelo binário). Salve como PBIP ou exporte PBIT.")
        except Exception as exc:  # artefato corrompido não interrompe a análise
            warnings.append(f"{f.name}: erro ao ler ({type(exc).__name__}: {str(exc)[:120]})")
    return artifacts, warnings
