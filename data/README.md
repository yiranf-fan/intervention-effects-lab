# Datasets

## Hillstrom Email Marketing

Randomized email assignment (control vs Mens vs Womens) with conversion and spend outcomes; used for ATE/CATE and uplift/policy examples in `causaltoolkit`. 

**Source**: Direct CSV from [MineThatData original](http://www.minethatdata.com/Kevin_Hillstrom_MineThatData_E-MailAnalytics_DataMiningChallenge_2008.03.20.csv)

**Raw dataset**: **64,000 customers**, randomized email A/B test
| Metric | Control (No Email) | Mens Email | Womens Email |
|--------|--------------------|------------|--------------|
| **Customers** | ~21,333 | ~21,333 | ~21,334 |
| **Converters** | ~3,100 (14.5%) | ~3,200 (15.0%) | ~3,100 (14.5%) |
| **Avg Spend** | $10.22 | $11.23 | $10.45 |

**Transformation** (`ingest_events.py --hillstrom`):

## Synthetic Healthcare Patient Journey Events

 Randomized reminder vs control for post-discharge patients, where reminders increase follow-up completion and reduce 30‑day readmissions, with stronger effects for older and higher‑risk segments; used to exercise segmentation, FDR, guardrails, and SRM checks in `experimentplatform`. 

- Generator: `experimentplatform/analytics/generate_health_events.py`: (reproducible synthetic health events generator for experimentation demos)
- Default output: `data/raw/health_journey_events.csv`
- Default experiment: `health_exp_reminder_30d` (`control` vs `reminder`)

The synthetic journey includes events such as `admission`, `lab_test`, `reminder_sms`,
`followup_completed`, `discharge`, `length_of_stay`, and optional `readmission_30d`.
It also emits segments (`region`, `device`, `age_bucket`, `risk_segment`) for segment/FDR analysis.

> Note: Health effect sizes and heterogeneity are stylized for portfolio experimentation workflows, not clinically calibrated.

***

# Unified schema

Both tech and health datasets are normalized into the same **events** schema before analysis:

- `user_id` / `patient_id`: entity identifier  
- `timestamp`: event time (UTC)  
- `experiment_id`: experiment key (e.g., `exp_email`, `health_exp_reminder_30d`)  
- `variant`: arm label (e.g., `control`, `treatment`, `reminder`)  
- `event_name`: event type (page view, purchase, admission, readmission_30d, etc.)  
- `value`: numeric payload when relevant (revenue, lab value, length_of_stay, flags as 0/1)  
- Segments: `region`, `device`, `age_bucket`, `risk_segment`, plus optional channel-like fields

All ingestion scripts in `experimentplatform/analytics` write into this schema so `/compute_metrics` and the causal toolkit can treat tech and health domains consistently.
