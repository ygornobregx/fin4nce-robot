"""Validação do initData das Web Apps (Mini Apps) do Telegram.

Segue a especificação oficial: o Telegram assina os campos com HMAC-SHA256
usando uma chave derivada do token do bot. Só biblioteca padrão, sem rede.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

# Tolerância para relógios levemente adiantados (segundos).
TOLERANCIA_FUTURO = 60


class InitDataError(Exception):
    """initData inválido. A mensagem descreve o motivo, nunca os dados."""


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int = 3600,
                       now: int | None = None) -> dict:
    """Valida o initData e devolve o dict do campo `user`."""
    try:
        pares = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise InitDataError("initData malformado")

    campos = {}
    for chave, valor in pares:
        if chave in campos:
            raise InitDataError("campo repetido no initData")
        campos[chave] = valor

    hash_recebido = campos.pop("hash", None)
    if hash_recebido is None:
        raise InitDataError("hash ausente")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(campos.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    esperado = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperado, hash_recebido):
        raise InitDataError("assinatura invalida")

    try:
        auth_date = int(campos["auth_date"])
    except (KeyError, ValueError):
        raise InitDataError("auth_date ausente ou invalido")

    agora = int(time.time()) if now is None else now
    if agora - auth_date > max_age_seconds:
        raise InitDataError("initData expirado")
    if auth_date - agora > TOLERANCIA_FUTURO:
        raise InitDataError("auth_date no futuro")

    if "user" not in campos:
        raise InitDataError("user ausente")
    try:
        user = json.loads(campos["user"])
    except ValueError:
        raise InitDataError("user nao e JSON valido")
    if not isinstance(user, dict):
        raise InitDataError("user nao e um objeto")
    user_id = user.get("id")
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        raise InitDataError("user.id ausente ou invalido")
    return user
