"""
telegram_notifier.py — Envio de notificações via Telegram Bot API.

Usa HTML como modo de formatação (parse_mode=HTML).
Faz escaping seguro de todos os campos dinâmicos.
Não expõe tokens em logs.
Erros no Telegram não interrompem a execução principal.
"""

import html
import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
RETRY_DELAY = 5  # segundos

# A Bot API aceita cerca de 1 mensagem por segundo em um mesmo chat. Sem um
# intervalo mínimo entre envios, lotes grandes (ex.: baseline inicial com o
# catálogo inteiro) tomam 429 em sequência e mensagens acabam descartadas.
MIN_SEND_INTERVAL = 1.2  # segundos entre mensagens consecutivas

# Quantas esperas por rate limit tolerar em uma única mensagem. Diferente de
# MAX_RETRIES: um 429 não é falha, é instrução para aguardar.
MAX_RATE_LIMIT_WAITS = 5


# ---------------------------------------------------------------------------
# Helpers de escaping e formatação
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """Escapa texto para uso seguro em mensagem HTML do Telegram."""
    return html.escape(str(text or ""), quote=False)


def _descricao_do_erro(response) -> str:
    """Extrai o campo `description` da resposta de erro da Bot API."""
    try:
        return str(response.json().get("description") or "sem descrição")
    except (ValueError, AttributeError, requests.exceptions.JSONDecodeError):
        return "resposta sem JSON"


def _dica_para_erro(status_code: int, descricao: str) -> str:
    """
    Traduz erros comuns da Bot API em ação concreta.

    A Bot API responde 400 tanto para chat inexistente quanto para HTML inválido;
    sem essa distinção o log não indica o que corrigir.
    """
    desc = descricao.lower()

    if "chat not found" in desc:
        return (
            "O bot nunca recebeu mensagem desse chat. Abra o Telegram, procure o bot "
            "e envie /start — um bot não pode iniciar conversa. Depois confira o "
            "TELEGRAM_CHAT_ID em https://api.telegram.org/bot<TOKEN>/getUpdates."
        )
    if "bot was blocked" in desc:
        return "O bot foi bloqueado por esse usuário. Desbloqueie o bot no Telegram."
    if "unauthorized" in desc or status_code == 401:
        return "TELEGRAM_BOT_TOKEN inválido ou revogado. Gere outro com o @BotFather."
    if "can't parse entities" in desc or "can't parse" in desc:
        return "A mensagem tem HTML inválido para o parse_mode=HTML. É bug de formatação, não de configuração."
    if "chat_id is empty" in desc:
        return "TELEGRAM_CHAT_ID não foi preenchido."
    return "Verifique TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID."


def _truncate(text: str, max_len: int = 200) -> str:
    """Trunca texto para evitar mensagens excessivamente longas."""
    text = str(text or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


# ---------------------------------------------------------------------------
# Estruturas de dados de notificação
# ---------------------------------------------------------------------------

@dataclass
class NewBootcampNotification:
    """Dados para notificação de novo bootcamp."""

    name: str
    company: str
    url: str
    classification: str
    score: int
    evidences: list[str]
    observation: str
    identified_at: str
    deadline: str = ""
    days_left: Optional[int] = None


@dataclass
class UpdateNotification:
    """Dados para notificação de atualização de bootcamp."""

    name: str
    company: str
    url: str
    previous_classification: str
    current_classification: str
    evidences: list[str]


@dataclass
class DailySummary:
    """Dados para o resumo diário."""

    new_count: int
    update_count: int
    alta_count: int
    media_count: int
    baixa_count: int
    expirada_count: int


@dataclass
class OpenBootcamp:
    """Um bootcamp com inscrição aberta, para o resumo de abertos."""

    name: str
    url: str
    classification: str
    deadline: str
    days_left: int


# ---------------------------------------------------------------------------
# Construção das mensagens
# ---------------------------------------------------------------------------

def _linha_de_prazo(deadline: str, days_left: Optional[int]) -> str:
    """
    Linha de prazo de inscrição, com urgência destacada.

    Retorna string vazia quando não há prazo conhecido, para a mensagem não
    exibir um campo vago.
    """
    if not deadline or days_left is None:
        return "\n"

    quando = _esc(deadline)

    if days_left < 0:
        return f"⌛ <b>Inscrições:</b> encerradas em {quando}\n\n"
    if days_left == 0:
        return f"🔥 <b>Inscrições:</b> ÚLTIMO DIA ({quando})\n\n"
    if days_left == 1:
        return f"🔥 <b>Inscrições:</b> encerram amanhã ({quando})\n\n"
    if days_left <= 7:
        return f"🔥 <b>Inscrições:</b> abertas, faltam {days_left} dias ({quando})\n\n"
    return f"✅ <b>Inscrições:</b> abertas até {quando} ({days_left} dias)\n\n"


def _build_new_bootcamp_message(n: NewBootcampNotification) -> str:
    """Constrói mensagem HTML para novo bootcamp."""
    evidences_html = "\n".join(
        f"  • {_esc(_truncate(ev, 120))}" for ev in n.evidences[:5]
    ) or "  • (sem evidências específicas identificadas)"

    return (
        f"🚨 <b>NOVO BOOTCAMP DIO</b>\n\n"
        f"🏢 <b>Empresa:</b> {_esc(n.company) or 'não informada'}\n"
        f"📚 <b>Bootcamp:</b> {_esc(n.name)}\n"
        f"🎯 <b>Chance de contratação:</b> {_esc(n.classification)}\n"
        f"📊 <b>Pontuação:</b> {n.score}\n"
        f"{_linha_de_prazo(n.deadline, n.days_left)}\n"
        f"🔎 <b>Evidências:</b>\n{evidences_html}\n\n"
        f"⚠️ <b>Observação:</b> {_esc(_truncate(n.observation, 200))}\n\n"
        f"🔗 <b>Página:</b> {_esc(n.url)}\n"
        f"📅 <b>Identificado em:</b> {_esc(n.identified_at)}"
    )


def _build_update_message(u: UpdateNotification) -> str:
    """Constrói mensagem HTML para atualização de bootcamp."""
    evidences_html = "\n".join(
        f"  • {_esc(_truncate(ev, 120))}" for ev in u.evidences[:5]
    ) or "  • (sem novas evidências específicas)"

    return (
        f"🔄 <b>ATUALIZAÇÃO EM BOOTCAMP DIO</b>\n\n"
        f"🏢 <b>Empresa:</b> {_esc(u.company) or 'não informada'}\n"
        f"📚 <b>Bootcamp:</b> {_esc(u.name)}\n\n"
        f"A classificação mudou: "
        f"<b>{_esc(u.previous_classification)}</b> → <b>{_esc(u.current_classification)}</b>\n\n"
        f"Novas evidências:\n{evidences_html}\n\n"
        f"🔗 {_esc(u.url)}"
    )


# Limite da Bot API por mensagem. Truncamos com folga para caber cabeçalho e rodapé.
MAX_MESSAGE_CHARS = 4000
MAX_DIGEST_ITEMS = 25


def _build_open_digest_message(abertos: list[OpenBootcamp]) -> str:
    """
    Monta o resumo dos bootcamps com inscrição aberta, do mais urgente ao menos.

    Trunca a lista se necessário: a Bot API rejeita mensagens acima de 4096
    caracteres, e uma mensagem perdida é pior que uma lista incompleta.
    """
    if not abertos:
        return (
            "📋 <b>BOOTCAMPS COM INSCRIÇÃO ABERTA</b>\n\n"
            "Nenhum bootcamp com inscrição aberta no momento.\n"
            "Você será avisado assim que abrir algum."
        )

    ordenados = sorted(abertos, key=lambda b: b.days_left)
    mostrados = ordenados[:MAX_DIGEST_ITEMS]
    ocultos = len(ordenados) - len(mostrados)

    blocos: list[str] = []
    for b in mostrados:
        if b.days_left == 0:
            prazo = f"🔥 <b>ÚLTIMO DIA</b> ({_esc(b.deadline)})"
        elif b.days_left == 1:
            prazo = f"🔥 <b>encerra amanhã</b> ({_esc(b.deadline)})"
        elif b.days_left <= 7:
            prazo = f"🔥 faltam <b>{b.days_left} dias</b> ({_esc(b.deadline)})"
        else:
            prazo = f"✅ até {_esc(b.deadline)} ({b.days_left} dias)"

        blocos.append(
            f"<b>{_esc(_truncate(b.name, 70))}</b>\n"
            f"{prazo} · {_esc(b.classification)}\n"
            f"{_esc(b.url)}"
        )

    corpo = "\n\n".join(blocos)
    cabecalho = f"📋 <b>BOOTCAMPS COM INSCRIÇÃO ABERTA ({len(ordenados)})</b>\n\n"
    rodape = f"\n\n<i>… e mais {ocultos} não listados.</i>" if ocultos > 0 else ""

    mensagem = cabecalho + corpo + rodape

    # Salvaguarda: se ainda estourar, corta blocos até caber.
    while len(mensagem) > MAX_MESSAGE_CHARS and len(blocos) > 1:
        blocos.pop()
        ocultos = len(ordenados) - len(blocos)
        rodape = f"\n\n<i>… e mais {ocultos} não listados.</i>"
        mensagem = cabecalho + "\n\n".join(blocos) + rodape

    return mensagem


def _build_summary_message(s: DailySummary) -> str:
    """Constrói mensagem HTML do resumo diário."""
    return (
        f"📊 <b>RESUMO DIO</b>\n\n"
        f"Novos bootcamps: <b>{s.new_count}</b>\n"
        f"Atualizações: <b>{s.update_count}</b>\n"
        f"Chance alta: <b>{s.alta_count}</b>\n"
        f"Chance média: <b>{s.media_count}</b>\n"
        f"Chance baixa: <b>{s.baixa_count}</b>\n"
        f"Expirados ignorados: <b>{s.expirada_count}</b>"
    )


# ---------------------------------------------------------------------------
# Envio de mensagens
# ---------------------------------------------------------------------------

class TelegramNotifier:
    """
    Envia mensagens para um chat do Telegram via Bot API.

    Não loga o token. Erros são capturados e logados sem propagar.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        min_send_interval: float = MIN_SEND_INTERVAL,
    ):
        self._token = bot_token
        self._chat_id = chat_id
        self._api_url = TELEGRAM_API_BASE.format(token=bot_token)
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._min_send_interval = max(min_send_interval, 0.0)
        self._last_send_at: Optional[float] = None

    def _throttle(self) -> None:
        """Garante o intervalo mínimo desde o envio anterior."""
        if self._min_send_interval <= 0 or self._last_send_at is None:
            return
        espera = self._min_send_interval - (time.monotonic() - self._last_send_at)
        if espera > 0:
            time.sleep(espera)

    def _send(self, message: str) -> bool:
        """
        Envia mensagem respeitando o intervalo mínimo entre envios, com retry.

        Args:
            message: texto HTML da mensagem.

        Returns:
            True se enviado com sucesso, False caso contrário.
        """
        payload = {
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        rate_limit_waits = 0
        attempt = 0

        while attempt < MAX_RETRIES:
            attempt += 1
            self._throttle()

            try:
                response = self._session.post(
                    self._api_url,
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                )
                self._last_send_at = time.monotonic()

                if response.status_code == 200:
                    data = response.json()
                    if data.get("ok"):
                        logger.info("Mensagem Telegram enviada com sucesso.")
                        return True
                    else:
                        logger.error(
                            "Telegram retornou ok=false: %s",
                            data.get("description", "sem descrição"),
                        )
                        return False

                if response.status_code == 429:
                    # Rate limit não é falha: aguarda o retry_after indicado e
                    # repete sem consumir uma das tentativas de erro.
                    rate_limit_waits += 1
                    if rate_limit_waits > MAX_RATE_LIMIT_WAITS:
                        logger.error(
                            "Rate limit persistente do Telegram após %d esperas. Desistindo desta mensagem.",
                            MAX_RATE_LIMIT_WAITS,
                        )
                        return False
                    try:
                        retry_after = float(
                            response.json().get("parameters", {}).get("retry_after", RETRY_DELAY)
                        )
                    except (ValueError, TypeError, requests.exceptions.JSONDecodeError):
                        retry_after = RETRY_DELAY
                    logger.warning(
                        "Rate limit Telegram. Aguardando %.1fs (espera %d/%d).",
                        retry_after, rate_limit_waits, MAX_RATE_LIMIT_WAITS,
                    )
                    time.sleep(retry_after)
                    attempt -= 1
                    continue

                descricao = _descricao_do_erro(response)
                logger.error(
                    "Erro HTTP %d ao enviar para Telegram (tentativa %d/%d): %s",
                    response.status_code, attempt, MAX_RETRIES, descricao,
                )

                # 4xx (exceto 429) são erros de configuração — repetir não resolve.
                if 400 <= response.status_code < 500:
                    logger.error(
                        "Erro definitivo de configuração do Telegram. %s",
                        _dica_para_erro(response.status_code, descricao),
                    )
                    return False

                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)

            except requests.exceptions.RequestException as exc:
                self._last_send_at = time.monotonic()
                logger.error(
                    "Erro de conexão ao enviar para Telegram (tentativa %d/%d): %s",
                    attempt, MAX_RETRIES, exc,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)

        logger.error("Falha definitiva ao enviar mensagem para Telegram após %d tentativas.", MAX_RETRIES)
        return False

    def notify_new_bootcamp(self, n: NewBootcampNotification) -> bool:
        """Envia notificação de novo bootcamp."""
        message = _build_new_bootcamp_message(n)
        return self._send(message)

    def notify_update(self, u: UpdateNotification) -> bool:
        """Envia notificação de atualização de bootcamp."""
        message = _build_update_message(u)
        return self._send(message)

    def send_daily_summary(
        self,
        summary: DailySummary,
        send_empty: bool = False,
    ) -> bool:
        """
        Envia resumo diário.

        Args:
            summary: dados do resumo.
            send_empty: se True, envia mesmo sem novidades.

        Returns:
            True se enviado ou ignorado intencionalmente, False em caso de erro.
        """
        has_activity = (
            summary.new_count > 0
            or summary.update_count > 0
        )

        if not has_activity and not send_empty:
            logger.info("Resumo diário sem novidades e SEND_EMPTY_SUMMARY=false. Pulando.")
            return True

        message = _build_summary_message(summary)
        return self._send(message)

    def send_open_digest(
        self,
        abertos: list[OpenBootcamp],
        send_empty: bool = False,
    ) -> bool:
        """
        Envia o resumo dos bootcamps com inscrição aberta.

        Args:
            abertos: bootcamps com inscrição em aberto.
            send_empty: se True, envia mesmo quando não há nenhum aberto.

        Returns:
            True se enviado ou ignorado intencionalmente.
        """
        if not abertos and not send_empty:
            logger.info("Nenhum bootcamp aberto e envio de resumo vazio desativado. Pulando.")
            return True

        return self._send(_build_open_digest_message(abertos))

    def close(self) -> None:
        """Fecha a Session HTTP."""
        self._session.close()


# ---------------------------------------------------------------------------
# Factory: retorna notifier ou stub noop
# ---------------------------------------------------------------------------

class _NoopNotifier:
    """Notifier que não faz nada — usado quando Telegram não está configurado."""

    def notify_new_bootcamp(self, n: NewBootcampNotification) -> bool:
        logger.info("Telegram não configurado. Notificação de novo bootcamp ignorada: %s", n.name)
        return True

    def notify_update(self, u: UpdateNotification) -> bool:
        logger.info("Telegram não configurado. Notificação de atualização ignorada: %s", u.name)
        return True

    def send_daily_summary(self, summary: DailySummary, send_empty: bool = False) -> bool:
        logger.info("Telegram não configurado. Resumo diário ignorado.")
        return True

    def send_open_digest(self, abertos: list, send_empty: bool = False) -> bool:
        logger.info(
            "Telegram não configurado. Resumo de abertos ignorado (%d bootcamps).",
            len(abertos),
        )
        return True

    def close(self) -> None:
        pass


def build_notifier(
    bot_token: str,
    chat_id: str,
    min_send_interval: float = MIN_SEND_INTERVAL,
) -> "TelegramNotifier | _NoopNotifier":
    """
    Retorna um TelegramNotifier configurado ou um stub noop.

    Args:
        bot_token: token do bot do Telegram.
        chat_id: ID do chat de destino.
        min_send_interval: intervalo mínimo entre mensagens, em segundos.

    Returns:
        TelegramNotifier se configurado, _NoopNotifier caso contrário.
    """
    if bot_token and chat_id:
        return TelegramNotifier(bot_token, chat_id, min_send_interval=min_send_interval)
    logger.warning("Telegram não configurado. Usando notifier noop.")
    return _NoopNotifier()
