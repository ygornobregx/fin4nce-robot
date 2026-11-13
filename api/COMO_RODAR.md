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

## 2. Subir o bot e a API juntos (`iniciar.py`, PRO-94)

O jeito recomendado, igual ao da produção, é o iniciador. Da raiz do repositório:

```
python iniciar.py --porta 8000
```

Ele carrega o `.env` da raiz (sem sobrescrever o que já está no ambiente do terminal) e:

1. roda a migração (`bot.iniciar_banco()`) num processo próprio e espera terminar. Se ela falhar, nada sobe;
2. sobe o bot (`python bot.py`) e a API (`python -m uvicorn api.main:app --host 0.0.0.0 --port <porta> --workers 1`).
   A API roda num processo só, porque o limite de 10 interpretações por minuto fica na memória;
3. vigia os dois: se um terminar, encerra o outro e sai com código 1;
4. com `Ctrl + C` (ou o "Parar" do painel do Discloud), encerra os dois e sai.

A porta padrão é `8080` (a do Discloud); localmente, use `--porta 8000` por causa do ngrok. O log mostra, nesta
ordem, as linhas do iniciador (`[iniciar] Migração concluída`, `[iniciar] Bot iniciado` e
`[iniciar] API iniciada na porta 8000`) e depois as do bot e da API, misturadas (cada um escreve quando fica pronto):
`Bot rodando com sucesso!`, a linha de conferência (abaixo), a linha do Menu Button e
`Uvicorn running on http://0.0.0.0:8000`. Se um processo cair: `[iniciar] Processo bot terminou (código X): encerrando
o outro` (ou `api`). No `Ctrl + C`: `[iniciar] Encerrando o bot e a API`.

Para subir só a API, como antes (sem o bot nem a migração):

```
python -m uvicorn api.main:app --port 8000
```

### Página de diagnóstico (`PAGINA_DIAGNOSTICO`)

A `/teste/` só existe com `PAGINA_DIAGNOSTICO` igual a `1`, `true` ou `sim` (sem diferenciar maiúsculas). Sem ela,
`/teste/` responde `404 {"detail":"Not Found"}`, a pasta `mini-apps/teste-initdata/` nem é olhada (a API sobe sem
ela) e a `/app/` continua igual. A variável é lida uma vez, na subida da API, e **só do ambiente do processo**:

- pelo `iniciar.py`, basta a linha `PAGINA_DIAGNOSTICO=1` no `.env` de teste (o iniciador carrega o `.env` antes de
  subir a API);
- com o `uvicorn` direto, o `.env` não é lido na subida: ponha a variável no terminal antes
  (Git Bash: `PAGINA_DIAGNOSTICO=1 python -m uvicorn api.main:app --port 8000`).

Na produção ela fica **ausente**.

### Menu Button e linha de conferência (bot)

Ao ligar, o bot escreve no log `Bot: @<username> | Banco: TESTE` (o host da `TURSO_DATABASE_URL` tem `fin4ncetest`)
ou `Bot: @<username> | Banco: PRODUÇÃO`, sem URL, host nem token. Em seguida, se a `URL_MINI_APP` começar com
`https://`, ele mesmo configura o Menu Button "Abrir o L1d3r" com essa URL (`Menu Button configurado: Abrir o L1d3r`);
sem a variável, não mexe no botão (`Menu Button: sem URL_MINI_APP, não alterado`). Se o Telegram recusar, o log mostra
`Aviso: não consegui configurar o Menu Button (<tipo do erro>). O bot continua normalmente.`, sem a URL. Assim a URL
fica num lugar só, o `.env`: no BotFather, só conferir.

### Produção (Discloud)

O `discloud.config` aponta para o `iniciar.py` (`TYPE=site`, `RAM=512`, `ID=l1d3r`, `AUTORESTART=true`), na porta
`8080`. O Discloud instala só o `requirements.txt`, que desde a PRO-94 também tem `fastapi`, `uvicorn` e `httpx`; o
`requirements-api.txt` continua para o uso local. O `.env` vai no deploy, e o painel de variáveis fica vazio (uma
variável do painel venceria a do `.env` sem aviso). O `.discloudignore` deixa de fora os testes, as ferramentas, o
`.env.teste` (e `.env.*`) e a página `mini-apps/teste-initdata/`.

### Backup e restauração do banco (`ferramentas/backup_banco.py`)

```
python ferramentas/backup_banco.py dump <arquivo.sql>
python ferramentas/backup_banco.py restaurar <arquivo.sql>
```

Usa o banco do `.env` da raiz. Imprime `Banco: TESTE` ou `Banco: PRODUÇÃO` (a mesma regra da linha de conferência) e
as linhas de cada tabela. O `dump` só lê e recusa um arquivo que já existe. O `restaurar` só grava num banco
**vazio** (recusa qualquer banco com tabela), pede para digitar `sim` e grava tudo numa transação só; no fim, confere
as contagens com as do dump. Guarde o arquivo fora do repositório e do Obsidian: ele tem os dados reais.

## 3. Rotas da API

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
- `POST /cadastro` (PRO-96): cria o cadastro de quem ainda não tem. Corpo
  `{"nome": "Ygor", "dia_fechamento": 5, "categorias": ["Alimentação", "Casa", "Lazer", "Pet"]}`:
  `nome` de 1 a 30 caracteres (aparado, gravado como veio); `dia_fechamento` inteiro de 1 a 31 (não vale
  `true`, `5.5` nem `"5"`); `categorias` lista de 1 a 20 nomes (contados depois de juntar as repetidas: "lazer",
  " Lazer " e "LAZER" viram "Lazer", na primeira grafia e na ordem), cada um com até 30 caracteres. Grava
  `usuarios` com renda `0.0` e `horario_alerta` `NULL` (nenhum alerta diário) e as categorias, numa transação só,
  com a data de criação do servidor (Brasília). `201` devolve `{"user_id", "nome", "dia_fechamento", "categorias"}`
  (as categorias como foram gravadas). O `user_id` vem só do initData: `user_id`, `renda_mensal`,
  `horario_alerta` ou qualquer outro campo a mais no corpo são ignorados. As regras ficam no `cadastro.py`, o
  mesmo módulo que o cadastro antigo do bot usa.
- Página de diagnóstico em `/teste/` (só com `PAGINA_DIAGNOSTICO`, PRO-94), que chama `/me`, `/resumo`, `/historico`, `/gastos/recentes` e
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

Erros de `POST /cadastro` (PRO-96), sempre `{"detail": "<código>"}`: `401 nao_autorizado`; `409 usuario_ja_cadastrado`
(já existe linha em `usuarios`, antes ou durante a gravação, como no toque duplo; nada é alterado); `422 nome_invalido`,
`dia_invalido`, `categorias_invalidas`; `503 banco_ocupado` (banco ocupado nas 4 tentativas, com esperas de 0,25 s,
0,5 s e 1 s); `500 erro_interno` (sem detalhes; no log, só "Cadastro falhou: erro_interno" e o nome da classe).
- Um corpo que nem é JSON (ou campo de texto com número) dá o `422` padrão do FastAPI (`detail` em lista).

## 4. Abrir o túnel

```
ngrok http 8000
```

Com domínio estático: `ngrok http --url=<domínio estático> 8000`.

## 5. Configurar o BotFather

Desde a PRO-94, o próprio bot configura o Menu Button com a `URL_MINI_APP` ao ligar (seção 2): no BotFather do
**bot de teste**, só confira (`/mybots` → o bot → Bot Settings → Menu Button) se a URL é
`https://<host-do-ngrok>/app/` (a home). A página de diagnóstico das rotas fica em `/teste/` (com `PAGINA_DIAGNOSTICO`).

## 6. Porta de entrada no bot de teste (`URL_MINI_APP`, PRO-96)

Com a variável `URL_MINI_APP` no `.env` do **bot de teste** (a URL da home pelo ngrok, `https://<host-do-ngrok>/app/`),
o `/start` de quem **não tem cadastro** mostra só "Bem-vindo ao L1d3r!" e o botão "Abrir o L1d3r", que abre o Mini
App; nada é gravado e o chat não pergunta nada. Quem já tem cadastro continua vendo o Menu Principal.

- A variável é lida a cada `/start` e só vale se começar com `https://` (o Telegram exige HTTPS); sem ela, ou com
  outro começo, o cadastro antigo no chat continua igual.
- Não é segredo, mas fica só no ambiente: nunca no código, nas notas nem nos testes.
- Na produção (PRO-94), ela entra só no 2º deploy, com o endereço do Discloud (`https://l1d3r.<final>/app/`); até lá,
  o `/start` segue com o cadastro antigo.

## 7. Tela de aviso do ngrok

No primeiro acesso, o ngrok (plano gratuito) pode mostrar uma tela de aviso antes da
página HTML; basta confirmar uma vez. O `fetch` da página envia o cabeçalho
`ngrok-skip-browser-warning`, então as chamadas a `/me`, `/resumo`, `/historico` e `/movimentacoes/recentes` não são interceptadas.

## 8. Testes

Todos rodam sem `.env` e sem internet:

```
python testes_initdata.py
python testes_rotas_leitura.py
python testes_rotas_registro.py
python testes_cadastro.py
python testes_estabelecimento.py
python testes_calculos.py
python testes_iniciar.py
python testes_backup_banco.py
```

O `testes_iniciar.py` usa processos filhos falsos (nunca sobe o bot nem a API de verdade) e o
`testes_backup_banco.py` usa só bancos SQLite e libsql locais, em arquivos temporários.
