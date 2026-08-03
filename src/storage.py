"""
storage.py — Persistência atômica do histórico de bootcamps em JSON.

Garante:
- gravação atômica (escrita em temp + rename)
- leitura segura mesmo com arquivo vazio ou inexistente
- atualização incremental sem perda de dados anteriores
- prevenção de duplicidades via stable_id
"""

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Caminho padrão relativo à raiz do projeto
DEFAULT_STORAGE_PATH = Path(__file__).parent.parent / "data" / "bootcamps.json"


# ---------------------------------------------------------------------------
# Estrutura de registro
# ---------------------------------------------------------------------------

@dataclass
class BootcampRecord:
    """Representa um bootcamp armazenado no histórico."""

    stable_id: str
    name: str
    company: str
    url: str
    first_seen_at: str
    last_checked_at: str
    classification: str = "INDETERMINADA"
    score: int = 0
    evidences: list[str] = field(default_factory=list)
    notification_status: str = "pending"  # pending | sent | skipped
    catalog_position: Optional[int] = None
    catalog_summary: str = ""
    status: str = ""
    launch_info: str = ""
    observation: str = ""
    relevant_excerpt_hash: str = ""
    previous_classification: str = ""
    update_notification_hashes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "stable_id": self.stable_id,
            "name": self.name,
            "company": self.company,
            "url": self.url,
            "first_seen_at": self.first_seen_at,
            "last_checked_at": self.last_checked_at,
            "classification": self.classification,
            "score": self.score,
            "evidences": self.evidences,
            "notification_status": self.notification_status,
            "catalog_position": self.catalog_position,
            "catalog_summary": self.catalog_summary,
            "status": self.status,
            "launch_info": self.launch_info,
            "observation": self.observation,
            "relevant_excerpt_hash": self.relevant_excerpt_hash,
            "previous_classification": self.previous_classification,
            "update_notification_hashes": self.update_notification_hashes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BootcampRecord":
        return cls(
            stable_id=data.get("stable_id", ""),
            name=data.get("name", ""),
            company=data.get("company", ""),
            url=data.get("url", ""),
            first_seen_at=data.get("first_seen_at", ""),
            last_checked_at=data.get("last_checked_at", ""),
            classification=data.get("classification", "INDETERMINADA"),
            score=data.get("score", 0),
            evidences=data.get("evidences", []),
            notification_status=data.get("notification_status", "pending"),
            catalog_position=data.get("catalog_position"),
            catalog_summary=data.get("catalog_summary", ""),
            status=data.get("status", ""),
            launch_info=data.get("launch_info", ""),
            observation=data.get("observation", ""),
            relevant_excerpt_hash=data.get("relevant_excerpt_hash", ""),
            previous_classification=data.get("previous_classification", ""),
            update_notification_hashes=data.get("update_notification_hashes", []),
        )


# ---------------------------------------------------------------------------
# Operações de armazenamento
# ---------------------------------------------------------------------------

def load_history(path: Path = DEFAULT_STORAGE_PATH) -> dict[str, BootcampRecord]:
    """
    Carrega o histórico de bootcamps do arquivo JSON.

    Args:
        path: caminho para o arquivo JSON.

    Returns:
        Dicionário {stable_id: BootcampRecord}.
    """
    if not path.exists():
        logger.info("Arquivo de histórico não encontrado em %s. Iniciando vazio.", path)
        return {}

    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            logger.info("Arquivo de histórico vazio. Iniciando vazio.")
            return {}

        raw: list[dict] = json.loads(content)
        if not isinstance(raw, list):
            logger.warning("Formato inesperado no histórico (esperava lista). Iniciando vazio.")
            return {}

        history: dict[str, BootcampRecord] = {}
        for item in raw:
            try:
                record = BootcampRecord.from_dict(item)
                if record.stable_id:
                    history[record.stable_id] = record
            except Exception as exc:
                logger.warning("Entrada inválida no histórico ignorada: %s", exc)

        logger.info("Histórico carregado: %d bootcamps.", len(history))
        return history

    except json.JSONDecodeError as exc:
        logger.error("JSON inválido no histórico: %s. Iniciando vazio.", exc)
        return {}
    except OSError as exc:
        logger.error("Erro ao ler histórico: %s. Iniciando vazio.", exc)
        return {}


def save_history(
    history: dict[str, BootcampRecord],
    path: Path = DEFAULT_STORAGE_PATH,
) -> None:
    """
    Salva o histórico de bootcamps atomicamente no arquivo JSON.

    A escrita é feita em arquivo temporário no mesmo diretório e depois
    substituída com os.replace(), garantindo atomicidade em sistemas POSIX
    e minimizando risco de corrupção no Windows.

    Args:
        history: dicionário {stable_id: BootcampRecord}.
        path: caminho de destino.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    data = [record.to_dict() for record in history.values()]

    # Escrita atômica via arquivo temporário no mesmo diretório
    dir_path = str(path.parent)
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp", prefix="bootcamps_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
            logger.info("Histórico salvo atomicamente: %d registros em %s.", len(data), path)
        except Exception:
            # Tenta remover o temporário se falhar
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as exc:
        logger.error("Falha ao salvar histórico em %s: %s", path, exc)
        raise


def upsert_record(
    history: dict[str, BootcampRecord],
    record: BootcampRecord,
) -> tuple[bool, bool]:
    """
    Insere ou atualiza um registro no histórico em memória.

    Args:
        history: dicionário atual do histórico.
        record: registro a inserir ou atualizar.

    Returns:
        (is_new, is_updated): flags indicando se é novo ou atualizado.
    """
    existing = history.get(record.stable_id)

    if existing is None:
        history[record.stable_id] = record
        logger.debug("Novo bootcamp inserido: %s", record.stable_id)
        return True, False

    # Atualiza campos que podem mudar sem criar nova entrada
    changed = False

    if existing.classification != record.classification:
        existing.previous_classification = existing.classification
        existing.classification = record.classification
        changed = True

    if existing.score != record.score:
        existing.score = record.score
        changed = True

    if existing.relevant_excerpt_hash != record.relevant_excerpt_hash:
        existing.relevant_excerpt_hash = record.relevant_excerpt_hash
        changed = True

    existing.last_checked_at = record.last_checked_at
    existing.evidences = record.evidences
    existing.observation = record.observation
    existing.catalog_position = record.catalog_position
    existing.catalog_summary = record.catalog_summary
    existing.status = record.status
    existing.launch_info = record.launch_info

    return False, changed


def mark_notification_sent(
    history: dict[str, BootcampRecord],
    stable_id: str,
    update_hash: Optional[str] = None,
) -> None:
    """
    Marca notificação como enviada para um bootcamp.

    Args:
        history: histórico atual.
        stable_id: identificador do bootcamp.
        update_hash: se fornecido, registra hash da atualização já notificada.
    """
    record = history.get(stable_id)
    if record is None:
        logger.warning("stable_id %s não encontrado ao marcar notificação.", stable_id)
        return

    record.notification_status = "sent"
    if update_hash and update_hash not in record.update_notification_hashes:
        record.update_notification_hashes.append(update_hash)
