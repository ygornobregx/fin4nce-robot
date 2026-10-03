# TASK: Resumo Mensal Sem Histórico e Com Texto Reformulado

## 🎯 Visão Geral da Feature
A mensagem de Resumo Mensal fica guardada no histórico do chat, diferente do Resumo Financeiro Diário e das outras notificações. Ela deve se comportar igual às demais: ocupar o lugar do Menu Principal, sem deixar duas mensagens no chat, e voltar ao Menu Principal ao clicar em "Pular". Além disso, o texto deve ser reformulado para trazer o ranking dos 3 maiores gastos por categoria, o total gasto no mês inteiro e a sobra do mês.

## 🐞 Descrição do Bug
**Atual (errado):** O Resumo Mensal permanece no histórico do chat depois de enviado.
**Esperado (correto):** Mesmo comportamento do Resumo Financeiro Diário: uma única mensagem no chat, e o botão "Pular" apaga o resumo e mostra o Menu Principal.

## 🛠️ Objetivos e Regras de Negócio (UI/UX)

**1. Comportamento Igual às Outras Notificações:**
*   Reaproveitar exatamente o mesmo mecanismo e o mesmo botão já usados no Resumo Financeiro Diário, sem criar uma lógica nova.
*   Nunca ficar duas mensagens no chat (resumo + menu antigo).

**2. Novo Texto da Mensagem (reproduzir exatamente, inclusive as linhas em branco):**

```
Olá, (USUÁRIO)! Tudo bem? Segue abaixo o seu Resumo Mensal de Gastos no Mês (MÊS VIGENTE)!


1º Maior Gasto no Mês de (MÊS): (CATEGORIA COM MAIOR GASTO)
└ Total Gasto: (VALOR GASTO NA CATEGORIA)

──────────

2º Maior Gasto no Mês de (MÊS): (CATEGORIA)
└ Total Gasto: (VALOR GASTO NA CATEGORIA)

──────────

3º Maior Gasto no Mês de (MÊS): (CATEGORIA)
└ Total Gasto: (VALOR GASTO NA CATEGORIA)

──────────

Total Gasto do Mês Inteiro: (VALOR)
Sobra do Mês: (VALOR)

──────────

Hoje, suas categorias zeram os valores de gastos e voltam à estaca zero, seguindo a lógica de fechamento da sua fatura.
```

**3. Regras de Conteúdo:**
*   `(USUÁRIO)` é o nome cadastrado do usuário.
*   Os 3 maiores gastos são ordenados por valor total gasto na categoria dentro do ciclo.
*   ✏️ **Interpretação aplicada:** a numeração "primeiro a terceiro, tipo 1, 2, 3 em colocação" foi entendida como **1º, 2º, 3º**.
*   Linhas "Total Gasto" sob cada colocação usam o prefixo `└`, igual às outras telas do bot. As duas linhas finais (Total Gasto do Mês Inteiro e Sobra do Mês) ficam sem `└`, como as linhas do topo do Resumo Diário.
*   Se houver menos de 3 categorias com gasto, mostrar apenas as existentes. Se nenhuma categoria teve gasto, omitir os blocos numerados e manter a saudação, os totais e a frase final.
*   O nome do mês sempre capitalizado, em português, e sem quebra de linha (mesmo padrão da tarefa "Nome do Mês Dinâmico").
*   Separadores: reaproveitar o mesmo tipo de separador já usado no Resumo Diário, evitando sempre quebra de linhas.

## ⚠️ Pontos a Confirmar Antes de Implementar
*   **Cálculo da "Sobra do Mês":** é a soma das sobras de todas as categorias (usando a função central de Orçamento/Gasto/Sobra), ou renda mensal menos o total gasto? Confirmar com o Ygor antes de implementar.
*   **Qual mês exibir:** o esperado é o mês do ciclo que acabou de fechar, usando a lógica central de ciclo da tarefa "Fechamento Automático de Fatura". Confirmar com o Ygor.

## 🔗 Dependência
Este resumo depende do cálculo correto do ciclo/fatura (tarefa "Correção do Fechamento Automático de Fatura"). Reutilizar a mesma função central de ciclo e de Orçamento/Gasto/Sobra, sem duplicar lógica.

## 💻 Instruções de Implementação para o `bot.py`
1. Localize a função ou job que envia o Resumo Mensal.
2. Localize como o Resumo Financeiro Diário é enviado (ele já segue o padrão correto) e reaproveite o mesmo mecanismo. Remova qualquer parte do código que mantenha o Resumo Mensal no histórico.
3. Reescreva a montagem do texto seguindo o template acima, calculando o ranking, o total e a sobra pela função central de cálculo.
4. Sugira uma forma de disparar o resumo manualmente no ambiente de teste, para validar sem esperar o dia de fechamento. Não deixar nenhum comando de teste permanente no bot de produção.
5. Teste: o resumo chega, ocupa o lugar do menu, "Pular" volta ao Menu Principal e fica só uma mensagem no chat.

## 📋 Regra de Resposta (Economia de Tokens)
1. Antes de qualquer código, me pergunte sobre os dois pontos marcados com ⚠️.
2. Confirme em 2 linhas como o Resumo Diário faz hoje o envio e qual função envia o Resumo Mensal.
3. Aguarde minha aprovação antes de aplicar as alterações.

## Entregável
Retorne apenas o código atualizado da função de envio e da montagem do texto do Resumo Mensal.