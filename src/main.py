"""
main.py — Orquestrador principal do DIO Bootcamp Monitor.

Fluxo:
1. Carrega configuração
2. Configura logging
3. Carrega histórico JSON
4. Busca catálogo público
5. Detecta novos bootcamps e alterações
6. Busca detalhes (respeitando MAX_DETAIL_PAGES)
7. Classifica
8. Salva histórico
9. Envia notificações Telegram
10. Envia resumo diário (opcional)
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import Config, load_config
from classifier import (
    CLASSIFICATION_ALTA,
    CLASSIFICATION_EXPIRADA,
    CLASSIFICATION_INDETERMINADA,
    CLASSIFICATION_MEDIA,
    CLASSIFICATION_BAIXA,
    ENROLLMENT_ABERTO,
    ENROLLMENT_ENCERRADO,
    classify,
    evaluate_enrollment,
    hoje_brasil,
    parse_deadline,
)
from dio_scraper import CatalogBootcamp, DioScraper
from storage import (
    BootcampRecord,
    DEFAULT_STORAGE_PATH,
    load_history,
    mark_notification_sent,
    save_history,
    upsert_record,
)
from telegram_notifier import (
    DailySummary,
    NewBootcampNotification,
    OpenBootcamp,
    UpdateNotification,
    build_notifier,
)

TZ = ZoneInfo("America/Sao_Paulo")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(level: str) -> None:
    """Configura o sistema de logging com nível configurável."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


# ---------------------------------------------------------------------------
# Helpers de tempo
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Retorna timestamp atual em ISO 8601 com timezone de São Paulo."""
    return datetime.now(tz=TZ).isoformat()


def _now_display() -> str:
    """Retorna timestamp formatado para exibição."""
    return datetime.now(tz=TZ).strftime("%d/%m/%Y %H:%M (BRT)")


# ---------------------------------------------------------------------------
# Detecção de alterações relevantes
# ---------------------------------------------------------------------------

def _is_relevant_update(existing: BootcampRecord, new_hash: str, new_classification: str) -> bool:
    """
    Verifica se uma atualização é relevante e ainda não foi notificada.

    Considera relevante quando:
    - A classificação mudou para melhor (ex.: INDETERMINADA → ALTA)
    - O hash de evidências mudou (conteúdo novo detectado) e não foi notificado antes

    Args:
        existing: registro atual no histórico.
        new_hash: hash das novas evidências.
        new_classification: nova classificação detectada.

    Returns:
        True se a atualização deve gerar notificação.
    """
    if not existing.previous_classification:
        # Nunca foi atualizado antes
        pass

    classification_changed = (
        existing.classification != new_classification
        and new_classification not in (CLASSIFICATION_INDETERMINADA, CLASSIFICATION_EXPIRADA)
    )
    hash_changed = (
        new_hash
        and new_hash != existing.relevant_excerpt_hash
        and new_hash not in existing.update_notification_hashes
    )

    return classification_changed or hash_changed


# ---------------------------------------------------------------------------
# Orquestrador principal
# ---------------------------------------------------------------------------

def run(config: Config, storage_path: Path = DEFAULT_STORAGE_PATH) -> None:
    """
    Executa o fluxo completo do monitor.

    Args:
        config: configurações carregadas.
        storage_path: caminho para o arquivo JSON de histórico.
    """
    logger = logging.getLogger(__name__)
    logger.info("=== DIO Bootcamp Monitor iniciando ===")

    # Carrega histórico
    history = load_history(storage_path)
    is_first_run = len(history) == 0
    logger.info("Histórico carregado: %d registros. Primeira execução: %s.", len(history), is_first_run)

    # Inicializa scraper e notifier
    scraper = DioScraper(base_url=config.dio_bootcamp_url, delay=config.request_delay_seconds)
    notifier = build_notifier(config.telegram_bot_token, config.telegram_chat_id)

    # Contadores para resumo diário
    new_count = 0
    update_count = 0
    alta_count = 0
    media_count = 0
    baixa_count = 0
    expirada_count = 0

    try:
        # Busca catálogo
        catalog: list[CatalogBootcamp] = scraper.fetch_catalog()

        if not catalog:
            logger.warning("Catálogo vazio. Encerrando sem alterações.")
            _save_and_exit(history, storage_path, notifier, config, DailySummary(0, 0, 0, 0, 0, 0))
            return

        logger.info("Catálogo: %d bootcamps encontrados.", len(catalog))

        # Separa novos, conhecidos e potencialmente alterados
        new_bootcamps: list[CatalogBootcamp] = []
        known_bootcamps: list[CatalogBootcamp] = []

        for bc in catalog:
            if bc.stable_id not in history:
                new_bootcamps.append(bc)
            else:
                known_bootcamps.append(bc)

        logger.info(
            "Novos: %d | Conhecidos: %d",
            len(new_bootcamps),
            len(known_bootcamps),
        )

        # Determina quais páginas de detalhe buscar
        pages_fetched = 0

        # Prioriza novos bootcamps para detalhe
        bootcamps_to_detail: list[CatalogBootcamp] = list(new_bootcamps)

        # Adiciona conhecidos com chance de atualização relevante, do menos
        # recentemente verificado para o mais recente. Sem essa ordenação, os
        # slots restantes de MAX_DETAIL_PAGES seriam sempre consumidos pelos
        # mesmos primeiros bootcamps do catálogo e mudanças no restante da
        # lista nunca seriam detectadas.
        #
        # Bootcamps com prazo vencido ficam de fora: a data não volta atrás, e
        # uma nova edição entra no catálogo com outro slug (portanto, outro
        # stable_id). Rechecá-los gastaria os slots do dia com programas mortos
        # — hoje 212 dos 217 — e adiaria por semanas a reverificação dos poucos
        # que ainda estão abertos, que são justamente os que importam.
        recheck_candidates = []
        skipped_expired = 0
        for bc in known_bootcamps:
            existing = history.get(bc.stable_id)
            if not existing or existing.notification_status != "sent":
                continue
            status, _ = evaluate_enrollment(bc.launch_info)
            if status == ENROLLMENT_ENCERRADO:
                skipped_expired += 1
                continue
            recheck_candidates.append(bc)

        recheck_candidates.sort(key=lambda bc: history[bc.stable_id].last_checked_at or "")
        bootcamps_to_detail.extend(recheck_candidates)

        if skipped_expired:
            logger.info(
                "Rechecagem: %d candidatos com inscrição aberta. %d ignorados por prazo vencido.",
                len(recheck_candidates),
                skipped_expired,
            )

        # Processa bootcamps com detalhe
        for bc in bootcamps_to_detail:
            if pages_fetched >= config.max_detail_pages:
                logger.info("Limite de %d páginas de detalhe atingido.", config.max_detail_pages)
                break

            is_new = bc.stable_id not in history
            existing = history.get(bc.stable_id)

            # Busca detalhe
            detail_text = ""
            try:
                detail_text = scraper.fetch_detail(bc.url) or ""
                pages_fetched += 1
            except Exception as exc:
                logger.warning("Erro ao buscar detalhe de '%s': %s. Continuando.", bc.name, exc)

            # Classifica
            # O prazo do catálogo entra na classificação: bootcamp com inscrição
            # encerrada vira EXPIRADA e não gera notificação.
            result = classify(detail_text or bc.summary, deadline=bc.launch_info)

            now = _now_iso()

            if is_new:
                # Novo bootcamp
                record = BootcampRecord(
                    stable_id=bc.stable_id,
                    name=bc.name,
                    company=bc.company,
                    url=bc.url,
                    first_seen_at=now,
                    last_checked_at=now,
                    classification=result.classification,
                    score=result.score,
                    evidences=result.evidences,
                    notification_status="pending",
                    catalog_position=bc.catalog_position,
                    catalog_summary=bc.summary,
                    status=bc.status,
                    launch_info=bc.launch_info,
                    observation=result.observation,
                    relevant_excerpt_hash=result.relevant_excerpt_hash,
                    enrollment_status=result.enrollment_status,
                    days_left=result.days_left,
                )
                upsert_record(history, record)

                # Contabiliza
                new_count += 1
                _count_classification(result.classification, alta_count, media_count, baixa_count, expirada_count)

                # Notifica (exceto na primeira execução com INITIAL_NOTIFY=false)
                should_notify = not is_first_run or config.initial_notify
                if should_notify and result.classification != CLASSIFICATION_EXPIRADA:
                    _notify_new(notifier, record, _now_display())
                    mark_notification_sent(history, bc.stable_id)
                else:
                    logger.info(
                        "Bootcamp '%s' registrado sem notificação (primeira execução ou expirado).",
                        bc.name,
                    )
                    history[bc.stable_id].notification_status = "skipped"

            else:
                # Bootcamp conhecido — verifica alteração relevante
                record_update = BootcampRecord(
                    stable_id=bc.stable_id,
                    name=bc.name,
                    company=bc.company,
                    url=bc.url,
                    first_seen_at=existing.first_seen_at if existing else now,
                    last_checked_at=now,
                    classification=result.classification,
                    score=result.score,
                    evidences=result.evidences,
                    notification_status=existing.notification_status if existing else "pending",
                    catalog_position=bc.catalog_position,
                    catalog_summary=bc.summary,
                    status=bc.status,
                    launch_info=bc.launch_info,
                    observation=result.observation,
                    relevant_excerpt_hash=result.relevant_excerpt_hash,
                    enrollment_status=result.enrollment_status,
                    days_left=result.days_left,
                )

                _, was_updated = upsert_record(history, record_update)

                if was_updated and existing:
                    if _is_relevant_update(existing, result.relevant_excerpt_hash, result.classification):
                        update_count += 1
                        _notify_update(notifier, history[bc.stable_id], existing.classification)
                        mark_notification_sent(
                            history, bc.stable_id, result.relevant_excerpt_hash
                        )

        # Processa novos sem detalhe (limite atingido ou sem URL)
        for bc in new_bootcamps:
            if bc.stable_id in history:
                continue  # já processado acima

            now = _now_iso()
            result = classify(bc.summary, deadline=bc.launch_info)
            record = BootcampRecord(
                stable_id=bc.stable_id,
                name=bc.name,
                company=bc.company,
                url=bc.url,
                first_seen_at=now,
                last_checked_at=now,
                classification=result.classification,
                score=result.score,
                evidences=result.evidences,
                notification_status="pending",
                catalog_position=bc.catalog_position,
                catalog_summary=bc.summary,
                status=bc.status,
                launch_info=bc.launch_info,
                observation=result.observation,
                relevant_excerpt_hash=result.relevant_excerpt_hash,
                enrollment_status=result.enrollment_status,
                days_left=result.days_left,
            )
            upsert_record(history, record)
            new_count += 1

            should_notify = not is_first_run or config.initial_notify
            if should_notify and result.classification != CLASSIFICATION_EXPIRADA:
                _notify_new(notifier, record, _now_display())
                mark_notification_sent(history, bc.stable_id)
            else:
                history[bc.stable_id].notification_status = "skipped"

        # Reconta classificações para o resumo
        alta_count = media_count = baixa_count = expirada_count = 0
        for sid, rec in history.items():
            if rec.notification_status == "sent":
                c = rec.classification
                if c == CLASSIFICATION_ALTA:
                    alta_count += 1
                elif c == CLASSIFICATION_MEDIA:
                    media_count += 1
                elif c == CLASSIFICATION_BAIXA:
                    baixa_count += 1
                elif c == CLASSIFICATION_EXPIRADA:
                    expirada_count += 1

        # Salva histórico
        save_history(history, storage_path)

        # Resumo diário
        summary = DailySummary(
            new_count=new_count,
            update_count=update_count,
            alta_count=alta_count,
            media_count=media_count,
            baixa_count=baixa_count,
            expirada_count=expirada_count,
        )

        if config.send_daily_summary:
            notifier.send_daily_summary(summary, send_empty=config.send_empty_summary)

        # Resumo dos bootcamps com inscrição aberta
        if _deve_enviar_digest(config):
            abertos = coletar_abertos(history)
            logger.info("Resumo de abertos: %d bootcamps com inscrição em aberto.", len(abertos))
            notifier.send_open_digest(abertos, send_empty=config.send_empty_summary)

        logger.info(
            "=== Monitor encerrado. Novos: %d | Atualizações: %d | Total histórico: %d ===",
            new_count,
            update_count,
            len(history),
        )

    except Exception as exc:
        logger.critical("Erro crítico no monitor: %s", exc, exc_info=True)
        # Tenta salvar o histórico mesmo em caso de erro crítico
        try:
            save_history(history, storage_path)
        except Exception as save_exc:
            logger.error("Falha ao salvar histórico após erro crítico: %s", save_exc)
        raise

    finally:
        scraper.close()
        notifier.close()


# ---------------------------------------------------------------------------
# Helpers de notificação
# ---------------------------------------------------------------------------

def coletar_abertos(history: dict, today=None) -> list[OpenBootcamp]:
    """
    Monta a lista de bootcamps com inscrição aberta a partir do histórico.

    O prazo é reavaliado agora, a partir de `launch_info`, em vez de confiar no
    `enrollment_status` gravado: só uma parte do catálogo é reclassificada por
    execução (MAX_DETAIL_PAGES), então o valor persistido pode estar velho. A
    data, não.

    Args:
        history: histórico completo, indexado por stable_id.
        today: data de referência; padrão é hoje.

    Returns:
        Bootcamps abertos, ordenados do mais urgente ao menos.
    """
    abertos: list[OpenBootcamp] = []

    for record in history.values():
        status, dias = evaluate_enrollment(record.launch_info, today)
        if status != ENROLLMENT_ABERTO or dias is None:
            continue
        abertos.append(
            OpenBootcamp(
                name=record.name,
                url=record.url,
                classification=record.classification,
                deadline=_formata_prazo(record.launch_info),
                days_left=dias,
            )
        )

    abertos.sort(key=lambda b: (b.days_left, b.name))
    return abertos


def _deve_enviar_digest(config: Config, today=None) -> bool:
    """Verifica se o resumo de abertos deve ser enviado nesta execução."""
    if not config.send_open_digest:
        return False
    if not config.open_digest_weekdays:
        return True
    hoje = today or hoje_brasil()
    return hoje.weekday() in config.open_digest_weekdays


def _formata_prazo(raw: str) -> str:
    """Formata o prazo do catálogo para exibição na notificação."""
    prazo = parse_deadline(raw)
    return prazo.strftime("%d/%m/%Y") if prazo else ""


def _notify_new(notifier, record: BootcampRecord, display_time: str) -> None:
    """Envia notificação de novo bootcamp sem propagar erros."""
    try:
        notif = NewBootcampNotification(
            name=record.name,
            company=record.company,
            url=record.url,
            classification=record.classification,
            score=record.score,
            evidences=record.evidences,
            observation=record.observation,
            identified_at=display_time,
            deadline=_formata_prazo(record.launch_info),
            days_left=record.days_left,
        )
        notifier.notify_new_bootcamp(notif)
    except Exception as exc:
        logging.getLogger(__name__).warning("Erro ao notificar novo bootcamp '%s': %s", record.name, exc)


def _notify_update(notifier, record: BootcampRecord, previous_classification: str) -> None:
    """Envia notificação de atualização sem propagar erros."""
    try:
        notif = UpdateNotification(
            name=record.name,
            company=record.company,
            url=record.url,
            previous_classification=previous_classification,
            current_classification=record.classification,
            evidences=record.evidences,
        )
        notifier.notify_update(notif)
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Erro ao notificar atualização de '%s': %s", record.name, exc
        )


def _count_classification(
    classification: str,
    alta: int,
    media: int,
    baixa: int,
    expirada: int,
) -> tuple[int, int, int, int]:
    if classification == CLASSIFICATION_ALTA:
        return alta + 1, media, baixa, expirada
    if classification == CLASSIFICATION_MEDIA:
        return alta, media + 1, baixa, expirada
    if classification == CLASSIFICATION_BAIXA:
        return alta, media, baixa + 1, expirada
    if classification == CLASSIFICATION_EXPIRADA:
        return alta, media, baixa, expirada + 1
    return alta, media, baixa, expirada


def _save_and_exit(history, storage_path, notifier, config, summary) -> None:
    save_history(history, storage_path)
    if config.send_daily_summary:
        notifier.send_daily_summary(summary, send_empty=config.send_empty_summary)
    notifier.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    config = load_config()
    setup_logging(config.log_level)
    run(config)
