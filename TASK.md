# TASK: Sobra Divergente entre "Ver Resumo" e "Meses Anteriores e Futuros" (Pista: Transferências)

## 🎯 Visão Geral da Feature
A **Sobra** de uma categoria aparece diferente em "Ver Resumo" e em "Ver Meses Anteriores e Futuros → Mês Atual", sendo que os dois mostram o mesmo mês. O Ygor fez uma transferência de Lazer para Alimentação neste mês e suspeita que seja isso. Além de corrigir o cálculo, decidir se "Mês Atual" faz sentido na navegação de meses.

## 🐞 Descrição do Bug
**Atual (errado):** A Sobra do mês atual em "Ver Resumo" é diferente da mostrada em "Meses Anteriores e Futuros → Mês Atual", pelo menos nas categorias envolvidas na transferência (Lazer e Alimentação).

**Esperado (correto):** Todas as telas que mostram um mês/ciclo (Ver Resumo e a navegação de meses passados e futuros) devem mostrar valores **idênticos** para o mesmo período, incluindo o efeito das transferências.

## 🔗 Relação com Outras Tarefas
*   O mesmo sintoma já foi reportado na tarefa **"Valor Divergente entre Ver Resumo e Ver Meses Anteriores e Futuros (Mês Atual)"**. Verifique se ela foi implementada e por que a divergência continua. Esta tarefa traz uma pista nova: as transferências.
*   Também se liga a **"Transferência entre Categorias (Orçamento/Gasto/Sobra Dinâmicos)"** e **"Fechamento Automático de Fatura"** (a que ciclo cada transferência pertence).

## 🔍 Investigação Obrigatória (causa raiz)
Verifique estas hipóteses e diga qual confere:
*   **a)** A função de "Ver Resumo" considera as transferências (tabela `transferencias`) no orçamento/sobra da categoria, e a tela "Mês Atual" da navegação não considera (ou o contrário).
*   **b)** A transferência é atribuída a um mês/ciclo de forma diferente em cada tela (por exemplo pela data da transferência no mês de calendário numa tela, e pelo ciclo de fatura na outra).
*   **c)** O cálculo existe duplicado em dois lugares, sem usar uma função central única.
*   **d)** Um lado usa valor **guardado** (ex: orçamento ajustado da categoria) e o outro **recalcula** a partir das transações e transferências.

Reproduza no ambiente de teste: crie uma transferência entre duas categorias, compare Orçamento, Gasto e Sobra em "Ver Resumo" e em "Mês Atual" da navegação, e confira também um mês passado e um mês futuro.

## ⚠️ Decisão de Layout (só depois da correção do cálculo)
O "Mês Atual" na navegação repete o que "Ver Resumo" já mostra. Opções:
*   **A) Remover "Mês Atual" da navegação**, deixando só meses anteriores e futuros (recomendado: menos redundância e menos chance de divergir).
*   **B) Manter**, desde que use exatamente a mesma função de cálculo.

Pergunte ao Ygor. Ele pode preferir decidir isso junto da reformulação geral do layout do bot. Nesse caso, entregue só a Etapa 1.

## 🛠️ Objetivos e Regras de Negócio
1. **Uma fonte de cálculo:** Orçamento, Gasto e Sobra de qualquer mês/ciclo, em qualquer tela, vêm da mesma função central, incluindo o efeito das transferências do período.
2. **Regra atual de transferência mantida:** não mude a regra de a qual ciclo a transferência pertence. Apenas relate qual é a regra hoje e aplique-a de forma igual em todas as telas.
3. **Meses passados e futuros:** devem refletir as transferências do ciclo correspondente, também com o cálculo unificado.

## 💻 Instruções de Implementação para o `bot.py` (em Etapas)

**Etapa 1 — Diagnóstico e Correção do Cálculo:**
*   Reproduza, identifique a causa raiz e relate. Depois de aprovado, unifique o cálculo e valide: mês atual, um mês passado e um mês futuro, com e sem transferências, nas duas categorias envolvidas e em uma categoria sem transferência.

**Etapa 2 — Layout (somente após minha decisão):**
*   Se a decisão for a opção A, remova "Mês Atual" da navegação sem quebrar a paginação entre meses nem os botões de voltar.

## 📋 Regra de Resposta (Economia de Tokens)
1. Diagnóstico primeiro: diga em poucas linhas qual hipótese confere, antes de qualquer código.
2. Aguarde minha aprovação antes de cada etapa.
3. Não execute git add, commit nem push: o commit é feito manualmente por mim.

## Entregável
Diagnóstico da causa raiz e, depois de aprovado, o código corrigido das funções envolvidas no cálculo.