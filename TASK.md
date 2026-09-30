# TASK: Menus e Botões Mais Largos

## 🎯 Visão Geral da Feature
O menu principal e outras telas do bot estão com aparência "fina" — os botões não preenchem bem a largura do chat. O objetivo é deixar os botões mais largos, preenchendo melhor o espaço horizontal, sem quebra de linha em nenhum texto.

## ⚠️ Restrição Técnica a Considerar
A largura da "bolha" de mensagem de texto no Telegram é controlada automaticamente pelo cliente, com base no conteúdo (a linha mais longa do texto) — o bot não consegue forçar isso além de ajustar o próprio texto. Já a largura dos **botões do teclado inline** é controlável de verdade: quanto menos botões numa mesma linha, mais largo cada um fica (o Telegram estica os botões pra preencher a linha inteira). O foco prático e eficaz dessa tarefa deve ser o **layout de linhas/colunas dos botões**, não tentar forçar largura artificial no texto das mensagens.

## 🛠️ Objetivos e Regras de Negócio (UI/UX)

**1. Menu Principal:**
*   Reduzir a quantidade de botões por linha (ex: se hoje tem 2 por linha, deixar 1 por linha), fazendo cada botão ocupar mais a largura da tela.

**2. Outras Telas:**
*   Aplicar o mesmo princípio nas demais telas do bot que tenham múltiplos botões numa linha e pareçam "finas" (ex: Ver Resumo, Categorias, Gastos Recorrentes) — usando bom senso: nem toda tela precisa virar 1 botão por linha, algumas fazem mais sentido com 2 (ex: pares de ação como "Categorias | Transferir").

**3. Sem Quebra de Linha:**
*   Confirmar que nenhum texto de botão quebra linha ao ficar mais largo.

## 💻 Instruções de Implementação para o `bot.py`
1. Localize o `InlineKeyboardMarkup` do menu principal e ajuste a quantidade de botões por linha.
2. Faça um levantamento das demais telas com múltiplos botões numa linha e proponha, para cada uma, se deve virar 1 por linha ou manter agrupado — sem aplicar ainda.
3. Depois da minha aprovação, aplique as mudanças e teste visualmente no bot de teste (Telegram) para confirmar que ficou mais largo e sem quebra de linha.

## 📋 Regra de Resposta (Economia de Tokens)
Antes de aplicar qualquer código:
1. Liste todas as telas/menus que serão alteradas e a proposta de novo layout (quantos botões por linha em cada uma).
2. Aguarde minha aprovação — é uma mudança visual, quero decidir o layout antes de você aplicar em massa.

## Entregável
Retorne a lista de telas com a proposta de layout e, após aprovação, o código atualizado de cada uma.