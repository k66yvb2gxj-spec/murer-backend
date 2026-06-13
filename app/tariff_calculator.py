"""
Deterministic piece-rate calculator for Norwegian masonry (Fellesoverenskomsten).

All arithmetic lives here — the LLM never computes areas or payouts.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from app.models.extraction import ArbeidEkstraksjon, MaterialFormat
from app.models.tariff import (
    ApningBeregning,
    BeregnetResultat,
    TariffConfig,
    VeggBeregning,
    WorkerProfile,
    WorkerStatus,
)

TWOPLACES = Decimal("0.01")


def _to_decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _round2(value: Decimal) -> float:
    return float(value.quantize(TWOPLACES, rounding=ROUND_HALF_UP))


def beregn_apning_areal(bredde_m: Optional[float], hoyde_m: Optional[float]) -> float:
    """Single opening area in sqm. Returns 0 if dimensions missing."""
    if bredde_m is None or hoyde_m is None:
        return 0.0
    if bredde_m <= 0 or hoyde_m <= 0:
        return 0.0
    return _round2(_to_decimal(bredde_m) * _to_decimal(hoyde_m))


def apning_kvalifiserer_for_fradrag(
    enkelt_areal_kvm: float, min_kvm: float
) -> bool:
    """
    Fellesoverenskomsten for Byggfag: openings are deducted only when
    the individual opening area is >= 0.5 sqm (configurable via tariff).
    """
    return enkelt_areal_kvm >= min_kvm


def beregn_apninger(
    ekstraksjon: ArbeidEkstraksjon, min_kvm: float
) -> tuple[list[ApningBeregning], float]:
    """Process all openings and return per-opening breakdown + total deduction."""
    resultater: list[ApningBeregning] = []
    total_fradrag = Decimal("0")

    for apning in ekstraksjon.apninger:
        enkelt = beregn_apning_areal(apning.bredde_m, apning.hoyde_m)
        totalt = _round2(_to_decimal(enkelt) * _to_decimal(apning.antall))
        gjelder = apning_kvalifiserer_for_fradrag(enkelt, min_kvm)
        fradrag = _to_decimal(totalt) if gjelder else Decimal("0")

        resultater.append(
            ApningBeregning(
                type=apning.type.value,
                antall=apning.antall,
                enkelt_areal_kvm=enkelt,
                totalt_areal_kvm=totalt,
                fradrag_gjelder=gjelder,
                fradrag_kvm=_round2(fradrag),
            )
        )
        total_fradrag += fradrag

    return resultater, _round2(total_fradrag)


def beregn_vegger(ekstraksjon: ArbeidEkstraksjon) -> tuple[list[VeggBeregning], float]:
    """Gross wall area before opening deductions."""
    resultater: list[VeggBeregning] = []
    total = Decimal("0")

    for vegg in ekstraksjon.vegger:
        if vegg.lengde_m is None or vegg.hoyde_m is None:
            brutto = Decimal("0")
        else:
            enkelt = _to_decimal(vegg.lengde_m) * _to_decimal(vegg.hoyde_m)
            brutto = enkelt * _to_decimal(vegg.antall_flater)

        brutto_f = _round2(brutto)
        resultater.append(
            VeggBeregning(
                beskrivelse=vegg.beskrivelse,
                brutto_kvm=brutto_f,
                antall_flater=vegg.antall_flater,
            )
        )
        total += _to_decimal(brutto_f)

    return resultater, _round2(total)


def _material_sats_nokel(arbeidstype: Optional[str], material: MaterialFormat) -> str:
    """Map work type + material format to a tariff config key."""
    base = (arbeidstype or "pussing").lower().replace(" ", "_")
    mat = material.value if material != MaterialFormat.UKJENT else "småsekk"
    return f"{base}_{mat}_kvm"


def hent_sats(tariff: TariffConfig, sats_nokel: str) -> float:
    """Fetch rate from active tariff config; raise if missing."""
    if sats_nokel not in tariff.satser:
        fallback = f"pussing_småsekk_kvm"
        if fallback in tariff.satser:
            return tariff.satser[fallback]
        raise KeyError(f"Manglende tariff-sats for nøkkel: {sats_nokel}")
    return tariff.satser[sats_nokel]


def hent_laerling_faktor(worker: WorkerProfile, tariff: TariffConfig) -> float:
    """Apprentice class factor from worker status and tariff config."""
    if worker.status == WorkerStatus.FAGARBEIDER:
        return 1.0
    if worker.status == WorkerStatus.UKJENT:
        return 1.0

    faktor = tariff.laerling_faktorer.get(worker.status.value)
    if faktor is None:
        return 1.0
    return faktor


def beregn_piece_rate(
    ekstraksjon: ArbeidEkstraksjon,
    worker: WorkerProfile,
    tariff: TariffConfig,
    disclaimer: str,
    beregnet_dato: Optional[date] = None,
) -> BeregnetResultat:
    """
    Full deterministic payout calculation.

    netto_kvm = brutto_total - fradrag (only openings >= min_kvm)
    total = netto_kvm * sats * laerling_faktor
    """
    dato = beregnet_dato or date.today()
    min_kvm = tariff.apning_min_kvm

    vegg_resultater, brutto_total = beregn_vegger(ekstraksjon)
    apning_resultater, fradrag_total = beregn_apninger(ekstraksjon, min_kvm)

    netto = _round2(max(Decimal("0"), _to_decimal(brutto_total) - _to_decimal(fradrag_total)))

    sats_nokel = _material_sats_nokel(ekstraksjon.arbeidstype, ekstraksjon.material_format)
    sats = hent_sats(tariff, sats_nokel)
    laerling_faktor = hent_laerling_faktor(worker, tariff)

    grunnlonn = _round2(_to_decimal(netto) * _to_decimal(sats))
    total_utbetaling = _round2(_to_decimal(grunnlonn) * _to_decimal(laerling_faktor))

    return BeregnetResultat(
        prosjekt_navn=ekstraksjon.prosjekt_navn,
        murer_navn=ekstraksjon.murer_navn or worker.navn,
        arbeidstype=ekstraksjon.arbeidstype,
        material_format=ekstraksjon.material_format.value,
        vegger=vegg_resultater,
        apninger=apning_resultater,
        brutto_total_kvm=brutto_total,
        fradrag_total_kvm=fradrag_total,
        netto_kvm=netto,
        sats_nok_per_kvm=sats,
        sats_nokel=sats_nokel,
        grunnlonn_nok=grunnlonn,
        laerling_faktor=laerling_faktor,
        laerling_status=worker.status.value,
        total_utbetaling_nok=total_utbetaling,
        usikkerhets_flagg=ekstraksjon.usikkerhets_flagg,
        usikkerhets_grunn=ekstraksjon.usikkerhets_grunn,
        beregnet_dato=dato,
        disclaimer=disclaimer,
    )
