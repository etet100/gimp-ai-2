# Amazon Bedrock provider support

Record of the changes that added Amazon Bedrock as an alternative provider to
this fork, the two new features built on it, the bugs found along the way, and
how each piece was verified.

Fork of [lukaso/gimp-ai](https://github.com/lukaso/gimp-ai), branched from
`436c37e` ("Remove version number from Readme", v0.14.0).

## Summary

The provider is chosen once in `Filters → AI → Settings` and applies
throughout.

| Feature | OpenAI | Bedrock | Notes |
|---|---|---|---|
| Inpainting | yes | yes | |
| Layer Composite | yes | yes | Bedrock flattens the layers first - see [Layer composite](#layer-composite) |
| Generate Layer / Generate New Image | yes | yes | Bedrock generation runs in its own region - see [Generation](#generation) |
| **Erase Object** | — | yes | New. Three alternative erasures per run |
| **Generative Upscale** | — | yes | New. Fast, Conservative or Creative |

Erase Object is the feature this fork was actually built for - removing
photobombers - and it needed considerably more than a provider swap. See
[Erase Object](#erase-object).

Eight bugs were found and fixed while building this. Four of them broke the
OpenAI path as well and had nothing to do with Bedrock - including one that
meant AI results were never actually placed into the image at all.

The recurring theme, and the reason this document records measurements rather
than intentions: most of what broke was invisible to inspection and only showed
up when something was actually run and counted. A mask that looked right had
zero selection pixels in it; a compositing call that reported success placed
nothing; a request that worked on a small test image exceeded a payload cap on a
real one.

## Model choice

The original request was for Stable Diffusion XL. `stability.stable-diffusion-xl-v1`
is **no longer offered on Bedrock** — it does not appear in
`aws bedrock list-foundation-models` in either `us-east-1` or `us-west-2`.

Stable Diffusion 3.5 Large is available but is text-to-image and image-to-image
only. Its API takes `prompt`, `image` and `strength`, with no mask parameter, so
it cannot do masked inpainting.

The model used for inpainting is therefore `stability.stable-image-inpaint-v1:0`,
Stability's current inpainting model on Bedrock, which takes an image plus a
mask. It is configurable in Settings if you want to point at something else.

### Models used

| Purpose | Model | Region | Inference profile |
|---|---|---|---|
| Inpainting | `stability.stable-image-inpaint-v1:0` | config `region` | required |
| Erase Object | `stability.stable-image-erase-object-v1:0` | config `region` | required |
| Upscale (fast) | `stability.stable-fast-upscale-v1:0` | config `region` | required |
| Upscale (conservative) | `stability.stable-conservative-upscale-v1:0` | config `region` | required |
| Upscale (creative) | `stability.stable-creative-upscale-v1:0` | config `region` | required |
| Generation | `stability.sd3-5-large-v1:0` | `generation_region`, default `us-west-2` | **must not have one** |

The inference-profile column is the trap. Every editing model is rejected
without the geography prefix (`us.`, `eu.`, `apac.`) and the generation model is
rejected *with* it, so `resolve_credentials()` takes a `use_inference_profile`
flag rather than applying one rule everywhere.

## Files

### New

| File | Purpose |
|---|---|
| `bedrock_client.py` | SigV4 signing, credential resolution, request building. No GIMP dependencies, standard library only. |
| `tests/test_gimp_pipeline.py` | Mask and layer-flattening tests that run inside headless GIMP. |
| `tests/test_native_context.py` | Native-resolution context window, pure and offline. |
| `tests/test_erase_in_gimp.py` | Full erase path against a real photo, including the procedure itself (makes live calls). |
| `tests/test_inpaint_in_gimp.py` | Inpainting variations end to end (makes live calls). |
| `tests/test_generate_in_gimp.py` | Text-to-image end to end (makes a live call). |
| `tests/test_fit_in_gimp.py` | Generated-layer canvas fitting, no API calls. |
| `tests/test_upscale_in_gimp.py` | Upscale variants and dialog construction (live calls optional). |
| `tests/probe_placement.py` | Compares layer placement methods; found the Gegl no-op. |
| `BEDROCK.md` | This document. |

### Modified

| File | Change |
|---|---|
| `gimp-ai-plugin.py` | Bedrock provider, Erase Object procedure, credential handling, settings UI, mask and compositing fixes |
| `install_plugin.py` | Ships `bedrock_client.py`; Bedrock setup instructions |
| `build_release.py` | Ships `bedrock_client.py` |
| `config.json.example` | `api_provider` and the `bedrock` config block, including erase settings |
| `README.md` | Erase Object usage, Bedrock configuration, sandboxed-GIMP caveat |
| `INSTALL.md` | Three required files instead of two |
| `TROUBLESHOOTING.md` | Third required file in the folder listing |
| `CHANGELOG.md` | 0.15.0 section |
| `.github/copilot-instructions.md` | Architecture, mask conventions, layer placement rule, integration tests |
| `coordinate_utils.py` | `calculate_native_context()` for native-resolution windows |
| `.gitignore` | Test output artefacts |

Installing the plugin now requires **three** files rather than two:
`gimp-ai-plugin.py`, `coordinate_utils.py`, `bedrock_client.py`.

## `bedrock_client.py`

No boto3. GIMP ships its own Python interpreter and installing packages into it
is painful, so requests are signed with `hmac`/`hashlib` from the standard
library. This keeps the project's "no external dependencies" property.

Public API:

- `resolve_credentials(bedrock_config)` — resolves credentials and region.
  Precedence: plugin config, then environment, then `~/.aws`. Returns a `source`
  field naming which one won, for diagnostics. Raises `BedrockError` if nothing
  usable is found.
- `inference_profile_id(model_id, region)` — adds the geography prefix.
- `build_invoke_request(creds, body_bytes, now=None)` — returns `(url, headers)`.
  The `now` parameter exists so signing is deterministic under test.
- `build_inpaint_body(image_b64, mask_b64, prompt, ...)` — request body.
- `demo()` — self-check, run with `python3 bedrock_client.py`.

### Three things the live API forced

These were each found by getting a 4xx from Bedrock, not from the documentation:

1. **Signing service name is `bedrock`, not `bedrock-runtime`**, even though the
   host is `bedrock-runtime.{region}.amazonaws.com`. Otherwise:
   `403 Credential should be scoped to correct service: 'bedrock'`.

2. **The model id's colon is percent-encoded once in the URL and twice in the
   canonical request.** SigV4 requires double-encoded path segments for every
   service except S3. The wire path carries `%3A`, the string that gets signed
   carries `%253A`. Otherwise the signature mismatches.

3. **Stability image models cannot be invoked on-demand.** A bare model id gives
   `400 Invocation of model ID ... with on-demand throughput isn't supported`.
   They require a cross-region inference profile, which is the model id with a
   geography prefix (`us.`, `eu.`, `apac.`). `inference_profile_id()` adds this
   automatically based on region, and leaves already-prefixed ids and ARNs alone.

## Generation

Text-to-image uses `stability.sd3-5-large-v1:0`, and differs from every other
Bedrock path here in two ways that both cause hard failures if ignored:

1. **Different region.** Stability's generation models are not offered in
   `us-east-1`, where all the editing models live. Confirmed by listing:
   us-east-1 has only `amazon.nova-canvas-v1:0`, and that is `LEGACY`.
   us-west-2 has `sd3-5-large`, `stable-image-core` and `stable-image-ultra`,
   all `ACTIVE`. Generation therefore has its own `generation_region`, default
   `us-west-2`. No extra setup is needed for the second region: Bedrock serves
   these models on demand, so there is no per-region model-access request, and
   the generation call succeeded in us-west-2 on an account that had only ever
   been used in us-east-1.
2. **No inference profile.** The editing models must be invoked through a
   geography-prefixed profile; generation must not be. Measured directly:

   | Model id | Result |
   |---|---|
   | `stability.sd3-5-large-v1:0` | works, returned 1216x832 |
   | `us.stability.sd3-5-large-v1:0` | `400 The provided model identifier is invalid` |

   `resolve_credentials()` grew a `use_inference_profile` flag for this, and
   `generation_credentials()` passes it as False.

Generation also takes an **aspect ratio**, not pixel dimensions, from a fixed
list (21:9 through 9:21), and returns roughly a megapixel at that ratio.
`aspect_ratio_for()` maps the canvas shape to the nearest one on the list. Note
that 4:3 is not on it, so a 2048x1536 canvas maps to 5:4 rather than 3:2 - 1.25
is closer to 1.333 than 1.5 is.

### Fitting the result to the canvas

Generation ignores the selection and always produces a whole-canvas layer;
generating into a selection is what Inpainting already does.

Models return their own resolution, which rarely matches the canvas, and the
layer used to be inserted at that size at (0,0) - leaving a visible gap. It is
now scaled to **cover** the canvas and centred, so the tighter axis matches
exactly and the other overhangs and can be cropped.

GIMP 3 has no Lanczos filter. `InterpolationType` is `none, linear, cubic,
nohalo, lohalo` - Lanczos was removed after 2.8. NoHalo is the intended
enlarging filter and LoHalo the reducing one, so `_fit_generated_layer()`
picks between them by direction of scale.

Worth noting how large the enlargement can get: a 2048x1536 canvas maps to a
5:4 aspect, generates at 1216x832, and is then enlarged 1.85x to 2245x1536. That
is the case where a generative upscale would earn its place - see below.

## Generative Upscale

`Filters → AI → Generative Upscale` enlarges the **active layer** (not the whole
image) using one of Stability's three upscale models, chosen from a dropdown.

Measured on a 2048x1536 photo:

| Variant | Input sent | Output | Factor | Time | Payload |
|---|---|---|---|---|---|
| fast | 1182x886 (reduced) | 4728x3544 | 4.0x | 6.1s | 4.5MB |
| conservative | 2048x1536 | 3592x2696 | 1.8x | 32.2s | 2.2MB |
| creative | 2048x1536 | 3632x2728 | 1.8x | 68.1s | 2.1MB |

Fast is a strict 4x; the other two target about 4K regardless of input. So Fast
yields the largest image but only after its input has been reduced, while the
other two work from the full-resolution layer.

### Four things the API enforced

None of these are in the documentation; each came from a 400:

1. **Fast accepts `image` and `output_format` only.** Anything else, `seed`
   included, fails with `Invalid field 'seed' in request. Available fields for
   upscale-fast are: image, output_format`.
2. **Fast caps each side at 1536px** (`Image width must be at most 1536, but is
   1600`), so larger layers are reduced with LoHalo before sending.
3. **Conservative and Creative require a non-empty prompt** - `prompt must be at
   least 1 character`. A neutral default is substituted when the user leaves the
   box empty, rather than letting the call fail.
4. **PNG responses exceed Bedrock's 16MB payload cap at these sizes.** All three
   variants therefore request WebP. Fast is the trap here: a small test input
   returned PNG fine, and only a realistic one blew the limit at 4728x3544
   (38,849,158 bytes). GIMP loads WebP through its bundled `file-webp` plug-in.

The documented input limits are also wrong in one place: the prose says
Conservative takes "images between 64x64 and 1 megapixel", but it accepted a
3.1MP layer without complaint. The 9,437,184 pixel figure in the parameter list
is the real ceiling.

## `gimp-ai-plugin.py`

### Added

| Method | Purpose |
|---|---|
| `_restrict_config_permissions` | chmod 600 on the config file |
| `_get_provider` | Reads `api_provider`; falls back to OpenAI if `bedrock_client` is missing |
| `_credentials_ready` | Single credential gate for both providers |
| `_log_bedrock_environment` | Prints what the plugin can see; masks secret values |
| `_on_clear_bedrock_credentials` | Removes stored AWS credentials |
| `_flatten_layers_for_bedrock` | Composites selected layers into one image for Bedrock |
| `_convert_mask_to_luminance` | Converts the OpenAI mask to white-on-black (Bedrock mask, OpenAI mask hint) |
| `_call_bedrock_edit` | Bedrock InvokeModel call |
| `_calculate_native_context_extraction` | Native-resolution context window |
| `_call_bedrock_erase` | Object-erase model call |
| `_run_bedrock_variations` | Runs erase/inpaint variations concurrently |
| `_call_edit_threaded` | Dispatches to the configured provider |
| `run_erase_object` | The Erase Object procedure |
| `run_upscale` | The Generative Upscale procedure |
| `_show_upscale_dialog` | Variant dropdown and prompt |
| `_call_bedrock_upscale` | Upscale model call |
| `_export_layer_for_upscale` | Exports the active layer, reduced to fit input limits |
| `_add_layer_from_bytes` | Adds WebP/JPEG results without canvas fitting |
| `_call_bedrock_generation` | Text-to-image call |
| `_call_generation_threaded` | Dispatches generation to the configured provider |

`_call_bedrock_edit` returns the same `{"data": [{"b64_json": ...}]}` shape the
OpenAI path returns, so the entire compositing pipeline is provider-agnostic and
needed no changes.

### Integration points

Only three lines in `run_inpaint` differ by provider: the credential check, the
mask conversion, and the dispatch to `_call_edit_threaded`.

### Layer composite

The two providers differ in kind here, not just in endpoint.

OpenAI's `images/edits` accepts an **array** of up to 16 images, so the plugin
sends each selected layer separately and gpt-image-1 can use one layer as a
reference for another. `stability.stable-image-inpaint-v1:0` accepts exactly
**one** image plus a mask, and Bedrock offers no general multi-image compositing
model — the nearest options (`stable-image-style-guide`, `stable-style-transfer`,
`stable-image-control-structure`) each take one image plus a single reference,
which is a different operation.

So on Bedrock the selected layers are flattened by GIMP first —
`_flatten_layers_for_bedrock` copies them into a temp image in stack order,
preserving offsets, flattens, and reuses `_extract_context_region` for scaling
and padding — and the model only ever sees the combined pixels. It then inpaints
the masked region with the prompt.

Consequence worth knowing: a prompt like "put the object from layer 2 onto the
table in layer 1" works on OpenAI and does not on Bedrock, because Bedrock never
sees the layers as separate images. Bedrock composites are better described as
"harmonise this masked region of the flattened stack".

A selection mask is required for Bedrock composites: the model needs to be told
which region to change. Running without one is refused with an explanatory
message rather than silently regenerating the whole canvas.

### Settings dialog

Rebuilt as two columns inside a scrolled window. It was taller than a 1080p
screen, putting the Save button out of reach.

Measured on a 1536x864 logical display: **952x649 allocated, 854x173 minimum**.
The scrolled window means the buttons cannot be pushed off screen at any size.

Bedrock fields: AWS region, model ID, access key ID, secret access key, Bedrock
API key, and a *Clear Stored AWS Credentials* button. The two secret fields are
write-only — blank keeps what is stored, matching the existing OpenAI key field —
so the Clear button is the way to remove them.

Since the config file can now hold an AWS secret key, `_save_config()` chmods it
to 600 on every write.

## Erase Object

Object removal is the main use case this fork was built for (removing
photobombers), and it needed more than a provider swap.

### Why a separate procedure

Removal is one unambiguous operation. It needs no prompt, and offering one is
actively harmful: measured on a real photo, prompted inpainting invented a whole
new person sitting in a red seat, while the erase model simply removed the
subject. A separate menu item also means no mode toggle - select, run, done.

### What makes it better than prompted inpainting

Measured on a real 2048x1536 photo with a 160x159 selection over a
motion-blurred person:

| Variant | Result |
|---|---|
| **erase, ~1MP native window, grow_mask 5** | Clean removal, backdrop and stage-light line continue |
| erase, same window, grow_mask 20 | Mask grew far enough to swallow neighbours; model reconstructed them |
| erase, 1536x1536 window, grow_mask 10 | Worse - invented a black object and a figure |
| inpaint + prompt, same window | Hallucinated an entire new person in a red seat |
| the old pipeline (288px crop upscaled 3.56x, inpaint + prompt) | Hallucinated a second theatre: proscenium arch, rows of empty seats |

Three things follow from that:

1. **Use the erase model.** `stability.stable-image-erase-object-v1:0` takes no
   prompt and fills from surrounding context. Prompted inpainting invents.
2. **Never upscale.** `calculate_native_context()` takes a window of about one
   megapixel at native resolution and only ever scales down. The old path
   upscaled a 288x287 crop 3.56x, so 92% of what the model saw was interpolated
   and it generated content to match that softness.
3. **Do not exceed ~1MP.** The 1536x1536 window was measurably worse: Stability
   downsamples internally above about a megapixel, so the extra window size buys
   nothing and costs native detail.

### Variations

Three erasures are generated per run, as three layers named `AI Erase 1 of 3`
onwards with only the first visible - the same idea as Photoshop's generative
fill alternatives. Each call gets its own random seed, which is what makes the
results differ.

They are worth having: on the test photo, one variation invented a garbled
string of text across a background sign while the other two were clean. With a
single result that would simply be the answer.

The calls run concurrently in a `ThreadPoolExecutor` inside the existing worker
thread. They are network-bound at roughly 15 seconds each, so three in sequence
would mean a 50 second wait; concurrently the whole run measured **17.2 seconds**
end to end in GIMP. A variation that fails is dropped and the remainder are
still placed, so throttling degrades the result rather than failing the run.

Count is `bedrock.erase_variations`, clamped to 1-4.

### Pasting back

Erase pastes the **whole context window** rather than masking the result to the
selection. The model returns the window with only the masked region changed
(measured: 35,491 of 1,048,576 pixels, about 3.4%) and has already blended
across the mask edge. Masking back to the selection rectangle discarded that
blending and left a visible rectangular seam - clearly present in the first
working version, and gone once the whole window was pasted.

## Bugs found

### 1. AI results were never placed into the image

Compositing placed the result with a Gegl chain:
`gegl:buffer-source -> gegl:translate -> gegl:write-buffer`, then
`merge_shadow()`. Under GIMP 3.2 that chain **writes nothing at all**.

Measured with `tests/probe_placement.py`, placing a known red 1024x1024 patch at
(81,375) in a 2048x1536 layer:

| Method | Pixels placed | Location |
|---|---|---|
| Gegl translate + write-buffer | **0** | nothing written |
| `Layer.new_from_drawable` + `set_offsets` | 1,048,576 | exactly (81,375)-(1104,1398) |

This is also why an earlier attempt to fix bug 2 with `gegl:translate` had no
effect. Placement now uses the layer copy, and the end-to-end erase went from
0 pixels changed to 35,841 changed in the right place.

### 2. Inpainting masks were empty unless the selection was in the top-left corner

**This was pre-existing and affected the OpenAI path identically.** It is not a
Bedrock bug; Bedrock is just what exposed it.

The mask was built by compositing the full-image selection channel into an
extract-region-sized buffer through a GEGL graph. Both buffers sit at origin
`(0,0)`, so the composite sampled the image's top-left corner rather than the
extract region. For a selection at (513,808) in a 2048x1536 image with an
extract region at (449,744), the selection fell entirely outside the sampled
area. The resulting mask was uniformly black — "inpaint nothing" — and the model
dutifully returned the image unchanged.

Measured, same inputs before and after:

| | white (inpaint) pixels | location |
|---|---|---|
| before | 4,096 | padding rows only |
| after | 320,352 | (228,230)–(795,793) |

Expected region was (227,229)–(796,795). 320,352 ≈ 568×564, which is the
160×159 selection scaled by 3.556. The 4,096 before is exactly `1024 × 2 + 1024 × 2`,
the padding rows — nothing from the selection at all.

The fix builds the mask with plain GIMP fills on `image.duplicate()`, which
carries the selection with it, then crops to the extract region. No coordinate
arithmetic to get wrong.

An initial attempt to offset the channel with `gegl:translate` did not work and
was abandoned in favour of the rewrite.

### 3. `gegl:color-to-alpha` silently produced no transparency

The step meant to make the selection transparent produced **zero** transparent
pixels. This was invisible while bug 1 was present, because there was no white
in the mask for it to act on. The selection is now cleared to transparency
directly with `edit_clear()`, which removes GEGL from the mask path entirely.

### 4. Mask padding was left transparent

Padding added to reach the target aspect ratio was transparent, which reads as
"inpaint this" in both conventions. It is now filled black (preserve).

### 5. The prompt and composite dialogs demanded an OpenAI key regardless of provider

`_get_api_key()` only ever reads `config["openai"]["api_key"]`. Three gates in
`_show_prompt_dialog` and two in `_show_composite_dialog` called it
unconditionally, so with Bedrock selected and no OpenAI key the dialog opened
Settings, found no OpenAI key, and looped forever. Saving the provider could not
break the loop because nothing re-read it afterwards.

All credential gates now route through `_credentials_ready(provider)`, and the
dialog re-reads the provider after Settings closes.

### 6. Result layers had no alpha before being grown to canvas size

`resize_to_image_size()` on a layer without an alpha channel fills the new area
with opaque background rather than transparency, which hid the entire rest of
the photo behind a white rectangle. Only visible once the selection layer mask
stopped covering it up.

### 7. Erase Object crashed on its own progress dialog

`_create_progress_widget()` returns a `(frame, label)` tuple, but the new
procedure unpacked it as a single widget:
`TypeError: argument child: Expected Gtk.Widget, but got tuple`. The headless
test called the helpers directly and never ran the procedure, so the UI wiring
was untested. The test now drives `run_erase_object` itself.

### 8. Result dimensions were assumed to match the requested size

`_download_and_composite_result` cropped padding at absolute pixel offsets valid
only at the requested size. If a provider returned a different resolution the
crop was wrong. It now rescales to the requested shape first. Latent for OpenAI,
fixed for both.

## Mask conventions

The two providers disagree, which is why `_convert_mask_to_luminance` exists:

| Provider | Inpaint area | Preserve area |
|---|---|---|
| OpenAI | transparent | opaque black |
| Bedrock / Stability | white | black |

The conversion flattens the mask onto white. Feathered selection edges survive
as greyscale, which Stability reads as partial inpaint strength.

## Verification

What was actually run, as opposed to reasoned about:

- **`python3 bedrock_client.py`** — signing, credential source precedence,
  inference profile prefixes, request body. Deterministic signature pinned
  against a fixed clock.
- **Live Bedrock calls** — 1024x1024 and 1536x1024 both round-trip at matching
  dimensions. Mask polarity confirmed with a known circular mask: the inpaint
  landed inside the circle, background preserved.
- **`tests/test_gimp_pipeline.py`** — runs the real mask and layer-flattening
  code inside headless GIMP. Asserts the selection lands in the mask, in the
  right place, with the right polarity for both providers, and that flattening
  produces one correctly sized image with the upper layer present in it. All
  eight checks pass. The pre-fix code measured 0 transparent and 4,096
  white-in-padding, so the mask checks discriminate.
- **End-to-end** — the plugin's actual mask bytes sent to Bedrock with the prompt
  "a large red apple": painted precisely inside the masked region, background
  untouched.
- **Settings dialog** — rendered against a real display and measured; also driven
  programmatically to confirm credentials persist, that a blank secret field
  preserves the stored value, and that Clear removes everything.
- **Sandbox behaviour** — confirmed from inside the snap that `~/.aws` is
  unreadable, that `AWS_*` environment variables pass through, that
  `bedrock_client` imports under GIMP's Python 3.12.3, and that TLS to
  `bedrock-runtime` connects.
- **`tests/test_generate_in_gimp.py`** — generation end to end in GIMP: the
  model id is unprefixed, the canvas maps to the right aspect ratio, the call
  returns PNG data, and it lands as a layer. Ran against a real prompt and
  produced a correct 1344x768 landscape image for a 1600x900 canvas.
- **`tests/test_upscale_in_gimp.py`** — all three variants against a real photo:
  input respects each variant's limits, the call succeeds, the response is under
  the payload cap, GIMP loads the WebP, and the result is larger with the aspect
  ratio preserved. It also builds the upscale dialog and dismisses it on a timer,
  with no API calls - that check exists because a helper returning a tuple and
  being unpacked as a single widget has already shipped once.
- **`tests/test_fit_in_gimp.py`** — 25 checks over five canvas/generation size
  combinations (enlarge, reduce, portrait, exact match): the layer covers the
  canvas, one axis matches exactly, aspect ratio is preserved, and it is centred
  with no gap. No API calls, so it is cheap to run.
- **`tests/test_native_context.py`** — 17 checks on the context window: never
  upscales, stays inside the image for edge and corner selections, scales
  oversized windows down to about the target area, keeps the aspect ratio within
  what Stability accepts.
- **Erase, end to end in GIMP** against the real photo: the photobomber is gone,
  35,841 pixels changed inside the context window, no seam, and the stage-light
  line and backdrop run continuously through the repaired area. Before the
  placement fix the same run changed **0** pixels.
- **`run_erase_object` itself**, called as a procedure with a stub `procedure`
  object, so the dialog wiring is covered too. Testing only the helpers let a
  widget/tuple mismatch reach the user once; the test now drives the real entry
  point.
- **Variations** — three layers created, named and ordered correctly, only the
  first visible, and their pixel contents genuinely differ (sampled sums 116945
  / 118638 / 116814). That last check matters: if the seeds had no effect the
  result would be three identical layers and no error. Whole run measured at
  **17.2 seconds** for three concurrent calls.

**Not verified:** the composite and inpaint procedures driven through the
interactive GIMP UI - only `run_erase_object` is called as a whole procedure.
Everything below that (context window, mask, API call, placement, compositing)
is exercised end to end against a real photo. The Bedrock layer composite path
is verified up to the request payload, and its request shape is identical to
inpainting, which was verified against the live API at 1024x1024 and 1536x1024 -
but the full composite has not been run end to end.

## Running the tests

```bash
# Offline: signing, credentials, request building
python3 bedrock_client.py

# Offline: coordinate transformations (unchanged, pre-existing)
python3 tests/run_tests.py

# Syntax
python3 -m py_compile gimp-ai-plugin.py coordinate_utils.py bedrock_client.py

# Offline: native-resolution context window
python3 tests/test_native_context.py

# Inside GIMP: mask construction, polarity and layer flattening
gimp -i --batch-interpreter=python-fu-eval \
     -b "import runpy; runpy.run_path('$PWD/tests/test_gimp_pipeline.py')"
cat tests/mask_test_result.txt

# Inside GIMP: full erase path against a photo (makes live Bedrock calls)
ERASE_TEST_PHOTO=/path/to/photo.jpg ERASE_TEST_SELECTION=x1,y1,x2,y2 \
gimp -i --batch-interpreter=python-fu-eval \
     -b "import runpy; runpy.run_path('$PWD/tests/test_erase_in_gimp.py')"
cat tests/erase_test_result.txt
```

The GIMP test writes its results to a file because GIMP exits at the end of a
batch run. It rebinds the plugin's methods onto a plain object: instantiating
`GimpAIPlugin` outside the plug-in protocol aborts GIMP with
`gimp_plug_in_constructed: assertion failed`.

## Configuration

```json
{
    "api_provider": "bedrock",
    "bedrock": {
        "region": "us-east-1",
        "model_id": "stability.stable-image-inpaint-v1:0",
        "access_key_id": "",
        "secret_access_key": "",
        "api_key": "",
        "profile": "",
        "negative_prompt": "",
        "seed": 0,
        "grow_mask": 5,
        "erase_variations": 3,
        "inpaint_variations": 3
    }
}
```

| Key | Default | Applies to |
|---|---|---|
| `region` | `us-east-1` | inpaint, erase, upscale |
| `model_id` | inpaint model | Inpainting |
| `erase_model_id` | erase model | Erase Object |
| `generation_region` | `us-west-2` | Generate Layer, Generate New Image |
| `generation_model_id` | SD 3.5 Large | Generate Layer, Generate New Image |
| `erase_variations` | Erase Object |
| `grow_mask` | inpaint, erase |
| `upscale_variant` | `conservative` | Generative Upscale |
| `upscale_creativity` | 0.35 | Conservative and Creative upscale |
| `seed`, `negative_prompt` | 0, empty | most models |
| `api_key`, `access_key_id`, `secret_access_key`, `profile` | unset | credentials |

`grow_mask` (0-20) expands and softens the mask edge; 5 measured better than 20,
which grew far enough to swallow neighbouring subjects. `erase_variations` (1-4)
is how many alternative erasures Erase Object produces; `inpaint_variations`
(1-4) does the same for Inpainting. `generation_region` and
`generation_model_id` are separate because generation is not available in the
region the editing models use.

## Known dead code

`_generate_gpt_image_layer_threaded` has no callers - the live generation path
is `_call_openai_generation` via `_call_generation_threaded`. It was left in
place rather than deleted, but it is not the function to edit when changing
generation behaviour.

Blank values fall through to the next credential source. The geography prefix is
added to `model_id` automatically.

## Sandboxed GIMP

Snap and flatpak builds cannot read `~/.aws`. The snap grants `$HOME/.config/GIMP`
through a `personal-files` interface, and the `home` interface covers only
non-hidden files, so the `~/.aws` fallback never fires. Verified directly:

```
$ snap run --shell gimp -c 'cat ~/.aws/config >/dev/null 2>&1 && echo CAN || echo CANNOT'
CANNOT read ~/.aws
```

Two options: type the credentials into Settings, or launch GIMP from a terminal
with `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` exported. Values typed into
Settings take precedence over the environment.

Note also that a sandboxed GIMP has a private `/tmp`. Debug images written with
debug mode on land in `/tmp/snap-private-tmp/snap.gimp/tmp/`, not the host `/tmp`.
