# TASK: Botão "Editar Gasto" com Opções "Editar Valor" e "Editar Categoria"

## 🎯 Visão Geral da Feature
Ao registrar um gasto e escolher a forma de pagamento, a mensagem final tem o botão **"Editar Gasto"**, que hoje só permite editar o valor. Esse botão passa a abrir duas opções: **"Editar Valor"** (comportamento atual) e **"Editar Categoria"** (novo), para corrigir a categoria do gasto recém-registrado sem precisar apagar e refazer.

## 🛠️ Objetivos e Regras de Negócio (UI/UX)

**1. Botão "Editar Gasto" Abre Duas Opções:**
*   Ao clicar em "Editar Gasto", mostrar uma tela com dois botões: **"Editar Valor"** e **"Editar Categoria"**, mais o botão de voltar no padrão do bot.
*   ✏️ O ticket não define o texto dessa tela. Sugestão: **"O Que Você Deseja Editar Neste Gasto?"**.

**2. "Editar Valor" (comportamento atual, sem mudanças):**
*   Texto: `Atualize o Valor da Sua Cobrança Enviando o Novo Valor...` (manter o texto que já existe hoje, se for diferente).

**3. "Editar Categoria" (novo). Texto da mensagem:**

```
Atualize a Categoria do Gasto Atual de R$ (VALOR DO GASTO) para uma das categorias abaixo:

└ Lazer

└ Alimentação

└ Casa

(demais categorias do usuário, no mesmo formato)

Digite em qual das categorias acima deseja enviar o gasto de: R$ (VALOR DO GASTO)
```

*   A lista traz **todas** as categorias existentes do usuário, uma por bloco, com uma linha em branco entre elas.

**4. Entrada do Usuário:**
*   O usuário digita o nome da categoria, com o mesmo tipo de entrada dos outros campos de edição.
*   A comparação ignora maiúsculas/minúsculas e acentos.
*   Se não encontrar a categoria: avisar, mostrar a lista de novo e manter o fluxo, sem quebrar.
*   Se for a mesma categoria que o gasto já tem: avisar que ele já está nela, sem alterar nada.

**5. Efeito da Troca:**
*   O gasto atual (o recém-registrado) passa para a nova categoria. O Gasto e a Sobra das **duas** categorias (a de origem e a de destino) devem refletir a mudança, usando a função central de cálculo de Orçamento/Gasto/Sobra, sem duplicar lógica. Se existirem valores guardados em vez de calculados, atualizar os dois lados.

**6. Fim do Fluxo e Chat Limpo:**
*   Depois de salvar, terminar do mesmo jeito que o "Editar Valor" termina hoje (reaproveitar), mostrando o gasto já atualizado.
*   Seguir o padrão do bot de chat limpo: substituir/editar a mensagem, sem deixar duplicatas.

## ⚠️ Pontos a Verificar Antes de Implementar
*   **Gasto parcelado:** se o gasto for no crédito, com parcelas, verifique como ele é guardado (transação, parcelamento, parcelas). Trocar a categoria deve atualizar **todos** os registros ligados a ele, não só um. Relate o que encontrou antes de implementar.
*   **"Editar Gasto" hoje:** verifique como esse botão funciona atualmente (estado do fluxo e handler) para não quebrar o "Editar Valor" ao colocar as duas opções.

## 💻 Instruções de Implementação para o `bot.py`
1. Localize o botão "Editar Gasto" no fim do registro de gasto e o handler do "Editar Valor".
2. Crie a tela intermediária com os dois botões e o botão de voltar.
3. Implemente o fluxo de "Editar Categoria": mensagem com a lista de categorias, novo estado de entrada, busca da categoria digitada (sem diferenciar maiúsculas e acentos) e a troca no banco.
4. Garanta o recálculo de Gasto/Sobra das duas categorias e o tratamento do caso parcelado (ponto ⚠️).
5. Teste: gasto à vista e gasto parcelado; categoria digitada com letra diferente e sem acento; categoria inexistente; mesma categoria; conferir Gasto/Sobra das duas categorias antes e depois.

## 📋 Regra de Resposta (Economia de Tokens)
1. Antes de qualquer código, relate o que encontrou sobre os dois pontos ⚠️ e confirme em 2 linhas quais funções serão alteradas.
2. Aguarde minha aprovação antes de aplicar.

## Entregável
Retorne apenas o código novo ou alterado: a tela intermediária, o fluxo "Editar Categoria" e a atualização no banco.