# Regras do Projeto: Agente Financeiro Telegram

## 🛠️ Stack Tecnológica
- **Linguagem:** Python 3.10+
- **Telegram:** `python-telegram-bot` (v20+ async)
- **IA / PLN:** Gemini API (`google-genai`)
- **Banco de Dados:** SQLite (`sqlite3`)
- **Fuso Horário:** `zoneinfo` (`America/Sao_Paulo`)

## ⚠️ REGRAS OBRIGATÓRIAS DE DESENVOLVIMENTO

1. **Fuso Horário (CRÍTICO):**
   - TODAS as datas, timestamps no banco e agendamentos (`JobQueue`) DEVEM usar o fuso de Brasília: `ZoneInfo("America/Sao_Paulo")`. NUNCA utilize UTC puro.

2. **Injeção de Categorias no Prompt:**
   - ANTES de enviar a mensagem do usuário para a IA categorizar um gasto, consulte o banco, busque as categorias ativas do usuário e injete essa lista no prompt.
   - Ordene no prompt que a IA classifique o gasto obrigatoriamente em uma das opções da lista, fazendo o mapeamento de sinônimos.

3. **Regras de Negócio e Lógica:**
   - **Sinais:** Despesas = valores POSITIVOS no banco. Receitas = valores NEGATIVOS.
   - **Chat Limpo:** Priorize editar a mensagem anterior (`edit_message_text`) para não empilhar mensagens no Telegram.
   - **Resiliência:** Chamadas ao Gemini devem usar `gerar_conteudo_com_retry()` para evitar erros temporários de API.

4. **Estrutura do Banco de Dados (SQLite):**
   - `usuarios`: user_id (PK), nome, renda_mensal, dia_fechamento, horario_alerta, ultimo_reset
   - `categorias`: id (PK), user_id, nome, orcamento_mensal, saldo_atual
   - `transacoes`: id (PK), user_id, categoria_nome, valor (float), data_transacao (timestamp BRT)