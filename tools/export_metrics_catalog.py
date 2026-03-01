#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

HELP_RE = re.compile(r"^# HELP ([a-zA-Z_:][a-zA-Z0-9_:]*) (.*)$")
TYPE_RE = re.compile(r"^# TYPE ([a-zA-Z_:][a-zA-Z0-9_:]*) (\w+)$")
SAMPLE_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{([^}]*)\})?\s+(.+)$")
LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"\\])*)"')

DERIVED_SUFFIXES = ("_bucket", "_sum", "_count", "_created")


def fetch_metrics(url: str, timeout: float = 8.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return (resp.read() or b"").decode("utf-8", errors="replace")


def family_name(metric_name: str, known_families: Dict[str, str]) -> str:
    if metric_name in known_families:
        return metric_name
    for suffix in DERIVED_SUFFIXES:
        if metric_name.endswith(suffix):
            base = metric_name[: -len(suffix)]
            if base in known_families:
                return base
    return metric_name


def parse_metrics(text: str) -> Dict[str, object]:
    helps: Dict[str, str] = {}
    types: Dict[str, str] = {}

    family_samples: Dict[str, set] = defaultdict(set)
    family_labels: Dict[str, set] = defaultdict(set)
    family_series_count: Dict[str, int] = defaultdict(int)
    family_label_values: Dict[str, Dict[str, set]] = defaultdict(lambda: defaultdict(set))

    lines = text.splitlines()
    for line in lines:
        m_help = HELP_RE.match(line)
        if m_help:
            helps[m_help.group(1)] = m_help.group(2)
            continue

        m_type = TYPE_RE.match(line)
        if m_type:
            types[m_type.group(1)] = m_type.group(2)
            continue

        if not line or line.startswith("#"):
            continue

        m_sample = SAMPLE_RE.match(line)
        if not m_sample:
            continue

        metric_name = m_sample.group(1)
        label_blob = m_sample.group(3) or ""
        family = family_name(metric_name, types)

        family_series_count[family] += 1
        family_samples[family].add(metric_name)

        if label_blob:
            for lm in LABEL_RE.finditer(label_blob):
                lk = lm.group(1)
                lv = lm.group(2).replace('\\"', '"')
                family_labels[family].add(lk)
                if len(family_label_values[family][lk]) < 20:
                    family_label_values[family][lk].add(lv)

    all_families = sorted(set(helps) | set(types) | set(family_samples))

    families: List[Dict[str, object]] = []
    for name in all_families:
        labels = sorted(family_labels.get(name, set()))
        label_values = {
            lk: sorted(family_label_values[name].get(lk, set()))
            for lk in labels
        }
        families.append(
            {
                "name": name,
                "type": types.get(name, ""),
                "help": helps.get(name, ""),
                "series_count": int(family_series_count.get(name, 0)),
                "labels": labels,
                "label_values": label_values,
                "sample_metrics": sorted(family_samples.get(name, set())),
            }
        )

    return {
        "generated_at_epoch": int(time.time()),
        "metric_family_count": len(families),
        "metric_families": families,
    }


def render_markdown(catalog: Dict[str, object], source_url: str) -> str:
    out: List[str] = []
    out.append("# PartyBox Metrics Catalog")
    out.append("")
    out.append(f"Source: `{source_url}`")
    out.append(f"Generated: `{catalog['generated_at_epoch']}` (unix epoch)")
    out.append(f"Metric families: `{catalog['metric_family_count']}`")
    out.append("")
    out.append("| Metric | Type | Labels | Series | Help |")
    out.append("|---|---|---|---:|---|")
    for fam in catalog.get("metric_families", []):
        if not isinstance(fam, dict):
            continue
        labels = ", ".join(fam.get("labels", []))
        help_text = str(fam.get("help", "")).replace("|", "\\|")
        out.append(
            f"| `{fam.get('name','')}` | `{fam.get('type','')}` | `{labels}` | {fam.get('series_count',0)} | {help_text} |"
        )
    out.append("")
    out.append("## Label Value Samples")
    out.append("")
    for fam in catalog.get("metric_families", []):
        if not isinstance(fam, dict):
            continue
        label_values = fam.get("label_values", {})
        if not label_values:
            continue
        out.append(f"### `{fam.get('name','')}`")
        for key in fam.get("labels", []):
            vals = label_values.get(key, [])
            out.append(f"- `{key}`: {', '.join(f'`{v}`' for v in vals)}")
        out.append("")

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export PartyBox metrics catalog for Grafana/dashboard work.")
    parser.add_argument("--url", default="http://127.0.0.1:5000/metrics", help="Prometheus metrics endpoint URL")
    parser.add_argument("--out-json", default="ops/grafana/metrics_catalog.json", help="Output JSON catalog path")
    parser.add_argument("--out-md", default="ops/grafana/metrics_catalog.md", help="Output markdown summary path")
    parser.add_argument("--out-prom", default="ops/grafana/metrics_snapshot.prom", help="Output raw .prom snapshot path")
    args = parser.parse_args()

    text = fetch_metrics(args.url)
    catalog = parse_metrics(text)
    catalog["source_url"] = args.url

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_prom = Path(args.out_prom)

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_prom.parent.mkdir(parents=True, exist_ok=True)

    out_prom.write_text(text, encoding="utf-8")
    out_json.write_text(json.dumps(catalog, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    out_md.write_text(render_markdown(catalog, args.url) + "\n", encoding="utf-8")

    print(f"wrote {out_prom}")
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
