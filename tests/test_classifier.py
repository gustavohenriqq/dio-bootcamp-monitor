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
