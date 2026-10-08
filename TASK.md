# TASK: Botão "Registrar Gasto Recorrente" do Menu Principal Não Responde

## 🎯 Visão Geral da Feature
No Menu Principal, o botão **"Registrar Gasto Recorrente"** (que abre a área para registrar e visualizar os gastos recorrentes ativos) não faz nada ao ser clicado. Todos os outros botões do Menu Principal funcionam normalmente, só esse não responde. Sem esse botão, a funcionalidade de gastos recorrentes fica inacessível.

## 🐞 Descrição do Bug
**Atual (errado):** Ao clicar em "Registrar Gasto Recorrente" no Menu Principal, literalmente nada acontece: nenhuma tela nova, nenhuma mensagem de erro.

**Esperado (correto):** Ao clicar, abre a tela de seleção de categoria ("Em qual categoria deseja adicionar ou visualizar gasto recorrente?"), como no fluxo original.

## 🔍 Investigação Obrigatória (causa raiz)
Verifique estas hipóteses e diga qual confere:
*   **a) `callback_data` diferente:** o `callback_data` do botão no Menu Principal não bate com o padrão do `CallbackQueryHandler` (ou do ponto de entrada do `ConversationHandler`) desse fluxo. Pode ter sido alterado ou renomeado numa tarefa recente.
*   **b) Handler não alcançado:** o handler não está registrado, foi removido ou duplicado, ou outro `CallbackQueryHandler` mais genérico, registrado antes (ordem ou grupo), está capturando o clique.
*   **c) Erro engolido:** o handler dispara, mas dá erro e ele é tratado silenciosamente (os logs do bot têm avisos "(ignorado)" e "Erro não tratado"). Confira os logs no momento do clique.
*   **d) Conflito de estado:** o usuário ficou preso no estado de outro `ConversationHandler` (por exemplo deixado por uma notificação, pela edição de gasto ou pela paginação), que intercepta o clique antes do fluxo recorrente.
*   **e) Regressão recente:** alguma tarefa recente (menu principal, notificações de cobrança recorrente, botão "Editar Gasto", estilo ou largura dos botões) mexeu no menu ou nesse fluxo.

Dicas de como investigar:
*   Adicione um log temporário que mostre o `callback_data` recebido a cada clique, para ver se o clique chega e qual handler o pega.
*   Use o Git (`git diff`, `git log`) para localizar mudanças recentes no Menu Principal e no handler desse botão.
*   Reproduza no ambiente de teste e anote se acontece sempre ou só depois de alguma ação (como receber uma cobrança, usar outro fluxo ou reiniciar o bot).

## ⚠️ Pergunta ao Ygor Antes de Corrigir
*   O bug acontece **sempre** ou só depois de alguma ação específica (receber uma notificação, usar outro botão, reiniciar o bot)? E em qual ambiente foi visto, teste ou produção? Isso ajuda a separar um erro de código de um estado preso.

## 💻 Instruções de Implementação para o `bot.py`
1. Reproduza o bug no ambiente de teste, com o log temporário do `callback_data`.
2. Identifique qual hipótese confere e relate antes de corrigir.
3. Depois de aprovado, corrija a causa raiz. Evite remendos: se for `callback_data` diferente, alinhe os dois lados; se for estado preso, corrija a saída do outro fluxo.
4. Valide: o botão abre a tela de categorias; depois de clicar em "Voltar" o fluxo retorna ao Menu Principal sem duplicata; os outros botões do Menu Principal continuam funcionando.
5. Faça uma auditoria rápida nos outros menus e botões que levam a esse fluxo (por exemplo, voltas de submenus) e liste qualquer um com problema parecido, sem corrigir ainda.

## 📋 Regra de Resposta (Economia de Tokens)
1. Diagnóstico primeiro: diga em poucas linhas qual hipótese confere, e me faça a pergunta do ponto ⚠️ se ainda precisar.
2. Aguarde minha aprovação antes de aplicar a correção.

## Entregável
Diagnóstico da causa raiz e, depois de aprovado, o código corrigido dos trechos envolvidos.