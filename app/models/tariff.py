"""Models for tariff configuration, worker profiles, and calculation results."""

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class WorkerStatus(str, Enum):
    FAGARBEIDER = "fagarbeider"
    LÆRLING_KLASSE_1 = "lærling_klasse_1"
    LÆRLING_KLASSE_2 = "lærling_klasse_2"
    LÆRLING_KLASSE_3 = "lærling_klasse_3"
    UKJENT = "ukjent"


class TariffConfig(BaseModel):
    """Active collective agreement rates — fetched from database, never hardcoded."""

    gyldig_fra: date
    gyldig_til: Optional[date] = None
    satser: dict[str, float] = Field(
        description="Nøkkel f.eks. 'pussing_småsekk_kvm' -> NOK per kvm"
    )
    apning_min_kvm: float = Field(
        0.5, description="Minste åpningsareal for fradrag (Fellesoverenskomsten)"
    )
    laerling_faktorer: dict[str, float] = Field(
        default_factory=lambda: {
            "lærling_klasse_1": 0.60,
            "lærling_klasse_2": 0.80,
            "lærling_klasse_3": 0.90,
        }
    )


class WorkerProfile(BaseModel):
    id: str
    navn: str
    status: WorkerStatus = WorkerStatus.UKJENT
    telefon: Optional[str] = None


class ApningBeregning(BaseModel):
    type: str
    antall: int
    enkelt_areal_kvm: float
    totalt_areal_kvm: float
    fradrag_gjelder: bool
    fradrag_kvm: float


class VeggBeregning(BaseModel):
    beskrivelse: Optional[str]
    brutto_kvm: float
    antall_flater: int


class BeregnetResultat(BaseModel):
    prosjekt_navn: Optional[str]
    murer_navn: Optional[str]
    arbeidstype: Optional[str]
    material_format: str
    vegger: list[VeggBeregning]
    apninger: list[ApningBeregning]
    brutto_total_kvm: float
    fradrag_total_kvm: float
    netto_kvm: float
    sats_nok_per_kvm: float
    sats_nokel: str
    grunnlonn_nok: float
    laerling_faktor: float
    laerling_status: str
    total_utbetaling_nok: float
    usikkerhets_flagg: bool
    usikkerhets_grunn: Optional[str]
    beregnet_dato: date
    disclaimer: str
