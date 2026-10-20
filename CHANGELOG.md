# Changelog

All notable changes to the GIMP AI Plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Documentation moved to a GitHub Pages site built from `docs/`
  (https://etet100.github.io/gimp-ai-2/). The README is now a short overview
  with a quick install; INSTALL.md, TROUBLESHOOTING.md, BEDROCK.md,
  ALGORITHMS.md and RELEASE.md live under `docs/`
- The release ZIP no longer includes INSTALL.md and TROUBLESHOOTING.md;
  QUICK_START.txt and the installer link to the online guides instead

## [0.15.0] - 2026-10-20

### Added

- **GPT-Image-2.5 models for OpenAI**: an *Image model* picker in Settings
  offers GPT-Image-2.5 Flare (fast, the new default), GPT-Image-2.5 Sunburst
  (slower, more precise) and GPT-Image-1 (legacy). Stored as `openai.model`;
  a hand-edited model id is kept selectable rather than lost

  - The 2.x models take any size in multiples of 16 (up to 3840px per side,
    3:1 aspect, 655,360-8,294,400 pixels) instead of gpt-image-1's three fixed
    shapes, so requests are sized by `get_gpt_image_shape()` in
    `coordinate_utils.py`. Content is kept at native resolution and only
    reduced above 2560x1440, which OpenAI marks as experimental
  - Focused inpainting on the 2.x models uses the same ~1MP native-resolution
    context window as Bedrock, instead of blowing a tight crop up to 1024px
  - **Mask hint**: the 2.x models see the edits mask as a blacked-out hole and
    tend to paint around it, so the selection is also sent as a white-on-black
    image and the prompt points at it. On by default for 2.x, off for
    gpt-image-1; override with `openai.mask_hint`
  - `input_fidelity` is sent to gpt-image-1 only; the 2.x models reject it
  - Request timeout raised to 300s, since the newer models are slower

- **Reference images (OpenAI)**: Inpainting, Generate Layer and Generate New
  Image can take other open images as references. Pick them from thumbnail
  dropdowns; more slots can be added up to the API's 16 input images (the
  edited image and mask hint count towards that). References are flattened and
  reduced to 1536px on the longest side, and the prompt tells the model which
  input image is which. Last choices are remembered while the images stay open

- **Generate New Image** (`Filters → AI → Generate New Image`, also
  `File → Create → Generate New Image`): available with no image open; asks for
  a size and opens the result as a new image. With GPT-Image-2.x it offers
  presets up to 4K plus a custom size; gpt-image-1 and Bedrock offer the shapes
  those models support

- **`Filters → AI → Settings...`** opens the settings dialog directly, also with
  no image open

- **Inpainting variations on Bedrock**: three alternative results per run as
  separate layers (`AI Inpaint 1 of 3` ...), run concurrently. The whole
  context window is pasted back without a selection-shaped layer mask, which
  removes the rectangular seam. Configurable via `bedrock.inpaint_variations`
  (1-4). OpenAI keeps a single call, since its edits endpoint regenerates the
  whole window

- **Clip result to selection** option in the inpainting dialog. Unticked, the
  layer mask is kept but disabled, so edges the model drew outside the selection
  stay visible and the clip is one click away

- **Try Again** after a failed request: the dialog stays open with the error
  shown and the prompt and options kept, so a transient error (throttling,
  timeout) or a missing selection can be fixed and retried without starting over

- Every dialog shows which provider, model and main parameters the operation
  will use, refreshed when Settings change

- **Amazon Bedrock inpainting provider**: inpainting can now run through Amazon
  Bedrock instead of OpenAI, using Stability AI's inpainting model
  (`stability.stable-image-inpaint-v1:0`) via its cross-region inference profile.

  - Applies to **AI Inpainting**, **AI Layer Composite**, image generation
    and the new **AI Erase Object**
  - Provider is selected in `Filters → AI → Settings`
  - Credentials come from an access key/secret typed into Settings, a Bedrock
    API key, environment variables, or `~/.aws`
  - Requests are SigV4-signed with the standard library, so boto3 is not needed
  - New required file: `bedrock_client.py`

- **AI Erase Object** (`Filters → AI → Erase Object`): select an object, run it,
  done. No prompt and no options - removal is unambiguous, and asking the user to
  describe it only invites the model to invent content instead of removing.

  - Uses Stability's purpose-built `stability.stable-image-erase-object-v1:0`
  - Takes its context window at **native resolution** rather than snapping to
    the OpenAI shapes. The old path upscaled a 288x287 crop 3.56x to reach
    1024x1024, so 92% of what the model saw was interpolated
  - Pastes the whole context window back instead of masking to the selection,
    which preserves the blending the model already did across the mask edge
  - Produces three alternative erasures as three layers (only the first
    visible), run concurrently so all three take about as long as one -
    measured at 17s. Configurable via `bedrock.erase_variations` (1-4)

- **Bedrock image generation** via `stability.sd3-5-large-v1:0`. Note that
  Stability's generation models are only offered in `us-west-2`, not the
  `us-east-1` where the editing models live, so generation has its own
  `bedrock.generation_region` setting. They are also invoked on-demand with a
  bare model id: an inference-profile prefix is rejected with "The provided
  model identifier is invalid."

- **AI Generative Upscale** (`Filters → AI → Generative Upscale`): enlarges the
  **active layer** using Stability's upscale models, chosen from a dropdown -
  Fast (4x, ~6s), Conservative (~4K, ~32s) or Creative (~4K, ~68s).

  - Fast accepts only `image` and `output_format`; any other field, `seed`
    included, is rejected outright
  - Fast also caps input at 1536px per side, so larger layers are reduced first
  - Conservative and Creative require a non-empty prompt; a neutral default is
    substituted when the user leaves it blank
  - All three request WebP: a large PNG response exceeds Bedrock's 16MB payload
    cap (Fast hit it at 4728x3544, 38.8MB)

### Changed

- **Image Generator is now Generate Layer** (`Filters → AI → Generate Layer`).
  It needs an open image but no selected layer, and adds a layer that covers
  the canvas. With GPT-Image-2.x the layer is requested at the canvas's own
  aspect ratio instead of the nearest of three shapes
- Full Image inpainting still sends the whole image, but its result layer is
  now masked to the selection as in Focused mode
- Dialogs were redesigned: numbered sections with icons, a larger prompt box,
  and a status area with a bold headline and wrapping, selectable details so
  long API errors can be read and copied. Styling follows the GIMP theme
- Masks of 4 MB or more are rejected before upload with a clear message,
  instead of failing at the OpenAI edits endpoint
- Generated layers are now scaled to cover the canvas and centred, instead of
  being dropped in at the model's own resolution and leaving a gap. Uses NoHalo
  for enlarging and LoHalo for reducing (GIMP 3 has no Lanczos)

- Settings dialog is now two columns and scrollable, so the Save button stays
  reachable on 1080p and smaller screens
- `config.json` is written with mode 600, since it can hold API keys and AWS secrets

### Fixed

- **AI results were never actually placed into the image.** Compositing used a
  Gegl `buffer-source -> translate -> write-buffer` chain which writes nothing at
  all under GIMP 3.2 - measured directly at zero pixels - so the result layer
  stayed empty. Results are now placed with `Layer.new_from_drawable()` plus
  `set_offsets()`, which was measured placing all 1,048,576 pixels at the exact
  expected coordinates. Affected every mode and both providers
- Result layers had no alpha channel before being grown to canvas size, so the
  new area was filled with opaque background and hid the rest of the image
- Erase Object unpacked `_create_progress_widget()` as a single widget when it
  returns a `(frame, label)` tuple, crashing on its own progress dialog
- **Inpainting masks were empty unless the selection touched the top-left
  corner of the image.** The mask was built by compositing the full-size
  selection channel into an extract-region-sized buffer at the origin, so the
  selection fell outside the mask and every inpaint was told to change nothing.
  Masks are now built with GIMP fills on a duplicate of the image, which carries
  the selection, and cropped to the extract region afterwards. This affected the
  OpenAI path too
- Mask padding was left transparent, which reads as "inpaint this"; it is now
  filled black (preserve) to match the surrounding context
- Credential checks in the prompt dialog demanded an OpenAI key even when the
  provider was Bedrock, which trapped the dialog in a settings loop
- AI results whose dimensions differ from the size requested are now rescaled
  before padding is cropped, instead of being cropped at the wrong offsets

### Added (tests)

- `tests/test_gimp_pipeline.py` runs inside GIMP headless and asserts the mask
  actually contains the selection, in the right place, with the right polarity
  for each provider, and that layer flattening for Bedrock composites produces
  one correctly sized image containing the upper layer
- `tests/test_native_context.py` covers the native-resolution context window
- `tests/test_erase_in_gimp.py` runs the full erase path against a real photo
- `tests/probe_placement.py` compares layer placement methods inside GIMP; it is
  what identified the Gegl no-op

### Notes

- Stability generation takes an aspect ratio, not pixel dimensions, and returns
  about a megapixel at that ratio. The canvas shape is mapped to the nearest
  ratio it accepts, so a 4:3 canvas becomes 5:4 rather than 3:2
- Layer composite works differently on Bedrock: its inpainting model takes a
  single image, so the selected layers are flattened by GIMP first and the model
  only sees the combined pixels. A selection mask is required
- Stability's SDXL 1.0 (`stability.stable-diffusion-xl-v1`) is no longer
  available on Bedrock, so the current Stability inpainting model is used

## [0.9.0] - [0.14.0] - 2025-11-16 / 2025-11-17

Release-tooling versions, bumped automatically by the release workflow.

### Added

- `VERSION` constant in `gimp-ai-plugin.py`, `tools/bump_version.py` and the
  GitHub Actions bump-and-release workflow (see `RELEASE.md`)
- Automated installer `install_plugin.py`, shipped in the release ZIP

### Fixed

- Installer detects every installed GIMP 3.x version and lets the user choose,
  defaulting to the latest stable one (0.12.0)
- Debug images are saved to the system temp directory instead of a hard-coded
  `/tmp`, which does not exist on Windows; coordinate tests updated (0.14.0)
- Version bumping and release workflow fixes (0.9.0 - 0.11.0)

## [0.8.0-beta] - 2024-10-04

### 🎉 Initial Beta Release

#### Added

- **AI Inpainting**: Fill selected areas with AI-generated content using text prompts

  - Context-aware inpainting that considers surrounding image content
  - Support for complex selections and masks
  - Intelligent coordinate transformation for optimal AI processing

- **AI Image Generation**: Create new images from text descriptions

  - Generates images as new layers in current document
  - Respects GIMP canvas dimensions and aspect ratios
  - Multiple size options with automatic optimization

- **AI Layer Composite**: Intelligently blend new content into existing images

  - Seamless integration of AI-generated content
  - Automatic color and lighting matching
  - Preserves image quality and coherence

- **Settings Management**: User-friendly configuration through GIMP dialogs

  - Secure API key storage in GIMP preferences
  - No external configuration files needed
  - Multiple AI provider support (OpenAI ready, extensible)

- **Robust Processing Pipeline**:

  - Threaded API calls with progress indicators
  - Cancellable operations
  - Memory-efficient image handling
  - Error recovery and user feedback

#### Technical Features

- **Zero External Dependencies**: Uses only Python standard library and GIMP APIs
- **Cross-Platform**: Designed for macOS, Linux, and Windows
- **GIMP 3.x Compatible**: Works with GIMP 3.0.4+ and 3.1.x

#### Known Issues/Limitations

- Debug output still visible in GIMP Console
- Windows and Linux installation paths need validation from users
- Large image processing (>2048px) may be slow on some systems
- Limited to OpenAI
- Resolution limited by OpenAI constraints (1024px, 1536px)

#### Testing Status

- ✅ **macOS**: Fully tested on Apple Silicon and Intel Macs
- ❓ **Linux**: Needs beta testing on Ubuntu/Fedora
- ❓ **Windows**: Needs beta testing on Windows 10/11

### 🔧 Developer Notes

- Plugin architecture supports easy addition of new AI providers
- Coordinate transformation system handles complex image manipulations
- Comprehensive test suite for coordinate calculations
- Modular design for future feature additions

---

## Beta Testing

This is a beta release. Please report:

1. Installation success/failure
2. Plugin visibility in GIMP menus
3. Any crashes or errors
4. Performance with different image sizes
5. General usability feedback

**Report issues at**: [GitHub Issues](https://github.com/lukaso/gimp-ai/issues)

---

### Support

- **Requirements**: GIMP 3.0.4 or newer, Internet connection
- **Installation**: Copy 3 files to GIMP plugins directory, or run `install_plugin.py`
- **Configuration**: `Filters > AI > Settings...` in GIMP
- **Documentation**: See README.md and TROUBLESHOOTING.md
