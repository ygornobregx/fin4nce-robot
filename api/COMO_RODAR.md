# Como rodar a API local do Mini App (PRO-72)

Antes de tudo: o `.env` local deve apontar para o **bot de teste** (`TOKEN`) e para o
banco **`fin4nce-teste`** (`TURSO_DATABASE_URL` e `TURSO_AUTH_TOKEN`). Nunca use os
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

Rotas: `GET /saude` (sem autenticação), `GET /me` (exige `Authorization: tma <initData>`)
e a página de teste em `/app/`.

## 3. Abrir o túnel

```
ngrok http 8000
```

Com domínio estático: `ngrok http --url=<domínio estático> 8000`.

## 4. Configurar o BotFather

No **bot de teste**, defina o Menu Button (ou a Main Mini App) com a URL
`https://<host-do-ngrok>/app/`.

## 5. Tela de aviso do ngrok

No primeiro acesso, o ngrok (plano gratuito) pode mostrar uma tela de aviso antes da
página HTML; basta confirmar uma vez. O `fetch` da página envia o cabeçalho
`ngrok-skip-browser-warning`, então a chamada a `/me` não é interceptada.

## 6. Testes

Ambos rodam sem `.env` e sem internet:

```
python testes_initdata.py
python testes_calculos.py
```
