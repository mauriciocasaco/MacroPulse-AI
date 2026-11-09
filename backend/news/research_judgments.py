"""Reviewed references for the historical benchmark and local analysis endpoint."""

from dataclasses import dataclass
from datetime import date
from typing import Literal


# Each URL suffix identifies an independently read reference. The final field states
# the mechanism and what the cited source can support.
QUESTIONS = {
    "covid": {
        "GC=F": ("¿Qué evidencia vincula la pandemia con el precio de los futuros del oro?", (), "Posibles canales de refugio y tasas reales. Sin evidencia de futuros del oro."),
        "CL=F": ("¿Por qué la caída del consumo y los límites de almacenamiento llevaron al WTI a cotizar en negativo?", ("43495", "43875"), "Directa. Demanda, almacenamiento y vencimiento del contrato. Atribución de EIA, sin estimación causal propia."),
        "^GSPC": ("¿Cómo se relacionaron las tensiones de marzo de 2020 con las caídas en la bolsa estadounidense?", ("2020-may-financial-stability-report-asset-valuation.htm",), "Contextual. Caídas de acciones y deterioro de perspectivas, sin aislar causalidad ni cuantificar el S&P 500."),
        "VT": ("¿Cómo afectaron los cierres por COVID-19 a las perspectivas de crecimiento y financiación global?", ("2020-may-financial-stability-report-overview.htm", "2020-may-financial-stability-report-asset-valuation.htm"), "Contextual. La contracción y las condiciones financieras pueden afectar las acciones globales. Sin retorno de VT."),
        "VWO": ("¿Qué canales de contagio financiero hacia economías emergentes identificó la Reserva Federal al comienzo de la pandemia?", ("2020-may-financial-stability-report-near-term-risks.htm",), "Contextual. Desaceleración y vulnerabilidad financiera de economías emergentes. Sin retorno de VWO."),
        "BTC-USD": ("¿Qué evidencia contemporánea relaciona las liquidaciones de Bitcoin con el shock de COVID-19?", (), "Sin texto autorizado que documente liquidaciones o retornos de BTC."),
    },
    "ukraine": {
        "GC=F": ("¿Hay evidencia de que la invasión provocó compras de refugio en futuros del oro?", (), "Hipótesis de refugio. La volatilidad de metales por sí sola no permite atribuir el movimiento del oro."),
        "CL=F": ("¿Cómo reaccionaron los futuros WTI a la invasión rusa y al riesgo de interrupción del suministro?", ("51498", "2022-may-financial-stability-report-funding.htm"), "Directa. Movimiento de futuros y riesgo de oferta tras la invasión. El texto no separa todos los shocks simultáneos."),
        "^GSPC": ("¿Cómo pudieron el encarecimiento de materias primas y las tasas afectar las valoraciones de acciones estadounidenses?", ("2022-may-financial-stability-report-near-term-risks.htm",), "Contextual. Inflación y tasas elevan el riesgo para las valoraciones. Sin atribución aislada al S&P 500."),
        "VT": ("¿Cómo transmitió la invasión el shock de materias primas al comercio y a la inflación mundial?", ("2022-may-financial-stability-report-near-term-risks.htm",), "Contextual. Comercio, inflación y crecimiento global. Sin retorno de VT."),
        "VWO": ("¿Qué riesgo para las economías emergentes plantearon el shock de energía y el endurecimiento financiero mundial?", ("2022-may-financial-stability-report-near-term-risks.htm",), "Contextual. Materias primas y financiación inciden de forma heterogénea. Sin retorno de VWO."),
        "BTC-USD": ("¿Qué evidencia relaciona el comienzo de la invasión con el precio de Bitcoin?", (), "La mención incidental de criptoactivos en informes de riesgo no explica el retorno de Bitcoin."),
    },
    "genai": {
        "GC=F": ("¿Hay evidencia de un efecto de la IA generativa sobre los futuros del oro?", (), "Canal indirecto vía productividad y tasas reales sin evidencia de transmisión al oro."),
        "CL=F": ("¿Los centros de datos para IA generativa alteraron la demanda de petróleo o el precio de los futuros WTI?", (), "La demanda eléctrica de centros de datos no identifica demanda de crudo ni un efecto sobre WTI."),
        "^GSPC": ("¿Qué canal de productividad y beneficios podría conectar la adopción de IA generativa con valoraciones bursátiles?", ("cook20241001a.htm", "cook20230922a.htm"), "Contextual. Productividad y adopción empresarial como hipótesis, sin efecto observado sobre el S&P 500."),
        "VT": ("¿Qué impacto potencial sobre el crecimiento global planteó la adopción de IA generativa?", ("bowman20241122a.htm",), "Contextual. Adopción y productividad en la economía global. Sin atribución a VT."),
        "VWO": ("¿Hay evidencia de beneficios de IA generativa para acciones de mercados emergentes?", (), "Sin evidencia sobre valoraciones de emergentes o retornos de VWO."),
        "BTC-USD": ("¿Hay evidencia de que el auge de IA generativa movió el precio de Bitcoin?", (), "El consumo eléctrico conjunto de centros de datos y minería es contexto sectorial, sin vínculo probado con el precio."),
    },
}
# Read and assessed as irrelevant to the corresponding event-to-asset mechanism.
CONTROLS = {
    "covid": ("44237", "clarida20200225a.htm", "clarida20200221a.htm"),
    "ukraine": ("52940", "waller20220506a.htm"),
    "genai": ("64129", "kugler20241203a.htm", "63344"),
}
DIRECT = {("covid", "CL=F"), ("ukraine", "CL=F")}


@dataclass(frozen=True)
class ReviewedSource:
    source_id: str
    url: str
    published_on: date
    relevance: Literal["direct", "contextual"]
    mechanism: str


def _eia(number: str, day: str, mechanism: str) -> ReviewedSource:
    return ReviewedSource("eia", f"https://www.eia.gov/todayinenergy/detail.php?id={number}",
                          date.fromisoformat(day), "direct", mechanism)


def _fed(path: str, day: str, mechanism: str) -> ReviewedSource:
    return ReviewedSource("fed", f"https://www.federalreserve.gov/{path}",
                          date.fromisoformat(day), "contextual", mechanism)


# Exact source identities and report dates prevent suffix lookalikes and changed
# metadata from inheriting a judgment. Mechanisms are paraphrases of reviewed prose.
_REVIEWED = {
    "43495": _eia("43495", "2020-04-27", "EIA links the pandemic's petroleum demand shock to rising inventories. Scarce storage and low liquidity near physical settlement pushed the expiring WTI contract below zero."),
    "43875": _eia("43875", "2020-05-27", "EIA identifies scarce uncommitted storage in Cushing as a contributor to the April WTI decline and explains why other crude benchmarks could react differently."),
    "2020-may-financial-stability-report-asset-valuation.htm": _fed(
        "publications/2020-may-financial-stability-report-asset-valuation.htm", "2020-05-15",
        "The Federal Reserve links weaker global growth expectations and reduced risk appetite to lower risky asset prices during the onset of the pandemic. This describes a broad market channel."),
    "2020-may-financial-stability-report-overview.htm": _fed(
        "publications/2020-may-financial-stability-report-overview.htm", "2020-05-15",
        "The Federal Reserve describes pandemic containment measures closing sectors and sharply reducing economic activity. Lower activity is a channel through which the shock can affect equity cash-flow expectations."),
    "2020-may-financial-stability-report-near-term-risks.htm": _fed(
        "publications/2020-may-financial-stability-report-near-term-risks.htm", "2020-05-15",
        "The Federal Reserve identifies weak domestic and external demand as a risk for indebted firms in China and describes additional pressure on oil exporters from weak oil demand."),
    "51498": _eia("51498", "2022-03-04", "EIA reports that front-month WTI rose above $100 after the invasion and identifies risks to Russian oil supplies alongside recovering petroleum demand and slower production growth."),
    "2022-may-financial-stability-report-funding.htm": _fed(
        "publications/2022-may-financial-stability-report-funding.htm", "2022-05-09",
        "The Federal Reserve describes commodity-market volatility after the invasion raising margin calls and putting pressure on clearing participants. This is financing context for commodity markets."),
    "2022-may-financial-stability-report-near-term-risks.htm": _fed(
        "publications/2022-may-financial-stability-report-near-term-risks.htm", "2022-05-09",
        "The Federal Reserve identifies rising commodity prices and tighter financial conditions as channels that can worsen inflation and weaken growth after the invasion. Exposure varies by economy and market."),
    "cook20241001a.htm": _fed("newsevents/speech/cook20241001a.htm", "2024-10-01",
        "Cook discusses AI's potential to raise productivity as firms adopt it. Productivity could influence expected earnings, while the speech leaves the effect on equity valuations unmeasured."),
    "cook20230922a.htm": _fed("newsevents/speech/cook20230922a.htm", "2023-09-22",
        "Cook discusses how generative AI could change task productivity and the labor market. The implications for equity earnings depend on adoption and how businesses use the technology."),
    "bowman20241122a.htm": _fed("newsevents/speech/bowman20241122a.htm", "2024-11-22",
        "Bowman presents wider AI adoption and labor productivity as a possible global growth channel, with substantial uncertainty about the scale of benefits."),
}


def reviewed_sources(event: str, symbol: str) -> tuple[ReviewedSource, ...]:
    _, references, _ = QUESTIONS[event][symbol]
    return tuple(_REVIEWED[reference] for reference in references)

