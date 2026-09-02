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
  - `POST /api/qualitor/horas {protocolos:[...]}` — busca (sob demanda, em paralelo) as horas
    trabalhadas dos chamados informados e grava no SQLite (ver "Horas trabalhadas" abaixo).
  - `GET /api/entities` / `POST /api/entities {entities:[...]}` — carrega/salva o mapeamento
    de agentes (ver "Mapeamento manual de agentes" abaixo).
  - `GET /api/orpen/log` — histórico das sincronizações de ambas as fontes (coluna `source`
    distingue `orpen`/`qualitor`): data, período, linhas, sucesso/erro.
- `backend/db.py` — schema SQLite (`orpen_atendimentos`, `qualitor_chamados`,
  `qualitor_sync_state`, `entities_mapping`, `sync_log`).
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
    automaticamente — isso também afeta o **"Dentro do SLA?"** de chamados ainda abertos
    (ver abaixo): o valor reflete o momento da sincronização, não "agora". Se isso importar,
    será preciso um resync periódico da janela recente (não implementado ainda).
- Certificado do host (`172.31.1.81`) não bate com o domínio (`*.rcxit.com.br`) por ser
  acesso via IP interno/VPN — o client desabilita a verificação de TLS especificamente para
  essas chamadas.
- **Horas trabalhadas** não vem no `/ticket/list` — é preciso um `GET /ticket/{id}/followup`
  por chamado, somando a duração dos followups do tipo `HORAS TRABALHADAS`. Como isso custa
  uma chamada de API por chamado, o front-end só pede (via `POST /api/qualitor/horas`) as
  horas dos chamados cujo Responsável já está vinculado a alguma entidade mapeada — nunca
  para o histórico inteiro. As buscas rodam em paralelo (~15 por vez) e o resultado fica
  salvo no SQLite, então não é repetido depois da primeira vez.
- **Dentro do SLA?** usa o campo `is_overdue` que a própria API já devolve em `/ticket/list`
  (de graça, sem chamada extra) — ele é calculado ao vivo pelo Qualitor, inclusive pra
  chamados ainda abertos (compara com "agora", não só com a data de encerramento). Isso
  substituiu uma comparação manual (`Encerramento ≤ Previsão de resposta`) que só fazia
  sentido pra chamados já fechados e contava todo chamado aberto como "fora do prazo" —
  no mesmo período de teste, a correção mudou o SLA de 37,1% para 83,7%.

### Sincronização diária automática — Orpen

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

### Sincronização diária automática — Qualitor

`backend/sync_qualitor_daily.py` é o equivalente para o Qualitor — também roda sozinho,
sem o Flask de pé. Diferente do Orpen, não recebe data nenhuma: continua de onde o cursor
(`qualitor_sync_state.next_offset`) parou, exatamente como o botão "Buscar QUALITOR (API)"
faz. É um script separado (não um só cobrindo as duas fontes) porque o modelo de
sincronização é fundamentalmente diferente — Orpen é por intervalo de datas, Qualitor é
por cursor/offset.

```cron
10 2 * * * cd /caminho/para/backend && /usr/bin/python3 sync_qualitor_daily.py >> sync_qualitor_daily.log 2>&1
```

Alguns minutos depois do Orpen é só por organização (evitar os dois batendo na rede ao
mesmo tempo); não há dependência real entre os dois. Testar manualmente:
`python sync_qualitor_daily.py`.

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

2. **Dentro do SLA (Qualitor)** — quando os dados vêm da API, usa o `is_overdue` que o
   próprio Qualitor calcula (ver "Particularidades da API do Qualitor" acima). Só é
   **derivado** manualmente (`Encerramento ≤ Previsão de resposta`, sem filtrar por
   Situação) quando falta a coluna `Dentro do SLA?`/`Dentro do SLA` — ou seja, no upload
   manual do export do Qualitor, que não tem `is_overdue`.

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

### Persistência

O mapeamento é salvo automaticamente no SQLite (`entities_mapping`) a cada mudança — criar/
remover entidade, vincular/desvincular nome. Ao abrir a página, o mapeamento salvo é
recarregado sozinho; não depende mais de exportar/importar para sobreviver entre sessões.

### Exportar / importar mapeamento

- **⬇ Exportar mapeamento** — salva um arquivo `.json` com todas as entidades e
  seus vínculos. Útil como backup manual ou para copiar o mapeamento entre ambientes
  (ex.: de um `dashboard.db` de teste para o de produção).
- **⬆ Importar mapeamento** — carrega um `.json` exportado anteriormente. Vínculos
  cujos nomes-fonte não existam nos relatórios carregados são descartados
  silenciosamente.

## Deploy em produção (Docker)

Isto ainda é um runbook para quando o servidor Linux existir — nada aqui foi aplicado de
verdade, é o roteiro a seguir na hora. `Dockerfile` e `docker-compose.yml` na raiz do repo
já implementam o que está descrito abaixo.

- **Imagem**: só as dependências Python (`backend/requirements.txt`) entram na imagem. O
  código (`backend/`, `index.html`, `assets/`) vem de um **bind mount do repo inteiro**
  (`.:/app` no compose) — atualizar o app em produção é `git pull` + `docker compose up -d
  --build` (rebuild só é necessário se `requirements.txt` mudou; senão um `restart` já
  pega o código novo).
- **`.env` / `dashboard.db` / `qualitor_session.json`**: ficam exatamente onde o código já
  espera (`backend/`, ao lado dos scripts) — como o repo inteiro é montado no container,
  nenhuma mudança de caminho ou volume extra é necessária. Todos os três já estão no
  `.gitignore`, então `git pull` no servidor nunca toca neles. Provisionamento inicial:
  `.env` de produção é editado à mão e enviado por `scp`/SSH direto (nunca via git);
  `dashboard.db` começa vazio (`db.init_db()` cria sozinho) ou, pra levar o histórico já
  sincronizado, um `scp` avulso do arquivo atual; `qualitor_session.json` não precisa de
  nada, é criado no primeiro login.
- **Rede**: o container entra na macvlan **`vlan_sede`**, já existente no servidor (mesmo
  padrão dos outros projetos que já rodam lá), com IP fixo **`172.30.8.51`** — sem publicar
  porta via NAT do Docker. `docker-compose.yml` referencia `vlan_sede` como `external: true`
  (a network já precisa existir no host antes do `docker compose up`). Sem nginx na frente;
  não é necessário.
  - **Gotcha conhecido de macvlan**: por padrão o host não enxerga containers na mesma
    macvlan (limitação do driver, não bug) — `curl` direto do host ao IP do container pode
    falhar mesmo com tudo certo; validar de outra máquina na mesma rede/VPN. Não afeta o
    `docker exec` do cron abaixo, que roda dentro do container sem depender da rede.
- **Supervisão**: `restart: unless-stopped` no compose cobre reinício em crash e nas
  próximas subidas do Docker daemon (inclusive no boot da máquina, se o daemon já estiver
  configurado pra subir sozinho) — sem precisar de unit de systemd separada pro app.
- **Cron**: `sync_daily.py` e `sync_qualitor_daily.py` continuam agendados via crontab do
  **host** (não um cron dentro do container), executando dentro do container já em pé com
  `docker exec`:

  ```cron
  0 2 * * * docker exec dashboard-sup python sync_daily.py >> /opt/dashboard-sup/backend/sync_daily.log 2>&1
  10 2 * * * docker exec dashboard-sup python sync_qualitor_daily.py >> /opt/dashboard-sup/backend/sync_qualitor_daily.log 2>&1
  ```

- **Passo a passo (primeira subida)**:
  1. No servidor: `git clone <repo> /opt/dashboard-sup && cd /opt/dashboard-sup`.
  2. Criar `backend/.env` (copiar de `backend/.env.example` e preencher com as credenciais
     reais — `scp`/editor direto no servidor, nunca via git).
  3. Confirmar que a network `vlan_sede` já existe no host (`docker network ls`) e que
     `172.30.8.51` está livre nela.
  4. `docker compose up -d --build`.
  5. Conferir de outra máquina na mesma VPN: `curl http://172.30.8.51:5000/api/session`
     (ou abrir no navegador) — lembrando do gotcha de macvlan acima, testar do host mesmo
     pode não funcionar.
  6. Adicionar as duas linhas de cron acima (`crontab -e` no host).
- **Deploy de código depois da primeira vez**: SSH manual — `git pull` +
  `docker compose up -d --build` (ou só `docker compose restart` se não mexeu em
  dependências). Sem CI/CD por ora (escala do projeto não justifica ainda).

## Próximos passos (ideias)

- Resync periódico da janela recente do Qualitor, para capturar chamados antigos que
  mudaram de situação/foram encerrados depois da sincronização inicial.
- Filtros clicáveis por data (o filtro por agente já existe — seletor "Ver dados de:" acima
  dos KPIs).
- Exportar o dashboard renderizado em PDF.
- **Vincular chamado Qualitor ↔ atendimento Orpen pelo corpo do chamado**: o campo
  `description` do ticket (texto livre, retornado pelo `/ticket/list` — hoje não
  sincronizado, ver "Você tem acesso ao corpo do chamado?") às vezes menciona o protocolo da
  Orpen no meio do texto (ex.: "Verificar o protocolo 440130..."). A ideia é procurar um
  padrão tipo `Protocolo:xxxx` (ou variações — precisa levantar os formatos reais usados)
  nessa descrição pra vincular automaticamente o chamado do Qualitor ao atendimento
  correspondente da Orpen, complementando/reforçando o vínculo hoje feito só por nome de
  agente. Ainda não implementado — precisa decidir se o `description` passa a ser
  sincronizado e onde esse vínculo fica armazenado.
