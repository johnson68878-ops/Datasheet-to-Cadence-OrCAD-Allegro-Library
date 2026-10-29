# Evaluation datasheet inventory / 测试规格书清单

The corpus contains 42 wearable-electronics development documents, including ICs, discrete devices, optoelectronic components, a connector and package/module drawings. It is not 42 distinct IC models. Only device identifiers are published; source PDFs and customer-specific filename suffixes are excluded. No undocumented IC identity is guessed.

These samples exercise generic PDF/OCR extraction, dimensions, pin completeness, variants and CAD generation. They are regression/development inputs, not evidence of OCR neural-network retraining. Candidate labels below are parser observations, not an authoritative vendor package catalog.

Latest parser baseline: 2026-10-29; 42 documents, 54 candidates, 13 review-eligible candidates across 10 documents. Eligibility is not native acceptance.

| # | Device / document identity | Observed package candidates | Parser review eligibility |
|---|---|---|---|
| 1 | AST1220 | BGA500, AST1220 | Not eligible |
| 2 | ATBM6463 | QFN60 | Not eligible |
| 3 | BES2710IBP | BGA219 | Not eligible |
| 4 | ISOUSB111 | DWX0016A, DW0016B | Not eligible |
| 5 | CN3085 | SOIC8 | Not eligible |
| 6 | DSK16 | SOD-123FL | Not eligible |
| 7 | ETA3707 | DFN2x2-8, CSP-8 | CSP-8 |
| 8 | AiP74AUP1G125 | SOT23-5, SOT353 | Not eligible |
| 9 | SM340BF | SMB-FL | Not eligible |
| 10 | SM560C | SMC | Not eligible |
| 11 | PM8250 | No candidate | Not eligible |
| 12 | QCS8250 | No candidate | Not eligible |
| 13 | QV8012DH3RP | TO-220AB, TO-263, TO-251AA, THT (unresolved identity), TO-252AA | Not eligible |
| 14 | RB551V-30 | SOD-323 | Not eligible |
| 15 | STM32C531CB | QFN32, QFN48, QFN24, QFN20, QFP32, QFP48, QFP64, SOIC20 | QFP32, QFP48, QFP64, SOIC20 |
| 16 | VB1330 | SOT-23, TO-236 | Not eligible |
| 17 | PH42508-M22G150-R01 (connector) | CONNECTOR (unresolved identity) | Not eligible |
| 18 | MAX98361 | WLCSP9 | Not eligible |
| 19 | AFE4500 | YBG0042 | YBG0042 |
| 20 | AFE4510 | YBG0042 | YBG0042 |
| 21 | Apollo510 | WLCSP182, BGA225 | BGA225 |
| 22 | BQ25190 | YBG0030 | YBG0030 |
| 23 | CT DBLP31.12 | No candidate | Not eligible |
| 24 | DRV2624 | YFF0009 | YFF0009 |
| 25 | ICM-45686 | LGA14, CONNECTOR (unresolved identity) | Not eligible |
| 26 | GD55LB01GE | DFN8, SOIC16, BGA24 | Not eligible |
| 27 | AW8623X | No candidate | Not eligible |
| 28 | nRF54L15-CAAA-R | QFN48 | Not eligible |
| 29 | DS4733-03 (document identifier; IC identity unconfirmed) | No candidate | Not eligible |
| 30 | WL-CSP 1.31 x 2.91, 21 positions, 0.4 pitch (footprint drawing; IC identity unconfirmed) | No candidate | Not eligible |
| 31 | WL-CSP 1.31 x 2.91, 21 positions, 0.4 pitch (package drawing; IC identity unconfirmed) | WLCSP (unresolved identity) | Not eligible |
| 32 | OPT3007 | YMF0006A | Not eligible |
| 33 | SFH2705 | No candidate | Not eligible |
| 34 | SFH7016 | No candidate | Not eligible |
| 35 | SPL07-003 | LGA10 | Not eligible |
| 36 | SYN4778 | BGA40 | Not eligible |
| 37 | TCR3UG12ADS / TCR3UG series | WCSP4F | Not eligible |
| 38 | TMP118 | YMS0004A | YMS0004A |
| 39 | Display module document (not an IC; project identity omitted) | No candidate | Not eligible |
| 40 | VZ34-CG (document identifier; IC identity unconfirmed) | No candidate | Not eligible |
| 41 | W25Q01NW | BGA24, SOIC16, DFN8 | SOIC16 |
| 42 | W957D6NBGX | BGA49 | BGA49 |

## Historical native results

Allegro 17.4 standalone-PDF PASS: ETA3707 CSP-8; STM32C531CB QFP32/QFP48/QFP64/SOIC20; AFE4500 YBG0042; AFE4510 YBG0042; BQ25190 YBG0030; DRV2624 YFF0009; TMP118 YMS0004A; W25Q01NW SOIC16; W957D6NBGX BGA49. This is 9 PDFs / 12 candidates accumulated across runs, not a new all-corpus native run.

Apollo510 BGA225 has a native warning result and is excluded from PASS. MAX98361 plus explicitly supplied official drawing 21-100634 achieved an additional assisted-source PASS; standalone input remains blocked. TCR3UG circle evidence work is not integrated into default generation.

No 17.2 qualification or complete OLB success rate is inferred from this list. Actual CAD readback does not independently prove source interpretation or manufacturing approval.
