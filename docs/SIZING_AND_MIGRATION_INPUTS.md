# Sizing e planejamento de migração: o que o relatório entrega e o que falta

O relatório é um **insumo** para sizing e planejamento. Ele não converte consumo do BigQuery em consumo do Databricks e
não substitui uma POC. Este documento mostra o que usar de cada arquivo, com que confiança, e o que precisa vir de fora.

## Antes de usar qualquer número

1. Abra **Assessment Coverage** (`assessment_report.md`, seção 13). Números de componentes "Permission Denied" ou "Not Assessed"
   são **limite inferior** ou inexistentes; zero significa "sem visibilidade", não "sem recursos".
2. Cobertura abaixo de ~80% (Complete = 1, Partial = 0,5) indica que o plano ficaria cego em orquestração, rede ou BI.
   Conceda as permissões listadas em `missing_permissions.json` e reexecute os módulos afetados.

## O que o relatório entrega

| Necessidade | Onde | Confiança e observações |
|---|---|---|
| Volume a migrar (lógico e físico), por projeto, dataset, tabela e **localização** | Seções 4; `bigquery_tables.csv` | Alta (metadados de `TABLE_STORAGE`). Delta tende a ficar perto do volume físico; validar na POC |
| Crescimento e ingestão diária | Seção 4 | Só existe com histórico de Cloud Monitoring (`monitoring.viewer`); sem série, nada é estimado |
| Carga de consultas (jobs/dia, latência P50 a P99, fila, cache, falhas) | Seção 5; `workload_analysis.csv` | Alta para o BigQuery. Não inclui o que roda fora dele |
| Sazonalidade e horários de pico | Seção 5 | Alta, na janela coletada (até 180 dias) |
| Concorrência | Seção 5 | **Limite superior** (jobs ativos por minuto). Não converte em clusters |
| Consultas representativas para replay na POC | Seção 11; `databricks_sizing_inputs.json` | Alta; são as top por custo, slots, duração e recorrência |
| Complexidade e anti-patterns de SQL | Seção 6; `query_complexity.csv` | Média: calculada por métricas (modo `features`). Esforço de tradução exige `--include-sql` |
| Ordem de migração por dependência | Seção 6; `data_lineage.json`, `.graphml`, `.mmd` | Média a alta: confirmado por jobs; ciclos e objetos sem consumo são sinalizados |
| Pipelines e dependências externas | Seção 6; `pipeline_inventory.csv` | Só o que as permissões permitem ver (Dataform e DTS sempre; Composer, Dataflow e outros dependem de papéis) |
| Região do Databricks e recursos indisponíveis por região | Seção 10 | Snapshot da documentação; revalidar antes de decidir |
| Segurança e governança (IAM, ACL, CMEK, policy tags, masking) | Seção 9; `security_findings.csv` | Parcial se houver componentes negados |
| Custo | Seção 8 | **Faturado** só com Billing Export; caso contrário, estimativa de preço de lista |
| Readiness | Seção 10 | Indicativo. Dimensões sem evidência são excluídas e os pesos renormalizados |

## O que falta (precisa vir do cliente ou de outra coleta)

| Lacuna | Como resolver |
|---|---|
| Orquestração e serviços não vistos (Composer, Dataflow, Dataproc, Pub/Sub, Datastream, Cloud SQL, rede) | Conceder as permissões do papel e reexecutar; confirmar com o time o que roda fora do GCP |
| BI (modelos, relatórios, DirectQuery, refresh, RLS) | Executar o módulo Power BI (Scanner API ou PBIP/PBIT). Sem isso, não há candidato a POC nem sizing de BI |
| Custo faturado e linha de base para TCO | Habilitar Billing Export no BigQuery |
| Crescimento e ingestão | Conceder `monitoring.viewer`; habilitar `collect_partitions` se aceitável |
| Texto SQL e esforço de tradução | Executar com `--include-sql` (e `--store-sql` se quiser guardar), com autorização do cliente |
| SLAs, RTO/RPO, donos, residência, prazo, orçamento, habilidades | [`CUSTOMER_QUESTIONNAIRE.md`](CUSTOMER_QUESTIONNAIRE.md) |

## Como montar o sizing

1. **Armazenamento:** use o volume físico como ponto de partida e o lógico como teto; some crescimento observado, se houver.
2. **Computação:** **não** converta slots em DBUs. Replique as consultas representativas no Databricks SQL (serverless/Photon),
   meça tempo e DBU, e extrapole pela frequência observada (execuções/dia) e pela sazonalidade.
3. **Concorrência e BI:** a concorrência do relatório é limite superior. Calibre min/max de clusters com teste de carga na POC,
   usando o número de usuários e relatórios do Power BI quando disponível.
4. **Pipelines:** use a frequência e a duração observadas como base e valide custo e janela batch em Lakeflow Jobs.
5. **Cenários:** o relatório traz conservador, esperado e pico com premissas explícitas; ajuste com as respostas do questionário.

## Como montar o plano de migração

1. **Fundação (onda 0):** Unity Catalog, rede, identidades e governança, a partir da seção 9 e dos requisitos de residência e segurança.
2. **POC (onda 1):** escolha o caso de uso com o ranking de candidatos (`poc_candidates.json`, requer Power BI) ou por um domínio de dados
   de menor complexidade e uso relevante.
3. **Demais ondas:** ordene datasets pelo grafo de lineage (do menor ao maior número de dependências) e pela complexidade SQL;
   migre pipelines e BI depois dos dados; deixe streaming, CDC e ML para a fase seguinte; planeje o descomissionamento ao final.
4. **Risco de região:** resolva primeiro as localizações sem região Databricks suportada (seção 10 e "Volume por localização" na seção 4).
5. **Paridade:** defina com o cliente os critérios de validação (contagem, somas, amostras) por dataset antes do corte.
