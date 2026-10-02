# TASK: Valor Divergente entre "Ver Resumo" e "Ver Meses Anteriores e Futuros" (Mês Atual)

## 🐞 Descrição do Bug
Dentro de "Ver Resumo" (padrão), o Gasto e a Sobra da categoria "Alimentação" mostram um valor. Ao navegar por "Ver Meses Anteriores e Futuros" e clicar em "Mês Atual", que deveria mostrar essencialmente os mesmos dados (já que ambos representam o mês/ciclo atual), o valor aparece **diferente**. Não está confirmado se isso afeta só "Alimentação" ou todas as categorias.

## 🔍 Comportamento Esperado vs Atual

**Atual (errado):** "Ver Resumo" e "Mês Atual" (dentro da navegação de meses) mostram valores diferentes de Gasto/Sobra para a mesma categoria, no mesmo período.

**Esperado (correto):** Os dois devem mostrar exatamente os mesmos números, já que representam a mesma coisa — o resumo do ciclo/mês atual.

## 🔗 Possível Relação com Outra Tarefa em Andamento
Isso pode ser sintoma do mesmo problema da tarefa **"Correção do Fechamento Automático de Fatura"**: se "Ver Resumo" e a navegação de "Meses Anteriores e Futuros" calculam o período usando lógicas diferentes (uma considerando o `dia_fechamento`, outra usando mês de calendário puro), os valores vão divergir naturalmente. Investigue isso ANTES de tratar como um bug isolado — pode ser resolvido como parte da mesma correção central de ciclo.

## 💻 Instruções de Implementação para o `bot.py`
1. Localize as duas funções: a que monta "Ver Resumo" (padrão) e a que monta "Mês Atual" dentro de "Ver Meses Anteriores e Futuros".
2. Compare como cada uma calcula o período de referência (datas de início/fim do mês/ciclo) e como soma Gasto/Sobra por categoria.
3. Confirme se essa divergência é a mesma causa raiz da tarefa de Fechamento de Fatura — se for, trate como parte daquela correção central (não duplique a lógica).
4. Se for uma causa diferente, identifique e corrija separadamente, garantindo que ambas as telas usem a mesma fonte de cálculo.
5. Teste em TODAS as categorias que tiverem gasto, não só Alimentação, pra confirmar que o problema (ou a correção) é geral.

## 📋 Regra de Resposta (Economia de Tokens)
1. Confirme em 2 linhas se essa divergência tem a mesma causa raiz da tarefa de Fechamento de Fatura ou é algo separado.
2. Aguarde minha aprovação antes de aplicar qualquer correção.

## Entregável
Diagnóstico da causa raiz + código corrigido (unificado com a tarefa de Fechamento de Fatura, se for o caso).