"""
test_scraper.py — Testes do scraper de catálogo da DIO.

Sem acesso à internet. Usa fixtures HTML locais e monkeypatching.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dio_scraper import (
    DioScraper,
    _extract_from_html,
    _extract_from_next_data,
    _is_bootcamp_url,
    _make_stable_id,
    _normalize_url,
    extract_detail_text,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Testes de helpers de URL
# ---------------------------------------------------------------------------

class TestUrlHelpers:
    def test_normalize_relativa_para_absoluta(self):
        url = _normalize_url("/bootcamp/meu-bootcamp")
        assert url == "https://www.dio.me/bootcamp/meu-bootcamp"

    def test_mantem_url_absoluta(self):
        url = _normalize_url("https://www.dio.me/bootcamp/meu-bootcamp")
        assert url == "https://www.dio.me/bootcamp/meu-bootcamp"

    def test_url_vazia_retorna_vazia(self):
        assert _normalize_url("") == ""

    def test_is_bootcamp_url_positivo(self):
        assert _is_bootcamp_url("https://www.dio.me/bootcamp/meu-bootcamp") is True

    def test_is_bootcamp_url_negativo(self):
        assert _is_bootcamp_url("https://www.dio.me/courses/python") is False
        assert _is_bootcamp_url("https://www.example.com") is False

    def test_normalize_com_base_customizada(self):
        url = _normalize_url("/path", base="https://example.com")
        assert url == "https://example.com/path"


# ---------------------------------------------------------------------------
# Testes de make_stable_id
# ---------------------------------------------------------------------------

class TestStableId:
    def test_usa_slug_da_url(self):
        sid = _make_stable_id("Bootcamp Teste", "Empresa", "https://www.dio.me/bootcamp/meu-bootcamp-slug")
        assert sid == "meu-bootcamp-slug"

    def test_fallback_para_hash_sem_slug(self):
        sid = _make_stable_id("Bootcamp Teste", "Empresa", "https://www.dio.me/bootcamp")
        # Deve retornar hash de 16 chars
        assert len(sid) == 16

    def test_estavel_com_mesmos_dados(self):
        sid1 = _make_stable_id("Bootcamp A", "Empresa X", "https://www.dio.me/bootcamp/bootcamp-a")
        sid2 = _make_stable_id("Bootcamp A", "Empresa X", "https://www.dio.me/bootcamp/bootcamp-a")
        assert sid1 == sid2

    def test_diferente_para_nomes_diferentes(self):
        sid1 = _make_stable_id("Bootcamp A", "Empresa X", "https://www.dio.me/bootcamp")
        sid2 = _make_stable_id("Bootcamp B", "Empresa X", "https://www.dio.me/bootcamp")
        assert sid1 != sid2


# ---------------------------------------------------------------------------
# Testes de extração via __NEXT_DATA__
# ---------------------------------------------------------------------------

class TestExtractNextData:
    def test_extrai_bootcamps_do_next_data(self):
        html = load_fixture("catalog_sample.html")
        bootcamps = _extract_from_next_data(html)
        assert len(bootcamps) >= 3, f"Esperava ≥3 bootcamps, obteve {len(bootcamps)}"

    def test_sem_next_data_retorna_vazio(self):
        html = "<html><body><p>Sem next data</p></body></html>"
        bootcamps = _extract_from_next_data(html)
        assert bootcamps == []

    def test_next_data_invalido_retorna_vazio(self):
        html = '<html><script id="__NEXT_DATA__">INVALIDO{{{</script></html>'
        bootcamps = _extract_from_next_data(html)
        assert bootcamps == []

    def test_bootcamps_tem_stable_id(self):
        html = load_fixture("catalog_sample.html")
        bootcamps = _extract_from_next_data(html)
        for bc in bootcamps:
            assert bc.stable_id, f"Bootcamp '{bc.name}' sem stable_id."

    def test_bootcamps_tem_url_absoluta(self):
        html = load_fixture("catalog_sample.html")
        bootcamps = _extract_from_next_data(html)
        for bc in bootcamps:
            assert bc.url.startswith("https://"), f"URL não absoluta: {bc.url}"


# ---------------------------------------------------------------------------
# Regressão: estrutura real do __NEXT_DATA__ da DIO
#
# A busca genérica no JSON casava primeiro com props.pageProps.navigation[].results,
# que lista CARREIRAS (name+slug), e sintetizava /bootcamp/<slug-de-carreira> —
# URLs inexistentes que retornavam 404 em toda página de detalhe. A extração passou
# a ler o caminho canônico props.pageProps.bootcamps.
# ---------------------------------------------------------------------------

class TestExtractPagePropsReal:
    def test_extrai_apenas_bootcamps_reais(self):
        html = load_fixture("catalog_next_data_real.html")
        bootcamps = _extract_from_next_data(html)
        assert len(bootcamps) == 3, (
            f"Esperava exatamente os 3 de pageProps.bootcamps, obteve {len(bootcamps)}"
        )

    def test_nao_extrai_carreiras_da_navegacao(self):
        html = load_fixture("catalog_next_data_real.html")
        slugs = {bc.stable_id for bc in _extract_from_next_data(html)}
        for slug_de_carreira in ("ai-agent-builder", "ai-automation", "ai-developer"):
            assert slug_de_carreira not in slugs, (
                f"Carreira '{slug_de_carreira}' extraída como bootcamp — "
                "gera URL 404 na busca de detalhes."
            )

    def test_url_montada_a_partir_do_slug(self):
        html = load_fixture("catalog_next_data_real.html")
        urls = {bc.url for bc in _extract_from_next_data(html)}
        assert "https://www.dio.me/bootcamp/accenture-desenvolvimento-java-cloud" in urls

    def test_mapeia_campos_do_schema_atual(self):
        html = load_fixture("catalog_next_data_real.html")
        by_id = {bc.stable_id: bc for bc in _extract_from_next_data(html)}
        bc = by_id["accenture-desenvolvimento-java-cloud"]
        assert bc.name == "Accenture - Desenvolvimento Java e Cloud"
        assert bc.status == "BOOTCAMP"
        assert bc.launch_info == "2026-09-08"
        # Sem descrição no payload: resumo vem das skills, deduplicado
        assert bc.summary == "Java, Spring Boot"

    def test_skills_duplicadas_nao_repetem_no_resumo(self):
        html = load_fixture("catalog_next_data_real.html")
        by_id = {bc.stable_id: bc for bc in _extract_from_next_data(html)}
        bc = by_id["nublify-primeiros-passos-em-ia-e-cloud"]
        assert bc.summary == "AWS, Amazon Bedrock"

    def test_posicoes_sao_sequenciais(self):
        html = load_fixture("catalog_next_data_real.html")
        posicoes = [bc.catalog_position for bc in _extract_from_next_data(html)]
        assert posicoes == [0, 1, 2]

    def test_ignora_itens_sem_slug_ou_nome(self):
        html = """
        <html><script id="__NEXT_DATA__" type="application/json">
        {"props": {"pageProps": {"bootcamps": [
            {"name": "Sem slug"},
            {"slug": "sem-nome-aqui"},
            {"name": "Valido", "slug": "bootcamp-valido-teste"},
            "string solta",
            null
        ]}}}
        </script></html>
        """
        bootcamps = _extract_from_next_data(html)
        assert len(bootcamps) == 1
        assert bootcamps[0].stable_id == "bootcamp-valido-teste"

    def test_cai_na_busca_generica_sem_pageprops_bootcamps(self):
        """Se a DIO remover a chave, o fallback genérico ainda funciona."""
        html = """
        <html><script id="__NEXT_DATA__" type="application/json">
        {"props": {"pageProps": {"catalogo": {"itens": [
            {"name": "Bootcamp Legado", "slug": "bootcamp-legado-teste"}
        ]}}}}
        </script></html>
        """
        bootcamps = _extract_from_next_data(html)
        assert len(bootcamps) == 1
        assert bootcamps[0].stable_id == "bootcamp-legado-teste"

    def test_busca_generica_ignora_subarvore_de_navegacao(self):
        html = """
        <html><script id="__NEXT_DATA__" type="application/json">
        {"props": {"pageProps": {
            "navigation": [{"results": [{"name": "AI Agent Builder", "slug": "ai-agent-builder"}]}],
            "outros": [{"name": "Bootcamp Real", "slug": "bootcamp-real-teste"}]
        }}}
        </script></html>
        """
        bootcamps = _extract_from_next_data(html)
        assert len(bootcamps) == 1
        assert bootcamps[0].stable_id == "bootcamp-real-teste"

    def test_bootcamps_nao_e_lista_retorna_vazio(self):
        html = """
        <html><script id="__NEXT_DATA__" type="application/json">
        {"props": {"pageProps": {"bootcamps": {"erro": "formato inesperado"}}}}
        </script></html>
        """
        assert _extract_from_next_data(html) == []


# ---------------------------------------------------------------------------
# Testes de extração via HTML
# ---------------------------------------------------------------------------

class TestExtractFromHtml:
    def test_extrai_links_de_bootcamp(self):
        html = load_fixture("catalog_sample.html")
        bootcamps = _extract_from_html(html)
        assert len(bootcamps) >= 3, f"Esperava ≥3 via HTML, obteve {len(bootcamps)}"

    def test_sem_links_de_bootcamp_retorna_vazio(self):
        html = "<html><body><a href='/courses/python'>Python</a></body></html>"
        bootcamps = _extract_from_html(html)
        assert bootcamps == []

    def test_links_relativos_normalizados(self):
        html = """
        <html><body>
          <a href="/bootcamp/meu-bootcamp">
            <h3>Meu Bootcamp</h3>
          </a>
        </body></html>
        """
        bootcamps = _extract_from_html(html)
        assert len(bootcamps) == 1
        assert bootcamps[0].url.startswith("https://")

    def test_links_absolutos_mantidos(self):
        html = """
        <html><body>
          <a href="https://www.dio.me/bootcamp/bootcamp-absoluto">
            <h3>Bootcamp Absoluto</h3>
          </a>
        </body></html>
        """
        bootcamps = _extract_from_html(html)
        assert len(bootcamps) == 1
        assert bootcamps[0].url == "https://www.dio.me/bootcamp/bootcamp-absoluto"

    def test_deduplicacao(self):
        html = """
        <html><body>
          <a href="/bootcamp/mesmo-bootcamp"><h3>Bootcamp A</h3></a>
          <a href="/bootcamp/mesmo-bootcamp"><h3>Bootcamp A duplicado</h3></a>
        </body></html>
        """
        bootcamps = _extract_from_html(html)
        assert len(bootcamps) == 1


# ---------------------------------------------------------------------------
# Testes de extract_detail_text
# ---------------------------------------------------------------------------

class TestExtractDetailText:
    def test_extrai_texto_da_fixture_high(self):
        html = load_fixture("bootcamp_detail_high.html")
        text = extract_detail_text(html)
        assert "processo seletivo" in text.lower()
        assert len(text) > 50

    def test_remove_tags_script_e_style(self):
        html = """
        <html>
          <head><style>body { color: red; }</style></head>
          <body>
            <script>alert('xss')</script>
            <main><p>Conteúdo relevante do bootcamp</p></main>
          </body>
        </html>
        """
        text = extract_detail_text(html)
        assert "alert" not in text
        assert "color: red" not in text
        assert "Conteúdo relevante" in text

    def test_html_vazio_retorna_string(self):
        text = extract_detail_text("<html><body></body></html>")
        assert isinstance(text, str)

    def test_limite_de_caracteres(self):
        html = "<html><body><main>" + ("x " * 15_000) + "</main></body></html>"
        text = extract_detail_text(html)
        assert len(text) <= 20_000


# ---------------------------------------------------------------------------
# Testes de DioScraper (com mock de HTTP)
# ---------------------------------------------------------------------------

class TestDioScraper:
    def _make_scraper(self, catalog_html: str, detail_html: str = "") -> DioScraper:
        """Cria scraper com session mockada."""
        scraper = DioScraper(delay=0)

        mock_response_catalog = MagicMock()
        mock_response_catalog.status_code = 200
        mock_response_catalog.text = catalog_html

        mock_response_detail = MagicMock()
        mock_response_detail.status_code = 200
        mock_response_detail.text = detail_html

        mock_session = MagicMock()
        mock_session.get.side_effect = [
            mock_response_catalog,
            mock_response_detail,
        ]
        scraper._session = mock_session
        return scraper

    def test_fetch_catalog_retorna_lista(self):
        html = load_fixture("catalog_sample.html")
        scraper = self._make_scraper(html)
        catalog = scraper.fetch_catalog()
        assert isinstance(catalog, list)
        assert len(catalog) > 0

    def test_fetch_catalog_falha_http_retorna_vazio(self):
        scraper = DioScraper(delay=0)
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        scraper._session = mock_session
        catalog = scraper.fetch_catalog()
        assert catalog == []

    def test_fetch_detail_falha_nao_propaga_excecao(self):
        """Falha em página de detalhe não deve derrubar a execução."""
        scraper = DioScraper(delay=0)
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        scraper._session = mock_session

        # Deve retornar None sem lançar exceção
        result = scraper.fetch_detail("https://www.dio.me/bootcamp/inexistente")
        assert result is None

    def test_fetch_catalog_403_retorna_vazio(self):
        scraper = DioScraper(delay=0)
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_session = MagicMock()
        mock_session.get.return_value = mock_response
        scraper._session = mock_session
        catalog = scraper.fetch_catalog()
        assert catalog == []

    def test_deduplicacao_no_catalogo(self):
        html = """
        <html>
        <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{"bootcamps":[
          {"name":"BC Duplo","slug":"bc-duplo","partnerCompany":{"name":"Empresa"}},
          {"name":"BC Duplo","slug":"bc-duplo","partnerCompany":{"name":"Empresa"}}
        ]}}}
        </script>
        </html>
        """
        scraper = self._make_scraper(html)
        catalog = scraper.fetch_catalog()
        slugs = [bc.stable_id for bc in catalog]
        assert len(slugs) == len(set(slugs)), "Catálogo não deve conter duplicatas."
