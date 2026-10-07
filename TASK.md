# TASK: Cobrança Recorrente Ainda Duplica o Menu Principal (2ª Tentativa)

## 🎯 Visão Geral da Feature
A notificação de cobrança recorrente continua deixando duas mensagens de Menu Principal no chat: o menu antigo (que já estava lá) e o novo (gerado ao clicar em "Menu Principal" na notificação). A primeira tentativa de correção (reaproveitar o mecanismo do Resumo Financeiro Diário) não resolveu. Agora a prioridade é descobrir a causa raiz antes de mexer de novo.

## 🐞 Descrição do Bug
**Atual (errado):** Ao receber a cobrança recorrente e clicar em "Próxima" ou "Menu Principal", a mensagem que já estava no chat (ex: um Menu Principal de horas ou de um dia antes) continua lá. Resultado: duas mensagens de Menu Principal.

**Esperado (correto):** Depois de qualquer interação com a notificação, fica exatamente **uma** mensagem de interface no chat.

## 📎 O Que Já Foi Tentado
*   Reaproveitar o mecanismo de substituição do Resumo Financeiro Diário na cobrança recorrente: **não resolveu**.
*   Horário 05:00 e intervalo de 15 minutos: já aplicados, não fazem parte deste problema.
*   Não repetir a mesma abordagem sem entender por que ela falhou.

## 🛠️ Objetivos e Regras de Negócio (UI/UX)

**1. Nunca Duplicar:**
*   Depois de "Próxima", de "Menu Principal" clicado direto (caso o usuário não queira ver as outras cobranças ou esteja com pressa) ou de "Pular", sobra uma só mensagem de interface no chat.

**2. Mesmo Fluxo do Resumo Financeiro Diário:**
*   Ao receber, a notificação substitui o que estiver no chat. Ao clicar em "Menu Principal", remove a notificação e fica um Menu Principal. Isso vale para a paginação **e** para o clique direto no Menu Principal.

**3. Regra Alternativa Aceita (se for mais simples):**
*   Ao clicar em "Menu Principal" na notificação: se **existir** outra mensagem (um Menu Principal) no chat, apenas apagar a notificação. Se **não existir nenhuma**, a notificação vira o Menu Principal. O chat nunca fica vazio nem com duas.

**4. Paginação Inalterada:**
*   A lógica de "Próxima", o indicador de página e a mensagem única editada a cada cobrança continuam exatamente como estão.

## 🔍 Investigação Obrigatória (causa raiz)
Verifique estas hipóteses antes de propor qualquer correção:
*   **a)** O id do Menu Principal antigo fica guardado só em memória e se perde quando o bot reinicia (o Discloud reinicia o app), ou fica guardado num lugar que o job da cobrança (que roda sozinho, sem interação do usuário) não acessa.
*   **b)** O job da cobrança usa um caminho de envio diferente do Resumo Diário, por exemplo envia mensagem nova sem passar pela função que apaga a anterior, ou usa um id vazio ou desatualizado.
*   **c)** A falha ao apagar está sendo engolida silenciosamente (os logs do bot têm avisos "(ignorado)"). Confira os logs ao disparar a cobrança.
*   **d)** O Menu Principal é enviado em vários pontos do bot sem registrar o id, e o id "ativo" fica desatualizado.

Se a causa for tracking do id, a direção provável de correção é persistir o id da mensagem de interface ativa (por exemplo no banco, usando a rotina de migração automática já existente) e atualizá-lo toda vez que o bot enviar ou editar um menu ou notificação. Proponha isso só depois do diagnóstico.

## 💻 Instruções de Implementação para o `bot.py`
1. Reproduza no ambiente de teste: deixe um Menu Principal no chat, dispare a cobrança recorrente (sugira uma forma temporária de disparar manualmente, sem deixar comando de teste permanente) e observe os logs.
2. Compare o caminho do Resumo Diário (que funciona) com o da cobrança recorrente e relate as diferenças.
3. Identifique qual hipótese é a causa e proponha a correção. Aguarde minha aprovação.
4. Depois de aprovada, implemente e valide estes cenários:
   *   **A)** Menu antigo no chat, clicar em "Próxima" e depois em "Menu Principal": fica um só.
   *   **B)** Menu antigo no chat, clicar direto em "Menu Principal": fica um só.
   *   **C)** Nenhuma mensagem no chat: a notificação vira o Menu Principal.
   *   **D)** Reiniciar o bot entre o menu antigo e a cobrança (simula o reinício do Discloud): continua sem duplicar.

## 📋 Regra de Resposta (Economia de Tokens)
1. Diagnóstico primeiro: diga em poucas linhas qual hipótese confere, antes de qualquer código.
2. Aguarde minha aprovação antes de aplicar.

## Entregável
Diagnóstico da causa raiz e, depois de aprovado, o código atualizado dos trechos envolvidos.