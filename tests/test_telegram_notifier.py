"""
test_telegram_notifier.py — Testes do envio via Telegram Bot API.

Sem acesso à rede: a Session HTTP é substituída por mock e time.sleep é
interceptado para não tornar a suíte lenta.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from telegram_notifier import (
    MAX_RATE_LIMIT_WAITS,
    MAX_RETRIES,
    DailySummary,
    NewBootcampNotification,
    TelegramNotifier,
    _NoopNotifier,
    build_notifier,
)


def resposta(status: int, corpo: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = corpo
    return r


OK = {"ok": True, "result": {"message_id": 1}}


@pytest.fixture
def notifier():
    n = TelegramNotifier("123:ABC", "999", min_send_interval=0)
    n._session = MagicMock()
    return n


# ---------------------------------------------------------------------------
# Envio básico
# ---------------------------------------------------------------------------

class TestEnvio:
    def test_envio_bem_sucedido(self, notifier):
        notifier._session.post.return_value = resposta(200, OK)
        assert notifier._send("oi") is True
        assert notifier._session.post.call_count == 1

    def test_ok_false_nao_faz_retry(self, notifier):
        notifier._session.post.return_value = resposta(200, {"ok": False, "description": "chat not found"})
        assert notifier._send("oi") is False
        assert notifier._session.post.call_count == 1

    def test_token_nao_aparece_no_payload_logado(self, notifier):
        notifier._session.post.return_value = resposta(200, OK)
        notifier._send("oi")
        _, kwargs = notifier._session.post.call_args
        assert "123:ABC" not in str(kwargs["json"])

    def test_erro_5xx_esgota_tentativas(self, notifier):
        notifier._session.post.return_value = resposta(500, {})
        with patch("telegram_notifier.time.sleep"):
            assert notifier._send("oi") is False
        assert notifier._session.post.call_count == MAX_RETRIES

    def test_erro_4xx_nao_faz_retry(self, notifier):
        """chat not found é erro de configuração: repetir 3x só atrasa a execução."""
        notifier._session.post.return_value = resposta(
            400, {"ok": False, "description": "Bad Request: chat not found"}
        )
        with patch("telegram_notifier.time.sleep"):
            assert notifier._send("oi") is False
        assert notifier._session.post.call_count == 1

    def test_erro_4xx_loga_descricao_e_dica(self, notifier, caplog):
        notifier._session.post.return_value = resposta(
            400, {"ok": False, "description": "Bad Request: chat not found"}
        )
        with caplog.at_level("ERROR"), patch("telegram_notifier.time.sleep"):
            notifier._send("oi")
        log = caplog.text
        assert "chat not found" in log
        assert "/start" in log, "O log precisa dizer o que fazer, não só o código HTTP."

    def test_dica_para_token_invalido(self, notifier, caplog):
        notifier._session.post.return_value = resposta(
            401, {"ok": False, "description": "Unauthorized"}
        )
        with caplog.at_level("ERROR"), patch("telegram_notifier.time.sleep"):
            notifier._send("oi")
        assert "BotFather" in caplog.text

    def test_erro_de_conexao_esgota_tentativas(self, notifier):
        notifier._session.post.side_effect = requests.exceptions.ConnectionError("sem rede")
        with patch("telegram_notifier.time.sleep"):
            assert notifier._send("oi") is False
        assert notifier._session.post.call_count == MAX_RETRIES


# ---------------------------------------------------------------------------
# Rate limit (429)
# ---------------------------------------------------------------------------

class TestRateLimit:
    def test_429_nao_consome_tentativa_de_erro(self, notifier):
        """Dois 429 seguidos e depois sucesso: a mensagem precisa ser entregue."""
        notifier._session.post.side_effect = [
            resposta(429, {"parameters": {"retry_after": 1}}),
            resposta(429, {"parameters": {"retry_after": 1}}),
            resposta(200, OK),
        ]
        with patch("telegram_notifier.time.sleep"):
            assert notifier._send("oi") is True
        assert notifier._session.post.call_count == 3

    def test_429_respeita_retry_after(self, notifier):
        notifier._session.post.side_effect = [
            resposta(429, {"parameters": {"retry_after": 7}}),
            resposta(200, OK),
        ]
        with patch("telegram_notifier.time.sleep") as sleep:
            notifier._send("oi")
        assert 7 in [c.args[0] for c in sleep.call_args_list]

    def test_429_persistente_desiste_sem_loop_infinito(self, notifier):
        notifier._session.post.return_value = resposta(429, {"parameters": {"retry_after": 1}})
        with patch("telegram_notifier.time.sleep"):
            assert notifier._send("oi") is False
        assert notifier._session.post.call_count == MAX_RATE_LIMIT_WAITS + 1

    def test_429_sem_corpo_json_usa_padrao(self, notifier):
        r = MagicMock()
        r.status_code = 429
        r.json.side_effect = ValueError("sem json")
        notifier._session.post.side_effect = [r, resposta(200, OK)]
        with patch("telegram_notifier.time.sleep"):
            assert notifier._send("oi") is True


# ---------------------------------------------------------------------------
# Throttle entre mensagens
# ---------------------------------------------------------------------------

class TestThrottle:
    def test_primeira_mensagem_nao_espera(self, notifier):
        notifier._min_send_interval = 1.2
        notifier._session.post.return_value = resposta(200, OK)
        with patch("telegram_notifier.time.sleep") as sleep:
            notifier._send("primeira")
        sleep.assert_not_called()

    def test_mensagem_seguinte_aguarda_intervalo(self, notifier):
        notifier._min_send_interval = 1.2
        notifier._session.post.return_value = resposta(200, OK)
        with patch("telegram_notifier.time.sleep") as sleep:
            with patch("telegram_notifier.time.monotonic", side_effect=[100.0, 100.1, 100.1]):
                notifier._send("primeira")
                notifier._send("segunda")
        assert sleep.call_count == 1
        assert sleep.call_args.args[0] == pytest.approx(1.1, abs=0.01)

    def test_nao_espera_se_ja_passou_o_intervalo(self, notifier):
        notifier._min_send_interval = 1.2
        notifier._session.post.return_value = resposta(200, OK)
        with patch("telegram_notifier.time.sleep") as sleep:
            with patch("telegram_notifier.time.monotonic", side_effect=[100.0, 105.0, 105.0]):
                notifier._send("primeira")
                notifier._send("segunda")
        sleep.assert_not_called()

    def test_intervalo_zero_desliga_throttle(self, notifier):
        notifier._min_send_interval = 0
        notifier._session.post.return_value = resposta(200, OK)
        with patch("telegram_notifier.time.sleep") as sleep:
            notifier._send("a")
            notifier._send("b")
        sleep.assert_not_called()

    def test_intervalo_negativo_e_normalizado(self):
        n = TelegramNotifier("123:ABC", "999", min_send_interval=-5)
        assert n._min_send_interval == 0.0


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestBuildNotifier:
    def test_sem_credenciais_retorna_noop(self):
        assert isinstance(build_notifier("", ""), _NoopNotifier)
        assert isinstance(build_notifier("123:ABC", ""), _NoopNotifier)
        assert isinstance(build_notifier("", "999"), _NoopNotifier)

    def test_com_credenciais_retorna_telegram(self):
        n = build_notifier("123:ABC", "999")
        assert isinstance(n, TelegramNotifier)
        n.close()

    def test_repassa_intervalo_minimo(self):
        n = build_notifier("123:ABC", "999", min_send_interval=3.5)
        assert n._min_send_interval == 3.5
        n.close()

    def test_noop_nao_quebra(self):
        n = _NoopNotifier()
        assert n.notify_new_bootcamp(
            NewBootcampNotification("n", "c", "u", "ALTA", 10, [], "obs", "hoje")
        ) is True
        assert n.send_daily_summary(DailySummary(0, 0, 0, 0, 0, 0)) is True
        n.close()


# ---------------------------------------------------------------------------
# Linha de prazo na mensagem
# ---------------------------------------------------------------------------

from telegram_notifier import _build_new_bootcamp_message


def notif(deadline="", days_left=None):
    return NewBootcampNotification(
        name="Bootcamp Teste", company="Empresa", url="https://www.dio.me/bootcamp/x",
        classification="ALTA", score=60, evidences=["evidência"],
        observation="obs", identified_at="03/08/2026",
        deadline=deadline, days_left=days_left,
    )


class TestLinhaDePrazo:
    def test_prazo_confortavel(self):
        msg = _build_new_bootcamp_message(notif("08/09/2026", 36))
        assert "abertas até 08/09/2026" in msg
        assert "36 dias" in msg

    def test_prazo_urgente_destaca(self):
        msg = _build_new_bootcamp_message(notif("10/08/2026", 7))
        assert "🔥" in msg
        assert "faltam 7 dias" in msg

    def test_ultimo_dia(self):
        msg = _build_new_bootcamp_message(notif("03/08/2026", 0))
        assert "ÚLTIMO DIA" in msg

    def test_encerra_amanha(self):
        assert "amanhã" in _build_new_bootcamp_message(notif("04/08/2026", 1))

    def test_encerrado(self):
        msg = _build_new_bootcamp_message(notif("14/04/2021", -1937))
        assert "encerradas em 14/04/2021" in msg

    def test_sem_prazo_omite_a_linha(self):
        """Sem prazo conhecido, a mensagem não deve exibir campo vago."""
        msg = _build_new_bootcamp_message(notif())
        assert "Inscrições" not in msg

    def test_prazo_e_escapado(self):
        msg = _build_new_bootcamp_message(notif("<b>hack</b>", 5))
        assert "<b>hack</b>" not in msg.split("Inscrições")[1][:60]
