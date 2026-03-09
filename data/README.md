# Datasets

## Hillstrom Email Marketing

**Source**: Direct CSV from [MineThatData original](http://www.minethatdata.com/Kevin_Hillstrom_MineThatData_E-MailAnalytics_DataMiningChallenge_2008.03.20.csv)

**Raw dataset**: **64,000 customers**, randomized email A/B test
| Metric | Control (No Email) | Mens Email | Womens Email |
|--------|--------------------|------------|--------------|
| **Customers** | ~21,333 | ~21,333 | ~21,334 |
| **Converters** | ~3,100 (14.5%) | ~3,200 (15.0%) | ~3,100 (14.5%) |
| **Avg Spend** | $10.22 | $11.23 | $10.45 |

**Transformation** (`ingest_clickstream.py --hillstrom`):
