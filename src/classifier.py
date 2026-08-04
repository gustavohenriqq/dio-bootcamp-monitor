"""
classifier.py — Classificação contextual da chance de contratação em bootcamps DIO.

A classificação é EXPLICÁVEL: não se baseia apenas na presença isolada de palavras,
mas analisa frase e contexto ao redor de cada ocorrência.

Classificações possíveis:
    ALTA        — sinais fortes de processo seletivo ativo para emprego
    MÉDIA       — sinais moderados de oportunidade de recrutamento
    BAIXA       — menções vagas de oportunidade sem processo concreto
    EXPIRADA    — programa antigo ou prazo encerrado
    INDETERMINADA — sem evidências suficientes
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Os prazos do catálogo são datas brasileiras, de um site brasileiro. `date.today()`
# devolveria a data local da máquina — em um runner do GitHub, que roda em UTC,
# isso vira o dia seguinte a partir das 21h de Brasília, e um bootcamp que ainda
# aceita inscrição hoje seria dado como encerrado três horas antes da hora.
TZ_BRASIL = ZoneInfo("America/Sao_Paulo")


def hoje_brasil() -> date:
    """Data corrente no fuso de Brasília, independente do fuso da máquina."""
    return datetime.now(tz=TZ_BRASIL).date()

# ---------------------------------------------------------------------------
# Constantes de classificação
# ---------------------------------------------------------------------------

CLASSIFICATION_ALTA = "ALTA"
CLASSIFICATION_MEDIA = "MÉDIA"
CLASSIFICATION_BAIXA = "BAIXA"
CLASSIFICATION_EXPIRADA = "EXPIRADA"
CLASSIFICATION_INDETERMINADA = "INDETERMINADA"

# ---------------------------------------------------------------------------
# Situação da inscrição
#
# Vem do campo `finish` do catálogo (props.pageProps.bootcamps), que é uma data
# ISO — dado estruturado, muito mais confiável que heurística sobre o texto da
# página. Por isso o prazo vencido prevalece sobre a pontuação: não adianta a
# página falar de processo seletivo se as inscrições fecharam em 2021.
# ---------------------------------------------------------------------------

ENROLLMENT_ABERTO = "ABERTO"
ENROLLMENT_ENCERRADO = "ENCERRADO"
ENROLLMENT_DESCONHECIDO = "DESCONHECIDO"

# ---------------------------------------------------------------------------
# Resultado do classificador
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    """Resultado completo da classificação de um bootcamp."""

    classification: str
    score: int
    evidences: list[str] = field(default_factory=list)
    observation: str = ""
    relevant_excerpt_hash: str = ""
    # Situação da inscrição, derivada do prazo do catálogo (não do texto).
    enrollment_status: str = ENROLLMENT_DESCONHECIDO
    days_left: Optional[int] = None
    deadline: str = ""

    @property
    def is_open(self) -> bool:
        """True apenas quando há prazo conhecido e ele ainda não venceu."""
        return self.enrollment_status == ENROLLMENT_ABERTO

    def to_dict(self) -> dict:
        return {
            "classification": self.classification,
            "score": self.score,
            "evidences": self.evidences,
            "observation": self.observation,
            "relevant_excerpt_hash": self.relevant_excerpt_hash,
            "enrollment_status": self.enrollment_status,
            "days_left": self.days_left,
            "deadline": self.deadline,
        }


# ---------------------------------------------------------------------------
# Definição de sinais
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    """Sinal de classificação com padrão regex contextual e pontuação."""

    pattern: re.Pattern
    points: int
    label: str
    # Se True, o sinal é negativo (rebaixa a classificação)
    is_negative: bool = False
    # Se definido, o sinal exige que o match NÃO contenha esses termos
    # (para evitar falsos positivos no contexto imediato)
    exclude_context_patterns: list[re.Pattern] = field(default_factory=list)


def _compile(pattern: str, flags: int = re.IGNORECASE) -> re.Pattern:
    return re.compile(pattern, flags)


# Sinais fortes (+50 a +15)
STRONG_SIGNALS: list[Signal] = [
    Signal(
        pattern=_compile(
            r"processo\s+seletivo\s+(est[áa]\s+|estar[áa]\s+|segue\s+|continua\s+)?"
            r"(ativo|aberto|para|em\s+andamento)"
        ),
        points=50,
        label="processo seletivo ativo",
    ),
    Signal(
        pattern=_compile(r"primeira\s+etapa\s+do\s+processo\s+seletivo"),
        points=50,
        label="primeira etapa do processo seletivo",
    ),
    Signal(
        pattern=_compile(r"\d+\s+vagas?\s+(de\s+emprego|disponíveis?|abertas?|para\s+contrata)"),
        points=50,
        label="vagas de emprego disponíveis (quantidade informada)",
        exclude_context_patterns=[
            _compile(r"vagas?\s+gratuitas?"),
            _compile(r"vagas?\s+no\s+curso"),
            _compile(r"vagas?\s+no\s+bootcamp"),
        ],
    ),
    Signal(
        pattern=_compile(r"vagas?\s+(de\s+emprego|disponíveis?\s+para\s+contrata)"),
        points=50,
        label="vagas de emprego disponíveis",
        exclude_context_patterns=[
            _compile(r"vagas?\s+gratuitas?"),
            _compile(r"vagas?\s+no\s+curso"),
            _compile(r"vagas?\s+no\s+bootcamp"),
            _compile(r"vagas?\s+limitadas?"),
        ],
    ),
    Signal(
        pattern=_compile(r"contrata(ção|r)\s+(pela|pela empresa|direta)"),
        points=50,
        label="contratação pela empresa",
    ),
    Signal(
        pattern=_compile(r"recrutadores?\s+(da empresa\s+)?(acompanhar|acompanharão|estarão\s+presentes?)"),
        points=25,
        label="recrutadores acompanharão os participantes",
    ),
    Signal(
        pattern=_compile(r"candidatos?\s+selecionados?"),
        points=30,
        label="candidatos selecionados",
    ),
    Signal(
        pattern=_compile(r"teste\s+de\s+nivelamento\s+(para\s+vagas?|para\s+seleção)"),
        points=20,
        label="teste de nivelamento para vagas",
    ),
    Signal(
        pattern=_compile(r"entrevistas?\s+(com\s+a\s+empresa|técnica|de\s+emprego|para\s+contrata)"),
        points=25,
        label="entrevistas com a empresa",
    ),
    Signal(
        pattern=_compile(r"prazo\s+(para\s+participar|de\s+inscrição|para\s+participação)\s+da\s+seleção"),
        points=15,
        label="prazo para participar da seleção",
    ),
    Signal(
        pattern=_compile(r"(requisitos?\s+(acadêmicos?|de\s+formação|de\s+localização)\s+(para\s+a?\s*vaga|para\s+concorrer))"),
        points=15,
        label="requisitos de contratação",
    ),
    Signal(
        pattern=_compile(r"processo\s+seletivo\s+de\s+contrata"),
        points=50,
        label="processo seletivo de contratação",
    ),
    Signal(
        pattern=_compile(r"melhores\s+classificados[\w\s,]{0,40}ser[ãa]o\s+selecionados"),
        points=40,
        label="melhores classificados seguem no processo",
    ),
    Signal(
        pattern=_compile(r"vagas?\s+afirmativas?"),
        points=40,
        label="vagas afirmativas",
    ),
]

# Sinais médios (+25 a +10)
MEDIUM_SIGNALS: list[Signal] = [
    Signal(
        pattern=_compile(r"possibilidade\s+(real\s+de\s+)?contrata"),
        points=20,
        label="possibilidade real de contratação",
    ),
    Signal(
        pattern=_compile(r"entrevistas?\s+mapeadas?"),
        points=20,
        label="entrevistas mapeadas",
    ),
    Signal(
        pattern=_compile(r"recrutadores?\s+(poderão|podem)\s+acessar"),
        points=20,
        label="recrutadores poderão acessar os destaques",
    ),
    Signal(
        pattern=_compile(r"destaque\s+para\s+oportunidades"),
        points=15,
        label="destaque para oportunidades",
    ),
    Signal(
        pattern=_compile(r"conexão\s+direta\s+com\s+recrutamento"),
        points=20,
        label="conexão direta com recrutamento",
    ),
    Signal(
        pattern=_compile(r"(evento|mentoria)\s+(específica?|com)\s+(o\s+)?RH"),
        points=15,
        label="evento/mentoria com RH",
    ),
    Signal(
        pattern=_compile(r"gerar\s+chances?\s+de\s+contrata"),
        points=10,
        label="programa declara gerar chances de contratação",
    ),
    Signal(
        pattern=_compile(r"perfil\s+para\s+os?\s+recrutadores\s+d[ao]\s+\w+"),
        points=15,
        label="perfil exposto aos recrutadores da empresa parceira",
    ),
]

# Sinais fracos (+5 a +1)
#
# Aqui moram as frases que a DIO repete em praticamente toda página de bootcamp:
# a seção "Sua jornada" ("Tenha chances reais de contratação") e o mockup
# "Você no futuro", que simula uma mensagem de recrutador com nome e vaga
# fictícios. Medido sobre 65 páginas reais do catálogo (agosto/2026):
#
#     "chances reais de contratação"                 64/65 páginas (98%)
#     "força do perfil"                              38/65 páginas (58%)
#     "próximas etapas do processo de contratação"   28/65 páginas (43%)
#
# Elas indicam de fato a proposta da plataforma, então pontuam — mas com peso
# baixo. Tratá-las como sinal médio jogaria o catálogo inteiro para MÉDIA e a
# classificação deixaria de discriminar qualquer coisa. Com peso baixo, uma
# página que só tem boilerplate sai de INDETERMINADA para BAIXA, que é
# exatamente "menções vagas de oportunidade sem processo concreto".
WEAK_SIGNALS: list[Signal] = [
    Signal(
        pattern=_compile(r"talent\s+match"),
        points=5,
        label="perfil disponível na Talent Match",
    ),
    Signal(
        pattern=_compile(r"oportunidades?\s+em\s+empresas?\s+parceiras?"),
        points=3,
        label="oportunidades em empresas parceiras",
    ),
    Signal(
        pattern=_compile(r"aumente\s+suas\s+chances?"),
        points=2,
        label="aumente suas chances",
    ),
    Signal(
        pattern=_compile(r"certificado\s+(para\s+o\s+)?currículo"),
        points=1,
        label="certificado para o currículo",
    ),
    Signal(
        pattern=_compile(r"destaque\s+seu\s+perfil"),
        points=2,
        label="destaque seu perfil",
    ),
    Signal(
        pattern=_compile(r"chances?\s+reais?\s+de\s+contrata"),
        points=3,
        label="programa cita chances reais de contratação",
    ),
    Signal(
        pattern=_compile(r"pr[oó]ximas\s+etapas\s+do\s+processo\s+de\s+contrata"),
        points=2,
        label="menção a etapas de processo de contratação",
    ),
    Signal(
        pattern=_compile(r"for[çc]a\s+do\s+perfil"),
        points=1,
        label="força do perfil na plataforma",
    ),
    Signal(
        pattern=_compile(r"empresas?\s+(inovadoras?|nacionais?|multinacionais?)\s+(do\s+mercado|podem\s+encontrar)"),
        points=2,
        label="exposição a empresas do mercado",
    ),
    # Afirmações sobre a plataforma DIO em geral, não sobre este bootcamp ter
    # processo seletivo. Indicam exposição a recrutadores, e nada além disso.
    Signal(
        pattern=_compile(r"empresas?\s+(parceiras?\s+)?(da\s+DIO\s+)?que\s+est[ãa]o\s+contratando"),
        points=3,
        label="perfil disponível a empresas parceiras que contratam",
    ),
    Signal(
        pattern=_compile(r"empresas?\s+que\s+(contratam|buscam\s+contratar)"),
        points=3,
        label="plataforma expõe perfil a empresas que contratam",
    ),
]

# Sinais negativos (–100 a –80): indicam programa expirado
NEGATIVE_SIGNALS: list[Signal] = [
    Signal(
        pattern=_compile(r"inscrições?\s+(encerradas?|foram\s+encerradas?)"),
        points=-100,
        label="prazo de inscrição encerrado",
        is_negative=True,
    ),
    Signal(
        pattern=_compile(r"seleção\s+(encerrada|finalizada|concluída)"),
        points=-100,
        label="processo seletivo encerrado",
        is_negative=True,
    ),
    Signal(
        pattern=_compile(r"processo\s+seletivo\s+(de\s+)?(20[0-2][0-9])"),
        points=-80,
        label="processo seletivo de ano anterior",
        is_negative=True,
    ),
    Signal(
        pattern=_compile(r"edição\s+(de\s+)?(20[0-2][0-9])\b"),
        points=-80,
        label="edição de ano anterior",
        is_negative=True,
    ),
    Signal(
        pattern=_compile(r"prazo\s+de\s+seleção\s+já\s+venceu"),
        points=-100,
        label="prazo de seleção vencido",
        is_negative=True,
    ),
    Signal(
        pattern=_compile(r"depoimentos?\s+de\s+(ex-?alunos?|conclusão)"),
        points=-30,
        label="depoimentos de conclusão (indicativo de edição antiga)",
        is_negative=True,
    ),
]

# Falsos positivos explícitos — contextos que NÃO indicam emprego
FALSE_POSITIVE_PATTERNS: list[re.Pattern] = [
    _compile(r"vagas?\s+gratuitas?\s+(para\s+o\s+bootcamp|no\s+bootcamp|no\s+curso|do\s+curso)"),
    _compile(r"vagas?\s+gratuitas?\s+para\s+participa"),
    _compile(r"\d+\s+vagas?\s+gratuitas?"),
]


# ---------------------------------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------------------------------

def _extract_context(text: str, match: re.Match, window: int = 120) -> str:
    """Extrai janela de contexto ao redor de um match."""
    start = max(0, match.start() - window)
    end = min(len(text), match.end() + window)
    return text[start:end].strip()


def _is_false_positive(context: str) -> bool:
    """Verifica se o contexto corresponde a um falso positivo conhecido."""
    for pat in FALSE_POSITIVE_PATTERNS:
        if pat.search(context):
            return True
    return False


def _check_exclude_context(context: str, signal: Signal) -> bool:
    """Verifica se o contexto possui padrões de exclusão do sinal."""
    for exc_pat in signal.exclude_context_patterns:
        if exc_pat.search(context):
            return True
    return False


def parse_deadline(raw: str) -> Optional[date]:
    """
    Converte o prazo do catálogo em data.

    Aceita ISO (2026-08-31, e também com hora) e o formato brasileiro
    (31/08/2026). Retorna None para vazio ou irreconhecível — nesse caso a
    situação da inscrição fica DESCONHECIDO e nada é filtrado por prazo.
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    # ISO, com ou sem componente de hora
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

    # Formato brasileiro
    match = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", raw)
    if match:
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            return None

    logger.debug("Prazo em formato não reconhecido: %r", raw[:40])
    return None


def evaluate_enrollment(raw_deadline: str, today: Optional[date] = None) -> tuple[str, Optional[int]]:
    """
    Determina a situação da inscrição a partir do prazo.

    Args:
        raw_deadline: prazo como vem do catálogo.
        today: data de referência; padrão é hoje.

    Returns:
        (situação, dias restantes). Dias restantes é None quando não há prazo
        conhecido, 0 no último dia e negativo depois de vencido.
    """
    prazo = parse_deadline(raw_deadline)
    if prazo is None:
        return ENROLLMENT_DESCONHECIDO, None

    hoje = today or hoje_brasil()
    dias = (prazo - hoje).days

    if dias < 0:
        return ENROLLMENT_ENCERRADO, dias
    return ENROLLMENT_ABERTO, dias


def _formata_data(raw: str) -> str:
    """Formata o prazo para exibição (DD/MM/AAAA), com fallback para o original."""
    prazo = parse_deadline(raw)
    return prazo.strftime("%d/%m/%Y") if prazo else (raw or "").strip()


def _observacao_de_prazo(status: str, days_left: Optional[int], raw_deadline: str) -> str:
    """Frase legível sobre a situação da inscrição."""
    quando = _formata_data(raw_deadline)

    if status == ENROLLMENT_ENCERRADO:
        dias = abs(days_left) if days_left is not None else None
        if dias is not None and dias < 365:
            return f"Inscrições encerradas em {quando} (há {dias} dias)."
        return f"Inscrições encerradas em {quando}."

    if status == ENROLLMENT_ABERTO:
        if days_left == 0:
            return f"Último dia de inscrição: {quando}."
        if days_left == 1:
            return f"Inscrições encerram amanhã ({quando})."
        return f"Inscrições abertas até {quando} ({days_left} dias restantes)."

    return ""


def _compute_hash(evidences: list[str]) -> str:
    """Gera hash curto e determinístico das evidências."""
    joined = "|".join(sorted(evidences))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Classificador principal
# ---------------------------------------------------------------------------

def classify(
    text: str,
    deadline: str = "",
    today: Optional[date] = None,
) -> ClassificationResult:
    """
    Classifica a chance de contratação de um bootcamp com base no texto da página.

    Args:
        text: texto extraído da página de detalhes do bootcamp.
        deadline: prazo do catálogo (campo `finish`). Quando informado e já
            vencido, a classificação é EXPIRADA independentemente da pontuação.
        today: data de referência para o prazo; padrão é hoje.

    Returns:
        ClassificationResult com classificação, pontuação, evidências e hash.
    """
    enrollment_status, days_left = evaluate_enrollment(deadline, today)

    if not text or not text.strip():
        return ClassificationResult(
            classification=(
                CLASSIFICATION_EXPIRADA
                if enrollment_status == ENROLLMENT_ENCERRADO
                else CLASSIFICATION_INDETERMINADA
            ),
            score=0,
            observation=(
                _observacao_de_prazo(enrollment_status, days_left, deadline)
                if enrollment_status == ENROLLMENT_ENCERRADO
                else "Texto vazio ou indisponível."
            ),
            enrollment_status=enrollment_status,
            days_left=days_left,
            deadline=(deadline or "").strip(),
        )

    score = 0
    evidences: list[str] = []
    negative_evidences: list[str] = []
    total_negative = 0

    # Verifica sinais negativos primeiro
    for signal in NEGATIVE_SIGNALS:
        for match in signal.pattern.finditer(text):
            context = _extract_context(text, match)
            score += signal.points  # pontos negativos
            total_negative += abs(signal.points)
            snippet = context[:100].replace("\n", " ").strip()
            negative_evidences.append(f"[{signal.label}] {snippet}")

    # Sinais positivos: cada sinal pontua no MÁXIMO uma vez por página.
    #
    # Contar por ocorrência inflava o score de forma imprevisível: o texto que
    # chega aqui repete trechos (as seções priorizadas do scraper aparecem de
    # novo dentro do texto completo), então a mesma frase podia render pontos
    # 2 ou 3 vezes. Além disso, uma frase repetida não é evidência mais forte
    # do que a mesma frase dita uma vez.
    for signals, snippet_len in (
        (STRONG_SIGNALS, 100),
        (MEDIUM_SIGNALS, 100),
        (WEAK_SIGNALS, 80),
    ):
        for signal in signals:
            for match in signal.pattern.finditer(text):
                context = _extract_context(text, match)
                if _is_false_positive(context) or _check_exclude_context(context, signal):
                    logger.debug("Sinal '%s' ignorado por falso positivo no contexto.", signal.label)
                    continue
                score += signal.points
                snippet = context[:snippet_len].replace("\n", " ").strip()
                evidences.append(f"[{signal.label}] {snippet}")
                break  # sinal já pontuou; próximas ocorrências não somam

    # Deduplica evidências mantendo ordem
    seen: set[str] = set()
    unique_evidences: list[str] = []
    for ev in evidences:
        key = ev[:60]
        if key not in seen:
            seen.add(key)
            unique_evidences.append(ev)

    all_evidences = negative_evidences + unique_evidences

    # Determina classificação.
    #
    # O prazo do catálogo vem antes de tudo: é data estruturada, não heurística.
    # Um bootcamp cujas inscrições fecharam não interessa por mais promissor que
    # o texto pareça — 212 dos 217 bootcamps do catálogo já venceram, vários
    # ainda de 2021, e sem esse corte todos eles geram notificação.
    if enrollment_status == ENROLLMENT_ENCERRADO:
        classification = CLASSIFICATION_EXPIRADA
        observation = _observacao_de_prazo(enrollment_status, days_left, deadline)
    elif total_negative >= 80:
        classification = CLASSIFICATION_EXPIRADA
        observation = "Sinais de programa expirado ou processo seletivo encerrado."
    elif score >= 45:
        # Calibrado para a escala em que cada sinal pontua uma única vez por
        # página: um sinal forte sozinho (40 a 50 pontos) já caracteriza ALTA.
        # O limiar anterior, 60, só fazia sentido quando frases repetidas
        # somavam pontos várias vezes e inflavam o score.
        classification = CLASSIFICATION_ALTA
        observation = "Sinais fortes de processo seletivo ativo para contratação."
    elif score >= 20:
        classification = CLASSIFICATION_MEDIA
        observation = "Sinais moderados de oportunidade de recrutamento."
    elif score >= 3:
        classification = CLASSIFICATION_BAIXA
        observation = "Menções vagas de oportunidade sem processo concreto identificado."
    else:
        classification = CLASSIFICATION_INDETERMINADA
        observation = "Sem evidências suficientes de oportunidade de contratação."

    excerpt_hash = _compute_hash(all_evidences) if all_evidences else ""

    logger.debug(
        "Classificação: %s | Score: %d | Evidências: %d",
        classification,
        score,
        len(all_evidences),
    )

    # Acrescenta o prazo à observação quando a inscrição segue aberta, para a
    # notificação dizer quanto tempo resta.
    if enrollment_status == ENROLLMENT_ABERTO:
        observation = f"{observation} {_observacao_de_prazo(enrollment_status, days_left, deadline)}".strip()

    return ClassificationResult(
        classification=classification,
        score=score,
        evidences=all_evidences[:8],  # limita para não poluir a mensagem
        observation=observation,
        relevant_excerpt_hash=excerpt_hash,
        enrollment_status=enrollment_status,
        days_left=days_left,
        deadline=(deadline or "").strip(),
    )
