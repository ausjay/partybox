# PartyBox Grafana Assets

This folder contains Grafana-ready assets generated from the live PartyBox `/metrics` endpoint.

## Files

- `partybox_observability_dashboard.json`
  - Importable Grafana dashboard using PartyBox + process metrics.
- `metrics_snapshot.prom`
  - Raw Prometheus exposition snapshot from PartyBox.
- `metrics_catalog.json`
  - Machine-readable metric family inventory (type/help/labels/sample series counts).
- `metrics_catalog.md`
  - Human-readable table for dashboard design/review.

## Refresh the Metric Dump

Run on PartyBox:

```bash
tools/export_metrics_catalog.py \
  --url http://127.0.0.1:5000/metrics \
  --out-prom ops/grafana/metrics_snapshot.prom \
  --out-json ops/grafana/metrics_catalog.json \
  --out-md ops/grafana/metrics_catalog.md
```

## Import Dashboard

1. Grafana -> Dashboards -> New -> Import
2. Upload `ops/grafana/partybox_observability_dashboard.json`
3. Select your Prometheus datasource when prompted

## Recommended Prometheus Scrape Job

```yaml
scrape_configs:
  - job_name: partybox
    metrics_path: /metrics
    scrape_interval: 15s
    static_configs:
      - targets: ['partybox:5000']
```

## Notes

- Dashboard includes `or vector(0)` in rate panels where useful to avoid "No data" during idle periods.
- Top-25 panel uses `partybox_top_item_plays{mode="$top_mode"}` (`top_mode`: `partybox` or `spotify`).
- External receiver panels use:
  - `partybox_external_stream_active{mode="airplay|bluetooth"}`
  - `partybox_external_metadata_available{mode="airplay|bluetooth"}`
