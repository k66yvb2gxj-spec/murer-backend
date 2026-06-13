"""
Supabase data access: workers, tariffs, job persistence, media storage.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from supabase import Client, create_client

from app.config import Settings
from app.models.extraction import ArbeidEkstraksjon
from app.models.tariff import BeregnetResultat, TariffConfig, WorkerProfile, WorkerStatus

logger = logging.getLogger(__name__)


def _parse_worker_status(raw: Optional[str]) -> WorkerStatus:
    if not raw:
        return WorkerStatus.UKJENT
    mapping = {
        "fagarbeider": WorkerStatus.FAGARBEIDER,
        "lærling_klasse_1": WorkerStatus.LÆRLING_KLASSE_1,
        "laerling_klasse_1": WorkerStatus.LÆRLING_KLASSE_1,
        "lærling_klasse_2": WorkerStatus.LÆRLING_KLASSE_2,
        "laerling_klasse_2": WorkerStatus.LÆRLING_KLASSE_2,
        "lærling_klasse_3": WorkerStatus.LÆRLING_KLASSE_3,
        "laerling_klasse_3": WorkerStatus.LÆRLING_KLASSE_3,
    }
    return mapping.get(raw.lower(), WorkerStatus.UKJENT)


class SupabaseService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: Optional[Client] = None
        if settings.supabase_url and settings.supabase_service_key:
            self._client = create_client(settings.supabase_url, settings.supabase_service_key)

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    def _table(self, name: str):
        if not self._client:
            raise RuntimeError("Supabase er ikke konfigurert")
        return self._client.table(name)

    def hent_worker_etter_telefon(self, telefon: str) -> WorkerProfile:
        """Look up worker by phone number for apprentice factor."""
        if not self.is_configured:
            logger.warning("Supabase ikke konfigurert — bruker ukjent worker")
            return WorkerProfile(id="ukjent", navn="Ukjent", status=WorkerStatus.UKJENT, telefon=telefon)

        try:
            result = (
                self._table("workers")
                .select("id, navn, status, telefon")
                .eq("telefon", telefon)
                .limit(1)
                .execute()
            )
            if not result.data:
                return WorkerProfile(id="ukjent", navn="Ukjent", status=WorkerStatus.UKJENT, telefon=telefon)

            row = result.data[0]
            return WorkerProfile(
                id=row["id"],
                navn=row.get("navn", "Ukjent"),
                status=_parse_worker_status(row.get("status")),
                telefon=row.get("telefon"),
            )
        except Exception as exc:
            logger.error("Feil ved oppslag av worker: %s", exc)
            return WorkerProfile(id="ukjent", navn="Ukjent", status=WorkerStatus.UKJENT, telefon=telefon)

    def hent_worker_etter_navn(self, navn: str) -> WorkerProfile:
        if not self.is_configured or not navn:
            return WorkerProfile(id="ukjent", navn=navn or "Ukjent", status=WorkerStatus.UKJENT)

        try:
            result = (
                self._table("workers")
                .select("id, navn, status, telefon")
                .ilike("navn", f"%{navn}%")
                .limit(1)
                .execute()
            )
            if not result.data:
                return WorkerProfile(id="ukjent", navn=navn, status=WorkerStatus.UKJENT)

            row = result.data[0]
            return WorkerProfile(
                id=row["id"],
                navn=row.get("navn", navn),
                status=_parse_worker_status(row.get("status")),
                telefon=row.get("telefon"),
            )
        except Exception as exc:
            logger.error("Feil ved oppslag av worker etter navn: %s", exc)
            return WorkerProfile(id="ukjent", navn=navn, status=WorkerStatus.UKJENT)

    def hent_aktiv_tariff(self, pa_dato: Optional[date] = None) -> TariffConfig:
        """Fetch active tariff rates from database."""
        dato = pa_dato or date.today()

        if not self.is_configured:
            logger.warning("Supabase ikke konfigurert — bruker tom tariff (må fylles via DB)")
            return TariffConfig(gyldig_fra=dato, satser={})

        try:
            result = (
                self._table("tariff_config")
                .select("*")
                .lte("gyldig_fra", dato.isoformat())
                .order("gyldig_fra", desc=True)
                .limit(1)
                .execute()
            )
            if not result.data:
                raise ValueError("Ingen aktiv tariff funnet i databasen")

            row = result.data[0]
            return TariffConfig(
                gyldig_fra=date.fromisoformat(row["gyldig_fra"]),
                gyldig_til=date.fromisoformat(row["gyldig_til"]) if row.get("gyldig_til") else None,
                satser=row.get("satser", {}),
                apning_min_kvm=float(row.get("apning_min_kvm", 0.5)),
                laerling_faktorer=row.get("laerling_faktorer")
                or {
                    "lærling_klasse_1": 0.60,
                    "lærling_klasse_2": 0.80,
                    "lærling_klasse_3": 0.90,
                },
            )
        except Exception as exc:
            logger.error("Feil ved henting av tariff: %s", exc)
            raise

    def lagre_media(
        self,
        job_id: str,
        media_bytes: bytes,
        mime_type: str,
        worker_id: str,
    ) -> str:
        """Store raw media in Supabase storage; returns storage path."""
        if not self.is_configured:
            return ""

        ext = mime_type.split("/")[-1].replace("ogg", "ogg")
        path = f"innboks/{worker_id}/{job_id}.{ext}"

        try:
            self._client.storage.from_("worker_media").upload(
                path,
                media_bytes,
                {"content-type": mime_type, "upsert": "true"},
            )
            return path
        except Exception as exc:
            logger.error("Feil ved lagring av media: %s", exc)
            return ""

    def lagre_beregning(
        self,
        job_id: str,
        ekstraksjon: ArbeidEkstraksjon,
        resultat: BeregnetResultat,
        media_path: str,
        worker_id: str,
    ) -> None:
        if not self.is_configured:
            return

        record: dict[str, Any] = {
            "id": job_id,
            "worker_id": worker_id,
            "prosjekt_navn": resultat.prosjekt_navn,
            "murer_navn": resultat.murer_navn,
            "ekstraksjon": ekstraksjon.model_dump(mode="json"),
            "resultat": resultat.model_dump(mode="json"),
            "media_path": media_path,
            "usikkerhets_flagg": resultat.usikkerhets_flagg,
            "total_utbetaling_nok": resultat.total_utbetaling_nok,
            "beregnet_at": datetime.now(timezone.utc).isoformat(),
            "payout_calculated": True,
            "gdpr_purge_after": (
                datetime.now(timezone.utc) + timedelta(days=self._settings.gdpr_retention_days)
            ).isoformat(),
        }

        try:
            self._table("beregninger").upsert(record).execute()
        except Exception as exc:
            logger.error("Feil ved lagring av beregning: %s", exc)
