# Web-Search Evidence Archive

Raw JSON results of the 58 live web searches run on 2026-09-16 (z-ai web_search).
Every SJEV-WEB-* record in the master JSON is backed by one of these files:
open the file, find the cited publisher/URL, and compare with the record's
`source_url` + `accessed` fields. No evidence file = no record.

| Evidence file | Wave | Contents | Supports master-JSON section |
|---|---|---|---|
| web_evidence/a1_drewry.json | A logistics | Drewry World Container Index + Idle-Activated Container Index, Sep 2026 (6 results) | `web_freight_indices` |
| web_evidence/a2_redsea.json | A logistics | Red Sea transit status and diversion impact (6 results) | `web_freight_indices` |
| web_evidence/a3_tariff_india.json | A logistics | US-India pharma tariff (100% branded / generics exemption to Aug 2028) (6 results) | `web_tariffs_trade` |
| web_evidence/a4_tariff_world.json | A logistics | Global tariff actions and macro impact (Yale Budget Lab) (6 results) | `web_tariffs_trade` |
| web_evidence/a5_port_congestion.json | A logistics | Singapore / Rotterdam congestion, reefer restrictions (6 results) | `web_port_congestion` |
| web_evidence/a6_freightos.json | A logistics | Freightos Baltic Index (FBX) (5 results) | `web_freight_indices` |
| web_evidence/a7_disruption_cost.json | A logistics | Supply-chain disruption cost ($184bn Marsh, independent confirmation) (6 results) | `web_pharma_losses` |
| web_evidence/a8_scfi.json | A logistics | SCFI / CCFI Shanghai indices, Sep 2026 (6 results) | `web_freight_indices` |
| web_evidence/b1_pharma_cc_market.json | B cold chain | Pharma cold-chain market size refresh (6 results) | `web_coldchain_markets` |
| web_evidence/b2_vaccine_wastage.json | B cold chain | Vaccine wastage (WHO ~50%) verification (6 results) | `web_pharma_losses` |
| web_evidence/b3_biologics_loss.json | B cold chain | Biologics temperature-excursion losses, IQVIA $35bn (6 results) | `web_pharma_losses` |
| web_evidence/b4_temp_monitoring.json | B cold chain | Temperature-monitoring market size & IoT players (6 results) | `web_coldchain_markets` |
| web_evidence/b5_excursion_cost.json | B cold chain | Excursion cost studies (Kanwal 2026 et al.) (6 results) | `web_pharma_losses` |
| web_evidence/b6_clinical_logistics.json | B cold chain | Clinical-trial logistics cold-chain market (6 results) | `web_coldchain_markets` |
| web_evidence/b7_503a_3pl.json | B cold chain | GDP-compliant pharma 3PL landscape (6 results) | `web_competitor_updates` |
| web_evidence/b8_worldwide_coldstorage.json | B cold chain | Worldwide cold-storage capacity (GCCA) (6 results) | `web_coldchain_markets` |
| web_evidence/c1_project44.json | C competitors | project44 funding/valuation (Tracxn $912M) (6 results) | `web_competitor_updates` |
| web_evidence/c2_fourkites.json | C competitors | FourKites status (6 results) | `web_competitor_updates` |
| web_evidence/c3_everstream.json | C competitors | Everstream Analytics status (6 results) | `web_competitor_updates` |
| web_evidence/c4_altana.json | C competitors | Altana AI $1B valuation (6 results) | `web_competitor_updates` |
| web_evidence/c5_interos.json | C competitors | Interos $310M + Jan 2026 round (6 results) | `web_competitor_updates` |
| web_evidence/c6_blackkite.json | C competitors | Black Kite cyber-risk rating (3 results) | `web_competitor_updates` |
| web_evidence/c7_resilinc.json | C competitors | Resilinc status (6 results) | `web_competitor_updates` |
| web_evidence/c8_prewave.json | C competitors | Prewave $98M round (6 results) | `web_competitor_updates` |
| web_evidence/d1_ibp_api.json | D SAP | SAP IBP API (/IBP/API_STOCK etc.) (4 results) | `web_sap_updates` |
| web_evidence/d2_tm_api.json | D SAP | SAP TM API (TransportationOrderGenericRequest_In) (6 results) | `web_sap_updates` |
| web_evidence/d3_ariba_api.json | D SAP | SAP Ariba API (GET /orders) (5 results) | `web_sap_updates` |
| web_evidence/d4_joule.json | D SAP | SAP Joule collaborative agents timeline (6 results) | `web_sap_updates` |
| web_evidence/d5_genai_hub.json | D SAP | SAP Generative AI Hub model access & licensing (5 results) | `web_sap_updates` |
| web_evidence/d6_btp.json | D SAP | SAP AI Core token metering / BTP AI services (6 results) | `web_sap_updates` |
| web_evidence/e1_pharma_exports.json | E India | India pharma exports ($30,466.85M FY25, Q1 FY27 $8.10bn) (6 results) | `web_india_updates` |
| web_evidence/e2_india_cc.json | E India | India cold-chain market (IMARC INR 2,535.87bn) (6 results) | `web_india_updates` |
| web_evidence/e3_evin.json | E India | eVIN network (733 districts / 36 states-UTs / 29,000 cold points) (6 results) | `web_india_updates` |
| web_evidence/e4_coldstorage.json | E India | NCCD 8,815 cold storages / 40.22 MMT (6 results) | `web_india_updates` |
| web_evidence/e5_pharmexcil_target.json | E India | Pharmexcil $65bn/$130bn/$350bn targets (6 results) | `web_india_updates` |
| web_evidence/e6_api_dependency.json | E India | India API (bulk drug) import dependency 70-90% (6 results) | `web_india_updates` |
| web_evidence/f1_eugdp.json | F regulatory | EU GDP 2013/C 343/01 primary text (6 results) | `web_regulatory_verification` |
| web_evidence/f2_eugdp_revision.json | F regulatory | EU GDP 2015/C 95/01 revision (6 results) | `web_regulatory_verification` |
| web_evidence/f3_trs961.json | F regulatory | WHO TRS 961 Annex 9 primary text (6 results) | `web_regulatory_verification` |
| web_evidence/f4_usp1079.json | F regulatory | USP <1079> / <1079.4> (6 results) | `web_regulatory_verification` |
| web_evidence/f5_fda_part11.json | F regulatory | FDA 21 CFR Part 11 (6 results) | `web_regulatory_verification` |
| web_evidence/f6_cdsco.json | F regulatory | CDSCO GDP.PP Ver. 00 (Apr 2024) (6 results) | `web_regulatory_verification` |
| web_evidence/g1_timesfm.json | G AI stack | Google TimesFM license (Apache-2.0) (6 results) | `web_ai_stack` |
| web_evidence/g2_chronos.json | G AI stack | Amazon Chronos license (Apache-2.0) (6 results) | `web_ai_stack` |
| web_evidence/g3_moirai.json | G AI stack | Salesforce Moirai license gotcha (CC-BY-NC) (5 results) | `web_ai_stack` |
| web_evidence/g4_agents.json | G AI stack | Agent frameworks (LangGraph / CrewAI / AutoGen) (6 results) | `web_ai_stack` |
| web_evidence/g5_mcp.json | G AI stack | Model Context Protocol spec 2026-07-28 (6 results) | `web_ai_stack` |
| web_evidence/g6_hf_ts.json | G AI stack | Hugging Face time-series model shelf (4 results) | `web_ai_stack` |
| web_evidence/h1_openmeteo.json | H demo feeds | Open-Meteo free weather API (3 results) | `web_demo_feeds` |
| web_evidence/h2_ais.json | H demo feeds | aisstream.io free AIS feed (2 results) | `web_demo_feeds` |
| web_evidence/h3_freightos_api.json | H demo feeds | Freightos FBX data access (3 results) | `web_demo_feeds` |
| web_evidence/h4_gdelt.json | H demo feeds | GDELT free news/disruption feed (6 results) | `web_demo_feeds` |
| web_evidence/h5_ble_logger.json | H demo feeds | BLE temperature-logger options (6 results) | `web_demo_feeds` |
| web_evidence/h6_routing.json | H demo feeds | SeaRoutes / free routing APIs (4 results) | `web_demo_feeds` |
| web_evidence/i1_scm_share.json | I markets | SCM software market share (Gartner) (5 results) | `web_coldchain_markets` |
| web_evidence/i2_visibility_market.json | I markets | Supply-chain visibility market size (5 results) | `web_coldchain_markets` |
| web_evidence/i3_gartner_mv.json | I markets | 2026 Gartner Magic Quadrant leaders (5 results) | `web_competitor_updates` |
| web_evidence/i4_serialization.json | I markets | Drug serialization / DSCSA track-and-trace (5 results) | `web_regulatory_verification` |

Total: 58 evidence files, 321 raw search results.
Search execution scripts: scripts/sjev/run_searches.sh + retry_searches.py (in project repo).
