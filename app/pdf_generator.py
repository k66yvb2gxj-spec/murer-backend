"""
Professional Norwegian PDF reports for masonry piece-rate calculations.
"""

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models.tariff import BeregnetResultat

DISCLAIMER_STYLE = ParagraphStyle(
    "Disclaimer",
    parent=getSampleStyleSheet()["Normal"],
    fontSize=8,
    textColor=colors.HexColor("#555555"),
    spaceBefore=12,
    leading=11,
)


def _fmt_nok(amount: float) -> str:
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",") + " kr"


def _fmt_kvm(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " kvm"


def generer_beregning_pdf(resultat: BeregnetResultat) -> bytes:
    """Generate a finished PDF document with FDV parameters and calculation tables."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleNO",
        parent=styles["Heading1"],
        fontSize=16,
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "SubtitleNO",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#333333"),
        spaceAfter=12,
    )

    story: list = []

    story.append(Paragraph("Akonto / Stykkeprisberegning — Murerarbeid", title_style))
    story.append(
        Paragraph(
            f"Generert: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            subtitle_style,
        )
    )

    # Prosjektinfo
    prosjekt_data = [
        ["Felt", "Verdi"],
        ["Prosjekt", resultat.prosjekt_navn or "—"],
        ["Murer", resultat.murer_navn or "—"],
        ["Arbeidstype", resultat.arbeidstype or "—"],
        ["Materialformat", resultat.material_format],
        ["Beregningsdato", resultat.beregnet_dato.strftime("%d.%m.%Y")],
        ["Lærlingstatus", resultat.laerling_status],
    ]
    if resultat.usikkerhets_flagg:
        prosjekt_data.append(
            ["⚠ Usikkerhet", resultat.usikkerhets_grunn or "Uklare opplysninger i grunnlaget"]
        )

    prosjekt_table = Table(prosjekt_data, colWidths=[5 * cm, 11 * cm])
    prosjekt_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(Paragraph("Prosjektinformasjon", styles["Heading2"]))
    story.append(prosjekt_table)
    story.append(Spacer(1, 0.5 * cm))

    # Veggflater
    vegg_header = ["Beskrivelse", "Antall flater", "Brutto kvm"]
    vegg_rows = [vegg_header]
    for v in resultat.vegger:
        vegg_rows.append(
            [v.beskrivelse or "—", str(v.antall_flater), _fmt_kvm(v.brutto_kvm)]
        )
    vegg_rows.append(["Sum brutto", "", _fmt_kvm(resultat.brutto_total_kvm)])

    vegg_table = Table(vegg_rows, colWidths=[7 * cm, 3 * cm, 6 * cm])
    vegg_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f8f9fa")]),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(Paragraph("Veggflater (FDV)", styles["Heading2"]))
    story.append(vegg_table)
    story.append(Spacer(1, 0.5 * cm))

    # Åpninger med 0,5 kvm-regel
    apning_header = [
        "Type",
        "Ant.",
        "Enkelt kvm",
        "Totalt kvm",
        "Fradrag",
        "Fradrag kvm",
    ]
    apning_rows = [apning_header]
    for a in resultat.apninger:
        apning_rows.append(
            [
                a.type,
                str(a.antall),
                _fmt_kvm(a.enkelt_areal_kvm),
                _fmt_kvm(a.totalt_areal_kvm),
                "Ja" if a.fradrag_gjelder else "Nei (< 0,5 kvm)",
                _fmt_kvm(a.fradrag_kvm),
            ]
        )
    apning_rows.append(["Sum fradrag", "", "", "", "", _fmt_kvm(resultat.fradrag_total_kvm)])

    apning_table = Table(apning_rows, colWidths=[2.5 * cm, 1.5 * cm, 2.5 * cm, 2.5 * cm, 3 * cm, 2.5 * cm])
    apning_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f8f9fa")]),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(Paragraph("Åpninger — fradrag etter Fellesoverenskomsten (≥ 0,5 kvm)", styles["Heading2"]))
    story.append(apning_table)
    story.append(Spacer(1, 0.5 * cm))

    # Oppsummering
    oppsummering = [
        ["Post", "Beløp / verdi"],
        ["Brutto areal", _fmt_kvm(resultat.brutto_total_kvm)],
        ["Fradrag åpninger", _fmt_kvm(resultat.fradrag_total_kvm)],
        ["Netto areal", _fmt_kvm(resultat.netto_kvm)],
        ["Sats", f"{_fmt_nok(resultat.sats_nok_per_kvm)} / kvm ({resultat.sats_nokel})"],
        ["Grunnlønn (før lærlingsfaktor)", _fmt_nok(resultat.grunnlonn_nok)],
        ["Lærlingsfaktor", f"{resultat.laerling_faktor:.0%}"],
        ["Total utbetaling", _fmt_nok(resultat.total_utbetaling_nok)],
    ]

    opps_table = Table(oppsummering, colWidths=[8 * cm, 8 * cm])
    opps_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#eaf2f8")),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(Paragraph("Beregnet oppsummering", styles["Heading2"]))
    story.append(opps_table)
    story.append(Spacer(1, 0.8 * cm))

    story.append(Paragraph(resultat.disclaimer, DISCLAIMER_STYLE))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()


def generer_tekst_sammendrag(resultat: BeregnetResultat) -> str:
    """Plain-text summary for API responses — always ends with disclaimer."""
    linjer = [
        "STYKKEPRISBEREGNING",
        f"Prosjekt: {resultat.prosjekt_navn or '—'}",
        f"Murer: {resultat.murer_navn or '—'}",
        f"Netto areal: {_fmt_kvm(resultat.netto_kvm)}",
        f"Total utbetaling: {_fmt_nok(resultat.total_utbetaling_nok)}",
    ]
    if resultat.usikkerhets_flagg:
        linjer.append(
            f"MERK: Usikkerhetsflagg — {resultat.usikkerhets_grunn or 'grunnlaget bør kontrolleres manuelt'}"
        )
    linjer.append("")
    linjer.append(resultat.disclaimer)
    return "\n".join(linjer)
