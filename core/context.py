"""Contexto de execução compartilhado entre collectors, analyzers e exporters."""
from __future__ import annotations

import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from core.coverage import CoverageTracker
from utils.logging import get_logger
from utils.redaction import Redactor

log = get_logger("context")

DEFAULT_CONFIG: dict = {
    "gcp": {
        "organization_id": None,
        "projects": [],              # vazio = todos os projetos visíveis
        "exclude_projects": [],
        "exclude_project_patterns": ["^sys-", "^apps-script-"],
        "regions": [],               # vazio = regiões descobertas automaticamente
        "impersonate_service_account": None,
        "quota_project": None,
        "max_projects": 500,
        "max_assets_per_project": 20000,
    },
    "execution": {
        "concurrency": 4,
        "max_in_flight_requests": 12,   # teto global de requisições HTTP simultâneas (todos os módulos)
        "parallel_modules": True,         # módulos independentes rodam ao mesmo tempo; False = sequencial
        "timeout_seconds": 60,
        "max_retries": 5,
        "days": 90,
    },
    "bigquery": {
        "execution_project": None,     # projeto onde os jobs de metadados são executados
        # INFORMATION_SCHEMA.JOBS pode varrer dezenas de GB por consulta em projetos movimentados (janela de 90 dias).
        # O preflight estima o custo total antes da coleta; ajuste os limites conforme a política do cliente.
        "max_bytes_billed_per_query": 200 * 1024 ** 3,
        "max_total_bytes_billed": 2 * 1024 ** 4,
        "collect_columns": True,
        "collect_partitions": False,   # INFORMATION_SCHEMA.PARTITIONS é por dataset e pode custar mais
        "max_datasets_dataset_level_views": 200,
        "top_fingerprints": 200,
        "top_tables": 500,
        "low_usage_days": 90,
        "sensitive_column_patterns": [
            "cpf", "cnpj", "rg", "cns", "cartao_sus", "nome", "paciente", "nm_pac", "mae", "nascimento", "dt_nasc",
            "sexo", "genero", "raca", "cor", "etnia", "religiao", "telefone", "celular", "email", "e_mail",
            "endereco", "logradouro", "cep", "cid", "diagnostico", "prontuario", "laudo", "exame", "medicamento",
            "prescricao", "crm", "hiv", "gestante", "biometria", "saude", "doenca", "alergia", "procedimento",
        ],
    },
    "sql": {
        # none: não lê definições SQL; features: métricas calculadas no BigQuery (o texto não sai do BigQuery);
        # full: lê o texto SQL (exige --include-sql) e analisa com SQLGlot.
        "mode": "features",
        "store_sql_text": False,
        "max_sql_chars": 100_000,
    },
    "storage": {
        "sample_objects_per_bucket": 2000,
        "max_buckets_sampled": 200,
        "small_file_threshold_bytes": 16 * 1024 * 1024,
    },
    "pipelines": {
        "airflow_rest_api": True,
        "max_dag_runs_per_dag": 50,
        "max_dataflow_jobs": 500,
        "max_transfer_runs": 50,
        "dataform_compilation_graph": True,
    },
    "powerbi": {
        "enabled": True,
        "tenant_id": None,
        "client_id": None,
        "auth_mode": "service_principal",  # service_principal | device_code
        "client_secret_env": "PBI_CLIENT_SECRET",
        "offline_path": None,             # pasta com PBIP/TMDL/PBIT/model.bim/scanner JSON
        "scanner": {"lineage": True, "datasource_details": True, "dataset_schema": True,
                    "dataset_expressions": True, "artifact_users": False},
        "activity_days": 28,
        "refresh_history_top": 30,
        "max_refresh_datasets": 300,
        "principal_patterns": ["powerbi", "pbi", "gateway", "fabric"],
    },
    "billing": {
        "export_table": None,          # projeto.dataset.tabela; vazio = autodetectar
        "months": 12,
        "currency": None,
    },
    "pricing_estimates": {             # usados SOMENTE quando não há Billing Export; valores de lista a validar
        "currency": "USD",
        "bq_on_demand_per_tib": 6.25,
        "bq_active_logical_per_gib_month": 0.02,
        "bq_long_term_logical_per_gib_month": 0.01,
        "bq_active_physical_per_gib_month": 0.04,
        "bq_long_term_physical_per_gib_month": 0.02,
        "gcs_per_gib_month": {"STANDARD": 0.020, "NEARLINE": 0.010, "COLDLINE": 0.004, "ARCHIVE": 0.0012},
        "source": "Preços de lista públicos (US multi-região) - validar para a região e contrato do cliente",
    },
    "security": {
        "allowed_domains": [],          # domínios corporativos; outros = acesso externo
        "sa_key_max_age_days": 90,
    },
    "lineage": {
        "use_lineage_api": True,
        "max_lineage_api_entities": 200,
    },
    "workload_rules": {                # regras de classificação (regex sobre principal / labels)
        "etl_principals": ["gcp-sa-bigquerydatatransfer", "gcp-sa-dataform", "composer", "airflow", "dataflow",
                           "dataproc", "etl", "ingest", "pipeline", "dbt"],
        "bi_principals": ["powerbi", "pbi", "looker", "tableau", "qlik", "gateway", "metabase", "superset"],
        "ds_principals": ["vertex", "notebook", "colab", "jupyter", "datascience", "ml"],
        "bi_label_keys": ["looker_studio_datasource_id", "looker_studio_report_id", "sheets_trigger"],
        "etl_label_keys": ["airflow-dag", "airflow-task", "data_source_id", "dataform_repository_id", "dbt_invocation_id"],
    },
    "readiness_weights": {
        "data_volume": 15,
        "sql_complexity": 20,
        "pipeline_complexity": 20,
        "bi_coupling": 15,
        "platform_specific_features": 15,
        "security_governance": 15,
    },
    "databricks": {
        "regions_snapshot_file": "config/databricks_gcp_regions.yaml",
    },
    "output": {
        "excel": True,
        "anonymize": True,
        "redact_hosts": True,
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_yaml(path: str | Path) -> dict:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        if p.suffix == ".json":
            return json.loads(text)
        raise SystemExit("PyYAML não instalado: pip install PyYAML (ou use um arquivo .json)")
    return yaml.safe_load(text) or {}


def load_config(path: str | None) -> dict:
    if not path:
        return copy.deepcopy(DEFAULT_CONFIG)
    return deep_merge(DEFAULT_CONFIG, load_yaml(path))


def _json_default(o: Any):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, set):
        return sorted(o)
    if hasattr(o, "to_dict"):
        return o.to_dict()
    return str(o)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")


@dataclass
class Context:
    config: dict
    output_dir: Path
    coverage: CoverageTracker = field(default_factory=CoverageTracker)
    redactor: Redactor = field(default_factory=Redactor)
    gcp: Any = None            # ApiClient
    bq: Any = None             # BigQueryRunner
    powerbi: Any = None        # ApiClient
    identity: Any = None
    days: int = 90
    include_sql: bool = False
    projects: list = field(default_factory=list)
    args: dict = field(default_factory=dict)

    @property
    def raw_dir(self) -> Path:
        return self.output_dir / "raw"

    # --------------------------------------------------------------- raw I/O
    def save_raw(self, module: str, name: str, data: Any) -> Path:
        path = self.raw_dir / module / f"{name}.json"
        dump_json(path, data)
        return path

    def load_raw(self, module: str, name: str, default: Any = None) -> Any:
        path = self.raw_dir / module / f"{name}.json"
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def has_raw(self, module: str) -> bool:
        return (self.raw_dir / module).exists()

    # ---------------------------------------------------------------- helpers
    @property
    def concurrency(self) -> int:
        return int(self.config["execution"].get("concurrency", 4))

    def project_ids(self) -> list[str]:
        return [p["project_id"] for p in self.projects if p.get("selected", True)]

    def regions_for(self, project_id: str) -> list[str]:
        forced = self.config["gcp"].get("regions") or []
        for p in self.projects:
            if p["project_id"] == project_id:
                found = p.get("bigquery_locations") or []
                if forced:
                    return [r for r in found if r.lower() in {x.lower() for x in forced}] or []
                return found
        return list(forced)

    @property
    def io_workers(self) -> int:
        """Threads para laços de E/S (uma requisição por item). O teto real é o semáforo global do ApiClient."""
        return int(self.config["execution"].get("max_in_flight_requests", 8))

    def run_parallel(self, fn: Callable, items: Iterable, label: str = "", workers: int | None = None) -> list:
        """Executa fn(item) em paralelo controlado; exceções não interrompem as demais tarefas."""
        items = list(items)
        results = []
        if not items:
            return results
        with ThreadPoolExecutor(max_workers=max(1, workers or self.concurrency)) as pool:
            futures = {pool.submit(fn, it): it for it in items}
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as exc:  # o collector já registra a cobertura; aqui só logamos
                    log.warning("%s falhou para %s: %s", label or fn.__name__, futures[fut], exc)
        return results


def matches_any(value: str, patterns: Iterable[str]) -> bool:
    return any(re.search(p, value or "", re.IGNORECASE) for p in patterns or [])
