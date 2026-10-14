# Extending plant-cam

## Add an analyzer: one class, one config entry

```python
# my_pkg/leaf_area.py  (or a crop profile's plugins.py)
from plant_cam.core.registry import Analyzer, AnalyzerParams, register_analyzer
from plant_cam.core.types import AnalyzerResult, Context, Frame


@register_analyzer("leaf_area")
class LeafArea(Analyzer):
    class Params(AnalyzerParams):          # validated at startup; typos fail loudly
        min_area_px: int = 50

    cost = "light"                          # "heavy" = skipped first when the Pi runs hot

    def analyze(self, frame: Frame, ctx: Context) -> AnalyzerResult:
        canopy = ctx.results["classical"].metrics["canopy_coverage_pct"]   # earlier analyzers' output
        return AnalyzerResult(self.id, metrics={"leaf_area_index": canopy / 100})
```

```yaml
# config.yaml -- this list replaces the profile's default list
analyzers:
  - name: classical
  - name: leaf_area
    params: {min_area_px: 80}
  - name: strawberry_fruit
```

That's all: `metrics` become flat numeric payload keys (charted, usable in
automation rules). The same thing is exercised by
`tests/unit/test_config_registry.py::test_new_analyzer_is_one_class_and_one_config_entry`.

How the class gets imported, in order of preference:
1. **Crop-specific**: put it in `profiles/<crop>/plugins.py` (loaded with the profile).
2. **Separate package**: declare an entry point
   ```toml
   [project.entry-points."plant_cam.analyzers"]
   leaf_area = "my_pkg.leaf_area:LeafArea"
   ```
3. **Built in**: a module under `plant_cam/analyzers/`, imported in its `__init__.py`.

What an analyzer can return (`AnalyzerResult`):

| Field | Becomes |
|---|---|
| `metrics` | payload keys |
| `roi_metrics[roi]` | `roi_<roi>_<key>` payload keys |
| `detections` | boxes on snapshots/live view; inputs for later analyzers (`ctx.detections("leaf")`) |
| `signals` | evidence the crop profile maps to conditions (Stage C) |
| `samples` | crops for active learning |
| `vocabulary` | signal names this run *could* have produced (so absence counts as "not seen") |
| `skipped` | why it didn't run (no model, too hot) -- never an exception |

An analyzer that raises is logged and recorded as skipped; the others still run.

## Add a publisher

```python
from plant_cam.core.registry import Publisher, PublisherParams, register_publisher
from plant_cam.core.types import ZoneReport
from plant_cam.publishers.payload import build_payload


@register_publisher("http_post")
class HttpPost(Publisher):
    class Params(PublisherParams):
        url: str

    def publish(self, report: ZoneReport) -> None:
        # Must not raise on a network failure: log/buffer and return.
        ...
```

```yaml
publishers:
  - name: snapshot
  - name: mqtt
  - name: http_post
    params: {url: "http://192.168.1.20:9000/hook"}
```

`self.services` gives access to the broker link, the local store and the
gateway uploader. Publishers run in list order.

## Add a crop

Copy `profiles/strawberry/` to `profiles/<crop>/`, edit `profile.yaml`
(conditions, label vocabularies, risk indices, notes; see PROFILES.md),
replace `plugins.py` or drop it, set `profile.id: <crop>` in config.
Nothing in `plant_cam/` changes.

## Swap or retrain a model

See `training/README.md`. A model is a folder with `model.onnx` (or
`.tflite`) and `meta.json`; `deploy/deploy-model.sh` installs it into
`/opt/plant-cam/models/<slot>/<version>/` and points `<slot>/current` at
it. Any architecture works as long as the export follows the contract in
`plant_cam/runtime/__init__.py` (classifier logits/probs, or detector
`dets_v1`).
