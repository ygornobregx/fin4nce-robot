# TASK: Extrair as Regras de Cálculo para um Módulo Próprio (calculos.py) — Passo 1 do Mini App

## 🎯 Visão Geral da Feature
Hoje as regras que calculam ciclo de fatura, Gasto, Orçamento, Sobra e parcelas estão dentro do `bot.py`, misturadas com os handlers do Telegram. Para que o bot e a futura API do Mini App usem exatamente as mesmas regras, elas devem morar em um módulo próprio, importado pelos dois. **Esta tarefa só reorganiza o código: nenhuma tela, mensagem, botão ou valor pode mudar.** Ela não cria API.

## 🛠️ Objetivos e Regras de Negócio

**1. Criar `calculos.py` na raiz do projeto** com as regras de cálculo, **movidas** do `bot.py` (mover, não reescrever).

**2. O `bot.py` passa a importar do `calculos.py`.** Telas, jobs e handlers continuam chamando as mesmas funções, sem mudança de comportamento.

**3. Regras do módulo:**
*   Não importa `telegram` nem `bot.py`, e não executa nada ao ser importado (sem conexão com o banco, sem iniciar o bot, sem efeitos colaterais).
*   As funções recebem a conexão (`conn`) e os dados necessários por parâmetro, sem depender de variáveis globais do bot.
*   O "agora" (fuso de Brasília) passa a ser um parâmetro opcional, com padrão "agora em Brasília", para permitir testes com datas fixas.
*   Pode ser importado sem `.env` e sem internet.

**4. Fica no `bot.py`:** textos das mensagens, botões, handlers, jobs do Telegram e a abertura da conexão com o banco (`obter_conexao_db`). Funções que misturam cálculo e texto (como `montar_texto_painel`) são divididas: o cálculo vai para o módulo, o texto fica.

**5. Funções candidatas a mover** (confira no código, ajuste a lista e me confirme antes de mover): `calcular_ciclo_fatura`, `obter_ciclo_atual`, `obter_intervalo_ciclo`, `_SQL_FATURA_TRANSACAO`, `_SQL_PARCELAS_EXPANDIDAS`, `_SQL_ITENS_GASTO`, `calcular_gasto_fatura`, `calcular_status_categoria`, `calcular_sobra_categoria`, `categoria_existia_no_mes`, `montar_categorias_fatura`, `calcular_totais_mes` e a parte de dados de `_calcular_dados_resumo_mensal`.

**6. Sem mudança de comportamento:** todos os valores continuam idênticos aos de hoje.

## 🧪 Teste Automático (antes e depois)
*   Recrie o harness de cenários da validação do cálculo (SQLite temporário, sem tocar no Turso) como arquivo do projeto, `testes_calculos.py`, rodável com `python testes_calculos.py`.
*   Rode **antes** de mover (baseline) e **depois**. Os resultados devem ser idênticos.
*   Cenários mínimos (categoria Mercado, limite 1.000, avulso 100, receita 30, fechamento dia 28, ciclo 2026-10), com os valores já validados, Gasto / Sobra:

| Cenário | Gasto / Sobra |
|---|---|
| 1. Compra 3x de 300 no ciclo atual | 200 / 830 |
| 2. Parcela 2 de compra antiga (600 em 3x) | 300 / 730 |
| 3. "Banco Adiou" (400 em 2x) | 100 / 930 |
| 4. Parcelamento inativo | 100 / 930 |
| 5. Tudo junto + transferência de 50 | 400 / 680 |
| 6. Estouro (parcela 2 de 3.000 em 3x) | 1.100 / −70 |

*   Inclua também: fechamento dia 31 em mês curto, virada de ciclo (data antes e depois do fechamento) e categoria criada depois do mês consultado.

## ⚠️ A Verificar e Confirmar Antes de Mover
*   **Lista final de funções** a mover (proponha antes de mexer).
*   **Funções que misturam cálculo e Telegram/texto:** como vai dividir cada uma.
*   **Dependências globais** (fuso horário, constantes) e como serão passadas por parâmetro.
*   **Risco de import circular** entre `bot.py` e `calculos.py`.

## 📌 Fora do Escopo
*   Criar API, rotas ou usar ngrok (passos seguintes).
*   Mudar qualquer regra ou valor.
*   A chamada `app.run_polling(...)` no nível do arquivo `bot.py`: apenas relate se proteger com `if __name__ == "__main__":` faz sentido, sem alterar agora.
*   Exclusão lógica de categoria e vínculo por id.

## 💻 Instruções de Implementação (em Etapas)

**Etapa A: diagnóstico, sem alterar nada.**
1. Rode o harness e guarde o baseline.
2. Liste as funções candidatas, suas dependências e as que misturam cálculo e texto.
3. Proponha o plano de mover e **aguarde minha aprovação**.

**Etapa B: mover.**
1. Crie o `calculos.py` e ajuste os imports no `bot.py`.
2. Remova as definições antigas do `bot.py`, sem deixar código duplicado.

**Etapa C: validar.**
1. Rode `testes_calculos.py` e compare com o baseline: tudo idêntico.
2. Confirme que `python bot.py` importa sem erro (sem erros de import).

**Etapa D:** diga o que devo testar no bot de teste.

## 🚀 Observação de Deploy
O `calculos.py` precisa ir junto para o Discloud. O `testes_calculos.py` não precisa.

## 📋 Regra de Resposta (Economia de Tokens)
1. Diagnóstico primeiro (Etapa A): relate o que encontrou e aguarde minha aprovação antes de mover qualquer coisa.
2. Ao terminar, avise com "Etapa concluída", liste os arquivos alterados e o que devo testar.
3. Não execute git add, commit nem push: o commit é feito manualmente por mim.

## Entregável
O novo `calculos.py`, as alterações de imports no `bot.py`, o `testes_calculos.py` e a tabela de valores antes e depois.