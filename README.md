# Dashboard SUPORTE · ORPEN

Dashboard que reconstrói a visão de suporte da ORPEN a partir de dois relatórios:
**R97 (atendimentos Orpen)** e **Qualitor (chamados)** — ambos hoje já vêm direto das
respectivas **APIs**, com upload manual como alternativa/fallback.

## Como usar

1. Instale as dependências do backend (uma vez): `pip install -r backend/requirements.txt`.
2. Configure `backend/.env` (veja `backend/.env.example`) com as URLs/credenciais das APIs
   Orpen e Qualitor.
3. Suba o servidor: `python backend/app.py`.
4. Abra **http://127.0.0.1:5000/** no navegador (não abra mais o `index.html` por
   duplo-clique — ele agora depende do backend para a busca via API).
5. Escolha a data inicial/final e clique **🔄 Buscar ORPEN (API)** — o backend consulta
   a API, guarda os atendimentos num banco SQLite local (`backend/dashboard.db`,
   deduplicados por Protocolo) e o dashboard carrega o período pedido.
6. Clique em **🔄 Buscar QUALITOR (API)** — o backend sincroniza os chamados novos
   (ver detalhes na seção abaixo) e o dashboard carrega o mesmo período escolhido acima.
7. Clique em **⚙ Mapear agentes** para criar entidades e vincular nomes.
8. O dashboard se monta com os dados dos agentes mapeados.

Os uploads manuais (**Relatório ORPEN (R97)** / **Relatório QUALITOR**) continuam
disponíveis como alternativa/fallback caso alguma das APIs esteja fora do ar.

## Backend (armazenamento + API)

- `backend/app.py` — servidor Flask local: serve o front-end e expõe:
  - `POST /api/orpen/sync {start, end}` (dd/mm/aaaa) — busca na API da Orpen e grava/atualiza
    no SQLite.
  - `GET /api/orpen/data?start=...&end=...` — devolve os atendimentos já armazenados no período.
  - `POST /api/qualitor/sync {}` — sincroniza os chamados novos do Qualitor (ver abaixo).
  - `GET /api/qualitor/data?start=...&end=...` — devolve os chamados já armazenados no
    período, filtrando pela data de **Abertura**.
  - `GET /api/orpen/log` — histórico das sincronizações de ambas as fontes (coluna `source`
    distingue `orpen`/`qualitor`): data, período, linhas, sucesso/erro.
- `backend/db.py` — schema SQLite (`orpen_atendimentos`, `qualitor_chamados`,
  `qualitor_sync_state`, `sync_log`).
- `backend/orpen_client.py` — chamada HTTP à API da Orpen (`report_json`, reportId 97).
- `backend/qualitor_client.py` — chamada HTTP à API do Qualitor (login/refresh de token,
  paginação de `/ticket/list`, mapeamento dos campos do ticket para as colunas do export
  manual).
- Credenciais ficam em `backend/.env` (fora do git — veja `.gitignore`).

Sincronizar o mesmo período de novo não duplica linhas — cada atendimento/chamado é
identificado pelo **Protocolo** (Orpen) ou **id do ticket** (Qualitor) e é sobrescrito com
os dados mais recentes.

### Particularidades da API do Qualitor

- **Login por usuário de serviço**: `QUALITOR_USER`/`QUALITOR_PASSWORD` no `.env` são de uma
  conta dedicada ao sistema (não credenciais pessoais). O `access_token` fica só em memória
  do processo; o `refresh_token` é o único persistido em disco, em
  `backend/qualitor_session.json` (fora do git). Se o refresh falhar, o client faz login de
  novo automaticamente.
- **Sem filtro de data/status na API** — `/ticket/list` não aceita filtrar por período nem
  por situação. A sincronização é **incremental por offset**: a lista vem em ordem
  ascendente de id (mais antigos primeiro), então guardamos o offset já sincronizado
  (`qualitor_sync_state`) e cada sync só pagina a partir dali até encontrar uma página mais
  curta que o limite (fim da lista).
  - **Limitação conhecida**: como não há resync de chamados antigos, um chamado que mudar de
    situação/for encerrado bem depois de já ter sido sincronizado não é atualizado
    automaticamente. Se isso importar, será preciso um resync periódico da janela recente
    (não implementado ainda).
- Certificado do host (`172.31.1.81`) não bate com o domínio (`*.rcxit.com.br`) por ser
  acesso via IP interno/VPN — o client desabilita a verificação de TLS especificamente para
  essas chamadas.

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

- Resync periódico da janela recente do Qualitor, para capturar chamados antigos que
  mudaram de situação/foram encerrados depois da sincronização inicial.
- Agendar a sincronização da Orpen e do Qualitor automaticamente (frequência a definir —
  diária?).
- Filtros clicáveis por data e por agente.
- Exportar o dashboard renderizado em PDF.
