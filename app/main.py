"""
FastAPI entrypoint — WhatsApp webhook and health endpoints.
Designed for serverless deployment (e.g. Cloud Run, Modal, AWS Lambda via Mangum).
"""

import base64
import logging
import uuid
from typing import Any, Optional

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query, Request, Response
from starlette.datastructures import FormData, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import get_settings
from app.pdf_generator import generer_beregning_pdf, generer_tekst_sammendrag
from app.services.gdpr_cleanup import GdprCleanupService
from app.services.gemini_extractor import GeminiExtractor
from app.services.supabase_client import SupabaseService
from app.tariff_calculator import beregn_piece_rate

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Murer Backend",
    description="Automatisert stykkeprisberegning for murerfirma",
    version="1.0.0",
)


def _detect_media_type(mime_type: str) -> tuple[str, str]:
    """Return (category, normalized mime) where category is 'audio' or 'image'."""
    mime = (mime_type or "").lower()
    if mime.startswith("audio/") or mime in ("application/ogg", "video/ogg"):
        return "audio", mime if "/" in mime else "audio/ogg"
    if mime.startswith("image/"):
        return "image", mime
    if mime.endswith(".ogg") or "ogg" in mime:
        return "audio", "audio/ogg"
    return "image", mime or "image/jpeg"


async def _json_webhook_response(
    body: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Route a FastAPI Body()-parsed JSON webhook without re-reading the stream."""
    print("\n[SPION-LOGG] --- NY JSON-FORESPØRSEL MOTTATT I WEBHOOK ---")
    print(f"[SPION-LOGG] Nøkler funnet i JSON-pakken din: {list(body.keys())}")

    if "entry" in body:
        print("[SPION-LOGG] SPRENGSTOFF-FUNN: Fant 'entry' i JSON! Dette sender appen inn i Meta-sporet (ingen lagring).")
        return await _handle_meta_webhook_payload(body, background_tasks)

    if "media_base64" in body:
        print("[SPION-LOGG] SUKSESS: Fant 'media_base64'. Starter den ekte lagrings-pipelinen!")
        media_bytes, mime_type, phone = _extract_media_from_json(body)
        print(f"[SPION-LOGG] Ekstraherte test-data -> Mime: {mime_type}, Telefon: {phone}")
        result = await _process_media_pipeline(media_bytes, mime_type, phone)
        return JSONResponse(content=result, status_code=200)

    print("[SPION-LOGG] ADVARSEL: JSON-pakken inneholdt hverken 'entry' eller 'media_base64'!")
    return JSONResponse(
        content={
            "status": "mottatt",
            "melding": "Webhook mottatt uten mediedata.",
            "disclaimer": settings.disclaimer,
        },
        status_code=200,
    )


def _sender_phone_from_form(form: FormData) -> Optional[str]:
    value = form.get("sender_phone")
    if value is None or isinstance(value, UploadFile):
        return None
    return str(value)


async def _extract_media_from_multipart(
    form: FormData,
) -> tuple[bytes, str, Optional[str]]:
    """Parse media bytes from an already-consumed multipart form."""
    sender_phone = _sender_phone_from_form(form)

    for key in ("media", "file", "audio", "image", "document"):
        media_file = form.get(key)
        if media_file and hasattr(media_file, "read"):
            data = await media_file.read()
            mime = getattr(media_file, "content_type", None) or "application/octet-stream"
            return data, mime, sender_phone

    raise ValueError("Ingen mediefil funnet i forespørselen")


def _extract_media_from_json(
    body: dict[str, Any],
) -> tuple[bytes, str, Optional[str]]:
    """Parse media bytes from an already-parsed JSON object."""
    if "media_base64" not in body:
        raise ValueError("Ugyldig forespørselsformat — forventet media_base64 i JSON")

    mime = body.get("mime_type", "audio/ogg")
    phone = body.get("sender_phone")
    return base64.b64decode(body["media_base64"]), mime, str(phone) if phone else None


async def _process_media_pipeline(
    media_bytes: bytes,
    mime_type: str,
    sender_phone: Optional[str] = None,
) -> dict[str, Any]:
    """Full pipeline: Gemini extract → tariff calc → PDF."""
    print("\n[SPION-LOGG] === STARTER HOVED-PIPELINE FOR MURER-LAGRING ===")
    job_id = str(uuid.uuid4())
    media_category, normalized_mime = _detect_media_type(mime_type)
    print(f"[SPION-LOGG] Generert Jobb-ID: {job_id}")

    extractor = GeminiExtractor(settings)
    supabase = SupabaseService(settings)

    print("[SPION-LOGG] Kaller på Gemini AI for å tolke murer-tekst/lyd...")
    ekstraksjon = extractor.extract(media_bytes, media_category, normalized_mime)
    print(f"[SPION-LOGG] Gemini fullført! Fant murer_navn i teksten: '{ekstraksjon.murer_navn}'")

    print(f"[SPION-LOGG] Starter sjekk mot 'workers'-tabellen i Supabase for nummer: '{sender_phone}'")
    if sender_phone:
        worker = supabase.hent_worker_etter_telefon(sender_phone)
    elif ekstraksjon.murer_navn:
        print("[SPION-LOGG] Fant ikke telefonnummer, prøver å søke på navn funnet av Gemini...")
        worker = supabase.hent_worker_etter_navn(ekstraksjon.murer_navn)
    else:
        worker = supabase.hent_worker_etter_telefon("")

    print(f"[SPION-LOGG] Worker-søk ferdig! Fant worker i database: ID={worker.id}, Navn='{worker.navn}'")

    if ekstraksjon.murer_navn and worker.navn == "Ukjent":
        print("[SPION-LOGG] Worker var ukjent, oppdaterer midlertidig med navnet fra Gemini.")
        worker = worker.model_copy(update={"navn": ekstraksjon.murer_navn})

    print("[SPION-LOGG] Henter gjeldende tariff_config fra Supabase...")
    tariff = supabase.hent_aktiv_tariff()

    print("[SPION-LOGG] Regner ut lønnssatser basert på tariff...")
    resultat = beregn_piece_rate(
        ekstraksjon=ekstraksjon,
        worker=worker,
        tariff=tariff,
        disclaimer=settings.disclaimer,
    )

    print("[SPION-LOGG] Laster opp mediefil til Supabase Storage...")
    media_path = supabase.lagre_media(job_id, media_bytes, normalized_mime, worker.id)
    print(f"[SPION-LOGG] Fil lagret suksessfullt på sti: {media_path}")

    print("[SPION-LOGG] 🔥 DEKKLER RAD: Prøver å skrive til 'beregninger'-tabellen nå...")
    supabase.lagre_beregning(job_id, ekstraksjon, resultat, media_path, worker.id)
    print("[SPION-LOGG] 🎉 TABELL-LAGRING FULLFØRT UTEN KRASJ!")

    pdf_bytes = generer_beregning_pdf(resultat)
    tekst = generer_tekst_sammendrag(resultat)

    return {
        "job_id": job_id,
        "status": "ok",
        "usikkerhets_flagg": resultat.usikkerhets_flagg,
        "sammendrag": tekst,
        "resultat": resultat.model_dump(mode="json"),
        "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        "disclaimer": settings.disclaimer,
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/webhook/whatsapp")
async def whatsapp_verify(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> Response:
    """Meta WhatsApp webhook verification handshake."""
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="Verifisering feilet")


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: Optional[dict[str, Any]] = Body(
        default=None,
        description=(
            "JSON-body for Swagger-testing og Meta WhatsApp webhook. "
            "Bruk media_base64 + mime_type for lyd/bilde, eller entry for Meta-hendelser."
        ),
        examples={
            "media_base64": {
                "summary": "Lyd/bilde som base64",
                "value": {
                    "media_base64": "T2dnUwAC",
                    "mime_type": "audio/ogg",
                    "sender_phone": "4796040796",
                },
            },
        },
    ),
) -> JSONResponse:
    try:
        background_tasks.add_task(GdprCleanupService(settings).purge_expired_records)

        if payload is not None:
            return await _json_webhook_response(payload, background_tasks)

        content_type = request.headers.get("content-type", "").lower()

        if "multipart/form-data" in content_type:
            print("\n[SPION-LOGG] Mottok data som multipart/form-data form.")
            form = await request.form()
            media_bytes, mime_type, phone = await _extract_media_from_multipart(form)
            result = await _process_media_pipeline(media_bytes, mime_type, phone)
            return JSONResponse(content=result, status_code=200)

        return JSONResponse(
            content={
                "status": "mottatt",
                "melding": "Webhook mottatt uten mediedata.",
                "disclaimer": settings.disclaimer,
            },
            status_code=200,
        )

    except ValueError as exc:
        logger.warning("Valideringsfeil i webhook: %s", exc)
        return JSONResponse(
            content={"status": "feil", "melding": "Kunne ikke lese innkommende data."},
            status_code=422,
        )
    except KeyError as exc:
        logger.error("Manglende tariff-konfigurasjon: %s", exc)
        return JSONResponse(
            content={"status": "feil", "melding": "Tariff ikke konfigurert."},
            status_code=503,
        )
    except Exception as exc:
        logger.exception("Uventet feil i webhook: %s", exc)
        return JSONResponse(
            content={"status": "feil", "melding": "En intern feil oppstod."},
            status_code=500,
        )


async def _handle_meta_webhook_payload(
    body: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    print("\n[SPION-LOGG] !!! STOPP !!! Koden kjører nå '_handle_meta_webhook_payload'.")
    print("[SPION-LOGG] Siden dette er en simulert Meta-hendelse, lagres det ingenting i tabellene dine.")
    logger.info("Meta webhook event mottatt")
    background_tasks.add_task(GdprCleanupService(settings).purge_expired_records)

    return JSONResponse(
        content={
            "status": "mottatt",
            "melding": "Meta-hendelse registrert. Ingen lagring utført under denne ruten.",
            "disclaimer": settings.disclaimer,
        },
        status_code=200,
    )


@app.post("/admin/gdpr-cleanup")
async def trigger_gdpr_cleanup() -> dict[str, Any]:
    """Manual trigger for GDPR purge routine."""
    result = GdprCleanupService(settings).purge_expired_records()
    return {"status": "ok", **result, "disclaimer": settings.disclaimer}