# Consultas SQL utilizadas no assessment

Gerado automaticamente por `python3 main.py --print-queries`. Todas as consultas são `SELECT` sobre visões
`INFORMATION_SCHEMA` (ou sobre a tabela de Billing Export já existente), executadas após *dry run* e com
`maximumBytesBilled`. Colunas ausentes em uma região/versão são substituídas por `CAST(NULL AS ...)` após
validação de schema por dry run (`SELECT *` sem custo). Placeholders: `PROJECT_ID`, `REGION`, `DATASET`.

## Modo SQL `features`


### schemata

```sql
SELECT schema_name, location AS location, creation_time AS creation_time,
  last_modified_time AS last_modified_time
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.SCHEMATA
```

### schemata_options

```sql
SELECT schema_name, option_name, option_value FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.SCHEMATA_OPTIONS
WHERE option_name IN ('default_table_expiration_days', 'default_partition_expiration_days', 'max_time_travel_hours', 'storage_billing_model', 'default_kms_key_name', 'is_case_insensitive', 'labels')
```

### tables

```sql
SELECT table_schema, table_name, table_type, creation_time AS creation_time,
  base_table_catalog AS base_table_catalog, base_table_schema AS base_table_schema, base_table_name AS base_table_name,
  snapshot_time_ms AS snapshot_time_ms, is_insertable_into AS is_insertable_into
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.TABLES
```

### table_options

```sql
SELECT table_schema, table_name, option_name, option_value FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.TABLE_OPTIONS
WHERE option_name IN ('expiration_timestamp', 'partition_expiration_days', 'require_partition_filter', 'kms_key_name', 'uris', 'format', 'max_staleness', 'enable_refresh', 'refresh_interval_minutes', 'labels', 'table_format', 'storage_uri', 'file_format')
```

### table_storage

```sql
SELECT table_schema, table_name, table_type AS table_type, creation_time AS creation_time,
  storage_last_modified_time AS storage_last_modified_time, deleted AS deleted, total_rows AS total_rows, total_partitions AS total_partitions, total_logical_bytes AS total_logical_bytes, active_logical_bytes AS active_logical_bytes, long_term_logical_bytes AS long_term_logical_bytes, current_physical_bytes AS current_physical_bytes, total_physical_bytes AS total_physical_bytes, active_physical_bytes AS active_physical_bytes, long_term_physical_bytes AS long_term_physical_bytes, time_travel_physical_bytes AS time_travel_physical_bytes, fail_safe_physical_bytes AS fail_safe_physical_bytes
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.TABLE_STORAGE
```

### columns_agg

```sql
SELECT table_schema, table_name, COUNT(1) AS column_count,
  STRING_AGG(IF(is_partitioning_column = 'YES', column_name, NULL)) AS partition_columns,
  STRING_AGG(IF(clustering_ordinal_position IS NOT NULL, column_name, NULL), ','
             ORDER BY clustering_ordinal_position) AS clustering_columns,
  COUNTIF(STARTS_WITH(data_type, 'STRUCT')) AS struct_columns,
  COUNTIF(STARTS_WITH(data_type, 'ARRAY')) AS array_columns,
  COUNTIF(data_type = 'JSON') AS json_columns,
  COUNTIF(data_type = 'GEOGRAPHY') AS geography_columns
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.COLUMNS GROUP BY 1, 2
```

### column_types

```sql
SELECT REGEXP_EXTRACT(data_type, r'^[A-Z0-9_]+') AS base_type, COUNT(1) AS columns
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.COLUMNS GROUP BY 1
```

### sensitive_name_columns

```sql
SELECT table_schema, table_name, column_name, REGEXP_EXTRACT(LOWER(column_name), r'(?:^|_)(cpf|cnpj|rg|cns|cartao_sus|nome|paciente|nm_pac|mae|nascimento|dt_nasc|sexo|genero|raca|cor|etnia|religiao|telefone|celular|email|e_mail|endereco|logradouro|cep|cid|diagnostico|prontuario|laudo|exame|medicamento|prescricao|crm|hiv|gestante|biometria|saude|doenca|alergia|procedimento)(?:_|$|[0-9])') AS matched_pattern
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.COLUMNS WHERE REGEXP_CONTAINS(LOWER(column_name), r'(?:^|_)(cpf|cnpj|rg|cns|cartao_sus|nome|paciente|nm_pac|mae|nascimento|dt_nasc|sexo|genero|raca|cor|etnia|religiao|telefone|celular|email|e_mail|endereco|logradouro|cep|cid|diagnostico|prontuario|laudo|exame|medicamento|prescricao|crm|hiv|gestante|biometria|saude|doenca|alergia|procedimento)(?:_|$|[0-9])')
```

### policy_tag_columns

```sql
SELECT table_schema, table_name, column_name, field_path, policy_tags
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS WHERE ARRAY_LENGTH(policy_tags) > 0
```

### views

```sql
SELECT table_schema, table_name, use_standard_sql AS use_standard_sql, LENGTH(IFNULL(view_definition, '')) AS f_chars,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bJOIN\b')) AS f_joins,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bCROSS\s+JOIN\b')) AS f_cross_joins,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bON\b[^;]{0,200}?\bOR\b')) AS f_or_in_join,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\(\s*SELECT\b')) AS f_subqueries,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'(?:\bWITH\b|,)\s*`?[A-Z0-9_]+`?\s+AS\s*\(')) AS f_ctes,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bSELECT\s+DISTINCT\b')) AS f_select_distinct,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bSELECT\s+(?:[A-Z0-9_]+\.)?\*')) AS f_select_star,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bOVER\s*\(')) AS f_window_functions,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bUNNEST\s*\(')) AS f_unnest,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b(?:ARRAY|STRUCT)\s*[<(]')) AS f_arrays_structs,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bQUALIFY\b')) AS f_qualify,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bMERGE\b')) AS f_merge,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bEXECUTE\s+IMMEDIATE\b')) AS f_dynamic_sql,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b(?:DECLARE|LOOP|WHILE|REPEAT|BEGIN\s+TRANSACTION|EXCEPTION\s+WHEN)\b')) AS f_procedural,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bLANGUAGE\s+JS\b')) AS f_js_udf,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bREMOTE\s+WITH\s+CONNECTION\b')) AS f_remote_function,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bML\.[A-Z_]+\b')) AS f_bq_ml,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'_TABLE_SUFFIX|\*`')) AS f_wildcard_tables,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b_PARTITION(?:TIME|DATE)\b')) AS f_partition_pseudo_columns,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b(?:SAFE_)?CAST\s*\(')) AS f_casts,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b(?:SAFE_CAST|SAFE\.|FARM_FINGERPRINT|GENERATE_(?:DATE_|TIMESTAMP_)?ARRAY|PARSE_(?:DATE|DATETIME|TIMESTAMP)|FORMAT_(?:DATE|DATETIME|TIMESTAMP)|REGEXP_CONTAINS|JSON_EXTRACT(?:_SCALAR|_ARRAY)?|ST_[A-Z]+|APPROX_[A-Z_]+|NET\.[A-Z_]+|KEYS\.[A-Z_]+|AEAD\.)')) AS f_bq_specific_functions,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bUNION\s+(?:ALL|DISTINCT)\b')) AS f_unions,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bGROUP\s+BY\b')) AS f_group_by,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(view_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bCASE\b')) AS f_case_when
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.VIEWS
```

### materialized_views

```sql
SELECT table_schema, table_name, last_refresh_time AS last_refresh_time, refresh_watermark AS refresh_watermark
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.MATERIALIZED_VIEWS
```

### routines

```sql
SELECT routine_schema, routine_name, routine_type, routine_body AS routine_body, external_language AS external_language,
  data_type AS data_type, created AS created, last_altered AS last_altered,
  connection AS connection, security_type AS security_type, LENGTH(IFNULL(routine_definition, '')) AS f_chars,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bJOIN\b')) AS f_joins,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bCROSS\s+JOIN\b')) AS f_cross_joins,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bON\b[^;]{0,200}?\bOR\b')) AS f_or_in_join,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\(\s*SELECT\b')) AS f_subqueries,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'(?:\bWITH\b|,)\s*`?[A-Z0-9_]+`?\s+AS\s*\(')) AS f_ctes,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bSELECT\s+DISTINCT\b')) AS f_select_distinct,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bSELECT\s+(?:[A-Z0-9_]+\.)?\*')) AS f_select_star,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bOVER\s*\(')) AS f_window_functions,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bUNNEST\s*\(')) AS f_unnest,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b(?:ARRAY|STRUCT)\s*[<(]')) AS f_arrays_structs,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bQUALIFY\b')) AS f_qualify,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bMERGE\b')) AS f_merge,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bEXECUTE\s+IMMEDIATE\b')) AS f_dynamic_sql,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b(?:DECLARE|LOOP|WHILE|REPEAT|BEGIN\s+TRANSACTION|EXCEPTION\s+WHEN)\b')) AS f_procedural,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bLANGUAGE\s+JS\b')) AS f_js_udf,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bREMOTE\s+WITH\s+CONNECTION\b')) AS f_remote_function,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bML\.[A-Z_]+\b')) AS f_bq_ml,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'_TABLE_SUFFIX|\*`')) AS f_wildcard_tables,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b_PARTITION(?:TIME|DATE)\b')) AS f_partition_pseudo_columns,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b(?:SAFE_)?CAST\s*\(')) AS f_casts,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b(?:SAFE_CAST|SAFE\.|FARM_FINGERPRINT|GENERATE_(?:DATE_|TIMESTAMP_)?ARRAY|PARSE_(?:DATE|DATETIME|TIMESTAMP)|FORMAT_(?:DATE|DATETIME|TIMESTAMP)|REGEXP_CONTAINS|JSON_EXTRACT(?:_SCALAR|_ARRAY)?|ST_[A-Z]+|APPROX_[A-Z_]+|NET\.[A-Z_]+|KEYS\.[A-Z_]+|AEAD\.)')) AS f_bq_specific_functions,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bUNION\s+(?:ALL|DISTINCT)\b')) AS f_unions,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bGROUP\s+BY\b')) AS f_group_by,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(routine_definition, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bCASE\b')) AS f_case_when
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.ROUTINES
```

### row_access_policies

```sql
SELECT table_schema, table_name, row_access_policy_name FROM `PROJECT_ID`.`DATASET`.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES
```

### partitions_by_table

```sql
SELECT table_schema, table_name, COUNT(1) AS partitions,
  COUNTIF(partition_id NOT IN ('__NULL__', '__UNPARTITIONED__', '__STREAMING_UNPARTITIONED__')) AS dated_partitions,
  SUM(total_logical_bytes) AS logical_bytes, MAX(last_modified_time) AS last_modified_time
FROM `PROJECT_ID`.`DATASET`.INFORMATION_SCHEMA.PARTITIONS GROUP BY 1, 2
```

### partitions_daily

```sql
SELECT DATE(last_modified_time) AS day, COUNT(1) AS partitions_modified, SUM(total_logical_bytes) AS logical_bytes,
  SUM(total_rows) AS rows_in_modified_partitions
FROM `PROJECT_ID`.`DATASET`.INFORMATION_SCHEMA.PARTITIONS WHERE last_modified_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY) GROUP BY 1
```

### hourly

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT TIMESTAMP_TRUNC(creation_time, HOUR) AS hour, job_type, priority,
  COUNTIF(parent_job_id IS NULL) AS jobs, COUNTIF(IFNULL(statement_type, '') != 'SCRIPT') AS statements,
  COUNTIF(statement_type = 'SCRIPT') AS scripts, COUNTIF(parent_job_id IS NOT NULL) AS child_jobs,
  COUNTIF(failed AND parent_job_id IS NULL) AS failed, COUNTIF(cache_hit) AS cache_hits,
  SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', bytes_processed, 0)) AS bytes_processed,
  SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', bytes_billed, 0)) AS bytes_billed,
  SUM(IF(IFNULL(statement_type, '') != 'SCRIPT' AND reservation_id IS NULL, bytes_billed, 0)) AS on_demand_bytes_billed,
  SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', slot_ms, 0)) AS slot_ms,
  SUM(IF(parent_job_id IS NULL, run_ms, 0)) AS sum_run_ms, MAX(IF(parent_job_id IS NULL, run_ms, NULL)) AS max_run_ms,
  SUM(IF(parent_job_id IS NULL, queue_ms, 0)) AS sum_queue_ms,
  COUNT(DISTINCT principal) AS principals
FROM j GROUP BY 1, 2, 3
```

### duration_histogram

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT job_type, priority, RANGE_BUCKET(run_ms, [100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000, 120000, 300000, 600000, 1800000, 3600000]) AS run_bucket,
  RANGE_BUCKET(queue_ms, [100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000, 120000, 300000, 600000, 1800000, 3600000]) AS queue_bucket, COUNT(1) AS jobs
FROM j WHERE parent_job_id IS NULL AND run_ms IS NOT NULL GROUP BY 1, 2, 3, 4
```

### percentiles

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT job_type, priority, COUNT(1) AS jobs, AVG(run_ms) AS avg_run_ms,
  APPROX_QUANTILES(run_ms, 100)[OFFSET(50)] AS p50_ms, APPROX_QUANTILES(run_ms, 100)[OFFSET(90)] AS p90_ms,
  APPROX_QUANTILES(run_ms, 100)[OFFSET(95)] AS p95_ms, APPROX_QUANTILES(run_ms, 100)[OFFSET(99)] AS p99_ms,
  AVG(queue_ms) AS avg_queue_ms, APPROX_QUANTILES(queue_ms, 100)[OFFSET(95)] AS p95_queue_ms
FROM j WHERE parent_job_id IS NULL AND run_ms IS NOT NULL GROUP BY 1, 2
```

### fingerprints

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
),
agg AS (
  SELECT fp, ANY_VALUE(statement_type) AS statement_type, ANY_VALUE(priority) AS priority, COUNT(1) AS runs,
    COUNT(DISTINCT principal) AS principals, ANY_VALUE(principal) AS sample_principal,
    SUM(bytes_billed) AS bytes_billed, SUM(bytes_processed) AS bytes_processed, SUM(slot_ms) AS slot_ms,
    AVG(run_ms) AS avg_run_ms, MAX(run_ms) AS max_run_ms, APPROX_QUANTILES(run_ms, 100)[OFFSET(95)] AS p95_run_ms,
    COUNTIF(failed) AS failed, COUNTIF(cache_hit) AS cache_hits, MIN(creation_time) AS first_seen,
    MAX(creation_time) AS last_seen, COUNT(DISTINCT DATE(creation_time)) AS active_days,
    ARRAY_CONCAT_AGG(refs LIMIT 10) AS refs, ARRAY_CONCAT_AGG(label_kv LIMIT 5) AS label_kv,
    ANY_VALUE(dest) AS sample_destination, ANY_VALUE(query) AS sample_query
  FROM j WHERE job_type = 'QUERY' AND IFNULL(statement_type, '') != 'SCRIPT' GROUP BY fp
)
SELECT * EXCEPT (sample_query, refs, label_kv),
  LENGTH(IFNULL(sample_query, '')) AS f_chars,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bJOIN\b')) AS f_joins,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bCROSS\s+JOIN\b')) AS f_cross_joins,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bON\b[^;]{0,200}?\bOR\b')) AS f_or_in_join,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\(\s*SELECT\b')) AS f_subqueries,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'(?:\bWITH\b|,)\s*`?[A-Z0-9_]+`?\s+AS\s*\(')) AS f_ctes,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bSELECT\s+DISTINCT\b')) AS f_select_distinct,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bSELECT\s+(?:[A-Z0-9_]+\.)?\*')) AS f_select_star,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bOVER\s*\(')) AS f_window_functions,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bUNNEST\s*\(')) AS f_unnest,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b(?:ARRAY|STRUCT)\s*[<(]')) AS f_arrays_structs,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bQUALIFY\b')) AS f_qualify,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bMERGE\b')) AS f_merge,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bEXECUTE\s+IMMEDIATE\b')) AS f_dynamic_sql,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b(?:DECLARE|LOOP|WHILE|REPEAT|BEGIN\s+TRANSACTION|EXCEPTION\s+WHEN)\b')) AS f_procedural,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bLANGUAGE\s+JS\b')) AS f_js_udf,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bREMOTE\s+WITH\s+CONNECTION\b')) AS f_remote_function,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bML\.[A-Z_]+\b')) AS f_bq_ml,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'_TABLE_SUFFIX|\*`')) AS f_wildcard_tables,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b_PARTITION(?:TIME|DATE)\b')) AS f_partition_pseudo_columns,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b(?:SAFE_)?CAST\s*\(')) AS f_casts,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\b(?:SAFE_CAST|SAFE\.|FARM_FINGERPRINT|GENERATE_(?:DATE_|TIMESTAMP_)?ARRAY|PARSE_(?:DATE|DATETIME|TIMESTAMP)|FORMAT_(?:DATE|DATETIME|TIMESTAMP)|REGEXP_CONTAINS|JSON_EXTRACT(?:_SCALAR|_ARRAY)?|ST_[A-Z]+|APPROX_[A-Z_]+|NET\.[A-Z_]+|KEYS\.[A-Z_]+|AEAD\.)')) AS f_bq_specific_functions,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bUNION\s+(?:ALL|DISTINCT)\b')) AS f_unions,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bGROUP\s+BY\b')) AS f_group_by,
  ARRAY_LENGTH(REGEXP_EXTRACT_ALL(UPPER(REGEXP_REPLACE(REGEXP_REPLACE(IFNULL(sample_query, ''), r'--[^\n]*', ' '), r'(?s)/\*.*?\*/', ' ')), r'\bCASE\b')) AS f_case_when,
  ARRAY(SELECT x FROM UNNEST(refs) x WHERE x IS NOT NULL) AS refs,
  ARRAY(SELECT x FROM UNNEST(label_kv) x WHERE x IS NOT NULL) AS label_kv
FROM agg WHERE TRUE
QUALIFY RANK() OVER (ORDER BY slot_ms DESC) <= 200 OR RANK() OVER (ORDER BY bytes_billed DESC) <= 200
     OR RANK() OVER (ORDER BY runs DESC) <= 200 OR RANK() OVER (ORDER BY avg_run_ms DESC) <= 200
```

### fingerprint_summary

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
),
agg AS (SELECT fp, COUNT(1) AS runs, COUNT(DISTINCT DATE(creation_time)) AS days FROM j
        WHERE job_type = 'QUERY' AND IFNULL(statement_type, '') != 'SCRIPT' GROUP BY fp)
SELECT COUNT(1) AS distinct_fingerprints, COUNTIF(runs > 1) AS recurring_fingerprints,
  COUNTIF(days >= 7) AS fingerprints_7plus_days, SUM(runs) AS query_statements,
  SUM(IF(runs > 1, runs, 0)) AS runs_of_recurring
FROM agg
```

### principals

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT principal, COUNTIF(parent_job_id IS NULL) AS jobs, COUNTIF(job_type = 'QUERY') AS query_jobs,
  COUNTIF(job_type = 'LOAD') AS load_jobs, COUNTIF(job_type IN ('EXTRACT', 'COPY')) AS extract_copy_jobs,
  COUNTIF(statement_type IN ('INSERT', 'UPDATE', 'DELETE', 'MERGE', 'CREATE_TABLE_AS_SELECT', 'CREATE_TABLE',
                             'CREATE_VIEW', 'TRUNCATE_TABLE')) AS write_statements,
  COUNTIF(statement_type IN ('CREATE_MODEL', 'EXPORT_MODEL')) AS ml_statements,
  COUNTIF(statement_type = 'SELECT') AS select_statements,
  SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', bytes_billed, 0)) AS bytes_billed, SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', slot_ms, 0)) AS slot_ms,
  COUNT(DISTINCT fp) AS distinct_fingerprints, COUNTIF(cache_hit) AS cache_hits,
  COUNT(DISTINCT EXTRACT(HOUR FROM creation_time)) AS active_hours, COUNT(DISTINCT DATE(creation_time)) AS active_days,
  MIN(creation_time) AS first_seen, MAX(creation_time) AS last_seen
FROM j GROUP BY principal ORDER BY slot_ms DESC LIMIT 5000
```

### labels

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT kv, principal, COUNT(1) AS jobs, SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', slot_ms, 0)) AS slot_ms,
  SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', bytes_billed, 0)) AS bytes_billed, MAX(creation_time) AS last_seen
FROM j, UNNEST(label_kv) kv GROUP BY 1, 2 ORDER BY jobs DESC LIMIT 5000
```

### tables_read

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT ref AS table_ref, COUNT(1) AS jobs, COUNT(DISTINCT principal) AS principals, MAX(creation_time) AS last_read,
  MIN(creation_time) AS first_read, SUM(bytes_billed) AS attributed_bytes_billed,
  ARRAY_AGG(DISTINCT principal IGNORE NULLS LIMIT 25) AS principal_sample
FROM j, UNNEST(refs) ref WHERE IFNULL(statement_type, '') != 'SCRIPT' GROUP BY 1 ORDER BY jobs DESC LIMIT 2000
```

### writes

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT dest AS table_ref, job_type, IFNULL(statement_type, '') AS statement_type, COUNT(1) AS jobs,
  SUM(slot_ms) AS slot_ms, SUM(bytes_processed) AS bytes_processed, MAX(creation_time) AS last_write,
  COUNT(DISTINCT DATE(creation_time)) AS active_days, ARRAY_AGG(DISTINCT principal IGNORE NULLS LIMIT 10) AS principals,
  ARRAY_CONCAT_AGG(label_kv LIMIT 3) AS label_kv
FROM j WHERE dest IS NOT NULL AND IFNULL(statement_type, '') != 'SCRIPT' GROUP BY 1, 2, 3 LIMIT 50000
```

### edges

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT ref AS source_ref, dest AS target_ref, COUNT(1) AS jobs, MAX(creation_time) AS last_seen,
  ANY_VALUE(statement_type) AS statement_type, COUNT(DISTINCT DATE(creation_time)) AS active_days
FROM j, UNNEST(refs) ref WHERE dest IS NOT NULL AND ref != dest AND IFNULL(statement_type, '') != 'SCRIPT'
GROUP BY 1, 2 LIMIT 100000
```

### by_reservation

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT IFNULL(reservation_id, 'ON_DEMAND') AS reservation_id, IFNULL(edition, '') AS edition,
  COUNT(1) AS jobs, SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', slot_ms, 0)) AS slot_ms, SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', bytes_billed, 0)) AS bytes_billed
FROM j GROUP BY 1, 2
```

### bi_hourly

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT hour, principal, jobs, bytes_billed, slot_ms, ARRAY(SELECT x FROM UNNEST(refs) x WHERE x IS NOT NULL) AS refs
FROM (
  SELECT TIMESTAMP_TRUNC(creation_time, HOUR) AS hour, principal, COUNT(1) AS jobs,
    SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', bytes_billed, 0)) AS bytes_billed, SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', slot_ms, 0)) AS slot_ms,
    ARRAY_CONCAT_AGG(refs LIMIT 50) AS refs
  FROM j WHERE REGEXP_CONTAINS(LOWER(IFNULL(principal, '')), r'(powerbi|pbi|gateway|fabric|powerbi|pbi|looker|tableau|qlik|gateway|metabase|superset)') GROUP BY 1, 2 LIMIT 50000)
```

### timeline

```sql
WITH m AS (
  SELECT TIMESTAMP_TRUNC(period_start, MINUTE) AS minute, SUM(period_slot_ms) / 60000 AS slots,
    COUNT(DISTINCT job_id) AS active_jobs
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_TIMELINE_BY_PROJECT
  WHERE job_creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
    AND period_start >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY) AND IFNULL(statement_type, '') != 'SCRIPT'
  GROUP BY 1
)
SELECT TIMESTAMP_TRUNC(minute, HOUR) AS hour, SUM(slots) / 60 AS avg_slots, MAX(slots) AS peak_minute_slots,
  APPROX_QUANTILES(slots, 100)[OFFSET(95)] AS p95_active_minute_slots, MAX(active_jobs) AS peak_concurrent_jobs,
  AVG(active_jobs) AS avg_concurrent_jobs_active_minutes, COUNT(1) AS active_minutes
FROM m GROUP BY 1
```

### billing

```sql
SELECT invoice.month AS month, service.description AS service, SUM(cost) AS cost,
  SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS credits, ANY_VALUE(currency) AS currency FROM `BILLING_PROJECT.BILLING_DATASET.gcp_billing_export_resource_v1_XXXXXX_XXXXXX_XXXXXX` WHERE usage_start_time >= TIMESTAMP(DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH)) AND DATE(_PARTITIONTIME) >= DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH) GROUP BY 1, 2
```

### billing

```sql
SELECT invoice.month AS month, project.id AS project_id, SUM(cost) AS cost,
  SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS credits FROM `BILLING_PROJECT.BILLING_DATASET.gcp_billing_export_resource_v1_XXXXXX_XXXXXX_XXXXXX` WHERE usage_start_time >= TIMESTAMP(DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH)) AND DATE(_PARTITIONTIME) >= DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH) GROUP BY 1, 2
```

### billing

```sql
SELECT service.description AS service, sku.description AS sku, SUM(cost) AS cost,
  SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS credits, SUM(usage.amount) AS usage_amount, ANY_VALUE(usage.unit) AS usage_unit,
  SUM(usage.amount_in_pricing_units) AS pricing_amount, ANY_VALUE(usage.pricing_unit) AS pricing_unit
FROM `BILLING_PROJECT.BILLING_DATASET.gcp_billing_export_resource_v1_XXXXXX_XXXXXX_XXXXXX` WHERE usage_start_time >= TIMESTAMP(DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH)) AND DATE(_PARTITIONTIME) >= DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH) GROUP BY 1, 2 ORDER BY cost DESC LIMIT 1000
```

### billing

```sql
SELECT invoice.month AS month, service.description AS service, sku.description AS sku,
  SUM(cost) AS cost, SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS credits FROM `BILLING_PROJECT.BILLING_DATASET.gcp_billing_export_resource_v1_XXXXXX_XXXXXX_XXXXXX` WHERE usage_start_time >= TIMESTAMP(DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH)) AND DATE(_PARTITIONTIME) >= DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH)
  AND (service.description LIKE '%BigQuery%' OR LOWER(sku.description) LIKE '%egress%'
       OR LOWER(sku.description) LIKE '%network%' OR service.description IN ('Cloud Storage', 'Cloud Composer',
       'Dataflow', 'Cloud Dataproc', 'Dataproc', 'Datastream', 'Cloud Pub/Sub', 'Cloud Data Fusion', 'Dataform',
       'Vertex AI', 'Looker', 'Networking', 'Compute Engine', 'Cloud Run', 'Cloud Functions', 'Cloud SQL'))
GROUP BY 1, 2, 3
```

### billing

```sql
SELECT invoice.month AS month, c.type AS credit_type, SUM(c.amount) AS amount
FROM `BILLING_PROJECT.BILLING_DATASET.gcp_billing_export_resource_v1_XXXXXX_XXXXXX_XXXXXX`, UNNEST(credits) c WHERE usage_start_time >= TIMESTAMP(DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH)) AND DATE(_PARTITIONTIME) >= DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH) GROUP BY 1, 2
```

### billing

```sql
SELECT invoice.month AS month, IFNULL(cost_type, 'regular') AS cost_type, SUM(cost) AS cost
FROM `BILLING_PROJECT.BILLING_DATASET.gcp_billing_export_resource_v1_XXXXXX_XXXXXX_XXXXXX` WHERE usage_start_time >= TIMESTAMP(DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH)) AND DATE(_PARTITIONTIME) >= DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH) GROUP BY 1, 2
```

### billing

```sql
SELECT invoice.month AS month, IFNULL(location.region, location.location) AS region,
  SUM(cost) AS cost FROM `BILLING_PROJECT.BILLING_DATASET.gcp_billing_export_resource_v1_XXXXXX_XXXXXX_XXXXXX` WHERE usage_start_time >= TIMESTAMP(DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH)) AND DATE(_PARTITIONTIME) >= DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH) GROUP BY 1, 2
```

### billing

```sql
SELECT service.description AS service, resource.name AS resource_name,
  project.id AS project_id, SUM(cost) AS cost, SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS credits
FROM `BILLING_PROJECT.BILLING_DATASET.gcp_billing_export_resource_v1_XXXXXX_XXXXXX_XXXXXX` WHERE usage_start_time >= TIMESTAMP(DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH)) AND DATE(_PARTITIONTIME) >= DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH) AND usage_start_time >= TIMESTAMP(DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
  AND resource.name IS NOT NULL GROUP BY 1, 2, 3 ORDER BY cost DESC LIMIT 300
```

## Modo SQL `full`
_Somente com `--include-sql`: o texto SQL é lido para análise com SQLGlot (não é gravado sem `--store-sql`)._

### schemata

```sql
SELECT schema_name, location AS location, creation_time AS creation_time,
  last_modified_time AS last_modified_time
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.SCHEMATA
```

### schemata_options

```sql
SELECT schema_name, option_name, option_value FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.SCHEMATA_OPTIONS
WHERE option_name IN ('default_table_expiration_days', 'default_partition_expiration_days', 'max_time_travel_hours', 'storage_billing_model', 'default_kms_key_name', 'is_case_insensitive', 'labels')
```

### tables

```sql
SELECT table_schema, table_name, table_type, creation_time AS creation_time,
  base_table_catalog AS base_table_catalog, base_table_schema AS base_table_schema, base_table_name AS base_table_name,
  snapshot_time_ms AS snapshot_time_ms, is_insertable_into AS is_insertable_into, ddl AS ddl
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.TABLES
```

### table_options

```sql
SELECT table_schema, table_name, option_name, option_value FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.TABLE_OPTIONS
WHERE option_name IN ('expiration_timestamp', 'partition_expiration_days', 'require_partition_filter', 'kms_key_name', 'uris', 'format', 'max_staleness', 'enable_refresh', 'refresh_interval_minutes', 'labels', 'table_format', 'storage_uri', 'file_format')
```

### table_storage

```sql
SELECT table_schema, table_name, table_type AS table_type, creation_time AS creation_time,
  storage_last_modified_time AS storage_last_modified_time, deleted AS deleted, total_rows AS total_rows, total_partitions AS total_partitions, total_logical_bytes AS total_logical_bytes, active_logical_bytes AS active_logical_bytes, long_term_logical_bytes AS long_term_logical_bytes, current_physical_bytes AS current_physical_bytes, total_physical_bytes AS total_physical_bytes, active_physical_bytes AS active_physical_bytes, long_term_physical_bytes AS long_term_physical_bytes, time_travel_physical_bytes AS time_travel_physical_bytes, fail_safe_physical_bytes AS fail_safe_physical_bytes
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.TABLE_STORAGE
```

### columns_agg

```sql
SELECT table_schema, table_name, COUNT(1) AS column_count,
  STRING_AGG(IF(is_partitioning_column = 'YES', column_name, NULL)) AS partition_columns,
  STRING_AGG(IF(clustering_ordinal_position IS NOT NULL, column_name, NULL), ','
             ORDER BY clustering_ordinal_position) AS clustering_columns,
  COUNTIF(STARTS_WITH(data_type, 'STRUCT')) AS struct_columns,
  COUNTIF(STARTS_WITH(data_type, 'ARRAY')) AS array_columns,
  COUNTIF(data_type = 'JSON') AS json_columns,
  COUNTIF(data_type = 'GEOGRAPHY') AS geography_columns
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.COLUMNS GROUP BY 1, 2
```

### column_types

```sql
SELECT REGEXP_EXTRACT(data_type, r'^[A-Z0-9_]+') AS base_type, COUNT(1) AS columns
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.COLUMNS GROUP BY 1
```

### sensitive_name_columns

```sql
SELECT table_schema, table_name, column_name, REGEXP_EXTRACT(LOWER(column_name), r'(?:^|_)(cpf|cnpj|rg|cns|cartao_sus|nome|paciente|nm_pac|mae|nascimento|dt_nasc|sexo|genero|raca|cor|etnia|religiao|telefone|celular|email|e_mail|endereco|logradouro|cep|cid|diagnostico|prontuario|laudo|exame|medicamento|prescricao|crm|hiv|gestante|biometria|saude|doenca|alergia|procedimento)(?:_|$|[0-9])') AS matched_pattern
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.COLUMNS WHERE REGEXP_CONTAINS(LOWER(column_name), r'(?:^|_)(cpf|cnpj|rg|cns|cartao_sus|nome|paciente|nm_pac|mae|nascimento|dt_nasc|sexo|genero|raca|cor|etnia|religiao|telefone|celular|email|e_mail|endereco|logradouro|cep|cid|diagnostico|prontuario|laudo|exame|medicamento|prescricao|crm|hiv|gestante|biometria|saude|doenca|alergia|procedimento)(?:_|$|[0-9])')
```

### policy_tag_columns

```sql
SELECT table_schema, table_name, column_name, field_path, policy_tags
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.COLUMN_FIELD_PATHS WHERE ARRAY_LENGTH(policy_tags) > 0
```

### views

```sql
SELECT table_schema, table_name, use_standard_sql AS use_standard_sql, view_definition
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.VIEWS
```

### materialized_views

```sql
SELECT table_schema, table_name, last_refresh_time AS last_refresh_time, refresh_watermark AS refresh_watermark
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.MATERIALIZED_VIEWS
```

### routines

```sql
SELECT routine_schema, routine_name, routine_type, routine_body AS routine_body, external_language AS external_language,
  data_type AS data_type, created AS created, last_altered AS last_altered,
  connection AS connection, security_type AS security_type, routine_definition AS routine_definition, ddl AS ddl
FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.ROUTINES
```

### row_access_policies

```sql
SELECT table_schema, table_name, row_access_policy_name, filter_predicate FROM `PROJECT_ID`.`DATASET`.INFORMATION_SCHEMA.ROW_ACCESS_POLICIES
```

### partitions_by_table

```sql
SELECT table_schema, table_name, COUNT(1) AS partitions,
  COUNTIF(partition_id NOT IN ('__NULL__', '__UNPARTITIONED__', '__STREAMING_UNPARTITIONED__')) AS dated_partitions,
  SUM(total_logical_bytes) AS logical_bytes, MAX(last_modified_time) AS last_modified_time
FROM `PROJECT_ID`.`DATASET`.INFORMATION_SCHEMA.PARTITIONS GROUP BY 1, 2
```

### partitions_daily

```sql
SELECT DATE(last_modified_time) AS day, COUNT(1) AS partitions_modified, SUM(total_logical_bytes) AS logical_bytes,
  SUM(total_rows) AS rows_in_modified_partitions
FROM `PROJECT_ID`.`DATASET`.INFORMATION_SCHEMA.PARTITIONS WHERE last_modified_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY) GROUP BY 1
```

### hourly

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT TIMESTAMP_TRUNC(creation_time, HOUR) AS hour, job_type, priority,
  COUNTIF(parent_job_id IS NULL) AS jobs, COUNTIF(IFNULL(statement_type, '') != 'SCRIPT') AS statements,
  COUNTIF(statement_type = 'SCRIPT') AS scripts, COUNTIF(parent_job_id IS NOT NULL) AS child_jobs,
  COUNTIF(failed AND parent_job_id IS NULL) AS failed, COUNTIF(cache_hit) AS cache_hits,
  SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', bytes_processed, 0)) AS bytes_processed,
  SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', bytes_billed, 0)) AS bytes_billed,
  SUM(IF(IFNULL(statement_type, '') != 'SCRIPT' AND reservation_id IS NULL, bytes_billed, 0)) AS on_demand_bytes_billed,
  SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', slot_ms, 0)) AS slot_ms,
  SUM(IF(parent_job_id IS NULL, run_ms, 0)) AS sum_run_ms, MAX(IF(parent_job_id IS NULL, run_ms, NULL)) AS max_run_ms,
  SUM(IF(parent_job_id IS NULL, queue_ms, 0)) AS sum_queue_ms,
  COUNT(DISTINCT principal) AS principals
FROM j GROUP BY 1, 2, 3
```

### duration_histogram

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT job_type, priority, RANGE_BUCKET(run_ms, [100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000, 120000, 300000, 600000, 1800000, 3600000]) AS run_bucket,
  RANGE_BUCKET(queue_ms, [100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000, 120000, 300000, 600000, 1800000, 3600000]) AS queue_bucket, COUNT(1) AS jobs
FROM j WHERE parent_job_id IS NULL AND run_ms IS NOT NULL GROUP BY 1, 2, 3, 4
```

### percentiles

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT job_type, priority, COUNT(1) AS jobs, AVG(run_ms) AS avg_run_ms,
  APPROX_QUANTILES(run_ms, 100)[OFFSET(50)] AS p50_ms, APPROX_QUANTILES(run_ms, 100)[OFFSET(90)] AS p90_ms,
  APPROX_QUANTILES(run_ms, 100)[OFFSET(95)] AS p95_ms, APPROX_QUANTILES(run_ms, 100)[OFFSET(99)] AS p99_ms,
  AVG(queue_ms) AS avg_queue_ms, APPROX_QUANTILES(queue_ms, 100)[OFFSET(95)] AS p95_queue_ms
FROM j WHERE parent_job_id IS NULL AND run_ms IS NOT NULL GROUP BY 1, 2
```

### fingerprints

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
),
agg AS (
  SELECT fp, ANY_VALUE(statement_type) AS statement_type, ANY_VALUE(priority) AS priority, COUNT(1) AS runs,
    COUNT(DISTINCT principal) AS principals, ANY_VALUE(principal) AS sample_principal,
    SUM(bytes_billed) AS bytes_billed, SUM(bytes_processed) AS bytes_processed, SUM(slot_ms) AS slot_ms,
    AVG(run_ms) AS avg_run_ms, MAX(run_ms) AS max_run_ms, APPROX_QUANTILES(run_ms, 100)[OFFSET(95)] AS p95_run_ms,
    COUNTIF(failed) AS failed, COUNTIF(cache_hit) AS cache_hits, MIN(creation_time) AS first_seen,
    MAX(creation_time) AS last_seen, COUNT(DISTINCT DATE(creation_time)) AS active_days,
    ARRAY_CONCAT_AGG(refs LIMIT 10) AS refs, ARRAY_CONCAT_AGG(label_kv LIMIT 5) AS label_kv,
    ANY_VALUE(dest) AS sample_destination, ANY_VALUE(query) AS sample_query
  FROM j WHERE job_type = 'QUERY' AND IFNULL(statement_type, '') != 'SCRIPT' GROUP BY fp
)
SELECT * EXCEPT (sample_query, refs, label_kv),
  SUBSTR(sample_query, 1, 100000) AS sample_query,
  ARRAY(SELECT x FROM UNNEST(refs) x WHERE x IS NOT NULL) AS refs,
  ARRAY(SELECT x FROM UNNEST(label_kv) x WHERE x IS NOT NULL) AS label_kv
FROM agg WHERE TRUE
QUALIFY RANK() OVER (ORDER BY slot_ms DESC) <= 200 OR RANK() OVER (ORDER BY bytes_billed DESC) <= 200
     OR RANK() OVER (ORDER BY runs DESC) <= 200 OR RANK() OVER (ORDER BY avg_run_ms DESC) <= 200
```

### fingerprint_summary

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
),
agg AS (SELECT fp, COUNT(1) AS runs, COUNT(DISTINCT DATE(creation_time)) AS days FROM j
        WHERE job_type = 'QUERY' AND IFNULL(statement_type, '') != 'SCRIPT' GROUP BY fp)
SELECT COUNT(1) AS distinct_fingerprints, COUNTIF(runs > 1) AS recurring_fingerprints,
  COUNTIF(days >= 7) AS fingerprints_7plus_days, SUM(runs) AS query_statements,
  SUM(IF(runs > 1, runs, 0)) AS runs_of_recurring
FROM agg
```

### principals

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT principal, COUNTIF(parent_job_id IS NULL) AS jobs, COUNTIF(job_type = 'QUERY') AS query_jobs,
  COUNTIF(job_type = 'LOAD') AS load_jobs, COUNTIF(job_type IN ('EXTRACT', 'COPY')) AS extract_copy_jobs,
  COUNTIF(statement_type IN ('INSERT', 'UPDATE', 'DELETE', 'MERGE', 'CREATE_TABLE_AS_SELECT', 'CREATE_TABLE',
                             'CREATE_VIEW', 'TRUNCATE_TABLE')) AS write_statements,
  COUNTIF(statement_type IN ('CREATE_MODEL', 'EXPORT_MODEL')) AS ml_statements,
  COUNTIF(statement_type = 'SELECT') AS select_statements,
  SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', bytes_billed, 0)) AS bytes_billed, SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', slot_ms, 0)) AS slot_ms,
  COUNT(DISTINCT fp) AS distinct_fingerprints, COUNTIF(cache_hit) AS cache_hits,
  COUNT(DISTINCT EXTRACT(HOUR FROM creation_time)) AS active_hours, COUNT(DISTINCT DATE(creation_time)) AS active_days,
  MIN(creation_time) AS first_seen, MAX(creation_time) AS last_seen
FROM j GROUP BY principal ORDER BY slot_ms DESC LIMIT 5000
```

### labels

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT kv, principal, COUNT(1) AS jobs, SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', slot_ms, 0)) AS slot_ms,
  SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', bytes_billed, 0)) AS bytes_billed, MAX(creation_time) AS last_seen
FROM j, UNNEST(label_kv) kv GROUP BY 1, 2 ORDER BY jobs DESC LIMIT 5000
```

### tables_read

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT ref AS table_ref, COUNT(1) AS jobs, COUNT(DISTINCT principal) AS principals, MAX(creation_time) AS last_read,
  MIN(creation_time) AS first_read, SUM(bytes_billed) AS attributed_bytes_billed,
  ARRAY_AGG(DISTINCT principal IGNORE NULLS LIMIT 25) AS principal_sample
FROM j, UNNEST(refs) ref WHERE IFNULL(statement_type, '') != 'SCRIPT' GROUP BY 1 ORDER BY jobs DESC LIMIT 2000
```

### writes

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT dest AS table_ref, job_type, IFNULL(statement_type, '') AS statement_type, COUNT(1) AS jobs,
  SUM(slot_ms) AS slot_ms, SUM(bytes_processed) AS bytes_processed, MAX(creation_time) AS last_write,
  COUNT(DISTINCT DATE(creation_time)) AS active_days, ARRAY_AGG(DISTINCT principal IGNORE NULLS LIMIT 10) AS principals,
  ARRAY_CONCAT_AGG(label_kv LIMIT 3) AS label_kv
FROM j WHERE dest IS NOT NULL AND IFNULL(statement_type, '') != 'SCRIPT' GROUP BY 1, 2, 3 LIMIT 50000
```

### edges

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT ref AS source_ref, dest AS target_ref, COUNT(1) AS jobs, MAX(creation_time) AS last_seen,
  ANY_VALUE(statement_type) AS statement_type, COUNT(DISTINCT DATE(creation_time)) AS active_days
FROM j, UNNEST(refs) ref WHERE dest IS NOT NULL AND ref != dest AND IFNULL(statement_type, '') != 'SCRIPT'
GROUP BY 1, 2 LIMIT 100000
```

### by_reservation

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT IFNULL(reservation_id, 'ON_DEMAND') AS reservation_id, IFNULL(edition, '') AS edition,
  COUNT(1) AS jobs, SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', slot_ms, 0)) AS slot_ms, SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', bytes_billed, 0)) AS bytes_billed
FROM j GROUP BY 1, 2
```

### bi_hourly

```sql
WITH j AS (
  SELECT
    creation_time, start_time, end_time, job_id,
    IFNULL(job_type, 'UNKNOWN') AS job_type, statement_type, IFNULL(priority, 'UNKNOWN') AS priority, state,
    error_result IS NOT NULL AS failed, IFNULL(cache_hit, FALSE) AS cache_hit,
    IFNULL(total_bytes_processed, 0) AS bytes_processed, IFNULL(total_bytes_billed, 0) AS bytes_billed,
    IFNULL(total_slot_ms, 0) AS slot_ms, reservation_id, edition AS edition,
    IFNULL(user_email, principal_subject) AS principal,
    parent_job_id AS parent_job_id,
    TIMESTAMP_DIFF(end_time, start_time, MILLISECOND) AS run_ms,
    TIMESTAMP_DIFF(start_time, creation_time, MILLISECOND) AS queue_ms,
    COALESCE(query_info.query_hashes.normalized_literals, TO_HEX(MD5(REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(IFNULL(query, '')), r"'[^']*'", '?'), r'\b[0-9]+(?:\.[0-9]+)?\b', '?'), r'\s+', ' ')))) AS fp, ARRAY(SELECT DISTINCT FORMAT('%s.%s.%s', r.project_id, r.dataset_id, r.table_id) FROM UNNEST(referenced_tables) r WHERE r.table_id IS NOT NULL) AS refs, ARRAY(SELECT CONCAT(l.key, '=', IFNULL(l.value, '')) FROM UNNEST(labels) l) AS label_kv, IF(destination_table.table_id IS NULL OR STARTS_WITH(destination_table.dataset_id, '_'), NULL, FORMAT('%s.%s.%s', destination_table.project_id, destination_table.dataset_id, destination_table.table_id)) AS dest, query
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
  WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
)
SELECT hour, principal, jobs, bytes_billed, slot_ms, ARRAY(SELECT x FROM UNNEST(refs) x WHERE x IS NOT NULL) AS refs
FROM (
  SELECT TIMESTAMP_TRUNC(creation_time, HOUR) AS hour, principal, COUNT(1) AS jobs,
    SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', bytes_billed, 0)) AS bytes_billed, SUM(IF(IFNULL(statement_type, '') != 'SCRIPT', slot_ms, 0)) AS slot_ms,
    ARRAY_CONCAT_AGG(refs LIMIT 50) AS refs
  FROM j WHERE REGEXP_CONTAINS(LOWER(IFNULL(principal, '')), r'(powerbi|pbi|gateway|fabric|powerbi|pbi|looker|tableau|qlik|gateway|metabase|superset)') GROUP BY 1, 2 LIMIT 50000)
```

### timeline

```sql
WITH m AS (
  SELECT TIMESTAMP_TRUNC(period_start, MINUTE) AS minute, SUM(period_slot_ms) / 60000 AS slots,
    COUNT(DISTINCT job_id) AS active_jobs
  FROM `PROJECT_ID`.`region-region`.INFORMATION_SCHEMA.JOBS_TIMELINE_BY_PROJECT
  WHERE job_creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
    AND period_start >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY) AND IFNULL(statement_type, '') != 'SCRIPT'
  GROUP BY 1
)
SELECT TIMESTAMP_TRUNC(minute, HOUR) AS hour, SUM(slots) / 60 AS avg_slots, MAX(slots) AS peak_minute_slots,
  APPROX_QUANTILES(slots, 100)[OFFSET(95)] AS p95_active_minute_slots, MAX(active_jobs) AS peak_concurrent_jobs,
  AVG(active_jobs) AS avg_concurrent_jobs_active_minutes, COUNT(1) AS active_minutes
FROM m GROUP BY 1
```
