# Changelog

All notable changes to the GIMP AI Plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Amazon Bedrock inpainting provider**: inpainting can now run through Amazon
  Bedrock instead of OpenAI, using Stability AI's inpainting model
  (`stability.stable-image-inpaint-v1:0`) via its cross-region inference profile.

  - Applies to **AI Inpainting**, **AI Layer Composite** and the new
    **AI Erase Object**
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

### Changed

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

- Image generation remains OpenAI-only
- Layer composite works differently on Bedrock: its inpainting model takes a
  single image, so the selected layers are flattened by GIMP first and the model
  only sees the combined pixels. A selection mask is required
- Stability's SDXL 1.0 (`stability.stable-diffusion-xl-v1`) is no longer
  available on Bedrock, so the current Stability inpainting model is used

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

**Report issues at**: [GitHub Issues](https://github.com/yourusername/gimp-ai/issues)

---

### Support

- **Requirements**: GIMP 3.0.4+ or 3.1.x, Internet connection
- **Installation**: Copy 2 files to GIMP plugins directory
- **Configuration**: Use `Filters > AI` in GIMP and chose one of the options, then click `Settings` in the dialog
- **Documentation**: See README.md and TROUBLESHOOTING.md
