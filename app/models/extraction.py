"""Pydantic models for Gemini extraction — no calculations here."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MaterialFormat(str, Enum):
    SMÅSEKK = "småsekk"
    STORSEKK = "storsekk"
    PALL = "pall"
    UKJENT = "ukjent"


class ApningType(str, Enum):
    VINDU = "vindu"
    DØR = "dør"
    PORT = "port"
    ANNET = "annet"


class Apning(BaseModel):
    """Opening to be deducted per Fellesoverenskomsten 0,5 kvm-regel."""

    type: ApningType = ApningType.ANNET
    bredde_m: Optional[float] = Field(None, ge=0, description="Bredde i meter")
    hoyde_m: Optional[float] = Field(None, ge=0, description="Høyde i meter")
    antall: int = Field(1, ge=1)
    beskrivelse: Optional[str] = None


class Veggflate(BaseModel):
    """A wall surface segment with gross dimensions."""

    beskrivelse: Optional[str] = None
    lengde_m: Optional[float] = Field(None, ge=0)
    hoyde_m: Optional[float] = Field(None, ge=0)
    antall_flater: int = Field(1, ge=1)


class ArbeidEkstraksjon(BaseModel):
    """
    Structured extraction from audio or handwritten note.
    Gemini MUST NOT perform arithmetic — only extract raw dimensions.
    """

    prosjekt_navn: Optional[str] = None
    murer_navn: Optional[str] = None
    arbeidstype: Optional[str] = Field(
        None, description="F.eks. 'pussing', 'muring', 'reparasjon'"
    )
    vegger: list[Veggflate] = Field(default_factory=list)
    apninger: list[Apning] = Field(default_factory=list)
    material_format: MaterialFormat = MaterialFormat.UKJENT
    notater: Optional[str] = None
    usikkerhets_flagg: bool = False
    usikkerhets_grunn: Optional[str] = Field(
        None, description="Forklaring ved motstridende eller uklare opplysninger"
    )
    transkripsjon_sammendrag: Optional[str] = None
