"""Prints the Label Studio labelling config for VERT's classes, so the
labels people draw always match classes.yaml exactly.

    python labelstudio_config.py   # paste the output into
                                   # Project -> Settings -> Labeling Interface -> Code
"""
from common import load_classes

_COLOURS = {"healthy": "#4caf50", "issue": "#ff9800", "pest": "#e53935"}

lines = ['<View>', '  <Image name="image" value="$image" zoom="true"/>', '  <RectangleLabels name="label" toName="image">']
for c in load_classes():
    lines.append(f'    <Label value="{c["name"]}" background="{_COLOURS.get(c["kind"], "#9e9e9e")}"/>')
lines += ['  </RectangleLabels>', '</View>']
print("\n".join(lines))
