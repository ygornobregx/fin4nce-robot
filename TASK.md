# TASK: Unificar o Cálculo de Gasto (com Parcelas) e Corrigir a Renomeação de Categoria

## 🎯 Visão Geral da Feature
Antes de construir o Mini App, os números do bot precisam ser consistentes e confiáveis. Uma análise do código encontrou dois problemas reais: (1) o **Gasto** de uma categoria é calculado de formas diferentes conforme a tela, e as parcelas 2..N de compras parceladas antigas não entram na Sobra do "Ver Resumo"; (2) **renomear uma categoria** não atualiza parcelamentos, transferências nem gastos recorrentes, e esses registros ficam órfãos e somem do cálculo.

## 🐞 Etapa 1 — Gasto Diferente por Tela
**Atual (errado):**
*   Ver Resumo, detalhe da categoria, Resumo Diário e Resumo Mensal contam só `transacoes`. As parcelas 2..N de compras parceladas antigas nunca entram, porque não existem como transação. A Sobra do ciclo fica maior do que deveria.
*   "Ver Meses Anteriores e Futuros" (inclusive "Mês Atual") soma as transações avulsas (excluindo a parcela 1 de cada parcelamento) mais as parcelas expandidas que caem no mês, via `gasto_externo`.
*   Resultado: "Mês Atual" pode diferir do "Ver Resumo" quando há parcelas 2..N no ciclo ou depois de um "Banco Adiou".

**Esperado (correto):** Todas as telas calculam o Gasto da mesma forma, como uma fatura: transações avulsas do ciclo **mais** as parcelas de compras parceladas que caem naquele ciclo (a regra já definida: a parcela de novembro soma ao gasto de novembro).

## 🛠️ Regras de Negócio da Etapa 1
1. **Uma definição de Gasto:** adotar para todas as telas a lógica que a navegação de meses já usa (avulsas, sem a parcela 1 duplicada, mais as parcelas expandidas pelo `ciclo_referencia` dos parcelamentos ativos). Assim "Banco Adiou" funciona igual em todo lugar.
2. **Dentro da função central:** idealmente `calcular_status_categoria` passa a incluir as parcelas por conta própria, eliminando a necessidade do `gasto_externo` e da divergência entre telas.
3. **Resumo Diário:** o "Gasto de hoje" continua sendo só o que foi registrado hoje. Apenas a Sobra passa a considerar as parcelas do ciclo.
4. **Resumo Mensal e painel:** o ranking de maiores gastos, o total e a Sobra do Mês (`montar_texto_painel`) passam a refletir o novo Gasto, sem lógica própria.
5. **Parcelamentos inativos** (`ativo = 0`) não contam.
6. **Sem dupla contagem:** a parcela 1 não pode ser contada duas vezes (como transação e como parcela).
7. **Aviso esperado:** em produção a Sobra pode diminuir quando houver parcelas antigas no ciclo atual. Isso é o comportamento correto.

## 🐞 Etapa 2 — Renomear Categoria Quebra os Vínculos
**Atual (errado):** `salvar_novo_nome` atualiza só `transacoes`. Os vínculos por nome em `parcelamentos`, `transferencias` (origem e destino) e `gastos_recorrentes` ficam apontando para o nome antigo. Depois do rename, parcelas e transferências dessa categoria somem do cálculo.

**Esperado (correto):** Renomear atualiza **todas** as tabelas que referenciam a categoria pelo nome, de forma atômica (tudo ou nada, numa única transação).

## 🛠️ Regras de Negócio da Etapa 2
1. Propagar o novo nome para a própria categoria e para todas as tabelas que a referenciam pelo nome. Enumere todas, procurando cada uso do nome da categoria no código, e não apenas as quatro citadas.
2. Operação atômica: se qualquer atualização falhar, nada é alterado.
3. **Diagnóstico de dados antigos (somente leitura):** escreva uma consulta que liste registros órfãos (que apontam para um nome de categoria que não existe mais para aquele usuário) e relate o resultado. **Não corrija dados de produção sem minha aprovação.**
4. **Excluir categoria (apenas verificar e propor):** descreva o que acontece hoje com parcelamentos ativos, gastos recorrentes e transferências ligados a uma categoria excluída (hoje é um `DELETE` físico). Proponha o comportamento ideal, sem alterar ainda.
5. **Correção definitiva (apenas avaliar e propor):** avalie migrar o vínculo de nome para id da categoria. Não implemente agora.

## 📌 Fora do Escopo (decidir depois)
*   `dia_fechamento` e `orcamento_mensal` sem histórico (mudar o valor reclassifica o passado).
*   Cobranças recorrentes lançadas ficam iguais a gastos avulsos (sem marca nem vínculo com o modelo).
*   Colunas legadas (`categorias.saldo_atual`, `usuarios.ultimo_reset`) e `parcelas_pagas` nunca atualizada.
*   **Melhoria futura:** a transação da parcela 1 é reconhecida por `parcelamentos.data_criacao == transacoes.data_transacao` (mesma categoria). Um gasto avulso registrado no mesmo segundo e na mesma categoria de um parcelamento seria excluído do Gasto. Correção definitiva: coluna de vínculo (`transacoes.parcelamento_id`).

## 💻 Instruções de Implementação para o `bot.py` (em Etapas)
**Etapa 1:**
1. Reproduza no ambiente de teste e anote Gasto/Sobra de uma mesma categoria em cada tela (referências aproximadas, podem ter mudado): Ver Resumo, detalhe da categoria, navegação de meses (inclusive "Mês Atual"), Resumo Diário e Resumo Mensal.
2. Proponha a mudança em `calcular_status_categoria` e seus chamadores. Aguarde aprovação.
3. Implemente e valide estes cenários:
   *   Compra parcelada 3x registrada no ciclo atual, e no ciclo seguinte (para simular, crie no banco de teste um parcelamento com `ciclo_referencia` de um mês anterior, de modo que a parcela 2 caia no ciclo atual).
   *   "Banco Adiou" em uma parcela.
   *   Compras avulsas e parceladas na mesma categoria, com e sem transferências.
   *   Parcelamento inativo.
   *   Em todos: os valores de Ver Resumo, detalhe, "Mês Atual", Resumo Diário (Sobra) e Resumo Mensal batem entre si.

**Etapa 2:** só depois de eu aprovar a Etapa 1.
1. Relate as tabelas e colunas que referenciam categoria por nome e o resultado do diagnóstico de órfãos.
2. Proponha e, depois de aprovado, implemente o rename atômico. Teste renomeando uma categoria com parcelamento, transferência e gasto recorrente, e confira que tudo continua no cálculo.

## 📋 Regra de Resposta (Economia de Tokens)
1. Faça uma etapa por vez: antes de cada código, relate o que encontrou e confirme em poucas linhas o plano.
2. Aguarde minha aprovação antes de aplicar cada etapa.
3. Não execute git add, commit nem push: o commit é feito manualmente por mim.

## Entregável
Por etapa aprovada: o código alterado das funções envolvidas e a tabela de valores por tela nos cenários testados.