"""
config.py — Leitura, parsing e validação das configurações de ambiente.

Todas as variáveis são lidas de variáveis de ambiente. Para execução local,
um arquivo .env na raiz do projeto é carregado automaticamente por um parser
mínimo embutido (sem dependência de python-dotenv, mantendo as dependências
enxutas). Variáveis já presentes no ambiente têm precedência sobre o .env —
o que preserva o comportamento no GitHub Actions, onde os secrets são
injetados diretamente no ambiente.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Raiz do projeto: src/config.py → src/ → raiz
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


def load_dotenv(path: Path = DEFAULT_ENV_PATH, override: bool = False) -> int:
    """
    Carrega pares CHAVE=VALOR de um arquivo .env para os.environ.

    Suporta comentários (#), linhas em branco, aspas simples/duplas ao redor do
    valor e o prefixo opcional 'export '. Linhas malformadas são ignoradas com
    aviso, sem interromper a execução.

    Args:
        path: caminho do arquivo .env.
        override: se False (padrão), não sobrescreve variáveis já definidas.

    Returns:
        Quantidade de variáveis efetivamente carregadas.
    """
    if not path.is_file():
        logger.debug("Arquivo .env não encontrado em %s. Usando apenas o ambiente.", path)
        return 0

    loaded = 0
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Não foi possível ler %s: %s. Usando apenas o ambiente.", path, exc)
        return 0

    for lineno, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export "):].strip()

        if "=" not in line:
            logger.warning("Linha %d do .env ignorada (sem '='): %s", lineno, raw_line[:40])
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        # Remove aspas envolventes, preservando o conteúdo interno
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        if not override and key in os.environ:
            continue

        os.environ[key] = value
        loaded += 1

    if loaded:
        logger.debug("Carregadas %d variáveis de %s.", loaded, path)

    return loaded

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env_bool(key: str, default: bool) -> bool:
    """Lê variável de ambiente como booleano (true/1/yes → True)."""
    raw = os.environ.get(key, "").strip().lower()
    if raw in ("true", "1", "yes"):
        return True
    if raw in ("false", "0", "no"):
        return False
    return default


def _env_int(key: str, default: int, min_val: int = 0) -> int:
    """Lê variável de ambiente como inteiro com valor mínimo."""
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return max(value, min_val)
    except ValueError:
        logger.warning("Variável %s com valor inválido '%s', usando padrão %d.", key, raw, default)
        return default


def _env_str(key: str, default: str) -> str:
    """Lê variável de ambiente como string, retorna padrão se vazia."""
    return os.environ.get(key, "").strip() or default


def _env_weekdays(key: str) -> tuple[int, ...]:
    """
    Lê lista de dias da semana separados por vírgula (0=segunda … 6=domingo).

    Valores fora de 0-6 ou não numéricos são descartados com aviso. Lista vazia
    significa "todos os dias".
    """
    raw = os.environ.get(key, "").strip()
    if not raw:
        return ()

    dias: list[int] = []
    for parte in raw.split(","):
        parte = parte.strip()
        if not parte:
            continue
        try:
            dia = int(parte)
        except ValueError:
            logger.warning("Valor inválido em %s: '%s' não é um número. Ignorando.", key, parte)
            continue
        if 0 <= dia <= 6:
            if dia not in dias:
                dias.append(dia)
        else:
            logger.warning("Valor fora de 0-6 em %s: %d. Ignorando.", key, dia)

    return tuple(sorted(dias))


# ---------------------------------------------------------------------------
# Dataclass de configuração
# ---------------------------------------------------------------------------

@dataclass
class Config:
    """Agrupa todas as configurações da aplicação."""

    telegram_bot_token: str = field(default="")
    telegram_chat_id: str = field(default="")

    initial_notify: bool = False
    send_daily_summary: bool = False
    send_empty_summary: bool = False
    # Resumo com todos os bootcamps de inscrição aberta, ao final da execução.
    send_open_digest: bool = False
    # Restringe o resumo de abertos a dias da semana (0=segunda … 6=domingo).
    # Vazio = todos os dias.
    open_digest_weekdays: tuple[int, ...] = ()

    max_detail_pages: int = 10
    request_delay_seconds: float = 2.0

    log_level: str = "INFO"
    dio_bootcamp_url: str = "https://www.dio.me/bootcamp"

    @property
    def telegram_configured(self) -> bool:
        """Verifica se as credenciais do Telegram estão presentes."""
        return bool(self.telegram_bot_token and self.telegram_chat_id)


def load_config(env_path: Optional[Path] = DEFAULT_ENV_PATH) -> Config:
    """
    Carrega e valida as configurações a partir das variáveis de ambiente.

    Args:
        env_path: caminho do .env a carregar antes da leitura do ambiente.
            Passe None para ignorar qualquer arquivo e usar só os.environ —
            usado nos testes para não depender do .env local do desenvolvedor.

    Returns:
        Config: objeto com todas as configurações validadas.
    """
    if env_path is not None:
        load_dotenv(env_path)

    delay_raw = os.environ.get("REQUEST_DELAY_SECONDS", "").strip()
    try:
        delay = max(float(delay_raw), 0.0) if delay_raw else 2.0
    except ValueError:
        logger.warning("REQUEST_DELAY_SECONDS inválido, usando 2.0s.")
        delay = 2.0

    cfg = Config(
        telegram_bot_token=_env_str("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=_env_str("TELEGRAM_CHAT_ID", ""),
        initial_notify=_env_bool("INITIAL_NOTIFY", False),
        send_daily_summary=_env_bool("SEND_DAILY_SUMMARY", False),
        send_empty_summary=_env_bool("SEND_EMPTY_SUMMARY", False),
        send_open_digest=_env_bool("SEND_OPEN_DIGEST", False),
        open_digest_weekdays=_env_weekdays("OPEN_DIGEST_WEEKDAYS"),
        max_detail_pages=_env_int("MAX_DETAIL_PAGES", 10, min_val=1),
        request_delay_seconds=delay,
        log_level=_env_str("LOG_LEVEL", "INFO").upper(),
        dio_bootcamp_url=_env_str("DIO_BOOTCAMP_URL", "https://www.dio.me/bootcamp"),
    )

    if not cfg.telegram_configured:
        logger.warning(
            "TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurados. "
            "Notificações serão desativadas."
        )

    return cfg
