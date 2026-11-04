# Como rodar a API local do Mini App (PRO-72)

Antes de tudo: o `.env` local deve apontar para o **bot de teste** (`TOKEN`) e para o
banco **`fin4ncetest`** (`TURSO_DATABASE_URL` e `TURSO_AUTH_TOKEN`). Nunca use os
valores de produção. Não copie esses valores para lugar nenhum.

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
- `GET /resumo`: fatura vigente (gasto, sobra do mês e categorias; exige o mesmo cabeçalho).
- `GET /historico`: gasto total de cada mês com movimento, com os futuros marcados
  como `previsto` (exige o mesmo cabeçalho).
- `GET /gastos/recentes`: os 10 últimos gastos com descrição (os registrados a partir da PRO-78),
  com o vínculo da compra parcelada e o `estabelecimento` (PRO-81; `null` quando o gasto não tem
  estabelecimento, nunca um texto substituto) (exige o mesmo cabeçalho).
- `GET /gastos/{id}`: o detalhe de um gasto (PRO-80), com os mesmos campos de `/gastos/recentes` mais
  `parcelas_pagas` (calculado pela fatura vigente; `null` à vista). Gasto inexistente, de outro usuário,
  receita ou sem descrição dá `404 gasto_nao_encontrado` (exige o mesmo cabeçalho).
- Home do Mini App em `/app/` (PRO-77): a tela que o usuário vê, servida de `mini-apps/home/`. A tela de detalhe do gasto
  (PRO-80) é uma segunda tela dentro da mesma página, aberta ao tocar numa linha de "Últimos gastos".
- Página de diagnóstico em `/teste/`, que chama `/me`, `/resumo`, `/historico` e `/gastos/recentes` e mostra as quatro respostas.
- As duas páginas vão com `Cache-Control: no-cache`: o Telegram sempre confere com o servidor se a página
  mudou (sem mudança, a resposta é um 304). Depois de mudar o código, reinicie a API (`Ctrl + C` e o comando acima).

Erros de `/resumo`, `/historico`, `/gastos/recentes` e `/gastos/{id}`: `401 nao_autorizado`, `404 usuario_nao_cadastrado` e
`409 cadastro_incompleto` (usuário sem dia de fechamento). Só `/gastos/{id}` tem ainda o
`404 gasto_nao_encontrado`; um id que não é número dá `422` (padrão do FastAPI).

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
`ngrok-skip-browser-warning`, então as chamadas a `/me`, `/resumo`, `/historico` e `/gastos/recentes` não são interceptadas.

## 6. Testes

Todos rodam sem `.env` e sem internet:

```
python testes_initdata.py
python testes_rotas_leitura.py
python testes_estabelecimento.py
python testes_calculos.py
```
