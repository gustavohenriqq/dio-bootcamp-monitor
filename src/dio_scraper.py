"""
dio_scraper.py — Extração pública do catálogo de bootcamps da DIO.

Abordagem:
- Requisição HTTP simples à página pública do catálogo (sem login, sem Selenium).
- Parsing defensivo com BeautifulSoup4.
- Múltiplas heurísticas para detectar cards de bootcamp (a DIO usa React/Next.js
  com SSR parcial; os dados podem aparecer como JSON embutido ou como HTML renderizado).
- Fallback: caso nenhum card estruturado seja encontrado, tenta extrair links e
  âncoras com padrões de URL de bootcamp.

Decisão técnica documentada:
    A DIO utiliza renderização híbrida (Next.js). Parte do conteúdo do catálogo
    público em /bootcamp é entregue via JSON embutido em <script id="__NEXT_DATA__">
    ou via cards HTML. Este scraper tenta primeiro o JSON embutido, depois o HTML.
    Se ambos falharem (ex.: mudança de estrutura), registra o aviso e continua
    com lista vazia — sem crashar a execução.
"""

import hashlib
import html as html_module
import json
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurações de requisição
# ---------------------------------------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (compatible; DioBootcampMonitor/1.0; "
    "+https://github.com/seu-usuario/dio-bootcamp-monitor)"
)

REQUEST_TIMEOUT = 20  # segundos
MAX_RETRIES = 3
BACKOFF_BASE = 2  # segundos; atraso = BACKOFF_BASE ** tentativa


# ---------------------------------------------------------------------------
# Estrutura de dados do catálogo
# ---------------------------------------------------------------------------

@dataclass
class CatalogBootcamp:
    """Representa um bootcamp extraído do catálogo público."""

    stable_id: str
    name: str
    company: str
    url: str
    summary: str = ""
    status: str = ""
    launch_info: str = ""
    catalog_position: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "stable_id": self.stable_id,
            "name": self.name,
            "company": self.company,
            "url": self.url,
            "summary": self.summary,
            "status": self.status,
            "launch_info": self.launch_info,
            "catalog_position": self.catalog_position,
        }


# ---------------------------------------------------------------------------
# Session HTTP com retry e backoff
# ---------------------------------------------------------------------------

def _build_session() -> requests.Session:
    """Cria e configura a Session HTTP reutilizável."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })
    return session


def _fetch_with_retry(
    session: requests.Session,
    url: str,
    delay: float = 2.0,
) -> Optional[str]:
    """
    Faz requisição GET com retry e backoff progressivo.

    Trata: 403, 404, 429, 500 e erros de conexão.

    Args:
        session: Session HTTP configurada.
        url: URL a requisitar.
        delay: atraso base entre tentativas (segundos).

    Returns:
        Conteúdo HTML como string ou None em caso de falha definitiva.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)

            if response.status_code == 200:
                return response.text

            if response.status_code == 404:
                logger.warning("Página não encontrada (404): %s", url)
                return None

            if response.status_code == 403:
                logger.warning("Acesso negado (403): %s. Não tentaremos novamente.", url)
                return None

            if response.status_code == 429:
                wait = delay * (BACKOFF_BASE ** attempt)
                logger.warning("Rate limit (429) em %s. Aguardando %.1fs.", url, wait)
                time.sleep(wait)
                continue

            if response.status_code >= 500:
                wait = delay * (BACKOFF_BASE ** attempt)
                logger.warning(
                    "Erro do servidor (%d) em %s. Tentativa %d/%d. Aguardando %.1fs.",
                    response.status_code, url, attempt, MAX_RETRIES, wait,
                )
                time.sleep(wait)
                continue

            logger.warning("Status inesperado %d em %s.", response.status_code, url)
            return None

        except requests.exceptions.Timeout:
            wait = delay * (BACKOFF_BASE ** attempt)
            logger.warning("Timeout em %s. Tentativa %d/%d. Aguardando %.1fs.", url, attempt, MAX_RETRIES, wait)
            time.sleep(wait)

        except requests.exceptions.ConnectionError as exc:
            wait = delay * (BACKOFF_BASE ** attempt)
            logger.warning("Erro de conexão em %s: %s. Aguardando %.1fs.", url, exc, wait)
            time.sleep(wait)

        except requests.exceptions.RequestException as exc:
            logger.error("Erro irrecuperável em %s: %s", url, exc)
            return None

    logger.error("Todas as tentativas falharam para %s.", url)
    return None


# ---------------------------------------------------------------------------
# Helpers de parsing
# ---------------------------------------------------------------------------

def _make_stable_id(name: str, company: str, url: str) -> str:
    """
    Gera identificador estável e determinístico para um bootcamp.

    Usa slug da URL quando possível, com fallback para hash de nome+empresa.
    """
    # Tenta extrair slug da URL (ex.: /bootcamp/meu-bootcamp-nome)
    path = urllib.parse.urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1] if path else ""
    if slug and len(slug) > 8 and slug not in ("bootcamp", "bootcamps"):
        return slug[:80]

    # Fallback: hash de nome normalizado + empresa
    key = f"{name.lower().strip()}|{company.lower().strip()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _normalize_url(url: str, base: str = "https://www.dio.me") -> str:
    """Normaliza URL relativa para absoluta."""
    if not url:
        return ""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urllib.parse.urljoin(base, url)


def _clean_text(text: str) -> str:
    """Remove espaços excessivos e caracteres de controle."""
    if not text:
        return ""
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_bootcamp_url(url: str) -> bool:
    """Verifica se a URL parece ser de uma página de bootcamp da DIO."""
    path = urllib.parse.urlparse(url).path
    return bool(re.search(r"/bootcamp[s]?/[^/]+", path))


# ---------------------------------------------------------------------------
# Estratégia 1: JSON embutido (__NEXT_DATA__)
# ---------------------------------------------------------------------------

def _extract_from_next_data(html: str) -> list[CatalogBootcamp]:
    """
    Tenta extrair bootcamps do JSON embutido pelo Next.js em __NEXT_DATA__.

    A DIO usa Next.js e parte dos dados de catálogo pode estar serializada
    em <script id="__NEXT_DATA__">...</script>. Esta função tenta navegar
    na estrutura do JSON para encontrar a lista de bootcamps.

    Returns:
        Lista de CatalogBootcamp extraídos ou lista vazia se não encontrar.
    """
    soup = BeautifulSoup(html, "html.parser")
    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script_tag:
        return []

    try:
        data = json.loads(script_tag.string or "")
    except (json.JSONDecodeError, TypeError):
        logger.debug("__NEXT_DATA__ presente mas JSON inválido.")
        return []

    # Caminho conhecido e canônico: props.pageProps.bootcamps
    bootcamps = _extract_from_page_props(data)
    if bootcamps:
        logger.info("Extraídos %d bootcamps via pageProps.bootcamps.", len(bootcamps))
        return bootcamps

    # Fallback: busca recursiva por lista de bootcamps no JSON
    logger.debug("pageProps.bootcamps ausente ou vazio. Usando busca genérica no JSON.")
    bootcamps = []
    _search_json_for_bootcamps(data, bootcamps)

    if bootcamps:
        logger.info("Extraídos %d bootcamps via busca genérica no __NEXT_DATA__.", len(bootcamps))

    return bootcamps


def _extract_from_page_props(data: dict) -> list[CatalogBootcamp]:
    """
    Extrai bootcamps do caminho canônico props.pageProps.bootcamps do __NEXT_DATA__.

    Estrutura observada em cada item (agosto/2026):
        {"id", "slug", "name", "type", "image", "finish", "cover",
         "total_devs", "lp_slug", "skills": [{"name", ...}], "badge", "color"}

    Só a lista `bootcamps` contém bootcamps de verdade. O nó `navigation` do mesmo
    JSON tem objetos com `name`+`slug` de *carreiras* (ex.: "ai-agent-builder"),
    cujos slugs não correspondem a páginas /bootcamp/<slug> — daí a extração
    direcionada em vez da busca genérica.

    Returns:
        Lista de CatalogBootcamp ou lista vazia se o caminho não existir.
    """
    if not isinstance(data, dict):
        return []

    raw = data.get("props", {})
    if not isinstance(raw, dict):
        return []
    raw = raw.get("pageProps", {})
    if not isinstance(raw, dict):
        return []
    items = raw.get("bootcamps")
    if not isinstance(items, list):
        return []

    bootcamps: list[CatalogBootcamp] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        name = _clean_text(str(item.get("name") or ""))
        slug = _clean_text(str(item.get("slug") or item.get("lp_slug") or ""))
        if not name or not slug:
            continue

        url = _normalize_url(f"/bootcamp/{slug}")
        if not _is_bootcamp_url(url):
            continue

        # A empresa parceira não vem em campo próprio no schema atual; fica vazia
        # e é complementada pela página de detalhe quando disponível. Os nomes
        # alternativos cobrem variações já observadas do payload.
        partner = item.get("partnerCompany")
        if isinstance(partner, dict):
            company = _clean_text(str(partner.get("name") or ""))
        else:
            company = _clean_text(str(item.get("company") or ""))

        # Resumo: descrição quando houver; senão, as skills do bootcamp. Serve
        # apenas de fallback de classificação se a página de detalhe falhar.
        summary = _clean_text(
            str(item.get("shortDescription") or item.get("description") or item.get("summary") or "")
        )
        if not summary:
            skills = item.get("skills")
            if isinstance(skills, list):
                names: list[str] = []
                for skill in skills:
                    if isinstance(skill, dict):
                        skill_name = _clean_text(str(skill.get("name") or ""))
                        if skill_name and skill_name not in names:
                            names.append(skill_name)
                summary = ", ".join(names)
        summary = summary[:200]

        bootcamps.append(
            CatalogBootcamp(
                stable_id=_make_stable_id(name, company, url),
                name=name,
                company=company,
                url=url,
                summary=summary,
                status=_clean_text(str(item.get("status") or item.get("type") or "")),
                launch_info=_clean_text(
                    str(item.get("finish") or item.get("enrollmentDeadline") or item.get("startDate") or "")
                ),
                catalog_position=len(bootcamps),
            )
        )

    return bootcamps


# Chaves do __NEXT_DATA__ cujas subárvores nunca contêm bootcamps.
_NOISE_JSON_KEYS = frozenset({
    "navigation",
    "menu",
    "header",
    "footer",
    "_nextI18Next",
    "initialI18nStore",
    "locales",
})


def _search_json_for_bootcamps(
    node: object,
    result: list[CatalogBootcamp],
    depth: int = 0,
) -> None:
    """
    Busca recursivamente em estruturas JSON por objetos que pareçam bootcamps.
    Limita profundidade para evitar loops em estruturas circulares (não esperadas,
    mas defensivo).
    """
    if depth > 12:
        return

    if isinstance(node, list):
        for item in node:
            _search_json_for_bootcamps(item, result, depth + 1)

    elif isinstance(node, dict):
        # Detecta se este nó parece ser um bootcamp
        has_name = "name" in node or "title" in node
        has_slug = "slug" in node or "alias" in node or "permalink" in node
        has_url = "url" in node or "link" in node or has_slug

        if has_name and has_url:
            name = _clean_text(str(node.get("name") or node.get("title") or ""))
            company = _clean_text(
                str(
                    node.get("partnerCompany", {}).get("name", "") if isinstance(node.get("partnerCompany"), dict)
                    else node.get("company", "") or ""
                )
            )
            slug = node.get("slug") or node.get("alias") or node.get("permalink") or ""
            url_raw = node.get("url") or node.get("link") or (f"/bootcamp/{slug}" if slug else "")
            url = _normalize_url(str(url_raw))

            summary = _clean_text(
                str(node.get("shortDescription") or node.get("description") or node.get("summary") or "")
            )
            status = _clean_text(str(node.get("status") or ""))
            launch_info = _clean_text(str(node.get("enrollmentDeadline") or node.get("startDate") or ""))

            if name and _is_bootcamp_url(url):
                stable_id = _make_stable_id(name, company, url)
                bootcamp = CatalogBootcamp(
                    stable_id=stable_id,
                    name=name,
                    company=company,
                    url=url,
                    summary=summary,
                    status=status,
                    launch_info=launch_info,
                    catalog_position=len(result),
                )
                result.append(bootcamp)
                return  # Não percorre filhos deste nó

        for key, value in node.items():
            # Subárvores de navegação/i18n contêm objetos com name+slug que não são
            # bootcamps (carreiras, itens de menu) e gerariam URLs inexistentes.
            if key in _NOISE_JSON_KEYS:
                continue
            _search_json_for_bootcamps(value, result, depth + 1)


# ---------------------------------------------------------------------------
# Estratégia 2: Parsing de HTML (cards e âncoras)
# ---------------------------------------------------------------------------

def _extract_from_html(html: str) -> list[CatalogBootcamp]:
    """
    Tenta extrair bootcamps diretamente do HTML renderizado.

    Heurísticas aplicadas (em ordem):
    1. Elementos <a> que apontam para /bootcamp/<slug>
    2. Cards com atributos de dados (data-testid, aria-label) sugestivos
    3. Blocos com padrão visual de card (classes css com "card", "bootcamp")

    O parsing é defensivo: campos ausentes recebem valores padrão seguros.
    """
    soup = BeautifulSoup(html, "html.parser")
    bootcamps: list[CatalogBootcamp] = []
    seen_urls: set[str] = set()

    # Busca todos os links que apontam para /bootcamp/<slug>
    anchors = soup.find_all("a", href=True)

    for anchor in anchors:
        href = anchor.get("href", "")
        url = _normalize_url(href)

        if not _is_bootcamp_url(url):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Tenta extrair nome do bootcamp
        name = ""
        # Tenta heading dentro do card
        heading = anchor.find(["h1", "h2", "h3", "h4", "h5"])
        if heading:
            name = _clean_text(heading.get_text())

        # Tenta atributo aria-label
        if not name:
            name = _clean_text(anchor.get("aria-label", ""))

        # Tenta título do link
        if not name:
            name = _clean_text(anchor.get("title", ""))

        # Tenta texto direto do anchor
        if not name:
            name = _clean_text(anchor.get_text())

        # Nome mínimo: slug da URL
        if not name or len(name) < 3:
            path = urllib.parse.urlparse(url).path
            slug = path.rstrip("/").split("/")[-1]
            name = slug.replace("-", " ").title()

        # Tenta extrair empresa do contexto do card
        company = ""
        parent = anchor.parent
        for _ in range(4):  # sobe até 4 níveis
            if parent is None:
                break
            img = parent.find("img", alt=True)
            if img:
                alt = _clean_text(img.get("alt", ""))
                if alt and len(alt) > 2:
                    company = alt
                    break
            # Tenta padrões de texto de empresa
            for span in parent.find_all(["span", "p", "small"]):
                txt = _clean_text(span.get_text())
                if txt and len(txt) < 50 and txt != name:
                    # Heurística: textos curtos próximos ao link podem ser a empresa
                    company = txt
                    break
            if company:
                break
            parent = parent.parent if parent else None

        # Resumo: tenta encontrar parágrafo de descrição no contexto
        summary = ""
        parent = anchor.parent
        for _ in range(3):
            if parent is None:
                break
            para = parent.find("p")
            if para:
                txt = _clean_text(para.get_text())
                if txt and txt != name:
                    summary = txt[:200]
                    break
            parent = parent.parent if parent else None

        stable_id = _make_stable_id(name, company, url)
        bootcamp = CatalogBootcamp(
            stable_id=stable_id,
            name=name,
            company=company,
            url=url,
            summary=summary,
            catalog_position=len(bootcamps),
        )
        bootcamps.append(bootcamp)

    if bootcamps:
        logger.info("Extraídos %d bootcamps via parsing HTML.", len(bootcamps))

    return bootcamps


# ---------------------------------------------------------------------------
# Extração de detalhes de página individual
# ---------------------------------------------------------------------------

def extract_detail_text(html: str) -> str:
    """
    Extrai texto limpo da página de detalhes de um bootcamp.

    Prioriza: descrição longa, objetivos, requisitos, informações de seleção.
    Remove: menus, rodapés, scripts, estilos.

    Args:
        html: conteúdo HTML da página.

    Returns:
        Texto limpo concatenado dos blocos relevantes.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove ruído estrutural
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()

    # Tenta extrair seções específicas com prioridade
    priority_sections: list[str] = []

    # Seções com atributos comuns de conteúdo
    content_selectors = [
        {"id": re.compile(r"(description|about|detail|content|objective|requirement)", re.I)},
        {"class": re.compile(r"(description|about|detail|content|objective|requirement|bootcamp)", re.I)},
    ]

    for selector in content_selectors:
        for tag in soup.find_all(True, selector):
            txt = _clean_text(tag.get_text(separator=" "))
            if txt and len(txt) > 30:
                priority_sections.append(txt)

    # Texto completo do main ou body como fallback
    main = soup.find("main") or soup.find("body") or soup
    full_text = _clean_text(main.get_text(separator=" "))

    # Combina: seções prioritárias + texto completo (deduplicado implicitamente pelo classificador)
    combined = " ".join(priority_sections) + " " + full_text
    return combined[:20_000]  # limita para não sobrecarregar o classificador


# ---------------------------------------------------------------------------
# Interface pública do scraper
# ---------------------------------------------------------------------------

class DioScraper:
    """
    Scraper público do catálogo de bootcamps da DIO.

    Não faz login, não contorna proteções, não usa Selenium.
    Trabalha exclusivamente com conteúdo público entregue por requisição HTTP.
    """

    def __init__(self, base_url: str = "https://www.dio.me/bootcamp", delay: float = 2.0):
        self.base_url = base_url
        self.delay = delay
        self._session = _build_session()

    def fetch_catalog(self) -> list[CatalogBootcamp]:
        """
        Busca e extrai o catálogo público de bootcamps.

        Returns:
            Lista de CatalogBootcamp ordenados por posição no catálogo.
            Retorna lista vazia em caso de falha, sem propagar exceção.
        """
        logger.info("Iniciando extração do catálogo: %s", self.base_url)
        html = _fetch_with_retry(self._session, self.base_url, self.delay)

        if not html:
            logger.error("Não foi possível obter o catálogo. Retornando lista vazia.")
            return []

        # Estratégia 1: JSON embutido
        bootcamps = _extract_from_next_data(html)

        # Estratégia 2: Parsing HTML
        if not bootcamps:
            bootcamps = _extract_from_html(html)

        if not bootcamps:
            logger.warning(
                "Nenhum bootcamp extraído do catálogo. "
                "A estrutura da página pode ter mudado. "
                "Verifique o README para instruções de depuração."
            )

        # Deduplica por stable_id mantendo a primeira ocorrência (posição mais alta)
        seen: set[str] = set()
        unique: list[CatalogBootcamp] = []
        for bc in bootcamps:
            if bc.stable_id not in seen:
                seen.add(bc.stable_id)
                unique.append(bc)

        logger.info("Catálogo extraído: %d bootcamps únicos.", len(unique))
        return unique

    def fetch_detail(self, url: str) -> Optional[str]:
        """
        Busca o texto de detalhe de um bootcamp específico.

        Args:
            url: URL pública da página de detalhes.

        Returns:
            Texto extraído ou None em caso de falha.
        """
        logger.info("Buscando detalhes: %s", url)
        time.sleep(self.delay)

        html = _fetch_with_retry(self._session, url, self.delay)
        if not html:
            logger.warning("Falha ao obter detalhes de %s. Continuando.", url)
            return None

        return extract_detail_text(html)

    def close(self) -> None:
        """Fecha a Session HTTP."""
        self._session.close()
