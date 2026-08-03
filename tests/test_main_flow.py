"""
test_main_flow.py — Testes de integração do fluxo principal.

Sem acesso à internet. Usa monkeypatching completo do scraper e notifier.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import Config
from storage import BootcampRecord, load_history, save_history
from dio_scraper import CatalogBootcamp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(**kwargs) -> Config:
    defaults = dict(
        telegram_bot_token="",
        telegram_chat_id="",
        initial_notify=False,
        send_daily_summary=False,
        send_empty_summary=False,
        max_detail_pages=5,
        request_delay_seconds=0,
        log_level="WARNING",
        dio_bootcamp_url="https://www.dio.me/bootcamp",
    )
    defaults.update(kwargs)
    return Config(**defaults)


def make_catalog_bootcamp(
    stable_id: str = "bc-teste-001",
    name: str = "Bootcamp Teste",
    company: str = "Empresa Teste",
    url: str = "https://www.dio.me/bootcamp/bc-teste-001",
    summary: str = "",
) -> CatalogBootcamp:
    return CatalogBootcamp(
        stable_id=stable_id,
        name=name,
        company=company,
        url=url,
        summary=summary,
        catalog_position=0,
    )


DETAIL_TEXT_ALTA = (
    "Processo seletivo ativo para 20 vagas de emprego disponíveis. "
    "Recrutadores da empresa acompanharão os participantes. "
    "Candidatos selecionados farão entrevistas técnicas com a empresa."
)

DETAIL_TEXT_BAIXA = (
    "Certificado para o currículo e perfil disponível na Talent Match."
)


# ---------------------------------------------------------------------------
# Testes de primeira execução
# ---------------------------------------------------------------------------

class TestPrimeiraExecucao:
    def test_primeira_execucao_sem_initial_notify_nao_notifica(self, tmp_path):
        """Na primeira execução com INITIAL_NOTIFY=false, registra sem notificar."""
        from main import run

        config = make_config(initial_notify=False)
        storage_path = tmp_path / "bootcamps.json"

        catalog = [make_catalog_bootcamp()]

        with patch("main.DioScraper") as MockScraper, \
             patch("main.build_notifier") as MockNotifier:

            mock_scraper = MagicMock()
            mock_scraper.fetch_catalog.return_value = catalog
            mock_scraper.fetch_detail.return_value = DETAIL_TEXT_ALTA
            MockScraper.return_value = mock_scraper

            mock_notifier = MagicMock()
            MockNotifier.return_value = mock_notifier

            run(config, storage_path)

            # Não deve ter enviado notificação de novo bootcamp
            mock_notifier.notify_new_bootcamp.assert_not_called()

        # Deve ter salvo no histórico
        history = load_history(storage_path)
        assert "bc-teste-001" in history
        assert history["bc-teste-001"].notification_status == "skipped"

    def test_primeira_execucao_com_initial_notify_notifica(self, tmp_path):
        """Na primeira execução com INITIAL_NOTIFY=true, deve notificar."""
        from main import run

        config = make_config(initial_notify=True)
        storage_path = tmp_path / "bootcamps.json"

        catalog = [make_catalog_bootcamp()]

        with patch("main.DioScraper") as MockScraper, \
             patch("main.build_notifier") as MockNotifier:

            mock_scraper = MagicMock()
            mock_scraper.fetch_catalog.return_value = catalog
            mock_scraper.fetch_detail.return_value = DETAIL_TEXT_BAIXA
            MockScraper.return_value = mock_scraper

            mock_notifier = MagicMock()
            MockNotifier.return_value = mock_notifier

            run(config, storage_path)

            mock_notifier.notify_new_bootcamp.assert_called_once()


# ---------------------------------------------------------------------------
# Testes de detecção de novos bootcamps
# ---------------------------------------------------------------------------

class TestDeteccaoNovos:
    def test_novo_bootcamp_detectado_e_salvo(self, tmp_path):
        from main import run

        config = make_config(initial_notify=True)
        storage_path = tmp_path / "bootcamps.json"

        # Histórico com um bootcamp já conhecido
        existing = BootcampRecord(
            stable_id="bc-existente",
            name="Bootcamp Existente",
            company="Empresa X",
            url="https://www.dio.me/bootcamp/bc-existente",
            first_seen_at="2026-01-01T10:00:00-03:00",
            last_checked_at="2026-01-01T10:00:00-03:00",
            notification_status="sent",
        )
        save_history({"bc-existente": existing}, storage_path)

        # Catálogo com o existente + um novo
        novo = make_catalog_bootcamp(
            stable_id="bc-novo",
            name="Bootcamp Novo",
            url="https://www.dio.me/bootcamp/bc-novo",
        )
        existente_catalog = make_catalog_bootcamp(
            stable_id="bc-existente",
            name="Bootcamp Existente",
            url="https://www.dio.me/bootcamp/bc-existente",
        )

        with patch("main.DioScraper") as MockScraper, \
             patch("main.build_notifier") as MockNotifier:

            mock_scraper = MagicMock()
            mock_scraper.fetch_catalog.return_value = [existente_catalog, novo]
            mock_scraper.fetch_detail.return_value = DETAIL_TEXT_BAIXA
            MockScraper.return_value = mock_scraper

            mock_notifier = MagicMock()
            MockNotifier.return_value = mock_notifier

            run(config, storage_path)

            # Deve ter notificado apenas o novo
            mock_notifier.notify_new_bootcamp.assert_called_once()

        history = load_history(storage_path)
        assert "bc-novo" in history
        assert "bc-existente" in history

    def test_bootcamp_expirado_nao_notificado(self, tmp_path):
        """Bootcamps classificados como EXPIRADA não devem gerar notificação."""
        from main import run

        config = make_config(initial_notify=True)
        storage_path = tmp_path / "bootcamps.json"

        expirado = make_catalog_bootcamp(
            stable_id="bc-expirado",
            name="Bootcamp 2024 Encerrado",
            url="https://www.dio.me/bootcamp/bc-expirado",
        )

        DETAIL_EXPIRADO = (
            "As inscrições foram encerradas em 2024. "
            "A seleção foi finalizada com sucesso."
        )

        with patch("main.DioScraper") as MockScraper, \
             patch("main.build_notifier") as MockNotifier:

            mock_scraper = MagicMock()
            mock_scraper.fetch_catalog.return_value = [expirado]
            mock_scraper.fetch_detail.return_value = DETAIL_EXPIRADO
            MockScraper.return_value = mock_scraper

            mock_notifier = MagicMock()
            MockNotifier.return_value = mock_notifier

            run(config, storage_path)

            mock_notifier.notify_new_bootcamp.assert_not_called()


# ---------------------------------------------------------------------------
# Testes de prevenção de duplicidade
# ---------------------------------------------------------------------------

class TestPrevencaoDuplicidade:
    def test_nao_renotifica_bootcamp_ja_enviado(self, tmp_path):
        """Bootcamp com notification_status='sent' não deve ser notificado novamente."""
        from main import run

        config = make_config(initial_notify=True)
        storage_path = tmp_path / "bootcamps.json"

        # Registra bootcamp já notificado
        ja_notificado = BootcampRecord(
            stable_id="bc-ja-notificado",
            name="Bootcamp Notificado",
            company="Empresa",
            url="https://www.dio.me/bootcamp/bc-ja-notificado",
            first_seen_at="2026-06-01T10:00:00-03:00",
            last_checked_at="2026-06-01T10:00:00-03:00",
            notification_status="sent",
            classification="ALTA",
            relevant_excerpt_hash="hash-original",
        )
        save_history({"bc-ja-notificado": ja_notificado}, storage_path)

        # Catálogo retorna o mesmo bootcamp sem mudança
        catalog_item = make_catalog_bootcamp(
            stable_id="bc-ja-notificado",
            name="Bootcamp Notificado",
            url="https://www.dio.me/bootcamp/bc-ja-notificado",
        )

        with patch("main.DioScraper") as MockScraper, \
             patch("main.build_notifier") as MockNotifier:

            mock_scraper = MagicMock()
            mock_scraper.fetch_catalog.return_value = [catalog_item]
            # Detalhe retorna mesmo texto (mesmo hash)
            mock_scraper.fetch_detail.return_value = DETAIL_TEXT_ALTA
            MockScraper.return_value = mock_scraper

            mock_notifier = MagicMock()
            MockNotifier.return_value = mock_notifier

            run(config, storage_path)

            # Notificação de NOVO não deve ser chamada (já existia)
            mock_notifier.notify_new_bootcamp.assert_not_called()


# ---------------------------------------------------------------------------
# Testes de resiliência
# ---------------------------------------------------------------------------

class TestResiliencia:
    def test_falha_em_detalhe_nao_para_execucao(self, tmp_path):
        """Erro ao buscar detalhe de um bootcamp não deve interromper os demais."""
        from main import run

        config = make_config(initial_notify=True)
        storage_path = tmp_path / "bootcamps.json"

        bc1 = make_catalog_bootcamp(stable_id="bc-ok", name="Bootcamp OK", url="https://www.dio.me/bootcamp/bc-ok")
        bc2 = make_catalog_bootcamp(stable_id="bc-falha", name="Bootcamp Falha", url="https://www.dio.me/bootcamp/bc-falha")

        def side_effect(url: str):
            if "bc-falha" in url:
                raise Exception("Erro simulado de rede")
            return DETAIL_TEXT_BAIXA

        with patch("main.DioScraper") as MockScraper, \
             patch("main.build_notifier") as MockNotifier:

            mock_scraper = MagicMock()
            mock_scraper.fetch_catalog.return_value = [bc1, bc2]
            mock_scraper.fetch_detail.side_effect = side_effect
            MockScraper.return_value = mock_scraper

            mock_notifier = MagicMock()
            MockNotifier.return_value = mock_notifier

            # Não deve propagar exceção
            run(config, storage_path)

        # Bootcamp OK deve ter sido salvo
        history = load_history(storage_path)
        assert "bc-ok" in history

    def test_falha_telegram_nao_para_salvamento(self, tmp_path):
        """Erro no Telegram não deve impedir salvamento do histórico."""
        from main import run

        config = make_config(initial_notify=True)
        storage_path = tmp_path / "bootcamps.json"

        catalog = [make_catalog_bootcamp()]

        with patch("main.DioScraper") as MockScraper, \
             patch("main.build_notifier") as MockNotifier:

            mock_scraper = MagicMock()
            mock_scraper.fetch_catalog.return_value = catalog
            mock_scraper.fetch_detail.return_value = DETAIL_TEXT_BAIXA
            MockScraper.return_value = mock_scraper

            mock_notifier = MagicMock()
            mock_notifier.notify_new_bootcamp.side_effect = Exception("Telegram offline")
            MockNotifier.return_value = mock_notifier

            # Não deve propagar exceção do Telegram
            run(config, storage_path)

        # Histórico deve ter sido salvo mesmo com falha no Telegram
        history = load_history(storage_path)
        assert "bc-teste-001" in history

    def test_catalogo_vazio_nao_crasha(self, tmp_path):
        from main import run

        config = make_config()
        storage_path = tmp_path / "bootcamps.json"

        with patch("main.DioScraper") as MockScraper, \
             patch("main.build_notifier") as MockNotifier:

            mock_scraper = MagicMock()
            mock_scraper.fetch_catalog.return_value = []
            MockScraper.return_value = mock_scraper

            mock_notifier = MagicMock()
            MockNotifier.return_value = mock_notifier

            run(config, storage_path)  # não deve lançar


# ---------------------------------------------------------------------------
# Testes de resumo diário
# ---------------------------------------------------------------------------

class TestResumoDiario:
    def test_resumo_enviado_quando_configurado(self, tmp_path):
        from main import run

        config = make_config(
            initial_notify=False,
            send_daily_summary=True,
            send_empty_summary=True,
        )
        storage_path = tmp_path / "bootcamps.json"

        with patch("main.DioScraper") as MockScraper, \
             patch("main.build_notifier") as MockNotifier:

            mock_scraper = MagicMock()
            mock_scraper.fetch_catalog.return_value = []
            MockScraper.return_value = mock_scraper

            mock_notifier = MagicMock()
            MockNotifier.return_value = mock_notifier

            run(config, storage_path)

            mock_notifier.send_daily_summary.assert_called_once()

    def test_resumo_nao_enviado_quando_desativado(self, tmp_path):
        from main import run

        config = make_config(send_daily_summary=False)
        storage_path = tmp_path / "bootcamps.json"

        with patch("main.DioScraper") as MockScraper, \
             patch("main.build_notifier") as MockNotifier:

            mock_scraper = MagicMock()
            mock_scraper.fetch_catalog.return_value = []
            MockScraper.return_value = mock_scraper

            mock_notifier = MagicMock()
            MockNotifier.return_value = mock_notifier

            run(config, storage_path)

            mock_notifier.send_daily_summary.assert_not_called()
