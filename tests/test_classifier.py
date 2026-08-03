"""
test_classifier.py — Testes do classificador de chance de contratação.

Sem acesso à internet. Baseado em fixtures HTML locais e textos sintéticos.
"""

import sys
from pathlib import Path

import pytest

# Adiciona src ao path para importação direta
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from classifier import (
    CLASSIFICATION_ALTA,
    CLASSIFICATION_BAIXA,
    CLASSIFICATION_EXPIRADA,
    CLASSIFICATION_INDETERMINADA,
    CLASSIFICATION_MEDIA,
    classify,
)
from dio_scraper import extract_detail_text

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    """Carrega fixture HTML e extrai texto limpo para classificação."""
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return extract_detail_text(html)


# ---------------------------------------------------------------------------
# Testes de classificação ALTA
# ---------------------------------------------------------------------------

class TestClassificacaoAlta:
    def test_processo_seletivo_ativo(self):
        text = (
            "Este bootcamp oferece um processo seletivo ativo para desenvolvedores. "
            "A primeira etapa do processo seletivo ocorre durante o bootcamp. "
            "Os candidatos selecionados terão entrevistas técnicas com a empresa. "
            "Vagas disponíveis: 50 vagas de emprego para engenheiros Python. "
            "Recrutadores da empresa acompanharão os participantes durante todo o programa."
        )
        result = classify(text)
        assert result.classification == CLASSIFICATION_ALTA, (
            f"Esperava ALTA, obteve {result.classification}. Score: {result.score}. "
            f"Evidências: {result.evidences}"
        )
        assert result.score >= 60

    def test_fixture_alta(self):
        text = _load_fixture("bootcamp_detail_high.html")
        result = classify(text)
        assert result.classification == CLASSIFICATION_ALTA, (
            f"Esperava ALTA para bootcamp de alto potencial. "
            f"Classificação: {result.classification}, Score: {result.score}"
        )

    def test_evidencias_presentes_para_alta(self):
        text = (
            "Processo seletivo ativo para 30 vagas de emprego em Python. "
            "Candidatos selecionados passarão por entrevistas com a empresa. "
            "Recrutadores da empresa acompanharão os participantes."
        )
        result = classify(text)
        assert len(result.evidences) >= 1, "Deve haver ao menos uma evidência para classificação ALTA."
        assert result.relevant_excerpt_hash != "", "Hash de evidências deve ser preenchido."


# ---------------------------------------------------------------------------
# Testes de classificação MÉDIA
# ---------------------------------------------------------------------------

class TestClassificacaoMedia:
    def test_possibilidade_contratacao(self):
        text = (
            "Este bootcamp oferece possibilidade real de contratação para os melhores participantes. "
            "Os recrutadores poderão acessar os perfis dos destaques. "
            "Haverá mentoria específica com o RH da empresa parceira."
        )
        result = classify(text)
        assert result.classification == CLASSIFICATION_MEDIA, (
            f"Esperava MÉDIA. Obteve {result.classification}. Score: {result.score}."
        )

    def test_fixture_media(self):
        text = _load_fixture("bootcamp_detail_medium.html")
        result = classify(text)
        assert result.classification in (CLASSIFICATION_MEDIA, CLASSIFICATION_ALTA), (
            f"Bootcamp médio deveria ser MÉDIA ou ALTA. Obteve: {result.classification}."
        )


# ---------------------------------------------------------------------------
# Testes de classificação BAIXA
# ---------------------------------------------------------------------------

class TestClassificacaoBaixa:
    def test_talent_match_e_certificado(self):
        text = (
            "Perfil disponível na Talent Match após a conclusão. "
            "Certificado para o currículo reconhecido pelo mercado. "
            "Destaque seu perfil para oportunidades em empresas parceiras."
        )
        result = classify(text)
        assert result.classification == CLASSIFICATION_BAIXA, (
            f"Esperava BAIXA para Talent Match + certificado. "
            f"Obteve {result.classification}. Score: {result.score}."
        )

    def test_fixture_baixa(self):
        text = _load_fixture("bootcamp_detail_low.html")
        result = classify(text)
        assert result.classification == CLASSIFICATION_BAIXA, (
            f"Fixture low deveria ser BAIXA. Obteve: {result.classification}."
        )

    def test_talent_match_isolado_nao_e_alta(self):
        """Talent Match sozinha não pode gerar classificação ALTA."""
        text = "Perfil disponível na Talent Match da plataforma DIO."
        result = classify(text)
        assert result.classification not in (CLASSIFICATION_ALTA,), (
            "Talent Match sozinha não deve gerar ALTA."
        )


# ---------------------------------------------------------------------------
# Testes de falso positivo
# ---------------------------------------------------------------------------

class TestFalsosPositivos:
    def test_vagas_gratuitas_nao_sao_vagas_de_emprego(self):
        text = (
            "Temos 500 vagas gratuitas para o bootcamp! "
            "Garanta já a sua vaga gratuita neste curso completo."
        )
        result = classify(text)
        assert result.classification != CLASSIFICATION_ALTA, (
            f"'vagas gratuitas' não devem ser confundidas com vagas de emprego. "
            f"Obteve: {result.classification}. Score: {result.score}."
        )

    def test_fixture_falso_positivo(self):
        text = _load_fixture("bootcamp_detail_false_positive.html")
        result = classify(text)
        assert result.classification != CLASSIFICATION_ALTA, (
            f"Fixture de falso positivo não deve gerar ALTA. "
            f"Obteve: {result.classification}. Score: {result.score}."
        )

    def test_vagas_limitadas_no_curso_nao_sao_emprego(self):
        text = "Vagas limitadas! Inscreva-se já no bootcamp gratuito."
        result = classify(text)
        assert result.classification != CLASSIFICATION_ALTA

    def test_vagas_no_bootcamp_nao_sao_emprego(self):
        text = "As vagas no bootcamp são limitadas. Reserve a sua vaga no curso."
        result = classify(text)
        assert result.classification not in (CLASSIFICATION_ALTA, CLASSIFICATION_MEDIA)


# ---------------------------------------------------------------------------
# Testes de classificação EXPIRADA
# ---------------------------------------------------------------------------

class TestClassificacaoExpirada:
    def test_inscricoes_encerradas(self):
        text = (
            "As inscrições foram encerradas em dezembro de 2024. "
            "A seleção foi finalizada com sucesso."
        )
        result = classify(text)
        assert result.classification == CLASSIFICATION_EXPIRADA, (
            f"Processo com inscrições encerradas deve ser EXPIRADA. "
            f"Obteve: {result.classification}. Score: {result.score}."
        )

    def test_edicao_antiga(self):
        text = (
            "Esta foi a edição de 2024 do bootcamp. "
            "Processo seletivo de 2024 encerrado com 200 aprovados."
        )
        result = classify(text)
        assert result.classification == CLASSIFICATION_EXPIRADA, (
            f"Edição antiga deve ser EXPIRADA. Obteve: {result.classification}."
        )

    def test_fixture_expirada(self):
        text = _load_fixture("bootcamp_detail_expired.html")
        result = classify(text)
        assert result.classification == CLASSIFICATION_EXPIRADA, (
            f"Fixture expired deve ser EXPIRADA. Obteve: {result.classification}."
        )

    def test_selecao_encerrada_nao_e_alta(self):
        text = (
            "Seleção encerrada. Candidatos selecionados já foram notificados. "
            "Inscrições foram encerradas no mês passado."
        )
        result = classify(text)
        assert result.classification == CLASSIFICATION_EXPIRADA


# ---------------------------------------------------------------------------
# Testes de classificação INDETERMINADA
# ---------------------------------------------------------------------------

class TestClassificacaoIndeterminada:
    def test_texto_vazio(self):
        result = classify("")
        assert result.classification == CLASSIFICATION_INDETERMINADA
        assert result.score == 0

    def test_texto_sem_sinais(self):
        text = "Aprenda programação com este bootcamp completo. Diversos módulos e projetos práticos."
        result = classify(text)
        assert result.classification == CLASSIFICATION_INDETERMINADA

    def test_none_como_texto_vazio(self):
        """Garante que None não causa exceção (via str vazío)."""
        result = classify("   ")
        assert result.classification == CLASSIFICATION_INDETERMINADA


# ---------------------------------------------------------------------------
# Testes de explicabilidade
# ---------------------------------------------------------------------------

class TestExplicabilidade:
    def test_classificacao_retorna_observacao(self):
        text = "Processo seletivo ativo para 20 vagas disponíveis de desenvolvedor."
        result = classify(text)
        assert result.observation, "Observação deve estar preenchida."

    def test_hash_determinístico(self):
        """Mesmo texto deve gerar mesmo hash."""
        text = "Talent Match disponível para participantes do bootcamp."
        result1 = classify(text)
        result2 = classify(text)
        assert result1.relevant_excerpt_hash == result2.relevant_excerpt_hash

    def test_to_dict_completo(self):
        text = "Certificado para o currículo e destaque seu perfil."
        result = classify(text)
        d = result.to_dict()
        assert "classification" in d
        assert "score" in d
        assert "evidences" in d
        assert "observation" in d
        assert "relevant_excerpt_hash" in d


# ---------------------------------------------------------------------------
# Boilerplate da plataforma DIO
#
# Frases como "Tenha chances reais de contratação" (seção "Sua jornada") e o
# mockup "Você no futuro" aparecem em praticamente toda página de bootcamp —
# "chances reais de contratação" foi medida em 64 de 65 páginas do catálogo.
# Elas precisam pontuar, senão a página fica INDETERMINADA mesmo tendo texto;
# mas com peso baixo, senão o catálogo inteiro vira MÉDIA e a classificação
# para de discriminar.
# ---------------------------------------------------------------------------

BOILERPLATE_DIO = (
    "Faça sua inscrição. Participe das mentorias ao vivo e alavanque sua carreira. "
    "Construa uma rede de contatos que poderá te ajudar nessa nova fase. "
    "Pratique com desafios de código. Conclua suas atividades e conquiste seu certificado. "
    "Tenha chances reais de contratação. "
    "Força do perfil na DIO: DIAMOND. Seu perfil tem alta força na DIO e grandes chances "
    "de se conectar com oportunidades de empresas inovadoras do mercado! "
    "Analisamos o seu perfil e vimos que ele é muito interessante para a vaga de Software "
    "Engineer que temos aberta! Esperamos você nas próximas etapas do processo de contratação! "
    "Vamos juntos embarcar nessa oportunidade?"
)


class TestBoilerplateDaPlataforma:
    def test_boilerplate_sozinho_nao_fica_indeterminada(self):
        """Página com o texto padrão da DIO tem, sim, alguma menção de oportunidade."""
        r = classify(BOILERPLATE_DIO)
        assert r.classification != CLASSIFICATION_INDETERMINADA, (
            "Página com 'chances reais de contratação' não pode sair como "
            f"'sem evidências'. Score: {r.score}"
        )

    def test_boilerplate_sozinho_nao_passa_de_baixa(self):
        """O que aparece em toda página não pode promover nada a MÉDIA/ALTA."""
        r = classify(BOILERPLATE_DIO)
        assert r.classification == CLASSIFICATION_BAIXA, (
            f"Boilerplate deveria dar BAIXA, obteve {r.classification} (score {r.score}). "
            "Pontuar boilerplate como sinal médio jogaria o catálogo inteiro para MÉDIA."
        )

    def test_mockup_de_recrutador_nao_vale_processo_seletivo(self):
        """O print 'Você no futuro' é ficção de marketing, com nome e vaga inventados."""
        mockup = (
            "Você no futuro. Seu nome. Software Engineer. "
            "Analisamos o seu perfil e vimos que ele é muito interessante para a vaga "
            "de Software Engineer que temos aberta! Esperamos você nas próximas etapas "
            "do processo de contratação!"
        )
        r = classify(mockup)
        assert r.classification in (CLASSIFICATION_BAIXA, CLASSIFICATION_INDETERMINADA), (
            f"Mockup fictício não pode indicar processo real. Obteve {r.classification}."
        )

    def test_sinal_real_supera_o_boilerplate(self):
        """Bootcamp com processo seletivo de verdade tem que se destacar do ruído."""
        real = BOILERPLATE_DIO + (
            " Os melhores classificados no Bootcamp serão selecionados para seguir "
            "nas fases seguintes do processo seletivo de contratação."
        )
        r = classify(real)
        assert r.classification == CLASSIFICATION_ALTA, (
            f"Esperava ALTA, obteve {r.classification} (score {r.score})."
        )
        assert r.score > classify(BOILERPLATE_DIO).score + 40


# ---------------------------------------------------------------------------
# Cada sinal pontua uma vez por página
#
# O texto que chega ao classificador repete trechos (o scraper concatena as
# seções priorizadas com o texto completo). Contar por ocorrência inflava o
# score: bootcamps viravam MÉDIA só porque "Talent Match" aparecia 5 vezes.
# ---------------------------------------------------------------------------

class TestSinalPontuaUmaVez:
    def test_frase_repetida_nao_multiplica_score(self):
        uma = classify("Perfil disponível na Talent Match.")
        cinco = classify("Perfil disponível na Talent Match. " * 5)
        assert uma.score == cinco.score, (
            f"Repetir a frase mudou o score de {uma.score} para {cinco.score}."
        )

    def test_talent_match_repetido_nao_alcanca_media(self):
        r = classify("Talent Match. " * 6)
        assert r.classification != CLASSIFICATION_MEDIA, (
            f"Talent Match repetido não é sinal moderado. Obteve {r.classification} "
            f"com score {r.score}."
        )

    def test_sinais_distintos_continuam_somando(self):
        """A deduplicação é por sinal, não um teto global de pontuação."""
        um = classify("Processo seletivo ativo para as vagas.")
        dois = classify(
            "Processo seletivo ativo para as vagas. "
            "Candidatos selecionados farão entrevistas técnicas com a empresa."
        )
        assert dois.score > um.score

    def test_evidencia_nao_repete_o_mesmo_sinal(self):
        r = classify("Talent Match. " * 4)
        labels = [e.split("]")[0] for e in r.evidences]
        assert len(labels) == len(set(labels)), f"Evidências duplicadas: {labels}"


# ---------------------------------------------------------------------------
# Sinais fortes acrescentados a partir do catálogo real
# ---------------------------------------------------------------------------

class TestSinaisDiscriminantes:
    def test_processo_seletivo_de_contratacao(self):
        r = classify("Os aprovados seguem no processo seletivo de contratação da empresa.")
        assert r.classification == CLASSIFICATION_ALTA, f"score {r.score}"

    def test_vagas_afirmativas(self):
        r = classify(
            "Mantendo o compromisso com a Diversidade e Inclusão, a WEX terá "
            "vagas afirmativas para pessoas com deficiência."
        )
        assert r.classification in (CLASSIFICATION_ALTA, CLASSIFICATION_MEDIA), f"score {r.score}"

    def test_um_sinal_forte_sozinho_basta_para_alta(self):
        """Limiar calibrado para a escala em que cada sinal pontua uma vez."""
        r = classify("O processo seletivo está aberto para os participantes.")
        assert r.classification == CLASSIFICATION_ALTA, (
            f"Um sinal forte isolado deveria bastar. Obteve {r.classification} (score {r.score})."
        )

    def test_frases_de_plataforma_valem_pouco(self):
        """'empresas que contratam' descreve a DIO, não um processo deste bootcamp."""
        r = classify(
            "Após gerar o seu certificado, o seu perfil ficará disponível para as "
            "empresas parceiras da DIO que estão contratando."
        )
        assert r.classification == CLASSIFICATION_BAIXA, f"{r.classification}, score {r.score}"


# ---------------------------------------------------------------------------
# Situação da inscrição a partir do prazo do catálogo
#
# O campo `finish` de props.pageProps.bootcamps é data ISO — dado estruturado,
# não heurística. Em agosto/2026, 212 dos 217 bootcamps do catálogo já tinham
# vencido, vários ainda de 2021. Sem esse corte, todos geram notificação.
# ---------------------------------------------------------------------------

from datetime import date

from classifier import (
    ENROLLMENT_ABERTO,
    ENROLLMENT_DESCONHECIDO,
    ENROLLMENT_ENCERRADO,
    evaluate_enrollment,
    parse_deadline,
)

HOJE = date(2026, 8, 3)


class TestParseDeadline:
    def test_iso(self):
        assert parse_deadline("2026-08-31") == date(2026, 8, 31)

    def test_iso_com_hora(self):
        assert parse_deadline("2026-08-31T23:59:00Z") == date(2026, 8, 31)

    def test_formato_brasileiro(self):
        assert parse_deadline("31/08/2026") == date(2026, 8, 31)

    def test_vazio_e_lixo_retornam_none(self):
        assert parse_deadline("") is None
        assert parse_deadline("   ") is None
        assert parse_deadline("em breve") is None
        assert parse_deadline("2026-13-45") is None


class TestEvaluateEnrollment:
    def test_prazo_futuro_esta_aberto(self):
        status, dias = evaluate_enrollment("2026-08-31", today=HOJE)
        assert status == ENROLLMENT_ABERTO
        assert dias == 28

    def test_ultimo_dia_ainda_conta_como_aberto(self):
        status, dias = evaluate_enrollment("2026-08-03", today=HOJE)
        assert status == ENROLLMENT_ABERTO
        assert dias == 0

    def test_prazo_vencido_esta_encerrado(self):
        status, dias = evaluate_enrollment("2026-07-31", today=HOJE)
        assert status == ENROLLMENT_ENCERRADO
        assert dias == -3

    def test_sem_prazo_fica_desconhecido(self):
        status, dias = evaluate_enrollment("", today=HOJE)
        assert status == ENROLLMENT_DESCONHECIDO
        assert dias is None


class TestPrazoNaClassificacao:
    TEXTO_FORTE = (
        "Processo seletivo ativo para 20 vagas de emprego disponíveis. "
        "Candidatos selecionados farão entrevistas técnicas com a empresa."
    )

    def test_prazo_vencido_expira_mesmo_com_texto_forte(self):
        """Não interessa o quão promissora é a página se a inscrição fechou."""
        r = classify(self.TEXTO_FORTE, deadline="2021-04-14", today=HOJE)
        assert r.classification == CLASSIFICATION_EXPIRADA, (
            f"Bootcamp de 2021 não pode ser {r.classification}."
        )
        assert r.enrollment_status == ENROLLMENT_ENCERRADO
        assert not r.is_open

    def test_prazo_aberto_preserva_a_classificacao(self):
        r = classify(self.TEXTO_FORTE, deadline="2026-09-08", today=HOJE)
        assert r.classification == CLASSIFICATION_ALTA
        assert r.is_open
        assert r.days_left == 36

    def test_sem_prazo_nao_filtra_nada(self):
        """Prazo ausente não pode ser tratado como vencido."""
        r = classify(self.TEXTO_FORTE, deadline="", today=HOJE)
        assert r.classification == CLASSIFICATION_ALTA
        assert r.enrollment_status == ENROLLMENT_DESCONHECIDO

    def test_prazo_invalido_nao_filtra_nada(self):
        r = classify(self.TEXTO_FORTE, deadline="data indefinida", today=HOJE)
        assert r.classification == CLASSIFICATION_ALTA
        assert r.enrollment_status == ENROLLMENT_DESCONHECIDO

    def test_observacao_informa_quando_encerrou(self):
        r = classify(self.TEXTO_FORTE, deadline="2026-07-31", today=HOJE)
        assert "31/07/2026" in r.observation
        assert "encerrada" in r.observation.lower()

    def test_observacao_informa_dias_restantes(self):
        r = classify(self.TEXTO_FORTE, deadline="2026-08-10", today=HOJE)
        assert "7 dias" in r.observation

    def test_texto_vazio_com_prazo_vencido_expira(self):
        r = classify("", deadline="2022-01-01", today=HOJE)
        assert r.classification == CLASSIFICATION_EXPIRADA

    def test_texto_vazio_sem_prazo_fica_indeterminada(self):
        r = classify("", deadline="", today=HOJE)
        assert r.classification == CLASSIFICATION_INDETERMINADA
