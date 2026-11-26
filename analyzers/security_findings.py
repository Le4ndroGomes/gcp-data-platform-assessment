"""Findings de segurança e governança (item 13), com foco em LGPD / dados de saúde.

Somente metadados. Indícios de dados sensíveis vêm de (a) perfis existentes do
Sensitive Data Protection e (b) NOMES de colunas — nunca de conteúdo. Esses
indícios são marcados como "não verificados".
"""
from __future__ import annotations

from collections import Counter

SEV_ORDER = {"Alta": 0, "Média": 1, "Baixa": 2, "Info": 3}


def _f(findings, sev, cat, resource, finding, evidence, rec, lgpd=False):
    findings.append({"severity": sev, "category": cat, "resource": resource, "finding": finding, "evidence": evidence,
                     "recommendation": rec, "lgpd_relevant": lgpd})


def analyze(inv: dict, config: dict) -> dict:
    sec = inv.get("security") or {}
    projects = sec.get("projects") or {}
    allowed = {d.lower() for d in config.get("security", {}).get("allowed_domains") or []}
    max_age = int(config.get("security", {}).get("sa_key_max_age_days", 90))
    findings: list[dict] = []
    stats = Counter()

    user_domains = Counter()
    for pid, p in projects.items():
        for b in (p.get("iam") or {}).get("bindings") or []:
            for m in b["members"]:
                if m["type"] in ("user", "group") and m.get("domain"):
                    user_domains[m["domain"]] += 1
    if not allowed and user_domains:
        allowed = {user_domains.most_common(1)[0][0]}
        inferred_domain = True
    else:
        inferred_domain = False

    perimeter_projects = set()
    for per in sec.get("vpc_service_controls") or []:
        perimeter_projects |= set(per.get("projects") or [])
    project_numbers = {p["project_id"]: p.get("project_number") for p in inv.get("projects") or []}

    for pid, p in projects.items():
        iam = p.get("iam") or {}
        public, primitive, external, default_sa_editor = [], [], set(), False
        for b in iam.get("bindings") or []:
            for m in b["members"]:
                stats["iam_members"] += 1
                if m.get("public"):
                    public.append(b["role"])
                if b["role"] in ("roles/owner", "roles/editor") and m["type"] in ("user", "group") :
                    primitive.append(m["id"])
                if b["role"] == "roles/editor" and m.get("default_compute_sa"):
                    default_sa_editor = True
                if m["type"] in ("user", "group", "domain") and m.get("domain") and m["domain"] not in allowed \
                        and not m["domain"].endswith("gserviceaccount.com"):
                    external.add(m["domain"])
        if public:
            _f(findings, "Alta", "IAM", pid, "Papéis concedidos a allUsers/allAuthenticatedUsers",
               ", ".join(sorted(set(public))), "Remover acesso público; usar principals específicos.", True)
        if primitive:
            _f(findings, "Média", "IAM", pid, f"{len(set(primitive))} usuários/grupos com papéis básicos (Owner/Editor)",
               "roles/owner, roles/editor", "Substituir por papéis predefinidos de menor privilégio.")
        if default_sa_editor:
            _f(findings, "Média", "IAM", pid, "Compute default service account com roles/editor", "roles/editor",
               "Remover concessão automática (org policy iam.automaticIamGrantsForDefaultServiceAccounts).")
        if external:
            _f(findings, "Média", "IAM", pid, f"Membros de domínios externos: {len(external)}",
               ", ".join(sorted(external)[:10]) + (" (domínio corporativo inferido)" if inferred_domain else ""),
               "Validar necessidade; restringir com iam.allowedPolicyMemberDomains.", True)
        audit = iam.get("audit_configs") or []
        if iam and not any(a["service"] in ("allServices", "storage.googleapis.com") and "DATA_READ" in a["log_types"] for a in audit):
            _f(findings, "Baixa", "Auditoria", pid, "Data Access logs (DATA_READ) não habilitados para Cloud Storage",
               "auditConfigs do projeto", "Avaliar habilitar DATA_READ para buckets com dados sensíveis.", True)
        old = [k for k in p.get("sa_keys") or [] if not k.get("disabled") and (k.get("age_days") or 0) > max_age]
        stats["sa_keys"] += len(p.get("sa_keys") or [])
        if p.get("sa_keys"):
            _f(findings, "Alta" if old else "Média", "Credenciais", pid,
               f"{len(p['sa_keys'])} chaves user-managed de service account ({len(old)} com > {max_age} dias)",
               "IAM serviceAccounts.keys (metadados)", "Migrar para Workload Identity / impersonação; rotacionar chaves.")
        for d in p.get("datasets") or []:
            stats["datasets_evaluated"] += 1
            stats["datasets_cmek"] += int(d.get("cmek"))
            if any(a.get("public") or a.get("id") in ("allUsers", "allAuthenticatedUsers") for a in d.get("access") or []):
                _f(findings, "Alta", "BigQuery", d["dataset"], "Dataset com acesso público", "datasets.get access",
                   "Remover allUsers/allAuthenticatedUsers.", True)
            ext = {a["domain"] for a in d.get("access") or [] if a.get("domain") and a["type"] in ("user", "group", "domain")
                   and a["domain"] not in allowed}
            if ext:
                _f(findings, "Média", "BigQuery", d["dataset"], "Acesso de domínio externo ao dataset", ", ".join(sorted(ext)[:5]),
                   "Validar compartilhamento externo.", True)
        op = p.get("org_policies") or {}
        if op:
            if not (op.get("gcp.resourceLocations") or {}).get("allowed_values"):
                _f(findings, "Média", "Residência de dados", pid, "Sem restrição de localização de recursos (gcp.resourceLocations)",
                   "Org Policy efetiva", "Restringir regiões permitidas (ex.: in:southamerica-east1-locations) conforme LGPD/contratos.", True)
            if not (op.get("iam.allowedPolicyMemberDomains") or {}).get("allowed_values"):
                _f(findings, "Baixa", "IAM", pid, "Domain restricted sharing não aplicado", "iam.allowedPolicyMemberDomains",
                   "Aplicar a política para restringir membros a domínios corporativos.")
            if not (op.get("iam.disableServiceAccountKeyCreation") or {}).get("enforced"):
                _f(findings, "Baixa", "Credenciais", pid, "Criação de chaves de SA não bloqueada", "iam.disableServiceAccountKeyCreation",
                   "Aplicar a política e usar Workload Identity Federation.")
        num = project_numbers.get(pid)
        if sec.get("vpc_service_controls") is not None and (sec.get("vpc_service_controls") or perimeter_projects) and num and num not in perimeter_projects:
            _f(findings, "Média", "Rede", pid, "Projeto fora de perímetros VPC Service Controls", "Access Context Manager",
               "Incluir projetos com dados sensíveis em perímetro (BigQuery/GCS restritos).", True)
        gov = p.get("governance") or {}
        stats["taxonomies"] += len(gov.get("taxonomies") or [])
        stats["data_policies"] += len(gov.get("data_policies") or [])
        stats["dlp_profiles"] += len(gov.get("dlp_table_profiles") or [])
        stats["dlp_high"] += sum(1 for t in gov.get("dlp_table_profiles") or [] if (t.get("sensitivity") or "").startswith("SENSITIVITY_HIGH"))
    if not sec.get("vpc_service_controls") and projects:
        _f(findings, "Info", "Rede", "organização", "Nenhum perímetro VPC Service Controls visível",
           "Access Context Manager (ou sem permissão)", "Confirmar com o time de segurança.", True)

    # Buckets
    for b in inv.get("buckets") or []:
        stats["buckets"] += 1
        if b.get("public_members"):
            _f(findings, "Alta", "Cloud Storage", b["bucket"], "Bucket com acesso público", ", ".join(b["public_members"]),
               "Remover acesso público e aplicar publicAccessPrevention=enforced.", True)
        elif b.get("public_access_prevention") != "enforced":
            stats["buckets_pap_not_enforced"] += 1
        if not b.get("uniform_bucket_level_access"):
            stats["buckets_fine_grained_acl"] += 1
        stats["buckets_cmek"] += int(bool(b.get("cmek")))
    if stats["buckets_pap_not_enforced"]:
        _f(findings, "Baixa", "Cloud Storage", f"{stats['buckets_pap_not_enforced']} buckets",
           "Public access prevention não aplicado (inherited)", "iamConfiguration.publicAccessPrevention",
           "Aplicar storage.publicAccessPrevention na organização.")
    if stats["buckets_fine_grained_acl"]:
        _f(findings, "Baixa", "Cloud Storage", f"{stats['buckets_fine_grained_acl']} buckets", "ACLs fine-grained (sem uniform bucket-level access)",
           "iamConfiguration.uniformBucketLevelAccess", "Habilitar uniform bucket-level access.")

    # Bancos com IP público
    for pid, svc in (inv.get("services") or {}).items():
        for i in svc.get("cloudsql") or []:
            if i.get("public_ip"):
                _f(findings, "Média", "Rede", f"{pid}/{i['name']}", "Cloud SQL com IP público habilitado", "settings.ipConfiguration",
                   "Preferir IP privado / PSC; restringir authorized networks.", True)
    for pid, streams in (inv.get("datastream") or {}).items():
        for s in streams or []:
            if s.get("connectivity") == "ip-allowlist":
                _f(findings, "Média", "Rede", s["stream"].split("/")[-1], "Datastream via IP allowlist (conexão pública)",
                   "connectionProfile", "Usar private connectivity.", True)

    # Dados sensíveis (somente metadados)
    sens = inv.get("sensitive_columns") or []
    tagged = {(c["table_ref"], c["column"]) for c in inv.get("policy_tag_columns") or []}
    untagged = [c for c in sens if (c["table_ref"], c["column"]) not in tagged]
    sens_tables = {c["table_ref"] for c in sens}
    if sens:
        _f(findings, "Alta" if untagged else "Info", "LGPD / dados sensíveis", f"{len(sens_tables)} tabelas",
           f"{len(sens)} colunas com nomes indicativos de dados pessoais/saúde ({len(untagged)} sem policy tag)",
           "INFORMATION_SCHEMA.COLUMNS (nomes; conteúdo NÃO verificado)",
           "Validar classificação; aplicar policy tags / masking; mapear para tags e column masks no Unity Catalog.", True)
    rap_tables = {r["table_ref"] for r in inv.get("row_access_policies") or []}
    findings.sort(key=lambda f: (SEV_ORDER[f["severity"]], f["category"]))
    return {
        "findings": findings,
        "summary": {"by_severity": dict(Counter(f["severity"] for f in findings)),
                    "lgpd_relevant": sum(f["lgpd_relevant"] for f in findings), **stats,
                    "sensitive_name_columns": len(sens), "sensitive_name_tables": len(sens_tables),
                    "policy_tag_columns": len(inv.get("policy_tag_columns") or []),
                    "tables_with_row_access_policies": len(rap_tables),
                    "vpc_sc_perimeters": len(sec.get("vpc_service_controls") or []),
                    "allowed_domains": sorted(allowed), "allowed_domains_inferred": inferred_domain},
        "sensitive_columns_by_pattern": dict(Counter(c.get("pattern") for c in sens).most_common(20)),
    }
