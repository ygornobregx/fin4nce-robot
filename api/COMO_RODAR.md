# Como rodar a API local do Mini App (PRO-72)

Antes de tudo: o `.env` local deve apontar para o **bot de teste** (`TOKEN`) e para o
banco **`fin4ncetest`** (`TURSO_DATABASE_URL` e `TURSO_AUTH_TOKEN`). Nunca use os
valores de produção. Não copie esses valores para lugar nenhum.

Desde a PRO-88, o `.env` também leva a chave do Gemini (`GEMINI_API_KEY`), lida só no
servidor. Ela não é obrigatória para a API subir: sem ela, só `POST /registro/interpretar`
responde `503 ia_indisponivel`; as leituras e as gravações continuam funcionando.

A tabela `chaves_registro` (PRO-88) é criada pelo bot (`iniciar_banco()`). Suba o bot
desta versão pelo menos uma vez antes de gravar pela API: sem a tabela, `POST /gastos`
e `POST /receitas` dão erro 500.

## 1. Instalar as dependências

No venv do projeto:

```
pip install -r requirements-api.txt
```

## 2. Subir a API

Da raiz do repositório:

```
python -m uvicorn api.main:app --port 8000
```

Rotas:

- `GET /saude`: sem autenticação.
- `GET /me`: nome do usuário (exige `Authorization: tma <initData>`).
- `GET /resumo`: fatura vigente (renda, gasto total, Sobra do Mês = renda − gasto, ou `null`
  antes da data de corte, e o gasto de cada categoria; exige o mesmo cabeçalho).
- `GET /historico`: gasto total de cada mês com movimento, com os futuros marcados
  como `previsto` (exige o mesmo cabeçalho).
- `GET /gastos/recentes`: os 10 últimos gastos com descrição (os registrados a partir da PRO-78),
  com o vínculo da compra parcelada e o `estabelecimento` (PRO-81; `null` quando o gasto não tem
  estabelecimento, nunca um texto substituto) (exige o mesmo cabeçalho).
- `GET /gastos/{id}`: o detalhe de um gasto (PRO-80), com os mesmos campos de `/gastos/recentes` mais
  `parcelas_pagas` (calculado pela fatura vigente; `null` à vista). Gasto inexistente, de outro usuário,
  receita ou sem descrição dá `404 gasto_nao_encontrado` (exige o mesmo cabeçalho).
- `GET /movimentacoes/recentes` (PRO-92): as 10 últimas movimentações **no total**, gastos e receitas numa
  lista só, `{"movimentacoes": [...]}`. Cada item tem os mesmos campos de `/gastos/recentes` mais o `tipo`
  (`"gasto"` ou `"receita"`); como os ids das duas tabelas são independentes, só o par (`tipo`, `id`) identifica
  a linha. O gasto segue as mesmas regras de `/gastos/recentes`. Na receita, `estabelecimento`, `categoria`,
  `total_parcelas` e `valor_total` vêm `null`, `parcelado` vem `false` e a descrição vazia vem `null` (a tela
  escreve "Receita"). `valor` é sempre positivo (o "+" e o verde são da tela). Ordem: data e hora da mais recente
  para a mais antiga; no mesmo segundo, gasto antes de receita e, no mesmo tipo, maior id primeiro. As
  transações negativas antigas ficam de fora (exige o mesmo cabeçalho).
- Home do Mini App em `/app/` (PRO-77): a tela que o usuário vê, servida de `mini-apps/home/`. A tela de detalhe do gasto
  (PRO-80) é uma segunda tela dentro da mesma página, aberta ao tocar numa linha de gasto em "Últimas Movimentações"
  (desde a PRO-92; tocar numa receita mostra o aviso "Em breve").
- `POST /registro/interpretar` (PRO-88): corpo `{"tipo": "gasto" | "receita", "texto": "..."}` (1 a 200
  caracteres). A IA lê o texto com as categorias do usuário e a rota devolve a prévia, **sem gravar**.
  Limite: 10 chamadas por minuto por usuário (só nesta rota).
- `POST /gastos` (PRO-88): grava o gasto confirmado. Corpo com `chave` (UUID v4 gerado pela tela, um por
  prévia), `valor` (total), `descricao`, `estabelecimento`, `categoria`, `metodo_pagamento` (`"Débito/Pix"` ou
  `"Crédito"`) e `parcelas` (1 a 12; Débito/Pix grava 1). `201` gravou; `200` com `"repetido": true` quando a
  mesma chave já tinha sido gravada (devolve o lançamento original, sem gravar de novo).
- `POST /receitas` (PRO-88): corpo `{"chave", "valor", "descricao"}`; mesmo `201`/`200` de `/gastos`.
- Página de diagnóstico em `/teste/`, que chama `/me`, `/resumo`, `/historico`, `/gastos/recentes` e
  `/movimentacoes/recentes` (PRO-92) e mostra as cinco respostas.
  Desde a PRO-88, tem também o bloco "Registro (diagnóstico)": "Interpretar" mostra a prévia em JSON e "Gravar"
  manda o mesmo pedido duas vezes ao mesmo tempo, com a mesma chave (prova do toque duplo: um `201` e um `200`
  com o mesmo `id`).
- As duas páginas vão com `Cache-Control: no-cache`: o Telegram sempre confere com o servidor se a página
  mudou (sem mudança, a resposta é um 304). Depois de mudar o código, reinicie a API (`Ctrl + C` e o comando acima).

Erros de `/resumo`, `/historico`, `/gastos/recentes`, `/movimentacoes/recentes` e `/gastos/{id}`: `401 nao_autorizado`, `404 usuario_nao_cadastrado` e
`409 cadastro_incompleto` (usuário sem dia de fechamento). Só `/gastos/{id}` tem ainda o
`404 gasto_nao_encontrado`; um id que não é número dá `422` (padrão do FastAPI).

Erros das rotas de registro (PRO-88), além dos três acima, sempre `{"detail": "<código>"}`:

- `/registro/interpretar`: `422 texto_invalido`, `tipo_invalido`, `tipo_divergente` (a IA entendeu o outro
  tipo), `valor_nao_entendido`, `categoria_nao_encontrada`; `429 limite_excedido` (com o cabeçalho
  `Retry-After`, em segundos); `502 resposta_ia_invalida`; `503 ia_indisponivel`.
- `/gastos`: `422 chave_invalida`, `valor_invalido` (maior que 0 e até R$ 1.000.000,00), `categoria_nao_encontrada`,
  `metodo_pagamento_invalido`, `parcelas_invalidas`, `descricao_longa` (mais de 100), `estabelecimento_longo` (mais de 60).
- `/receitas`: `422 chave_invalida`, `valor_invalido`, `descricao_longa`.
- `/gastos` e `/receitas`: `503 banco_ocupado` quando o banco continuou ocupado (ou desfez a transação) depois
  de 4 tentativas (esperas de 0,25 s, 0,5 s e 1 s). Nada fica gravado em dobro: a tela reenvia o **mesmo** corpo
  com a **mesma** chave (o mesmo vale para falha de rede), e a resposta vem `201` ou `200` com `"repetido": true`.
- As três rotas: erro inesperado dá `500 {"detail": "erro_interno"}`, sem a mensagem do banco nem o traceback
  (no log, só "Registro falhou: erro_interno" e o nome da classe).
- Um corpo que nem é JSON (ou campo de texto com número) dá o `422` padrão do FastAPI (`detail` em lista).

## 3. Abrir o túnel

```
ngrok http 8000
```

Com domínio estático: `ngrok http --url=<domínio estático> 8000`.

## 4. Configurar o BotFather

No **bot de teste**, defina o Menu Button (ou a Main Mini App) com a URL
`https://<host-do-ngrok>/app/` (a home). A página de diagnóstico das rotas fica em `/teste/`.

## 5. Tela de aviso do ngrok

No primeiro acesso, o ngrok (plano gratuito) pode mostrar uma tela de aviso antes da
página HTML; basta confirmar uma vez. O `fetch` da página envia o cabeçalho
`ngrok-skip-browser-warning`, então as chamadas a `/me`, `/resumo`, `/historico` e `/movimentacoes/recentes` não são interceptadas.

## 6. Testes

Todos rodam sem `.env` e sem internet:

```
python testes_initdata.py
python testes_rotas_leitura.py
python testes_rotas_registro.py
python testes_estabelecimento.py
python testes_calculos.py
```
