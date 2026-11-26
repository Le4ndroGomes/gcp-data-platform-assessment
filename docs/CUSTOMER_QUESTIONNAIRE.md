# Questionário complementar (para o cliente)

A ferramenta coleta o que é observável no ambiente. Os itens abaixo dependem do negócio e são necessários para fechar
o sizing e o plano de migração. Respostas curtas bastam; indique o responsável quando não souber.

## Requisitos de serviço

| Item | Por que importa | Resposta |
|---|---|---|
| Latência aceitável para dashboards e consultas interativas (por sistema ou relatório) | Define o tamanho dos warehouses e se há necessidade de cache/materialização | |
| Janela máxima das cargas batch (início e fim) | Define o tamanho dos clusters de pipeline | |
| RTO e RPO por domínio de dados | Define estratégia de réplica, backup e DR | |
| Horários e datas críticos (fechamento mensal, campanhas, sazonalidade) | Valida os picos observados no histórico | |

## Dados e governança

| Item | Por que importa | Resposta |
|---|---|---|
| Dono (área e pessoa) dos principais datasets | Priorização, validação de paridade e descarte | |
| Datasets que podem ser descartados ou arquivados | Reduz escopo e custo | |
| Classificação dos dados (pessoais, sensíveis, saúde, financeiros) e normas aplicáveis (LGPD, outras) | Desenho de governança e mascaramento | |
| Requisitos de residência dos dados (países/regiões permitidos) | Escolha da região Databricks; algumas localizações atuais podem não ter região suportada | |
| Requisitos de segurança (CMEK, rede privada, VPC Service Controls, SSO/SCIM) | Desenho da fundação | |

## Consumo e usuários

| Item | Por que importa | Resposta |
|---|---|---|
| Número de usuários por perfil (analistas, engenharia, ciência de dados, executivos) | Sizing e licenciamento | |
| Ferramentas de BI em uso (Power BI, Looker, outras) e relatórios críticos | Escolha do caso de uso da POC | |
| Aplicações que consultam o BigQuery diretamente (APIs, serviços) | Dependências de corte | |

## Orquestração e integrações (itens sem visibilidade na coleta)

| Item | Por que importa | Resposta |
|---|---|---|
| Orquestradores em uso (Composer/Airflow, Dataform, dbt, scripts, outros) e onde rodam | A coleta só enxerga o que as permissões liberam | |
| Origens externas e frequência de carga (SaaS, bancos, arquivos, APIs) | Plano de ingestão e CDC | |
| Ferramentas de terceiros que escrevem no BigQuery (conectores, ETL) | Reapontamento no corte | |

## Projeto de migração

| Item | Por que importa | Resposta |
|---|---|---|
| Prazo, janela de corte e restrições (congelamento, auditorias) | Sequenciamento das ondas | |
| Orçamento ou meta de custo | Dimensionamento e priorização | |
| Time e habilidades (SQL, Spark, Python, dbt, Terraform) | Esforço e treinamento | |
| Critérios de sucesso da POC (paridade de resultado, latência, custo) | Definição do caso de uso e dos benchmarks | |
