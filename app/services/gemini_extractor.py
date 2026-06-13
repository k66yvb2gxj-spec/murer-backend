"""
Gemini multi-modal extraction — dimensions only, no arithmetic.
"""

import json
import logging
from typing import Literal

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.config import Settings
from app.models.extraction import ArbeidEkstraksjon

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Du er en norsk transkripsjons- og dataekstraksjonsassistent for et murerfirma.

OPPGAVE: Analyser innkommende lyd (dialekt/tale) eller håndskrevet notat og ekstraher strukturerte data.

KRITISKE REGLER:
1. GJØR ALDRI MATEMATIKK. Ikke regn ut kvm, summer eller multipliser. Ekstraher kun rå dimensjoner (lengde, høyde, bredde, antall).
2. Tål norsk dialekt og byggfag-slang: "to vindu", "ett lite", "ca 40 kvm" betyr at 40 kvm kan være oppgitt direkte som total — noter det i notater, ikke beregn.
3. Sett usikkerhets_flagg=true hvis:
   - Dimensjoner motstrider hverandre
   - Antall åpninger er uklart
   - Prosjektnavn eller murer er uleselig/ukjent
   - Materialformat er tvetydig
4. material_format: velg blant småsekk, storsekk, pall, ukjent.
5. apninger: type vindu/dør/port/annet med bredde_m, hoyde_m, antall.
6. vegger: lengde_m, hoyde_m, antall_flater per veggflate.

Returner KUN gyldig JSON som matcher dette skjemaet:
{
  "prosjekt_navn": string|null,
  "murer_navn": string|null,
  "arbeidstype": string|null,
  "vegger": [{"beskrivelse": string|null, "lengde_m": number|null, "hoyde_m": number|null, "antall_flater": int}],
  "apninger": [{"type": "vindu"|"dør"|"port"|"annet", "bredde_m": number|null, "hoyde_m": number|null, "antall": int, "beskrivelse": string|null}],
  "material_format": "småsekk"|"storsekk"|"pall"|"ukjent",
  "notater": string|null,
  "usikkerhets_flagg": bool,
  "usikkerhets_grunn": string|null,
  "transkripsjon_sammendrag": string|null
}
"""


class GeminiExtractor:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = genai.Client(api_key=settings.gemini_api_key)

    def extract(
        self,
        media_bytes: bytes,
        media_type: Literal["audio", "image"],
        mime_type: str,
    ) -> ArbeidEkstraksjon:
        """Route audio or image to Gemini and parse into ArbeidEkstraksjon."""
        if media_type == "audio":
            user_hint = "Transkriber og ekstraher data fra denne talebeskjeden fra en murer på byggeplass."
        else:
            user_hint = "Les og ekstraher data fra dette håndskrevne notatet fra en murer."

        parts: list[types.Part] = [
            types.Part(text=EXTRACTION_PROMPT),
            types.Part(text=user_hint),
            types.Part.from_bytes(data=media_bytes, mime_type=mime_type),
        ]

        response = self._client.models.generate_content(
            model=self._settings.gemini_model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        raw_text = response.text or "{}"
        logger.debug("Gemini raw response: %s", raw_text[:500])

        try:
            payload = json.loads(raw_text)
            return ArbeidEkstraksjon.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error("Gemini parsing failed: %s", exc)
            return ArbeidEkstraksjon(
                usikkerhets_flagg=True,
                usikkerhets_grunn="Kunne ikke tolke AI-svar — manuell kontroll nødvendig.",
                notater=raw_text[:1000],
            )
