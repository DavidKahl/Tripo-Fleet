# Workflow and troubleshooting

## Configure a vehicle

Paths in `fleet.json` resolve relative to that file. A minimal fleet can look like this:

```json
{
  "concepts_dir": "concepts",
  "source_forward": "+x",
  "generation": {
    "model": "P2-20260801",
    "quad": true,
    "face_limit": 25000,
    "texture": true,
    "pbr": true
  },
  "vehicles": [
    {
      "id": "taxi_city",
      "family": "taxi",
      "length_m": 6.0,
      "width_m": 2.9,
      "height_m": 2.4
    }
  ]
}
```

Put views in `concepts/taxi_city/`. Use one consistent object, background and lighting across the images. A vehicle can override `generation`, `source_forward`, or explicit `views` paths. Recognized forward axes for local normalization are `+x`, `-x`, `+z`, `-z`. Unknown generation fields block that vehicle until corrected.

`length_m` determines uniform scale. Width and height are comparison references; they do not stretch the model into a bounding box. The checked-in fleet includes the later 7.2 m sweeper correction. The historical id `limosine` keeps its original spelling.

## Inspect the first model

Open `output/taxi_city/attempt-01/taxi_city_tripo.glb` in a viewer or Blender. Check the nose, up axis, pivot, dimensions, silhouette and underside. Multiview inputs do not include an underside reference. Change `source_forward` if needed, then run:

```console
uv run tripo-fleet normalize --only taxi_city
```

This rewrites derived models for **all downloaded attempts of that vehicle**. Preserve a derived version separately if you have manually changed it. Original API downloads remain untouched.

## Unreal materials and placement

The normalized GLB is in metres with +Y up and +Z nose, centered on its bounds. Unreal import settings and your traffic actor determine the engine orientation and unit conversion. The episode's catalog also applies a common -90 degree yaw; that is a convention of that scene, not an instruction for every Unreal project.

`textures/material.json` describes the extracted maps:

- Base color is the color texture.
- In a glTF metallic/roughness texture, G is roughness and B is metallic.
- Treat R as occlusion only when the material record says `packed_orm` is true.
- Source normal maps are OpenGL. `m0_normal_dx.png` has its green channel flipped for DirectX use; avoid applying the same flip twice.

The episode swapped catalog asset references to replace procedural vehicles with Tripo vehicles. You need your own import, material and placement setup; the private traffic scene is not a dependency of this tool.

## Recovery

| Symptom | Next step |
|---|---|
| No key found | Set `TRIPO_API_KEY` in your environment or repository `.env`. Only the key is read from `.env`; other overrides use environment variables. |
| 401/403 on balance | Check API registration, the API wallet and that your key matches the API region/host. |
| Images blocked in plan | Check filenames, PNG/JPEG format, dimensions, front view and total view count. |
| Task timed out locally | `resume --only <id>` polls the recorded task ID; it does not generate again. |
| API succeeded, local download failed | Use `resume` to query fresh output links and finish the download. |
| FBX conversion cannot start | Set `BLENDER_EXE` or add Blender to PATH; inspect `conversion.log` if Blender ran. The original local Windows setup also falls back to `E:/Blender/blender.exe`. |
| Conversion failed after raw FBX downloaded | Fix the Blender issue, then use `normalize --only <id>` to retry locally. |
| Submission failed without a saved task ID | Check the Developer Platform before generating again: the server may have accepted the task. |
| Generation ended failed | Inspect state/error and wallet. A new `run --only <id> --go` is a new paid submission. |
| Model is sideways or wrong length | Inspect geometry, change `source_forward` or `length_m`, then normalize locally. |
| Unicode errors in a Windows report | `uv run python -X utf8 -m tripo_fleet.cli report` |

The runner caps concurrency at five. Your account may have a lower limit; reduce `--parallel` accordingly. The cost estimate does not change with model/texture options, so consult current pricing when changing settings.

## What stays local

Keep `output/` intact while reviewing results: state, ledger, original FBX/GLB, task responses, requests, textures, conversion reports and normalization reports belong together. Signed URLs and file tokens are not suitable documentation examples. Git ignores the directory, private concept prompts, historical image variants, local model selections and production handoffs.

The repo contains selected input views and documentation screenshots. It does not include an API key, raw episode tasks, generated-model pack, Blender working scene, GeometryForge recipe or Unreal project.
