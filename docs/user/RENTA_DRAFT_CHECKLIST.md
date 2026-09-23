# Reviewing the Renta WEB draft (Modelo 100)

AEAT pre-fills a Modelo 100 (IRPF annual return) draft in Renta WEB from
withholding certificates, bank and broker reports, and other information it
already holds. For a self-employed person in estimacion directa simplificada,
that draft usually omits business-activity income and expenses entirely, and
it never applies decisions that depend on personal circumstances. This
checklist lists what to verify in the draft before confirming it, and states
plainly what this application does and does not do for that review.

## What this application covers

Run `autonomo-tax calculate --form 100 --year <YEAR> --db <db>` against the
posted rows for the year. It produces business-activity support figures only:
activity income, deductible expenses before the difficult-to-justify
allowance, that allowance, and the resulting net business result, tagged with
status `decision_support_requires_renta_web`. The calculation's own warning
says so directly: "This output is decision support and must be compared with
Renta WEB." If any transaction is flagged with an unsupported category for
this calculation, it refuses to produce a figure at all rather than guess.

## What this application does not cover

It does not compute personal IRPF, the general and savings tax bases, the
progressive rate scale, personal and family minimums, joint-taxation choices,
regional (autonomica) deductions, or the final amount to pay or receive.
Those depend on facts the application does not hold (family situation,
disability, mortgage or pension contributions, other income sources, region
of residence) and must be reviewed directly in Renta WEB or with an adviser.

## Checklist

1. **Business-activity figures.** Compare the activity income, expenses,
   difficult-to-justify allowance and net business result shown in the Renta
   WEB draft (or that you enter into it) against this application's Modelo
   100 support output for the same year. The allowance is 5% of the positive
   net result before it, capped at 2,000 EUR per year (7% for fiscal year
   2023 only). AEAT's pre-filled draft frequently omits business income and
   expenses, or the wrong amount from a withholding certificate can appear
   pre-filled; the taxpayer is responsible for the figures in the submitted
   return regardless of what AEAT pre-filled. Do not confirm the draft
   without entering or checking the business-activity section.

2. **New-activity 20% reduction (LIRPF art. 32.3).** Applies to net business
   income in the first tax period with a positive result and the following
   one. It is not applied automatically; the taxpayer must claim it. It does
   not apply in a period where more than 50% of activity income comes from a
   person or entity that was also the taxpayer's employer in the prior year,
   and it cannot apply to more than 100,000 EUR of net income per year. See
   [AEAT's manual on the reduction](https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos/irpf-2024/c07-rendimientos-actividades-economicas-estimacion-directa/fase-3-determinacion-rendimiento-neto-total/reduccion-rendimiento-neto-inicio-actividad-economica.html).
   A reduction missed in a filed return is not necessarily lost: it can be
   corrected by requesting a rectification of the self-assessment
   ([AEAT's rectification procedure](https://sede.agenciatributaria.gob.es/Sede/procedimientos/GZ28.shtml)),
   generally within the four-year limitation period (art. 66.c LGT).

3. **Personal and family minimums, joint vs. individual return, regional
   deductions.** This application states no amounts for these. Check the
   applicable year's [AEAT practical manual](https://sede.agenciatributaria.gob.es/Sede/manuales-practicos.html)
   and the [regional deductions guide](https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos/irpf-2025-deducciones-autonomicas/guia-deducciones-autonomicas.html)
   for the region of residence.

4. **Tax residence.** Under LIRPF art. 9, a person is a Spanish tax resident
   for a calendar year once they are present in Spain more than 183 days
   during that year (sporadic absences count unless residence elsewhere is
   proven). The IRPF tax period is the full calendar year (LIRPF art. 12);
   splitting it for a change of residence during the year is not provided
   for, the only exception being death (art. 13). Registering as autonomo or
   moving to Spain partway through the year can therefore still make someone
   a full-year Spanish tax resident once the 183-day threshold is met, and a
   full-year resident must declare worldwide income for that whole calendar
   year, not only income from the registration date.

5. **Foreign assets (Modelo 720/721).** These are separate annual
   informative returns, due 1 January to 31 March of the following year, not
   part of Modelo 100. This application only flags that the obligation may
   exist, using the thresholds AEAT applies: more than 50,000 EUR in a given
   category (accounts, securities, or real estate for Modelo 720; virtual
   currency for Modelo 721), or, once a return has been filed, an increase
   of more than 20,000 EUR in any category since the last one filed. It does
   not prepare or file either form.

6. **Payment.** The resulting amount can be split into two instalments
   without interest: 60% when filing, and 40% later, typically in early
   November. Confirm the exact date for the year on
   [AEAT's Renta campaign calendar](https://sede.agenciatributaria.gob.es/Sede/irpf/campana-renta/calendario-campana-renta.html)
   rather than assuming it repeats from a prior year.

7. **Keep the evidence.** Save the filed return and its justificante. If the
   application's AEAT documents feature is available, record the filing
   there the same way as for other forms — see
   [Saving the receipt](AEAT_DOCUMENTS.md#saving-the-receipt) — so the
   submission is archived alongside the other tax filings.

## Sources

Checked 2026-09-23:

- [AEAT: reduccion del rendimiento neto por inicio de actividad economica](https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos/irpf-2024/c07-rendimientos-actividades-economicas-estimacion-directa/fase-3-determinacion-rendimiento-neto-total/reduccion-rendimiento-neto-inicio-actividad-economica.html)
- [AEAT: rectificacion de autoliquidaciones de Gestion Tributaria](https://sede.agenciatributaria.gob.es/Sede/procedimientos/GZ28.shtml)
- [AEAT: manuales practicos (index)](https://sede.agenciatributaria.gob.es/Sede/manuales-practicos.html)
- [AEAT: guia de las deducciones autonomicas del IRPF 2025](https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos/irpf-2025-deducciones-autonomicas/guia-deducciones-autonomicas.html)
- [BOE: Ley 35/2006 (LIRPF), consolidated text — art. 9, 12, 13](https://www.boe.es/buscar/act.php?id=BOE-A-2006-20764)
- [AEAT: residencia habitual en territorio espanol](https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos/irpf-2024/c02-irpf-cuestiones-generales/sujecion-irpf-aspectos-personales/residencia-habitual-territorio-espanol.html)
- [AEAT: plazos de presentacion Modelo 720](https://sede.agenciatributaria.gob.es/Sede/todas-gestiones/impuestos-tasas/declaraciones-informativas/modelo-720-decla_____sobre-bienes-derechos-extranjero_/plazos-presentacion.html)
- [AEAT: forma de calcular el limite que obliga a declarar (Modelo 720 FAQ)](https://sede.agenciatributaria.gob.es/Sede/todas-gestiones/impuestos-tasas/declaraciones-informativas/modelo-720-decla_____sobre-bienes-derechos-extranjero_/preguntas-frecuentes/forma-calcular-limite-que-obliga-declarar.html)
- [AEAT: plazo de presentacion Modelo 721](https://sede.agenciatributaria.gob.es/Sede/todas-gestiones/impuestos-tasas/declaraciones-informativas/modelo-721-decla-sobre-monedas-extranjero/preguntas-frecuentes-sobre-modelo-721/plazo-presentacion-modelo-721.html)
- [AEAT: fraccionamiento del pago en dos plazos (60%/40%)](https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-ayuda-presentacion/irpf-2025/3-cuestiones-generales/3_3-declaracion-irpf/3_3_4-fraccionamiento-pago-domiciliacion.html)
- [AEAT: calendario de campana de Renta](https://sede.agenciatributaria.gob.es/Sede/irpf/campana-renta/calendario-campana-renta.html)
- [AEAT: gastos de dificil justificacion, estimacion directa simplificada](https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos/folleto-actividades-economicas/3-impuesto-sobre-renta-personas-fisicas/3_5-estimacion-directa-simplificada/3_5_2-calculo-rendimiento-neto.html)

The 7% difficult-to-justify-expenses rate for fiscal year 2023 (Disposicion
adicional quincuagesima sexta LIRPF, introduced by Ley 31/2022) was not
independently re-fetched from BOE for this check; it matches this
application's own `tax_rules.py`, which already cites the same rule, and
was corroborated by secondary sources describing that BOE provision during
this check.
