# Public datasets: licences (checked 2026-10-12)

**Summary: none of the public strawberry datasets is cleared for
commercial use.** Use them to *pre-train* and experiment; the models you
ship should be fine-tuned and tested on your own labelled photos. Check
each licence yourself before relying on it: the sources below disagree in
places.

| Dataset | What it gives | Licence as found | Use here |
|---|---|---|---|
| Strawberry Disease Detection Dataset (Afzaal et al., *Sensors* 21:6565, 2021), Kaggle `usmanafzaal/strawberry-disease-detection-dataset` | ~2,500 field photos, LabelMe polygons, 7 diseases | **Unclear.** Dataset Ninja lists CC BY 4.0; mirrors say "other" or Kaggle default; the description permits use/modification with citation. Derivative dataset on Mendeley: academic/research reuse. Read the Kaggle page's licence field. | `import_public.py afzaal` -> Stage B crops (leaf spot, angular leaf spot, powdery mildew leaf/fruit, anthracnose, grey mould, blossom blight). Cite the paper. |
| StrawDI_Db1 (University of Huelva), Zenodo 10.5281/zenodo.14648986 | 3,100 images, fruit instance masks | **Academic use & non-commercial only** | `import_public.py strawdi` -> Stage A `fruit` boxes. Research only. |
| PlantVillage (Mohanty et al.) strawberry classes | lab photos, healthy / leaf scorch | **Conflicting:** CC BY-SA 3.0 in the original paper; CC0 1.0 on Mendeley / Meta-Album mirrors | Not imported by default: single leaves on plain backgrounds generalise poorly to a greenhouse camera. |

Sources: [Dataset Ninja: Strawberry Disease Detection](https://datasetninja.com/strawberry-disease-detection) ·
[Mendeley derivative k2ptxmfjhj](https://data.mendeley.com/datasets/k2ptxmfjhj) ·
[gts.ai listing](https://gts.ai/dataset-download/strawberry-disease-detection-dataset/) ·
[StrawDI on Zenodo](https://zenodo.org/doi/10.5281/zenodo.14648986) ·
[StrawDI, Univ. of Huelva](https://produccioncientifica.uhu.es/documentos/67a9c7c819544708f8c72739) ·
[PlantVillage repo](https://github.com/spMohanty/PlantVillage-Dataset) ·
[PlantVillage paper, arXiv 1511.08060](https://alphaxiv.org/abs/1511.08060) ·
[Meta-Album PlantVillage](https://api.openml.org/d/44242)

## Models and code

| Component | Licence |
|---|---|
| torchvision models and pretrained weights (SSDLite320-MobileNetV3, MobileNetV3, EfficientNet) | BSD-3-Clause (code); ImageNet/COCO-pretrained weights are distributed by PyTorch. Check the weights' own terms for your use |
| ONNX Runtime | MIT |
| LiteRT (ai-edge-litert), onnx2tf | Apache-2.0 |
| NanoDet-Plus, YOLOX (benchmarked only) | Apache-2.0 |
| ~~Ultralytics YOLO~~ (previous pipeline, removed) | AGPL-3.0: would have placed copyleft obligations on the subsystem |

A trained model also inherits obligations from its **training data**:
weights trained on StrawDI are non-commercial. `meta.json` records
`trained_on`, so you can always tell what a deployed model came from.
