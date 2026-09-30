# TASK: Correção do Fechamento Automático de Fatura (Ciclo por Dia de Fechamento)

## 🎯 Visão Geral da Feature
O bot permite que cada usuário configure um **dia de fechamento** (`dia_fechamento`) que define quando o ciclo de gastos vira — como uma fatura de cartão de crédito de verdade, não o mês do calendário. Um gasto deve ser atribuído ao ciclo que ainda está "aberto" no momento em que é registrado, e o ciclo deve avançar automaticamente assim que a data passar do dia de fechamento configurado — mesmo que o mês do calendário ainda não tenha virado.

**Exemplo 1:** Fatura fecha dia 5. Gastei dia 2 de março, mas a fatura de fevereiro ainda não fechou (fecha só dia 5) — esse gasto ainda é de fevereiro.

**Exemplo 2:** Fatura fecha dia 29. Assim que o dia 29 passa, o ciclo já deve virar pro mês seguinte — mesmo estando ainda dentro do mesmo mês do calendário — porque qualquer gasto depois do dia 29 não é mais do ciclo que acabou de fechar.

## 🚨 Bug Confirmado em Produção (dados reais)
- `dia_fechamento` do usuário = 29
- A data atual já passou do dia 29 de setembro
- O bot **ainda mostra "Setembro"** como ciclo ativo, não gerou "Outubro"
- Mas os gastos **já aparecem zerados** — indica uma transição parcial/quebrada, não uma simples falta de atualização

## ⚠️ Verificação Urgente Antes de Codar
Antes de mexer em qualquer lógica, é importante entender exatamente o estado atual no banco de produção. Rode no SQL console do Turso (banco `fin4nce`):

```sql
SELECT * FROM usuarios WHERE user_id = 7803589807;
SELECT * FROM categorias WHERE user_id = 7803589807;
```

Reporte os valores exatos de qualquer campo relacionado a mês/ciclo ativo, antes de decidir a correção.

## 🛠️ Objetivos e Regras de Negócio

**1. Ciclo Baseado no Dia de Fechamento, não no Calendário:**
*   Toda lógica de "em qual mês esse gasto conta" deve usar o `dia_fechamento` do usuário como referência, não o mês do calendário puro.

**2. Avanço Automático e Confiável:**
*   Assim que a data atual passar do `dia_fechamento`, o ciclo ativo deve avançar para o próximo automaticamente — sem depender de migração manual ou de alguém perceber o problema.
*   Essa verificação deve rodar de forma confiável sempre que o bot iniciar e/ou em pontos-chave (registrar gasto, ver resumo), garantindo que o estado nunca fique "preso" num ciclo antigo.

**3. Autocorreção ao Subir para Produção:**
*   Ao migrar essa correção para produção, o bot deve **detectar sozinho** que o ciclo está desatualizado (como está acontecendo agora) e se autocorrigir, sem precisar de um ajuste manual no banco feito por nós.

**4. Remover "Método de Pagamento Mais Usado" das Faturas Mensais:**
*   Assim como já removemos do Resumo Diário, remover também dessa visão de fatura mensal — ficou feio. Não substituir por nada agora, deixamos pra depois.

## 💻 Instruções de Implementação para o `bot.py` (em Etapas)

**Etapa 1 — Diagnóstico:**
*   Rode a query acima e reporte o estado exato antes de mexer em código.

**Etapa 2 — Lógica Central do Ciclo:**
*   Localize (ou identifique a ausência de) uma função central que calcula "qual é o ciclo/fatura ativo agora" com base no `dia_fechamento`. Verifique se essa lógica existe de forma centralizada e reutilizada, ou se está duplicada/inconsistente em vários lugares (registrar gasto, ver resumo, etc.).

**Etapa 3 — Avanço Automático:**
*   Implemente/corrija a checagem que avança o ciclo automaticamente quando a data passa do dia de fechamento, rodando de forma confiável (na inicialização do bot e/ou via job diário já existente).
*   Inclua a autocorreção do estado já quebrado em produção como parte dessa lógica (detectar e corrigir automaticamente).

**Etapa 4 — Remover Método de Pagamento da Fatura Mensal:**
*   Remover essa linha da visão de fatura/resumo mensal.

## 📋 Regra de Resposta (Economia de Tokens)
1. Rode o diagnóstico (Etapa 1) primeiro e me reporte antes de qualquer código.
2. Confirme em poucas linhas onde está (ou não está) a lógica central de ciclo antes de mexer nela.
3. Aguarde minha aprovação antes de cada etapa seguinte — isso mexe com dados reais que estou usando agora.

## Entregável
Resultado do diagnóstico + código corrigido por etapa aprovada.