# Dashboard SUPORTE · ORPEN

Dashboard que reconstrói a visão de suporte da ORPEN a partir de dois relatórios:
**R97 (atendimentos Orpen)** — hoje já vem direto da **API da Orpen** — e
**Qualitor (chamados)** — ainda por upload manual.

## Como usar

1. Instale as dependências do backend (uma vez): `pip install -r backend/requirements.txt`.
2. Configure `backend/.env` (veja `backend/.env.example`) com a URL/credenciais da API Orpen.
3. Suba o servidor: `python backend/app.py`.
4. Abra **http://127.0.0.1:5000/** no navegador (não abra mais o `index.html` por
   duplo-clique — ele agora depende do backend para a busca via API).
5. Escolha a data inicial/final e clique **🔄 Buscar ORPEN (API)** — o backend consulta
   a API, guarda os atendimentos num banco SQLite local (`backend/dashboard.db`,
   deduplicados por Protocolo) e o dashboard carrega o período pedido.
6. Clique em **Relatório QUALITOR** e suba o export (`.csv` ou `.xlsx`) — ainda manual.
7. Clique em **⚙ Mapear agentes** para criar entidades e vincular nomes.
8. O dashboard se monta com os dados dos agentes mapeados.

O upload manual do R97 (**Relatório ORPEN (R97)**) continua disponível como alternativa/
fallback caso a API esteja fora do ar.

## Backend (armazenamento + API)

- `backend/app.py` — servidor Flask local: serve o front-end e expõe:
  - `POST /api/orpen/sync {start, end}` (dd/mm/aaaa) — busca na API da Orpen e grava/atualiza
    no SQLite.
  - `GET /api/orpen/data?start=...&end=...` — devolve os atendimentos já armazenados no período.
  - `GET /api/orpen/log` — histórico das sincronizações (data, período, linhas, sucesso/erro).
- `backend/db.py` — schema SQLite (`orpen_atendimentos`, `sync_log`).
- `backend/orpen_client.py` — chamada HTTP à API da Orpen (`report_json`, reportId 97).
- Credenciais ficam em `backend/.env` (fora do git — veja `.gitignore`).

Sincronizar o mesmo período de novo não duplica linhas — cada atendimento é identificado
pelo **Protocolo** e é sobrescrito com os dados mais recentes.

### Sincronização diária automática

`backend/sync_daily.py` sincroniza um único dia (por padrão, "ontem") sem precisar do
servidor Flask de pé — é pensado para rodar via **cron** no servidor onde isso for
implantado (o app vai rodar num servidor Linux acessível por VPN, não nesta máquina
Windows). Ainda não está agendado; quando o servidor estiver de pé, adicione algo como:

```cron
0 2 * * * cd /caminho/para/backend && /usr/bin/python3 sync_daily.py >> sync_daily.log 2>&1
```

Isso roda todo dia às 2h, sincronizando os atendimentos do dia anterior. Pode testar a
qualquer momento rodando manualmente: `python sync_daily.py` (sincroniza ontem) ou
`python sync_daily.py 24/06/2026` (data específica).

## Plug and play

O app funciona com os **exports crus** do sistema. Ele:

- localiza sozinho a linha de cabeçalho (o R97 vem com linhas de título antes);
- tolera variações de nome de coluna (acento, maiúscula, ordem, espaços);
- lê datas em `dd/mm/aaaa` e `dd/mm/aaaa - HH:MM`;
- **deriva** os campos calculados que não existem no cru (ver abaixo).

Quando o arquivo já vem com as colunas prontas (`Agente Finalizador`,
`Dentro do SLA?`, `Dia`), o app usa elas direto; senão, calcula.

## Regras de derivação

1. **Agente Finalizador (Orpen)** — para cada vínculo da entidade, busca a posição em
   que o nome-fonte aparece no texto da coluna `Agentes` (equivalente ao `LOCALIZAR`
   do Excel). O finalizador é quem tem a **maior posição** (apareceu mais à direita na
   cadeia). Nenhum vínculo encontrado → `Outros`.

2. **Dentro do SLA (Qualitor)** — chamado com `Situação = Encerrado` **e**
   `Encerramento ≤ Previsão de resposta`.

3. **Dia (Qualitor)** — data extraída da coluna `Abertura`.

## Métricas

- **KPIs:** Total Atendimentos Orpen, Satisfação Média, Total Chamados Qualitor,
  Chamados Dentro do SLA, Período.
- **Por agente:** Horas Trabalhadas, SLA de serviço (%), Atendimentos, Satisfação,
  SLA de atendimento (tempo médio de 1ª resposta), Média de Atendimentos/Dia,
  Média de Chamados/Dia.
- **Gráficos:** atendimentos por dia; barras por agente finalizador e por responsável.

## Mapeamento manual de agentes

Os agentes **não são detectados automaticamente** — o usuário cria entidades e vincula
os nomes-fonte de cada sistema. Isso garante controle total sobre quais nomes do Orpen
e do Qualitor pertencem ao mesmo agente.

### Fluxo

1. Após subir pelo menos um relatório, expanda **⚙ Mapear agentes**.
2. Digite o nome de exibição do agente e clique **+ Criar entidade**.
3. No card da entidade, use os seletores para vincular:
   - **Nomes no Orpen** — cada nome-fonte que aparece na coluna `Agentes` do R97.
   - **Nomes no Qualitor** — cada nome-fonte que aparece na coluna `Responsável`.
4. Um mesmo nome-fonte só pode pertencer a uma entidade.
5. Nomes ainda não vinculados aparecem na seção **"sem vínculo"** ao final do painel.

### Exportar / importar mapeamento

- **⬇ Exportar mapeamento** — salva um arquivo `.json` com todas as entidades e
  seus vínculos. Guarde-o junto com os relatórios do mês.
- **⬆ Importar mapeamento** — carrega um `.json` exportado anteriormente. Vínculos
  cujos nomes-fonte não existam nos relatórios carregados são descartados
  silenciosamente.

O mapeamento **não é salvo automaticamente** (sem `localStorage`) — exporte sempre
que quiser preservar a configuração entre sessões.

## Próximos passos (ideias)

- API do Qualitor (ainda não integrada — chamados continuam por upload manual).
- Agendar a sincronização da Orpen automaticamente (frequência a definir — diária?).
- Filtros clicáveis por data e por agente.
- Exportar o dashboard renderizado em PDF.
