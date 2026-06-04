# Dashboard SUPORTE · ORPEN

Dashboard local (1 arquivo HTML) que reconstrói a visão de suporte da ORPEN a partir
dos dois relatórios brutos: **R97 (atendimentos Orpen)** e **Qualitor (chamados)**.
Sem instalação, sem servidor — abre direto no navegador.

## Como usar

1. Abra `index.html` com duplo-clique (Chrome/Edge/Firefox).
2. Clique em **Relatório ORPEN (R97)** e suba o export (`.csv` ou `.xlsx`).
3. Clique em **Relatório QUALITOR** e suba o export (`.csv` ou `.xlsx`).
4. O dashboard se monta sozinho.

A ordem dos uploads não importa — o app identifica cada relatório pelas colunas.
Para trocar de mês, basta subir os arquivos novos.

## Plug and play

O app funciona com os **exports crus** do sistema. Ele:

- localiza sozinho a linha de cabeçalho (o R97 vem com linhas de título antes);
- tolera variações de nome de coluna (acento, maiúscula, ordem, espaços);
- lê datas em `dd/mm/aaaa` e `dd/mm/aaaa - HH:MM`;
- **deriva** os campos calculados que não existem no cru (ver abaixo).

Quando o arquivo já vem com as colunas prontas (`Agente Finalizador`,
`Dentro do SLA?`, `Dia`), o app usa elas direto; senão, calcula.

## Regras de derivação

1. **Agente Finalizador (Orpen)** — para cada agente, busca a posição em que a
   primeira palavra do nome aparece no texto da coluna `Agentes` (equivalente ao
   `LOCALIZAR` do Excel; ausente = 999). O finalizador é quem tem a **maior
   posição** (apareceu mais à direita na cadeia). Todos ausentes → `Outros`.

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

## Configuração de agentes

Os agentes são **detectados automaticamente**. No painel “⚙ Configurar agentes
exibidos” dá para marcar/desmarcar quem aparece e mapear nomes equivalentes caso o
mesmo agente venha grafado diferente nos dois sistemas. A config vale só na sessão.

## Próximos passos (ideias)

- Puxar os dados direto da API da Orpen / Qualitor em vez de upload manual.
- Filtros clicáveis por data e por agente.
- Exportar o dashboard renderizado em PDF.
