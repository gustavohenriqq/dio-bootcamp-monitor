"""
test_storage.py — Testes da camada de persistência (storage).

Sem acesso à internet. Usa arquivos temporários.
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from storage import (
    BootcampRecord,
    load_history,
    mark_notification_sent,
    save_history,
    upsert_record,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_record(
    stable_id: str = "test-id-001",
    name: str = "Bootcamp Teste",
    company: str = "Empresa Teste",
    url: str = "https://www.dio.me/bootcamp/bootcamp-teste",
    classification: str = "ALTA",
    score: int = 80,
    notification_status: str = "pending",
) -> BootcampRecord:
    return BootcampRecord(
        stable_id=stable_id,
        name=name,
        company=company,
        url=url,
        first_seen_at="2026-06-01T10:00:00-03:00",
        last_checked_at="2026-06-24T08:00:00-03:00",
        classification=classification,
        score=score,
        evidences=["[processo seletivo ativo] exemplo de evidência"],
        notification_status=notification_status,
        catalog_position=0,
        catalog_summary="Resumo do bootcamp",
        observation="Sinais fortes.",
        relevant_excerpt_hash="abc123",
    )


# ---------------------------------------------------------------------------
# Testes de load_history
# ---------------------------------------------------------------------------

class TestLoadHistory:
    def test_arquivo_inexistente_retorna_vazio(self, tmp_path):
        path = tmp_path / "inexistente.json"
        history = load_history(path)
        assert history == {}

    def test_arquivo_vazio_retorna_vazio(self, tmp_path):
        path = tmp_path / "vazio.json"
        path.write_text("", encoding="utf-8")
        history = load_history(path)
        assert history == {}

    def test_json_invalido_retorna_vazio(self, tmp_path):
        path = tmp_path / "invalido.json"
        path.write_text("{ invalid json !!!", encoding="utf-8")
        history = load_history(path)
        assert history == {}

    def test_lista_vazia_retorna_vazio(self, tmp_path):
        path = tmp_path / "lista_vazia.json"
        path.write_text("[]", encoding="utf-8")
        history = load_history(path)
        assert history == {}

    def test_carrega_registros_validos(self, tmp_path):
        path = tmp_path / "bootcamps.json"
        record = make_record()
        path.write_text(
            json.dumps([record.to_dict()], ensure_ascii=False),
            encoding="utf-8",
        )
        history = load_history(path)
        assert "test-id-001" in history
        assert history["test-id-001"].name == "Bootcamp Teste"

    def test_entradas_sem_stable_id_sao_ignoradas(self, tmp_path):
        path = tmp_path / "bootcamps.json"
        data = [{"name": "Sem ID", "company": "X", "stable_id": ""}]
        path.write_text(json.dumps(data), encoding="utf-8")
        history = load_history(path)
        assert history == {}

    def test_carrega_multiplos_registros(self, tmp_path):
        path = tmp_path / "bootcamps.json"
        r1 = make_record(stable_id="id-001", name="Bootcamp A")
        r2 = make_record(stable_id="id-002", name="Bootcamp B")
        path.write_text(
            json.dumps([r1.to_dict(), r2.to_dict()], ensure_ascii=False),
            encoding="utf-8",
        )
        history = load_history(path)
        assert len(history) == 2
        assert "id-001" in history
        assert "id-002" in history


# ---------------------------------------------------------------------------
# Testes de save_history
# ---------------------------------------------------------------------------

class TestSaveHistory:
    def test_salva_e_recarrega(self, tmp_path):
        path = tmp_path / "bootcamps.json"
        record = make_record()
        history = {record.stable_id: record}
        save_history(history, path)

        reloaded = load_history(path)
        assert record.stable_id in reloaded
        assert reloaded[record.stable_id].name == record.name

    def test_salva_atomicamente_cria_diretorios(self, tmp_path):
        path = tmp_path / "subdir" / "bootcamps.json"
        record = make_record()
        history = {record.stable_id: record}
        save_history(history, path)
        assert path.exists()

    def test_salva_lista_vazia(self, tmp_path):
        path = tmp_path / "bootcamps.json"
        save_history({}, path)
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content == []

    def test_sobrescreve_corretamente(self, tmp_path):
        path = tmp_path / "bootcamps.json"
        r1 = make_record(stable_id="id-001")
        save_history({"id-001": r1}, path)

        r2 = make_record(stable_id="id-002", name="Bootcamp Novo")
        save_history({"id-001": r1, "id-002": r2}, path)

        history = load_history(path)
        assert len(history) == 2


# ---------------------------------------------------------------------------
# Testes de upsert_record
# ---------------------------------------------------------------------------

class TestUpsertRecord:
    def test_insere_novo_registro(self):
        history = {}
        record = make_record()
        is_new, is_updated = upsert_record(history, record)
        assert is_new is True
        assert is_updated is False
        assert record.stable_id in history

    def test_atualiza_registro_existente(self):
        history = {}
        original = make_record(classification="INDETERMINADA", score=0)
        upsert_record(history, original)

        updated = make_record(
            classification="ALTA",
            score=80,
        )
        updated.last_checked_at = "2026-06-25T08:00:00-03:00"
        is_new, is_updated = upsert_record(history, updated)

        assert is_new is False
        assert is_updated is True
        assert history["test-id-001"].classification == "ALTA"
        assert history["test-id-001"].previous_classification == "INDETERMINADA"

    def test_nao_duplica_registro(self):
        history = {}
        r = make_record()
        upsert_record(history, r)
        upsert_record(history, r)
        assert len(history) == 1

    def test_sem_mudanca_nao_marca_updated(self):
        history = {}
        r = make_record(classification="ALTA", score=80)
        upsert_record(history, r)

        r2 = make_record(classification="ALTA", score=80)
        r2.relevant_excerpt_hash = "abc123"  # mesmo hash
        is_new, is_updated = upsert_record(history, r2)

        assert is_new is False
        assert is_updated is False


# ---------------------------------------------------------------------------
# Testes de mark_notification_sent
# ---------------------------------------------------------------------------

class TestMarkNotificationSent:
    def test_marca_notificacao_enviada(self):
        history = {}
        r = make_record()
        upsert_record(history, r)

        mark_notification_sent(history, r.stable_id)
        assert history[r.stable_id].notification_status == "sent"

    def test_registra_hash_de_atualizacao(self):
        history = {}
        r = make_record()
        upsert_record(history, r)

        mark_notification_sent(history, r.stable_id, update_hash="newhash123")
        assert "newhash123" in history[r.stable_id].update_notification_hashes

    def test_nao_duplica_hash(self):
        history = {}
        r = make_record()
        upsert_record(history, r)

        mark_notification_sent(history, r.stable_id, update_hash="hash-x")
        mark_notification_sent(history, r.stable_id, update_hash="hash-x")

        assert history[r.stable_id].update_notification_hashes.count("hash-x") == 1

    def test_stable_id_inexistente_nao_crasha(self):
        history = {}
        # Não deve lançar exceção
        mark_notification_sent(history, "id-que-nao-existe")


# ---------------------------------------------------------------------------
# Testes de BootcampRecord
# ---------------------------------------------------------------------------

class TestBootcampRecord:
    def test_to_dict_e_from_dict_roundtrip(self):
        original = make_record()
        d = original.to_dict()
        restored = BootcampRecord.from_dict(d)
        assert restored.stable_id == original.stable_id
        assert restored.classification == original.classification
        assert restored.evidences == original.evidences

    def test_from_dict_com_campos_faltantes(self):
        """from_dict deve usar defaults seguros para campos ausentes."""
        minimal = {"stable_id": "x", "name": "Test", "company": "Co", "url": "https://example.com"}
        record = BootcampRecord.from_dict(minimal)
        assert record.stable_id == "x"
        assert record.classification == "INDETERMINADA"
        assert record.evidences == []
        assert record.notification_status == "pending"
