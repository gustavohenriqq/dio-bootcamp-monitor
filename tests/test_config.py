"""
test_config.py — Testes do carregamento de configuração e do parser de .env.

Sem acesso à internet. Usa arquivos temporários e isola os.environ.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import Config, load_config, load_dotenv


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    """Remove as variáveis do projeto do ambiente antes de cada teste."""
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "INITIAL_NOTIFY",
        "SEND_DAILY_SUMMARY",
        "SEND_EMPTY_SUMMARY",
        "SEND_OPEN_DIGEST",
        "OPEN_DIGEST_WEEKDAYS",
        "MAX_DETAIL_PAGES",
        "REQUEST_DELAY_SECONDS",
        "LOG_LEVEL",
        "DIO_BOOTCAMP_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def escrever_env(tmp_path: Path, conteudo: str) -> Path:
    caminho = tmp_path / ".env"
    caminho.write_text(conteudo, encoding="utf-8")
    return caminho


# ---------------------------------------------------------------------------
# Parser de .env
# ---------------------------------------------------------------------------

class TestLoadDotenv:
    def test_carrega_pares_simples(self, tmp_path):
        env = escrever_env(tmp_path, "TELEGRAM_BOT_TOKEN=123:ABC\nTELEGRAM_CHAT_ID=999\n")
        assert load_dotenv(env) == 2
        assert os.environ["TELEGRAM_BOT_TOKEN"] == "123:ABC"
        assert os.environ["TELEGRAM_CHAT_ID"] == "999"

    def test_ignora_comentarios_e_linhas_vazias(self, tmp_path):
        env = escrever_env(
            tmp_path,
            "# comentario\n\n   \nTELEGRAM_CHAT_ID=42\n# outro comentario\n",
        )
        assert load_dotenv(env) == 1
        assert os.environ["TELEGRAM_CHAT_ID"] == "42"

    def test_remove_aspas_envolventes(self, tmp_path):
        env = escrever_env(
            tmp_path,
            'TELEGRAM_BOT_TOKEN="123:ABC"\nTELEGRAM_CHAT_ID=\'999\'\n',
        )
        load_dotenv(env)
        assert os.environ["TELEGRAM_BOT_TOKEN"] == "123:ABC"
        assert os.environ["TELEGRAM_CHAT_ID"] == "999"

    def test_aceita_prefixo_export(self, tmp_path):
        env = escrever_env(tmp_path, "export TELEGRAM_CHAT_ID=777\n")
        assert load_dotenv(env) == 1
        assert os.environ["TELEGRAM_CHAT_ID"] == "777"

    def test_preserva_sinal_de_igual_no_valor(self, tmp_path):
        env = escrever_env(tmp_path, "DIO_BOOTCAMP_URL=https://x.com/a?b=c&d=e\n")
        load_dotenv(env)
        assert os.environ["DIO_BOOTCAMP_URL"] == "https://x.com/a?b=c&d=e"

    def test_nao_sobrescreve_ambiente_por_padrao(self, tmp_path, monkeypatch):
        """No GitHub Actions os secrets vêm do ambiente e devem ter precedência."""
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "do-ambiente")
        env = escrever_env(tmp_path, "TELEGRAM_CHAT_ID=do-arquivo\n")
        assert load_dotenv(env) == 0
        assert os.environ["TELEGRAM_CHAT_ID"] == "do-ambiente"

    def test_override_true_sobrescreve(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "do-ambiente")
        env = escrever_env(tmp_path, "TELEGRAM_CHAT_ID=do-arquivo\n")
        assert load_dotenv(env, override=True) == 1
        assert os.environ["TELEGRAM_CHAT_ID"] == "do-arquivo"

    def test_arquivo_inexistente_nao_quebra(self, tmp_path):
        assert load_dotenv(tmp_path / "nao-existe.env") == 0

    def test_linha_sem_igual_e_ignorada(self, tmp_path):
        env = escrever_env(tmp_path, "LINHA SOLTA SEM IGUAL\nTELEGRAM_CHAT_ID=5\n")
        assert load_dotenv(env) == 1
        assert os.environ["TELEGRAM_CHAT_ID"] == "5"

    def test_valor_vazio_e_aceito(self, tmp_path):
        env = escrever_env(tmp_path, "TELEGRAM_BOT_TOKEN=\n")
        assert load_dotenv(env) == 1
        assert os.environ["TELEGRAM_BOT_TOKEN"] == ""


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_padroes_sem_ambiente(self):
        cfg = load_config(env_path=None)
        assert cfg.telegram_bot_token == ""
        assert cfg.initial_notify is False
        assert cfg.max_detail_pages == 10
        assert cfg.request_delay_seconds == 2.0
        assert cfg.log_level == "INFO"
        assert cfg.dio_bootcamp_url == "https://www.dio.me/bootcamp"

    def test_le_do_ambiente(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
        monkeypatch.setenv("INITIAL_NOTIFY", "true")
        monkeypatch.setenv("MAX_DETAIL_PAGES", "25")
        cfg = load_config(env_path=None)
        assert cfg.telegram_configured is True
        assert cfg.initial_notify is True
        assert cfg.max_detail_pages == 25

    def test_telegram_configured_exige_ambos(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
        assert load_config(env_path=None).telegram_configured is False

    def test_valores_invalidos_usam_padrao(self, monkeypatch):
        monkeypatch.setenv("MAX_DETAIL_PAGES", "abc")
        monkeypatch.setenv("REQUEST_DELAY_SECONDS", "xyz")
        cfg = load_config(env_path=None)
        assert cfg.max_detail_pages == 10
        assert cfg.request_delay_seconds == 2.0

    def test_max_detail_pages_respeita_minimo(self, monkeypatch):
        monkeypatch.setenv("MAX_DETAIL_PAGES", "0")
        assert load_config(env_path=None).max_detail_pages == 1


# ---------------------------------------------------------------------------
# Resumo de abertos
# ---------------------------------------------------------------------------

class TestConfigDigest:
    def test_padrao_desligado(self):
        cfg = load_config(env_path=None)
        assert cfg.send_open_digest is False
        assert cfg.open_digest_weekdays == ()

    def test_liga_pelo_ambiente(self, monkeypatch):
        monkeypatch.setenv("SEND_OPEN_DIGEST", "true")
        assert load_config(env_path=None).send_open_digest is True

    def test_dias_da_semana(self, monkeypatch):
        monkeypatch.setenv("OPEN_DIGEST_WEEKDAYS", "0,4")
        assert load_config(env_path=None).open_digest_weekdays == (0, 4)

    def test_dias_com_espacos_e_ordem_trocada(self, monkeypatch):
        monkeypatch.setenv("OPEN_DIGEST_WEEKDAYS", " 4 , 0 ,4")
        assert load_config(env_path=None).open_digest_weekdays == (0, 4)

    def test_descarta_dias_invalidos(self, monkeypatch):
        monkeypatch.setenv("OPEN_DIGEST_WEEKDAYS", "0,9,abc,-1,6")
        assert load_config(env_path=None).open_digest_weekdays == (0, 6)

    def test_lista_vazia_significa_todos_os_dias(self, monkeypatch):
        monkeypatch.setenv("OPEN_DIGEST_WEEKDAYS", "")
        assert load_config(env_path=None).open_digest_weekdays == ()
