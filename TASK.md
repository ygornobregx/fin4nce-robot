# TASK: Editar o Nome da Cobrança Recorrente

## 🎯 Visão Geral da Feature
Hoje, o título que aparece no botão de cada cobrança recorrente na lista (ex: "Bike Itau") não pode ser alterado depois de criado. Adicionar a opção de **editar o nome**, caso o usuário ache melhor ajustá-lo, para ter um controle mais certeiro das cobranças.

## 🛠️ Objetivos e Regras de Negócio (UI/UX)

**1. Novo Botão no Submenu de Edição:**
*   Na tela "Qual edição deseja realizar?", adicionar o botão **"Editar Nome da Cobrança"** junto de "Editar Data da Cobrança", "Editar Valor da Cobrança" e "Excluir Cobrança Recorrente", seguindo o layout de botões já usado nessa tela.

**2. Fluxo:**
*   Ao clicar, o bot pede o novo nome. Reaproveitar o padrão dos fluxos de "Editar Data" e "Editar Valor": mesmo tipo de entrada, mesmo botão de voltar e mesmo tratamento das mensagens do chat (apagar o prompt e a resposta do usuário, se for o padrão dos outros fluxos).
*   Depois de salvar, mostrar **"Gasto Recorrente Atualizado com Sucesso!"** e voltar à lista, já com o novo nome no botão.

**3. Validações do Nome:**
*   Nome vazio é rejeitado.
*   Tamanho máximo razoável (sugerir cerca de 30 caracteres), para o botão não quebrar linha. Se passar, avisar e pedir de novo.

**4. Onde o Novo Nome Aparece:**
*   No botão da lista, no topo da tela de detalhes e nas próximas notificações de cobrança, todos lendo do mesmo campo.
*   Transações já lançadas por essa cobrança **não são alteradas**: o histórico continua como estava.

**5. Sem Mudanças no Banco (a confirmar):**
*   O nome já existe como campo da cobrança, então nenhuma coluna nova deve ser necessária. Se for preciso, usar a rotina de migração automática já existente.

## ⚠️ Decisão a Confirmar
*   **Nome repetido na mesma categoria:** bloquear (recomendado, evita confusão na lista de botões) ou permitir? Pergunte ao Ygor antes de implementar.

## 💻 Instruções de Implementação para o `bot.py`
1. Localize a função que monta a tela "Qual edição deseja realizar?" e os handlers de "Editar Data" e "Editar Valor".
2. Adicione o botão "Editar Nome da Cobrança", o novo estado do fluxo e o handler, reaproveitando o padrão desses fluxos.
3. Atualize o nome no banco apenas na cobrança recorrente, sem tocar em transações já lançadas.
4. Aplique as validações da regra 3 e a decisão sobre nomes repetidos.
5. Teste: renomear e conferir o novo nome na lista, na tela de detalhes e na próxima notificação; testar nome vazio, nome muito longo e nome repetido.

## 📋 Regra de Resposta (Economia de Tokens)
1. Antes de qualquer código, confirme em 2 linhas quais funções e estados serão alterados e me pergunte sobre o ponto marcado com ⚠️.
2. Aguarde minha aprovação antes de aplicar.

## Entregável
Retorne apenas o código novo ou alterado: o botão no submenu, o handler de edição do nome e as validações.