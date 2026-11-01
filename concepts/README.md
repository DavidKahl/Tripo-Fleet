# Concept inputs

The twelve vehicles in `fleet.json` each have four canonical PNG inputs: `front`, `left`, `back`, `right`. Open [the gallery](index.html) locally in a browser to inspect them. The inputs were generated with ImageGen for the episode, then used with Tripo's multiview API.

These images establish appearance, not calibrated dimensions. Framing and component proportions can disagree between views. Review the first generated result, including its underside, before generating a batch. `length_m` controls uniform normalization; width and height remain generated proportions.

Only the four canonical views are distributed here. Production prompts, historical variants, top/bottom review views and local manifests remain outside the public repo. See [setup and usage](../README.md) and [image terms](../ASSETS.md).
