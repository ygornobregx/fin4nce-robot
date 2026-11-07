"""Prova do cadastro no Turso REAL (PRO-96), no banco de TESTE `fin4ncetest`.

Prova o cadastro.py e a rota POST /cadastro com o driver libsql de verdade,
naquilo que o SQLite dos testes automáticos não reproduz: a chave primária
violada no servidor, o SQLITE_BUSY, o rollback remoto no meio da transação, o
NULL, os acentos e a data de criação gravados, e o caminho antigo do bot
(substituir=True). Quem roda é o Opus principal; o resultado vai para a nota
da PRO-96 (ESCRITAS NO BANCO DE TESTE).

Uso (a partir da raiz do repositório):

    python ferramentas/prova_cadastro_turso.py
    python ferramentas/prova_cadastro_turso.py --env .env.teste
    python ferramentas/prova_cadastro_turso.py --sqlite caminho.db   (SÓ para os testes das travas)

Travas (as mesmas da ferramenta do banco de teste, PRO-90, importadas dela):
    1. Antes de conectar, a URL do arquivo de ambiente tem de ser a do
       fin4ncetest (e_banco_de_teste). Senão, aborta (código 3) sem conectar.
    2. Nunca imprime a URL nem o token: toda mensagem de erro passa pela
       ocultar_segredos.
    3. Ids FICTÍCIOS e negativos, de -9600999 a -9600001 (ids do Telegram são
       sempre positivos). Cada rodada usa o primeiro bloco de 100 ids sem
       nenhuma linha em 'usuarios' ou 'categorias'; sem bloco livre, aborta
       (código 5) e pede a limpeza.
    4. Toda conexão da prova passa pela ConexaoTravada: leitura só com SELECT
       (ou WITH); escrita só em 'usuarios' e 'categorias', só por INSERT, e só
       com user_id dentro da faixa. A única exceção é o cenário P8 (caminho
       antigo do bot, gravar_cadastro com substituir=True), que por definição
       faz INSERT OR REPLACE em 'usuarios' e DELETE FROM categorias WHERE
       user_id = ?, também só com o user_id dentro da faixa e só durante o P8.
       Qualquer outro comando, ou um user_id fora da faixa (inclusive
       positivo), é bloqueado ANTES de chegar ao banco, e a prova aborta
       (código 4).
    5. A API não lê o .env: o TestClient roda com dependency_overrides
       (obter_config com um token FALSO, com o qual a prova assina o initData;
       obter_conexao_db com a conexão travada; obter_agora com o relógio real
       de Brasília).
    6. A prova não apaga nada fora do P8: as linhas fictícias ficam no banco
       até a limpeza manual (o texto dela sai no fim, com a faixa exata).
    7. Dia de fechamento dos cadastros fictícios: nunca o de hoje nem o de
       amanhã (Brasília), para o job de fechamento do bot de teste não pegar
       as linhas antes da limpeza.

Códigos de saída: 0 todos os cenários OK; 1 algum cenário FALHOU (ou erro do
banco); 3 arquivo de ambiente com problema ou banco que não é o de teste;
4 a trava bloqueou um comando ou um user_id fora da faixa; 5 sem bloco livre.
"""
import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest import mock
from urllib.parse import urlencode

PASTA = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(PASTA)
for _caminho in (RAIZ, PASTA):
    if _caminho not in sys.path:
        sys.path.insert(0, _caminho)

from banco_teste import (  # noqa: E402
    NOME_BANCO_TESTE,
    ErroFerramenta,
    carregar_credenciais,
    conectar_turso,
    e_banco_de_teste,
    escolher_arquivo_env,
    ocultar_segredos,
)

import cadastro  # noqa: E402
import registro  # noqa: E402
from calculos import FUSO_BRASILIA  # noqa: E402

# --- FAIXA DE IDS FICTÍCIOS ---
FAIXA_INICIO = -9600999
FAIXA_FIM = -9600001
TAMANHO_BLOCO = 100

# Token FALSO, só para a API desta prova assinar e validar o initData. Não é o
# token do bot (a prova nunca lê o TOKEN do .env).
TOKEN_FALSO = "000000:PROVA-PRO-96-TOKEN-FALSO"

ESPERA_TRANSACAO_SEGURADA = 0.8   # P6: contra 0,25 + 0,5 + 1 = 1,75 s de novas tentativas
RODADAS_TOQUE_DUPLO = 10          # P7
RODADAS_COM_TRES_PEDIDOS = (3, 6, 9)


class ErroProva(Exception):
    """Aborto da prova, com mensagem segura para mostrar e o código de saída."""

    def __init__(self, mensagem, codigo):
        super().__init__(mensagem)
        self.codigo = codigo


class ViolacaoDaTrava(Exception):
    """Comando bloqueado pela ConexaoTravada (nunca chegou ao banco)."""


def id_na_faixa(user_id):
    return (isinstance(user_id, int) and not isinstance(user_id, bool)
            and FAIXA_INICIO <= user_id <= FAIXA_FIM)


def exigir_id_na_faixa(user_id):
    if not id_na_faixa(user_id):
        raise ErroProva(f"ABORTADO: user_id {user_id!r} fora da faixa fictícia "
                        f"({FAIXA_INICIO} a {FAIXA_FIM}). Nada foi gravado.", 4)
    return user_id


def blocos_da_faixa():
    """Blocos de TAMANHO_BLOCO ids, (menor, maior), do mais perto de zero para
    baixo: (-9600100, -9600001), (-9600200, -9600101), ... Só blocos inteiros."""
    blocos = []
    maior = FAIXA_FIM
    while maior - TAMANHO_BLOCO + 1 >= FAIXA_INICIO:
        blocos.append((maior - TAMANHO_BLOCO + 1, maior))
        maior -= TAMANHO_BLOCO
    return blocos


def escolher_bloco(conn):
    """O primeiro bloco sem nenhuma linha em 'usuarios' nem em 'categorias'."""
    for menor, maior in blocos_da_faixa():
        linhas = 0
        for tabela in ('usuarios', 'categorias'):
            linhas += conn.execute(f"SELECT COUNT(*) FROM {tabela} WHERE user_id BETWEEN ? AND ?",
                                   (menor, maior)).fetchone()[0]
        if linhas == 0:
            return menor, maior
    raise ErroProva(f"ABORTADO: nenhum bloco livre de {TAMANHO_BLOCO} ids na faixa {FAIXA_INICIO} a "
                    f"{FAIXA_FIM}. Faça a limpeza (texto no fim da última rodada) e rode de novo.", 5)


def escolher_dia(agora):
    """Dia de fechamento que não é o de hoje nem o de amanhã (Brasília), até 28
    (existe em todo mês, sem o ajuste de fim de mês)."""
    proibidos = {agora.day, (agora + timedelta(days=1)).day}
    return next(dia for dia in range(10, 29) if dia not in proibidos)


def assinar_init_data(user_id, token=TOKEN_FALSO, agora=None):
    """initData assinado como o Telegram faria, com o token FALSO da prova."""
    campos = {
        "auth_date": str(int(time.time() if agora is None else agora)),
        "query_id": "PROVA-PRO-96",
        "user": json.dumps({"id": user_id, "first_name": "Prova"}),
    }
    texto = "\n".join(f"{chave}={valor}" for chave, valor in sorted(campos.items()))
    segredo = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    campos["hash"] = hmac.new(segredo, texto.encode(), hashlib.sha256).hexdigest()
    return urlencode(campos)


def _sql_normalizado(sql):
    return ' '.join(str(sql).split())


# --- TRAVA DE ESCRITA ---
_LEITURA = re.compile(r'^(SELECT|WITH)\b', re.I)
_PALAVRAS_DE_ESCRITA = re.compile(
    r'\b(INSERT|UPDATE|DELETE|REPLACE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX)\b', re.I)
_ESCRITAS_PERMITIDAS = (
    re.compile(r'^INSERT INTO usuarios \(user_id, nome, renda_mensal, dia_fechamento, horario_alerta\) '
               r'VALUES \(\?, \?, \?, \?, \?\)$'),
    re.compile(r'^INSERT INTO categorias \(user_id, nome, orcamento_mensal, saldo_atual, data_criacao\) '
               r'VALUES \(\?, \?, \?, \?, \?\)$'),
)
# Só no P8 (caminho antigo do bot, gravar_cadastro com substituir=True).
_ESCRITAS_DO_CAMINHO_ANTIGO = (
    re.compile(r'^INSERT OR REPLACE INTO usuarios \(user_id, nome, renda_mensal, dia_fechamento, '
               r'horario_alerta\) VALUES \(\?, \?, \?, \?, \?\)$'),
    re.compile(r'^DELETE FROM categorias WHERE user_id = \?$'),
)


class Trava:
    """Estado compartilhado pelas conexões travadas: as violações (o que foi
    bloqueado) e se o caminho antigo do bot (P8) está liberado agora."""

    def __init__(self):
        self.violacoes = []
        self.caminho_antigo_liberado = False
        self._lock = threading.Lock()

    def conferir(self, sql, params):
        sql = _sql_normalizado(sql)
        params = tuple(params or ())
        if ';' in sql:
            self._bloquear(f"mais de um comando: {sql[:60]}")
        if _LEITURA.match(sql):
            if _PALAVRAS_DE_ESCRITA.search(sql):
                self._bloquear(f"leitura com palavra de escrita: {sql[:60]}")
            return
        permitidos = _ESCRITAS_PERMITIDAS
        if self.caminho_antigo_liberado:
            permitidos = permitidos + _ESCRITAS_DO_CAMINHO_ANTIGO
        if not any(padrao.match(sql) for padrao in permitidos):
            self._bloquear(f"comando não permitido: {sql[:60]}")
        if not params or not id_na_faixa(params[0]):
            self._bloquear(f"user_id fora da faixa fictícia: {params[0] if params else None!r}")

    def _bloquear(self, motivo):
        with self._lock:
            self.violacoes.append(motivo)
        raise ViolacaoDaTrava(motivo)


class ConexaoTravada:
    """Embrulho de uma conexão (libsql ou sqlite3): todo comando passa pela
    Trava ANTES de chegar ao banco."""

    def __init__(self, conn, trava):
        self._conn = conn
        self._trava = trava

    def execute(self, sql, params=()):
        self._trava.conferir(sql, params)
        return self._conn.execute(sql, params)

    def cursor(self):
        conexao = self
        real = self._conn.cursor()

        class Cursor:
            def execute(self, sql, params=()):
                conexao._trava.conferir(sql, params)
                real.execute(sql, params)
                return self

            def fetchone(self):
                return real.fetchone()

            def fetchall(self):
                return real.fetchall()

            @property
            def lastrowid(self):
                return real.lastrowid

            @property
            def description(self):
                return real.description

        return Cursor()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


# --- CONEXÃO INTERMEDIÁRIA (P3 A P7) ---
class _CursorVazio:
    def fetchone(self):
        return None

    def fetchall(self):
        return []


class ConexaoIntermediaria:
    """Repassa tudo à conexão de baixo (a travada) e, conforme o roteiro:
      - esconder_prechecagem=N: as N primeiras consultas de
        cadastro.SQL_USUARIO_EXISTE devolvem "não existe" sem ir ao banco (P3);
      - falha_na_categoria=(n, erro): o n-ésimo INSERT de categoria levanta
        'erro' sem ir ao banco (P4), só uma vez se falhar_uma_vez (P5).
    Registra os erros vindos do banco (classe e mensagem), e conta os INSERT
    em 'usuarios' (= tentativas de gravar), as consultas de existência e os
    INSERT de categoria."""

    def __init__(self, conn, esconder_prechecagem=0, falha_na_categoria=None, falhar_uma_vez=False,
                 erros=None):
        self._conn = conn
        self.esconder_prechecagem = esconder_prechecagem
        self.prechecagens_escondidas = 0
        self.falha_na_categoria = falha_na_categoria
        self.falhar_uma_vez = falhar_uma_vez
        self.falhas_injetadas = 0
        self.erros = [] if erros is None else erros
        self.insercoes_usuario = 0
        self.insercoes_categoria = 0
        self.consultas_existencia = 0

    def execute(self, sql, params=()):
        normalizado = _sql_normalizado(sql)
        if normalizado == cadastro.SQL_USUARIO_EXISTE:
            self.consultas_existencia += 1
            if self.esconder_prechecagem > 0:
                self.esconder_prechecagem -= 1
                self.prechecagens_escondidas += 1
                return _CursorVazio()
        if normalizado.startswith('INSERT INTO usuarios'):
            self.insercoes_usuario += 1
        if normalizado.startswith('INSERT INTO categorias'):
            self.insercoes_categoria += 1
            if self.falha_na_categoria is not None:
                numero, erro = self.falha_na_categoria
                if self.insercoes_categoria == numero and not (self.falhar_uma_vez and self.falhas_injetadas):
                    self.falhas_injetadas += 1
                    raise erro
        try:
            return self._conn.execute(sql, params)
        except Exception as erro:
            self.erros.append(erro)
            raise

    def commit(self):
        try:
            self._conn.commit()
        except Exception as erro:
            self.erros.append(erro)
            raise

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


# --- A PROVA ---
class Resultado:
    def __init__(self, codigo, cenario, esperado):
        self.codigo = codigo
        self.cenario = cenario
        self.esperado = esperado
        self.obtido = ""
        self.falhas = []

    @property
    def ok(self):
        return not self.falhas

    def conferir(self, descricao, condicao):
        if not condicao:
            self.falhas.append(descricao)
        return condicao


class Prova:
    def __init__(self, fabrica_conexao, saida, segredos):
        self.fabrica_conexao = fabrica_conexao
        self.saida = saida
        self.segredos = segredos
        self.trava = Trava()
        self.resultados = []
        self.ids_usados = []
        self.embrulho = None
        self.esperas = []
        self._lock = threading.Lock()

    # --- infraestrutura ---
    def escrever(self, texto=""):
        print(texto, file=self.saida)

    def ocultar(self, texto):
        return ocultar_segredos(texto, self.segredos)

    def descrever_erro(self, erro):
        return self.ocultar(f"{type(erro).__name__}: {erro}")

    def conectar(self):
        return ConexaoTravada(self.fabrica_conexao(), self.trava)

    def ler(self, sql, params=()):
        """Leitura pela conexão de leitura (travada), sem deixar transação aberta."""
        linhas = self.conn_leitura.execute(sql, params).fetchall()
        try:
            self.conn_leitura.rollback()
        except Exception:
            pass
        return [tuple(linha) for linha in linhas]

    def contar(self, tabela, user_id):
        return self.ler(f"SELECT COUNT(*) FROM {tabela} WHERE user_id = ?", (user_id,))[0][0]

    def contagens_fora_da_faixa(self):
        return tuple(
            self.ler(f"SELECT COUNT(*) FROM {tabela} WHERE user_id NOT BETWEEN ? AND ?",
                     (FAIXA_INICIO, FAIXA_FIM))[0][0]
            for tabela in ('usuarios', 'categorias')
        )

    def linhas_do_usuario(self, user_id):
        usuario = self.ler("SELECT user_id, nome, renda_mensal, dia_fechamento, horario_alerta, "
                           "typeof(horario_alerta) FROM usuarios WHERE user_id = ?", (user_id,))
        categorias = self.ler("SELECT id, nome, orcamento_mensal, saldo_atual, data_criacao FROM categorias "
                              "WHERE user_id = ? ORDER BY id", (user_id,))
        return usuario, categorias

    def proximo_id(self):
        try:
            user_id = next(self._ids)
        except StopIteration:
            raise ErroProva("ABORTADO: o bloco de ids acabou.", 5) from None
        exigir_id_na_faixa(user_id)
        self.ids_usados.append(user_id)
        return user_id

    def _conexao_da_requisicao(self):
        conn = self.conectar()
        if self.embrulho is not None:
            conn = self.embrulho(conn)
        try:
            yield conn
        finally:
            conn.close()

    def _instalar_api(self):
        from fastapi.testclient import TestClient
        from api.main import Config, app, obter_agora, obter_config, obter_conexao_db

        logging.getLogger("api").addHandler(logging.NullHandler())
        self.app = app
        app.dependency_overrides[obter_config] = lambda: Config(TOKEN_FALSO, "libsql://nao-usado", "nao-usado")
        app.dependency_overrides[obter_conexao_db] = self._conexao_da_requisicao
        app.dependency_overrides[obter_agora] = lambda: datetime.now(FUSO_BRASILIA)
        self._TestClient = TestClient
        self.client = TestClient(app, raise_server_exceptions=False)

    def novo_cliente(self):
        return self._TestClient(self.app, raise_server_exceptions=False)

    def cabecalho(self, user_id):
        exigir_id_na_faixa(user_id)
        return {"Authorization": "tma " + assinar_init_data(user_id)}

    def post_cadastro(self, user_id, corpo, client=None):
        cabecalho = self.cabecalho(user_id)   # recusa user_id fora da faixa ANTES do pedido
        return (client or self.client).post("/cadastro", json=corpo, headers=cabecalho)

    def get(self, rota, user_id):
        return self.client.get(rota, headers=self.cabecalho(user_id))

    @contextmanager
    def esperas_gravadas(self):
        """cadastro._esperar grava cada espera e dorme de verdade."""
        original = time.sleep

        def esperar(segundos):
            with self._lock:
                self.esperas.append(segundos)
            original(segundos)

        self.esperas = []
        with mock.patch.object(cadastro, '_esperar', esperar):
            yield self.esperas

    def conferir_trava(self):
        if self.trava.violacoes:
            raise ErroProva("ABORTADO: a trava bloqueou um comando antes do banco: "
                            + self.ocultar("; ".join(self.trava.violacoes)), 4)

    # --- execução ---
    def executar(self):
        self.escrever("PROVA DO CADASTRO NO BANCO DE TESTE (PRO-96)")
        self.conn_leitura = self.conectar()
        try:
            self._instalar_api()
            self.cenario_p0()
            for cenario in (self.cenario_p1_p2, self.cenario_p3, self.cenario_p4, self.cenario_p5,
                            self.cenario_p6, self.cenario_p7, self.cenario_p8):
                self.embrulho = None
                try:
                    cenario()
                except (ErroProva, ViolacaoDaTrava):
                    raise
                except Exception as erro:  # o cenário quebrou: FALHOU, com o erro sem segredos
                    # Todo cenário cria o seu Resultado na primeira linha: o último é o que quebrou.
                    resultado = self.resultados[-1]
                    resultado.falhas.append(f"exceção: {self.descrever_erro(erro)}")
                    resultado.obtido = resultado.obtido or f"exceção: {self.descrever_erro(erro)}"
                finally:
                    self.embrulho = None
                self.conferir_trava()
            self.cenario_p9()
            self.conferir_trava()
            self.imprimir_relatorio()
        except ViolacaoDaTrava:
            self.conferir_trava()
            raise
        finally:
            if hasattr(self, 'app'):
                self.app.dependency_overrides.clear()
            try:
                self.conn_leitura.close()
            except Exception:
                pass
        return all(resultado.ok for resultado in self.resultados)

    def novo_resultado(self, codigo, cenario, esperado):
        resultado = Resultado(codigo, cenario, esperado)
        self.resultados.append(resultado)
        return resultado

    # --- cenários ---
    def cenario_p0(self):
        r = self.novo_resultado("P0", "Pré-checagem", "trava ok; bloco livre; contagens fora da faixa guardadas")
        self.bloco = escolher_bloco(self.conn_leitura)
        menor, maior = self.bloco
        self._ids = iter(range(maior, menor - 1, -1))
        self.agora = datetime.now(FUSO_BRASILIA)
        self.dia = escolher_dia(self.agora)
        self.contagens_p0 = self.contagens_fora_da_faixa()
        r.conferir("dia do ciclo não é hoje nem amanhã",
                   self.dia not in {self.agora.day, (self.agora + timedelta(days=1)).day})
        r.obtido = (f"bloco {menor} a {maior}; dia {self.dia}; fora da faixa: usuarios "
                    f"{self.contagens_p0[0]}, categorias {self.contagens_p0[1]}")

    def cenario_p1_p2(self):
        r = self.novo_resultado("P1", "Cadastro pela rota",
                                "201 + contrato; renda 0, alerta NULL, acentos, repetidas juntas, data em "
                                "Brasília; /me, /resumo, /historico e /movimentacoes/recentes")
        uid = self.proximo_id()
        self.uid_p1 = uid
        enviadas = ["Alimentação", "Saúde", "lazer", " Lazer ", "LAZER", "Casa"]
        gravadas = ["Alimentação", "Saúde", "Lazer", "Casa"]
        antes = datetime.now(FUSO_BRASILIA)
        resposta = self.post_cadastro(uid, {"nome": "Prova P1", "dia_fechamento": self.dia, "categorias": enviadas})
        depois = datetime.now(FUSO_BRASILIA)
        r.conferir(f"status 201 (veio {resposta.status_code})", resposta.status_code == 201)
        r.conferir("corpo do contrato", resposta.status_code == 201 and resposta.json() == {
            "user_id": uid, "nome": "Prova P1", "dia_fechamento": self.dia, "categorias": gravadas})
        usuario, categorias = self.linhas_do_usuario(uid)
        r.conferir("uma linha em usuarios", len(usuario) == 1)
        if usuario:
            _, nome, renda, dia, alerta, tipo_alerta = usuario[0]
            r.conferir("nome gravado", nome == "Prova P1")
            r.conferir("renda 0.0", renda == 0.0)
            r.conferir("dia gravado", dia == self.dia)
            r.conferir("typeof(horario_alerta) = 'null'", alerta is None and tipo_alerta == 'null')
        r.conferir("categorias com acento iguais, repetidas juntas, na ordem",
                   [linha[1] for linha in categorias] == gravadas)
        r.conferir("orçamento e saldo 0", all(linha[2] == 0.0 and linha[3] == 0.0 for linha in categorias))
        for linha in categorias:
            data = linha[4]
            if not r.conferir(f"data_criacao com 19 caracteres ({data!r})", isinstance(data, str) and len(data) == 19):
                continue
            momento = datetime.strptime(data, '%Y-%m-%d %H:%M:%S').replace(tzinfo=FUSO_BRASILIA)
            r.conferir(f"data_criacao no horário de Brasília ({data})",
                       antes - timedelta(seconds=5) <= momento <= depois + timedelta(seconds=5))
        me = self.get("/me", uid)
        r.conferir(f"/me 200 (veio {me.status_code})",
                   me.status_code == 200 and me.json() == {"user_id": uid, "nome": "Prova P1"})
        resumo = self.get("/resumo", uid)
        corpo_resumo = resumo.json() if resumo.status_code == 200 else {}
        r.conferir(f"/resumo 200 (veio {resumo.status_code})", resumo.status_code == 200)
        r.conferir("/resumo com renda, gasto, receitas e Sobra 0",
                   (corpo_resumo.get("renda"), corpo_resumo.get("total_gasto"),
                    corpo_resumo.get("total_receitas"), corpo_resumo.get("sobra_mes")) == (0.0, 0.0, 0.0, 0.0))
        historico = self.get("/historico", uid)
        r.conferir(f"/historico 200 (veio {historico.status_code})", historico.status_code == 200)
        recentes = self.get("/movimentacoes/recentes", uid)
        r.conferir("/movimentacoes/recentes vazia",
                   recentes.status_code == 200 and recentes.json() == {"movimentacoes": []})
        r.obtido = (f"{resposta.status_code}; categorias {[linha[1] for linha in categorias]}; "
                    f"/me {me.status_code}, /resumo {resumo.status_code} "
                    f"(renda {corpo_resumo.get('renda')}, gasto {corpo_resumo.get('total_gasto')}, "
                    f"receitas {corpo_resumo.get('total_receitas')}, Sobra {corpo_resumo.get('sobra_mes')}), "
                    f"/historico {historico.status_code}, /movimentacoes/recentes {recentes.status_code}")

        r2 = self.novo_resultado("P2", "Já cadastrado pela rota", "409 usuario_ja_cadastrado; linhas de P1 iguais")
        antes_p2 = self.linhas_do_usuario(uid)
        resposta = self.post_cadastro(uid, {"nome": "Outro Nome", "dia_fechamento": 3, "categorias": ["Nova"]})
        depois_p2 = self.linhas_do_usuario(uid)
        r2.conferir(f"409 usuario_ja_cadastrado (veio {resposta.status_code})",
                    resposta.status_code == 409 and resposta.json() == {"detail": "usuario_ja_cadastrado"})
        r2.conferir("linhas de P1 idênticas antes e depois", antes_p2 == depois_p2)
        r2.obtido = f"{resposta.status_code} {resposta.text}; linhas iguais: {antes_p2 == depois_p2}"

    def cenario_p3(self):
        r = self.novo_resultado("P3", "Chave primária violada de verdade",
                                "409; nenhuma categoria a mais; erro do banco registrado")
        uid = self.proximo_id()
        primeira = self.post_cadastro(uid, {"nome": "Prova P3", "dia_fechamento": self.dia,
                                            "categorias": ["Mercado", "Lazer", "Casa"]})
        r.conferir(f"cadastro inicial 201 (veio {primeira.status_code})", primeira.status_code == 201)
        categorias_antes = self.contar('categorias', uid)
        intermediarias = []

        def embrulhar(conn):
            intermediaria = ConexaoIntermediaria(conn, esconder_prechecagem=1)
            intermediarias.append(intermediaria)
            return intermediaria

        self.embrulho = embrulhar
        resposta = self.post_cadastro(uid, {"nome": "Prova P3 B", "dia_fechamento": self.dia,
                                            "categorias": ["Extra 1", "Extra 2"]})
        self.embrulho = None
        categorias_depois = self.contar('categorias', uid)
        intermediaria = intermediarias[0] if intermediarias else None
        erros = [self.descrever_erro(e) for e in intermediaria.erros] if intermediaria else []
        r.conferir(f"409 usuario_ja_cadastrado (veio {resposta.status_code})",
                   resposta.status_code == 409 and resposta.json() == {"detail": "usuario_ja_cadastrado"})
        r.conferir("pré-checagem escondida uma vez", intermediaria is not None
                   and intermediaria.prechecagens_escondidas == 1)
        r.conferir("o INSERT bateu na chave primária (houve erro do banco)", bool(erros))
        r.conferir("nenhuma categoria a mais", categorias_antes == categorias_depois)
        r.obtido = (f"{resposta.status_code}; categorias {categorias_antes} -> {categorias_depois}; "
                    f"erro recebido: {erros[0] if erros else 'nenhum'}")

    def cenario_p4(self):
        r = self.novo_resultado("P4", "Desfazer no meio",
                                "500 erro_interno; zero linhas em usuarios e categorias")
        uid = self.proximo_id()
        self.embrulho = lambda conn: ConexaoIntermediaria(
            conn, falha_na_categoria=(3, RuntimeError("erro artificial da prova (P4)")))
        resposta = self.post_cadastro(uid, {"nome": "Prova P4", "dia_fechamento": self.dia,
                                            "categorias": ["Um", "Dois", "Tres", "Quatro"]})
        self.embrulho = None
        usuarios, categorias = self.contar('usuarios', uid), self.contar('categorias', uid)
        r.conferir(f"500 erro_interno (veio {resposta.status_code})",
                   resposta.status_code == 500 and resposta.json() == {"detail": "erro_interno"})
        r.conferir("resposta sem a mensagem do erro", "artificial" not in resposta.text)
        r.conferir("zero linhas em usuarios", usuarios == 0)
        r.conferir("zero linhas em categorias", categorias == 0)
        r.obtido = f"{resposta.status_code} {resposta.text}; usuarios {usuarios}, categorias {categorias}"

    def cenario_p5(self):
        r = self.novo_resultado("P5", "Erro passageiro e nova tentativa",
                                "nova tentativa e 201; um jogo só de categorias")
        uid = self.proximo_id()
        intermediarias = []

        def embrulhar(conn):
            intermediaria = ConexaoIntermediaria(
                conn, falha_na_categoria=(3, ValueError("erro artificial da prova (P5): SQLITE_BUSY")),
                falhar_uma_vez=True)
            intermediarias.append(intermediaria)
            return intermediaria

        lista = ["Um", "Dois", "Tres", "Quatro"]
        self.embrulho = embrulhar
        with self.esperas_gravadas() as esperas:
            resposta = self.post_cadastro(uid, {"nome": "Prova P5", "dia_fechamento": self.dia, "categorias": lista})
        self.embrulho = None
        usuario, categorias = self.linhas_do_usuario(uid)
        intermediaria = intermediarias[0] if intermediarias else None
        tentativas = intermediaria.insercoes_usuario if intermediaria else 0
        r.conferir(f"201 (veio {resposta.status_code})", resposta.status_code == 201)
        r.conferir("falha injetada uma vez", intermediaria is not None and intermediaria.falhas_injetadas == 1)
        r.conferir("duas tentativas", tentativas == 2)
        r.conferir("uma espera de 0,25 s", list(esperas) == [0.25])
        r.conferir("uma linha em usuarios", len(usuario) == 1)
        r.conferir("um jogo só de categorias", [linha[1] for linha in categorias] == lista)
        r.obtido = (f"{resposta.status_code}; tentativas {tentativas}; esperas {list(esperas)}; "
                    f"categorias {[linha[1] for linha in categorias]}")

    def cenario_p6(self):
        r = self.novo_resultado("P6", "Transação segurada (SQLITE_BUSY real)",
                                f"409 (nunca 500; 503 indica bug) com a conexão A segurando "
                                f"{ESPERA_TRANSACAO_SEGURADA} s")
        uid = self.proximo_id()
        inseriu = threading.Event()
        erros_a = []

        def conexao_a():
            try:
                conn_a = self.conectar()
                try:
                    conn_a.execute('INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento, horario_alerta) '
                                   'VALUES (?, ?, ?, ?, ?)', (uid, "Prova P6 A", 0.0, self.dia, None))
                    inseriu.set()
                    time.sleep(ESPERA_TRANSACAO_SEGURADA)
                    conn_a.commit()
                finally:
                    conn_a.close()
            except Exception as erro:
                erros_a.append(erro)
            finally:
                inseriu.set()

        intermediarias = []

        def embrulhar(conn):
            intermediaria = ConexaoIntermediaria(conn)
            intermediarias.append(intermediaria)
            return intermediaria

        thread_a = threading.Thread(target=conexao_a)
        thread_a.start()
        inseriu.wait(30)
        self.embrulho = embrulhar
        inicio = time.monotonic()
        with self.esperas_gravadas() as esperas:
            resposta = self.post_cadastro(uid, {"nome": "Prova P6 B", "dia_fechamento": self.dia,
                                                "categorias": ["Mercado", "Lazer"]})
        duracao = time.monotonic() - inicio
        self.embrulho = None
        thread_a.join(30)
        intermediaria = intermediarias[0] if intermediarias else None
        erros = [self.descrever_erro(e) for e in intermediaria.erros] if intermediaria else []
        tentativas = (intermediaria.insercoes_usuario if intermediaria else 0)
        r.conferir("conexão A sem erro", not erros_a)
        r.conferir(f"409 usuario_ja_cadastrado (veio {resposta.status_code})",
                   resposta.status_code == 409 and resposta.json() == {"detail": "usuario_ja_cadastrado"})
        r.conferir("uma linha em usuarios (a da conexão A)", self.contar('usuarios', uid) == 1)
        r.conferir("nenhuma categoria", self.contar('categorias', uid) == 0)
        r.obtido = (f"{resposta.status_code}; {duracao:.2f} s; INSERT em usuarios tentados {tentativas}; "
                    f"esperas {list(esperas)}; erros no meio: {erros or 'nenhum'}"
                    + (f"; erro na conexão A: {self.descrever_erro(erros_a[0])}" if erros_a else ""))

    def cenario_p7(self):
        r = self.novo_resultado("P7", "Toque duplo real",
                                f"{RODADAS_TOQUE_DUPLO} rodadas: um 201 e os outros 409, nunca 500 nem 503; "
                                "uma linha e um jogo de categorias")
        lista = ["Mercado", "Lazer", "Casa"]
        erros = []
        rodadas_ok = 0
        detalhes = []

        def embrulhar(conn):
            return ConexaoIntermediaria(conn, erros=erros)

        self.embrulho = embrulhar
        with self.esperas_gravadas() as esperas:
            for rodada in range(1, RODADAS_TOQUE_DUPLO + 1):
                uid = self.proximo_id()
                pedidos = 3 if rodada in RODADAS_COM_TRES_PEDIDOS else 2
                barreira = threading.Barrier(pedidos, timeout=30)
                status = []

                def pedir(uid=uid, barreira=barreira, status=status):
                    client = self.novo_cliente()
                    cabecalho = self.cabecalho(uid)
                    try:
                        barreira.wait()
                    except threading.BrokenBarrierError:
                        pass
                    resposta = client.post("/cadastro", json={"nome": f"Prova P7 {rodada}",
                                                              "dia_fechamento": self.dia, "categorias": lista},
                                           headers=cabecalho)
                    status.append(resposta.status_code)

                threads = [threading.Thread(target=pedir) for _ in range(pedidos)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(60)
                usuarios = self.contar('usuarios', uid)
                nomes = [linha[1] for linha in self.linhas_do_usuario(uid)[1]]
                certo = (sorted(status) == [201] + [409] * (pedidos - 1) and usuarios == 1 and nomes == lista)
                rodadas_ok += certo
                detalhes.append(f"r{rodada}:{'/'.join(map(str, sorted(status)))}")
                r.conferir(f"rodada {rodada} ({pedidos} pedidos): status {sorted(status)}, usuarios {usuarios}, "
                           f"categorias {nomes}", certo)
        self.embrulho = None
        tipos = {}
        for erro in erros:
            tipos[type(erro).__name__] = tipos.get(type(erro).__name__, 0) + 1
        exemplo = self.descrever_erro(erros[0]) if erros else "nenhum"
        r.obtido = (f"{rodadas_ok}/{RODADAS_TOQUE_DUPLO} rodadas ok ({' '.join(detalhes)}); caminho do erro "
                    f"passageiro usado {len(esperas)} vez(es); erros do banco vistos: {tipos or 'nenhum'}; "
                    f"exemplo: {exemplo}")

    def cenario_p8(self):
        r = self.novo_resultado("P8", "Caminho antigo do bot (substituir=True)",
                                "uma linha com os dados da 2ª vez e só as categorias da 2ª lista")
        uid = self.proximo_id()
        primeira, segunda = ["Mercado", "Lazer"], ["Casa", "Saúde", "Pet"]
        self.trava.caminho_antigo_liberado = True
        try:
            conn = self.conectar()
            try:
                cadastro.gravar_cadastro(conn, uid, "Prova P8 A", 3000.0, self.dia, "20:00", primeira,
                                         registro.momento_agora(), substituir=True)
                cadastro.gravar_cadastro(conn, uid, "Prova P8 B", 3000.0, self.dia, "20:00", segunda,
                                         registro.momento_agora(), substituir=True)
            finally:
                conn.close()
        finally:
            self.trava.caminho_antigo_liberado = False
        usuario, categorias = self.linhas_do_usuario(uid)
        r.conferir("uma linha em usuarios com os dados da 2ª vez",
                   [linha[1:5] for linha in usuario] == [("Prova P8 B", 3000.0, self.dia, "20:00")])
        r.conferir("só as categorias da 2ª lista", [linha[1] for linha in categorias] == segunda)
        r.obtido = f"usuarios {[linha[1:5] for linha in usuario]}; categorias {[linha[1] for linha in categorias]}"

    def cenario_p9(self):
        r = self.novo_resultado("P9", "Conferência final", "contagens fora da faixa iguais às de P0")
        depois = self.contagens_fora_da_faixa()
        r.conferir("contagens fora da faixa iguais às de P0", depois == self.contagens_p0)
        r.obtido = f"P0 {self.contagens_p0}; agora {depois}"

    # --- relatório ---
    def imprimir_relatorio(self):
        self.escrever()
        self.escrever("RESULTADO")
        self.escrever("| CENÁRIO | ESPERADO | OBTIDO | RESULTADO |")
        self.escrever("|---|---|---|---|")
        for resultado in self.resultados:
            situacao = "OK" if resultado.ok else "FALHOU"
            obtido = self.ocultar(resultado.obtido).replace("|", "/")
            self.escrever(f"| {resultado.codigo} - {resultado.cenario} | {resultado.esperado} | {obtido} | {situacao} |")
        for resultado in self.resultados:
            for falha in resultado.falhas:
                self.escrever(f"  {resultado.codigo} FALHOU: {self.ocultar(falha)}")
        self.escrever()
        menor, maior = self.bloco
        self.escrever(f"BLOCO DESTA RODADA: {menor} a {maior}")
        self.escrever(f"IDS USADOS ({len(self.ids_usados)}): {', '.join(str(i) for i in self.ids_usados)}")
        self.escrever()
        self.escrever("LINHAS CRIADAS (usuarios):")
        for linha in self.ler("SELECT user_id, nome, renda_mensal, dia_fechamento, horario_alerta, "
                              "typeof(horario_alerta) FROM usuarios WHERE user_id BETWEEN ? AND ? "
                              "ORDER BY user_id DESC", (menor, maior)):
            self.escrever(f"  {json.dumps(list(linha), ensure_ascii=False)}")
        self.escrever("LINHAS CRIADAS (categorias):")
        for linha in self.ler("SELECT id, user_id, nome, orcamento_mensal, saldo_atual, data_criacao FROM categorias "
                              "WHERE user_id BETWEEN ? AND ? ORDER BY user_id DESC, id", (menor, maior)):
            self.escrever(f"  {json.dumps(list(linha), ensure_ascii=False)}")
        self.escrever()
        self.escrever(texto_da_limpeza())


def texto_da_limpeza():
    return (
        "LIMPEZA (OBRIGATÓRIA, ANTES DO COMMIT; a prova não apaga nada fora do P8):\n"
        "1. O Opus confere o que vai sair:\n"
        "   python ferramentas/banco_teste.py \"SELECT 'usuarios' AS tabela, COUNT(*) AS linhas FROM usuarios "
        f"WHERE user_id BETWEEN {FAIXA_INICIO} AND {FAIXA_FIM} UNION ALL SELECT 'categorias', COUNT(*) FROM "
        f"categorias WHERE user_id BETWEEN {FAIXA_INICIO} AND {FAIXA_FIM}\"\n"
        f"2. O Ygor roda no painel do Turso (banco {NOME_BANCO_TESTE}), nesta ordem:\n"
        f"   DELETE FROM categorias WHERE user_id BETWEEN {FAIXA_INICIO} AND {FAIXA_FIM};\n"
        f"   DELETE FROM usuarios WHERE user_id BETWEEN {FAIXA_INICIO} AND {FAIXA_FIM};\n"
        "3. O Opus confere: zero linhas na faixa nas duas tabelas e as contagens fora da faixa iguais às de P0."
    )


def montar_argumentos():
    p = argparse.ArgumentParser(description="Prova do cadastro (PRO-96) no banco de TESTE (fin4ncetest).")
    p.add_argument("--env", help="arquivo de ambiente (padrão: .env.teste, senão .env)")
    p.add_argument("--sqlite", help="roda contra um arquivo SQLite local (só para os testes das travas)")
    return p


def main(argv=None, conectar=conectar_turso, saida=None, erro=None):
    saida = saida or sys.stdout
    erro = erro or sys.stderr
    args = montar_argumentos().parse_args(argv)
    segredos = []
    try:
        if args.sqlite:
            caminho = args.sqlite

            def fabrica():
                return sqlite3.connect(caminho, timeout=10, check_same_thread=False)
        else:
            url, token = carregar_credenciais(escolher_arquivo_env(args.env))
            segredos = [url, token]
            if not e_banco_de_teste(url):
                raise ErroProva(f"ABORTADO: o arquivo de ambiente não aponta para o {NOME_BANCO_TESTE}. "
                                "Nada foi conectado.", 3)

            def fabrica():
                return conectar(url, token)
        prova = Prova(fabrica, saida, segredos)
        tudo_ok = prova.executar()
        saida.flush()
        if not tudo_ok:
            print("PROVA FALHOU: veja os cenários marcados FALHOU acima.", file=erro)
            return 1
        return 0
    except (ErroFerramenta, ErroProva) as e:
        print(f"ERRO: {ocultar_segredos(e, segredos)}", file=erro)
        return e.codigo
    except Exception as e:  # erro do banco ou da rede: nunca vaza segredo
        print(f"ERRO ({type(e).__name__}): {ocultar_segredos(e, segredos)}", file=erro)
        return 1


if __name__ == "__main__":
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    sys.exit(main())
