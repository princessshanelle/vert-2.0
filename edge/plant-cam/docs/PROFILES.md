# Crop profiles

`profiles/<crop>/profile.yaml` holds everything crop-specific. All
thresholds are conservative starting points, **documented as tunable,
not agronomic truth**: watch your own greenhouse for a few weeks and
adjust. Bump `version` when you do; it is published with every reading.

| Section | What it controls |
|---|---|
| `analyzers` | default pipeline; config `analyzers:` overrides it |
| `labels` | model vocabularies; a model whose labels aren't listed is refused at load |
| `conditions` | how classifier labels (signals) become findings |
| `persistence` | `activate_after` consecutive sightings before a condition is active (alerts); `clear_after` misses to clear |
| `scoring` | label cut-offs, how yellow/brown share and environmental risk lower the score |
| `risk_indices` | environmental disease-pressure indices |
| `wetness` | how "wet" is estimated without a leaf-wetness sensor |
| `notes` | grower-facing sentences |

## Conditions

```yaml
- id: nitrogen_deficiency
  kind: nutrient            # disease | nutrient | pest | fruit
  signals: [uniform_yellowing]
  suspect_at: 0.45          # signal confidence to count at all
  detect_at: 0.75           # ...to be "detected" rather than "suspected"
  max_status: suspected     # nutrient findings never claim more from images alone
  min_count: 2              # e.g. more than one leaf
  severity: 0.25            # cost to visual_score when active (x confidence)
  corroborate: [{metric: yellow_ratio, op: ">=", value: 0.05}]
  note: "..."
```

Safeguards built into the strawberry profile, per the known pitfalls:

* **Red pigmentation.** Classical colour statistics never produce signals.
  Red tones are reported only as `red_leaf_ratio`, for context. The fruit
  counter rejects red blobs that are dull, purple-leaning or bordered by
  leaf. Tested on synthetic cold-stressed leaves: 0 fruit, no conditions.
* **Leaf spot vs leaf scorch** are separate classifier labels and separate
  conditions; telling them apart is the classifier's job and needs
  labelled examples of both.
* **Nutrients** are capped at `suspected`, need several leaves, and most
  need a corroborating canopy metric.
* **Flicker.** Nothing alerts until seen in 3 consecutive analyses
  (`activate_after`). An analysis that couldn't see (too dark, model
  skipped for heat) is not counted as a miss.

## Risk indices

```yaml
botrytis:
  window_h: 24
  temp_c: [15, 25]
  rh_pct: [90, 101]
  wet: or          # ignore | or | required | excluded
  onset_h: 4       # hours meeting the condition before risk starts rising
  full_h: 12       # hours for risk 1.0
```

Hours are counted from the zone's sensor history (sampled every 5 min,
persisted, gaps over 30 min count as unknown). "Wet" = leaf-wetness
sensor >= `leaf_wetness_min` if there is one, else RH >= `rh_min` or air
within `dewpoint_spread_c` of its dew point.

Defaults: Botrytis = mild (15-25 C) and humid (RH >= 90 %) *or* wet;
powdery mildew = 15-27 C, RH 75-95 % and **not** wet (free water
inhibits it). These are simplified, transparent pressure indicators.
