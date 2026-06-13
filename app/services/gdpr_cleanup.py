"""
GDPR data minimization: purge/anonymize worker media and PII 30 days after payout.
"""

import logging
from datetime import datetime, timezone

from supabase import Client, create_client

from app.config import Settings

logger = logging.getLogger(__name__)

ANONYMIZED_NAME = "[SLETTET]"


class GdprCleanupService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: Client | None = None
        if settings.supabase_url and settings.supabase_service_key:
            self._client = create_client(settings.supabase_url, settings.supabase_service_key)

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    def purge_expired_records(self) -> dict[str, int]:
        """
        Delete worker audio/media and anonymize full names for records
        where gdpr_purge_after has passed and payout was calculated.
        """
        if not self.is_configured:
            logger.info("GDPR-opprydding hoppet over — Supabase ikke konfigurert")
            return {"purged": 0, "anonymized": 0, "media_deleted": 0}

        now = datetime.now(timezone.utc).isoformat()
        purged = 0
        anonymized = 0
        media_deleted = 0

        try:
            result = (
                self._client.table("beregninger")
                .select("id, media_path, murer_navn, gdpr_purged")
                .eq("payout_calculated", True)
                .eq("gdpr_purged", False)
                .lte("gdpr_purge_after", now)
                .execute()
            )

            for row in result.data or []:
                job_id = row["id"]
                media_path = row.get("media_path")

                if media_path:
                    try:
                        self._client.storage.from_("worker_media").remove([media_path])
                        media_deleted += 1
                    except Exception as exc:
                        logger.error("Kunne ikke slette media %s: %s", media_path, exc)

                try:
                    self._client.table("beregninger").update(
                        {
                            "murer_navn": ANONYMIZED_NAME,
                            "media_path": None,
                            "ekstraksjon": None,
                            "gdpr_purged": True,
                            "gdpr_purged_at": now,
                        }
                    ).eq("id", job_id).execute()
                    purged += 1
                    anonymized += 1
                except Exception as exc:
                    logger.error("Kunne ikke anonymisere jobb %s: %s", job_id, exc)

            logger.info(
                "GDPR-opprydding fullført: %d poster, %d mediafiler slettet",
                purged,
                media_deleted,
            )
        except Exception as exc:
            logger.error("GDPR-opprydding feilet: %s", exc)

        return {"purged": purged, "anonymized": anonymized, "media_deleted": media_deleted}
