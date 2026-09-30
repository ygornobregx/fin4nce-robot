# TASK: Bug — Menu Principal Não Aparece na Notificação de Resumo Diário

## 🐞 Descrição do Bug
Na notificação de **Resumo Financeiro de Hoje** (o resumo automático enviado diariamente — diferente do Alerta Diário/Financeiro, que já tem outra correção em andamento), ao clicar em "Menu Principal", o bot apaga a mensagem da notificação, mas não mostra nada depois. O chat fica vazio, sem o Menu Principal aparecer.

## 🔍 Comportamento Esperado vs Atual

**Atual (errado):** Clicar em "Menu Principal" → a notificação some → nada aparece depois, chat vazio.

**Esperado (correto):** Clicar em "Menu Principal" → a notificação some → o Menu Principal aparece logo em seguida.

## ⚠️ Cuidado para Não Repetir o Bug de Duplicação
Já corrigimos um bug parecido no Alerta Diário, onde a solução foi só apagar a notificação (porque já existia um Menu Principal fixo em outro lugar do chat). Aqui parece ser diferente: como o resultado atual é "nada aparece", é provável que **não exista** um Menu Principal já visível nesse momento — então a correção certa aqui é apagar a notificação **e também mostrar/enviar o Menu Principal em seguida**, não só apagar.

Antes de implementar, confirme: nesse ponto específico (logo após a notificação de Resumo Diário), já existe algum Menu Principal fixo em algum lugar do chat, ou realmente não tem nenhum? Isso decide se a correção é "só mostrar" ou "mostrar, cuidando pra não duplicar".

## 💻 Instruções de Implementação para o `bot.py`
1. Localize o handler do botão "Menu Principal" especificamente na notificação de **Resumo Financeiro Diário** (não confundir com o do Alerta Diário/Financeiro, que é uma função separada).
2. Confirme o ponto ⚠️ acima antes de aplicar a correção.
3. Ajuste para que, após apagar a mensagem da notificação, o bot **exiba o Menu Principal** (reaproveitando a função central já usada em outros lugares do bot para mostrar o menu).
4. Teste o fluxo completo: recebe o Resumo Diário → clica em Menu Principal → notificação some → Menu Principal aparece, sem duplicar nada.

## 📋 Regra de Resposta (Economia de Tokens)
1. Confirme em 2 linhas o que encontrou sobre o ponto ⚠️ (existe ou não um Menu Principal já visível nesse momento).
2. Aguarde minha aprovação antes de aplicar a correção.

## Entregável
Retorne apenas o código atualizado do handler do botão "Menu Principal" da notificação de Resumo Financeiro Diário.