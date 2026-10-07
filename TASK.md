# TASK: Cobrança Recorrente Sem Mensagens Duplicadas (Mesmo Padrão do Resumo Diário) + Novo Horário

## 🎯 Visão Geral da Feature
Quando a notificação de cobrança recorrente chega, a mensagem que já estava no chat (ex: o Menu Principal antigo) continua lá, gerando duplicata. A notificação deve funcionar como o Resumo Financeiro Diário: ao ser enviada, substitui o que estiver no chat, ficando uma única mensagem. A paginação continua exatamente como está.

## 🐞 Descrição do Bug
**Atual (errado):** A notificação de cobrança recorrente chega e a mensagem anterior permanece no chat.

**Esperado (correto):** A notificação remove qualquer outra mensagem de interface que esteja no chat (ex: Menu Principal antigo), igual ao Resumo Financeiro Diário, e fica sozinha.

## 🛠️ Objetivos e Regras de Negócio (UI/UX)

**1. Mesma Lógica do Resumo Financeiro Diário:**
*   Ao enviar a notificação de cobrança recorrente, remover qualquer outra mensagem de interface que esteja no chat, usando exatamente o mesmo mecanismo que o Resumo Diário já usa para substituir a mensagem do chat.

**2. Paginação Inalterada:**
*   Não mexer na lógica de "Próxima", no indicador de página, na mensagem única editada a cada cobrança nem na mensagem de fim da paginação. Tudo isso continua igual.

**3. Botões:**
*   "Menu Principal"/"Pular" continuam se comportando como nas outras notificações, resultando em uma única mensagem no chat.

**4. Novo Horário e Intervalo:**
*   Horário padrão da cobrança recorrente: **05:00** (antes era 09:00).
*   Intervalo entre cobranças do mesmo dia: **15 minutos** (antes era 30). Não há problema em receber quase juntas, já que existe a paginação. A ordem continua sendo a de criação.
*   Usar o mesmo fuso horário dos outros jobs do bot (o servidor do Discloud pode estar em UTC, então garantir o horário no fuso de Brasília).
*   Estes valores substituem o 09:00 e os 30 minutos da tarefa "Persistência e Paginação das Mensagens de Cobrança Recorrente".

## 💻 Instruções de Implementação para o `bot.py`
1. Leia como o Resumo Financeiro Diário substitui a mensagem do chat e reaproveite exatamente esse mecanismo (mesma função ou mesmo padrão), sem criar uma lógica nova.
2. Aplique esse mecanismo no envio da notificação de cobrança recorrente.
3. Trate a falha ao apagar a mensagem anterior (mensagem já apagada pelo usuário ou antiga demais) sem derrubar o job, do mesmo jeito que o Resumo Diário faz.
4. **Verificar o caso de borda:** depois que o usuário dispensar a notificação ("Menu Principal"/"Pular"), uma cobrança seguinte no mesmo dia **não pode editar** a mensagem que virou menu. Ela deve criar uma notificação nova, aplicando a regra 1. Se o padrão do Resumo Diário já cobre isso, apenas confirme.
5. Ajuste o horário para 05:00 e o intervalo para 15 minutos.
6. Teste no ambiente de teste: a notificação chega e o menu antigo some; a paginação funciona como antes; fica uma só mensagem no chat.

## 📋 Regra de Resposta (Economia de Tokens)
1. Antes de qualquer código, confirme em 2 linhas como o Resumo Diário faz hoje a substituição e como vai aplicar isso na cobrança recorrente.
2. Aguarde minha aprovação antes de aplicar.

## Entregável
Retorne apenas o código atualizado do job e dos trechos envolvidos no envio da notificação de cobrança recorrente.