# TASK: "Próxima" na Cobrança Recorrente Não Mostra a Segunda Cobrança

## 🎯 Visão Geral da Feature
Quando há duas cobranças recorrentes no mesmo dia, a notificação mostra "1-2". Ao clicar em "Próxima", o bot deveria mostrar a segunda cobrança, mas mostra "Sem mais cobranças recorrentes lançadas hoje" e volta ao Menu Principal. O usuário usa esse resumo com notificação para acompanhar o que está gastando, então ele não pode perder a segunda cobrança.

## 🐞 Descrição do Bug
**Atual (errado):** Com duas cobranças no dia, a mensagem mostra "1-2". Ao clicar em "Próxima", não aparece a segunda cobrança: aparece a mensagem de fim e o bot volta ao Menu Principal.

**Esperado (correto):** Ao clicar em "Próxima", a **mesma mensagem é editada** com as informações da nova cobrança (indicador "2-2"). Só depois da última cobrança vale o fluxo normal: "Próxima" mostra a mensagem de fim por 3.5 segundos e volta ao Menu Principal.

## 🔍 Investigação Obrigatória (causa raiz)
Verifique estas hipóteses e diga qual confere:
*   **a) Tempo:** o indicador usa o total de cobranças programadas para o dia (2), mas a segunda cobrança só é lançada 15 minutos depois da primeira. Se "Próxima" for clicado antes disso, não há segunda cobrança já lançada para mostrar. É a hipótese principal.
*   **b) Estado da paginação:** a lista das cobranças do dia, ou o índice da página atual, não está sendo guardada ou avançada corretamente (por exemplo, guardada só em memória e perdida ao reiniciar o bot, ou o índice não incrementa).
*   **c) Contagem:** a verificação de "não há próxima" compara com a lista errada, ou conta só a cobrança atual.

Reproduza no ambiente de teste com duas cobranças recorrentes no mesmo dia e teste três cenários: (1) clicar em "Próxima" logo depois da primeira cobrança, (2) clicar depois que a segunda já foi lançada, (3) clicar depois de reiniciar o bot.

Esta tarefa pode compartilhar a causa com a tarefa "Cobrança Recorrente Ainda Duplica o Menu Principal" (estado em memória). Se fizer sentido, investigue as duas juntas.

## ⚠️ Decisão a Confirmar (só se a hipótese a confirmar)
Se "Próxima" for clicado enquanto a próxima cobrança ainda espera o horário dela, o que mostrar? Pergunte ao Ygor, apresentando estas opções:
*   **A) Lançar todas as cobranças do dia de uma vez, às 05:00 (recomendado).** Com a mensagem única editada, a segunda cobrança não gera outra notificação, então o espaçamento não ajuda o usuário. Lançar tudo junto deixa o indicador "1-2" sempre verdadeiro e a paginação sempre funcional.
*   **B) Manter o espaçamento e mostrar a próxima cobrança como "agendada para HH:MM".** Ela só é aplicada ao orçamento quando chegar a hora.
*   **C) Manter como está.** Não recomendado, é o bug atual.

## 🛠️ Objetivos e Regras de Negócio (UI/UX)
1. **"Próxima" edita a mensagem:** nunca envia mensagem nova nem gera duplicata. A mensagem é editada com o indicador e as informações da nova cobrança.
2. **Fim das cobranças:** depois da última, o fluxo normal continua (mensagem de fim por 3.5 segundos e volta ao Menu Principal).
3. **Sem mudar o resto:** formato da mensagem, botões, botão "Menu Principal" e horário 05:00 continuam como estão.

## 💻 Instruções de Implementação para o `bot.py`
1. Reproduza o bug nos cenários descritos e relate qual hipótese confere.
2. Se for a hipótese a, pergunte ao Ygor qual opção (A, B ou C) ele quer, antes de mexer no job.
3. Depois de aprovada, implemente a correção, garantindo que "Próxima" sempre edite a mensagem com a cobrança seguinte e que o fim das cobranças siga o fluxo normal.
4. Teste com duas e com três cobranças no mesmo dia, incluindo depois de reiniciar o bot.

## 📋 Regra de Resposta (Economia de Tokens)
1. Diagnóstico primeiro: diga em poucas linhas qual hipótese confere, e me pergunte sobre a decisão A/B/C se for o caso.
2. Aguarde minha aprovação antes de aplicar o código.

## Entregável
Diagnóstico da causa raiz e, depois de aprovado, o código atualizado dos trechos envolvidos (job e handler de "Próxima").