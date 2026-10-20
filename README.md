# GIMP AI Plugin Beta

A Python plugin for GIMP 3.0.4+ that integrates AI image generation capabilities directly into GIMP. This is a **beta release** seeking testers on all platforms.

Supports **OpenAI's GPT-Image models** (GPT-Image-2.5 Flare and Sunburst, plus the legacy gpt-image-1) and **Amazon Bedrock** (Stability AI) as alternative providers. Object removal and generative upscaling are Bedrock-only, since OpenAI has no equivalent models.

## ✨ Features

- **🎨 AI Inpainting**: Fill selected areas with AI-generated content using text prompts and selection masks
- **🧹 AI Erase Object**: Remove something from a photo and fill the gap from its surroundings - select it, run it, no prompt, three alternatives to choose from
- **🔍 AI Generative Upscale**: Enlarge the active layer with a model instead of plain resampling - Fast, Conservative or Creative
- **🖼️ AI Image Generation**: Create images from text descriptions - as a new layer or as a new image, optionally guided by reference images
- **🔄 AI Layer Composite**: Intelligently blend AI content into existing images
- **⚙️ Easy Configuration**: Built-in settings dialog (`Filters → AI → Settings...`), no external config files needed
- **☁️ Choice of provider**: OpenAI or Amazon Bedrock, chosen in Settings

## 📋 Requirements

- **GIMP 3.0.4 or newer** (all GIMP 3.X versions from 3.0.4 onwards)
- **Internet connection** for AI API calls
- **OpenAI API key** (get one at [platform.openai.com](https://platform.openai.com)), or **AWS credentials with Amazon Bedrock access**. Erase Object and Generative Upscale require Bedrock
- **Zero external dependencies** - uses only Python standard library + GIMP APIs

## 🚀 Installation

**Just 3 files to copy!** No external dependencies or complex setup.

### Easiest Way: Automated Installer 🎯

1. **Download** the [latest release ZIP](https://github.com/lukaso/gimp-ai/releases)
2. **Extract** the ZIP file
3. **Run** the installer: `python3 install_plugin.py`
4. **Restart GIMP** and configure your API key

Done! The installer handles everything automatically.

### Manual Install (3 Simple Steps)

1. **Download 3 files**: `gimp-ai-plugin.py`, `coordinate_utils.py` and `bedrock_client.py`
2. **Create folder**: Make a `gimp-ai-plugin` folder in your GIMP plug-ins directory
3. **Copy files**: Put all three files in that folder, restart GIMP

### Need Help? 📖

**👉 [Read the Complete Installation Guide (INSTALL.md)](INSTALL.md) 👈**

The installation guide includes:

- ✅ Step-by-step instructions for beginners
- ✅ How to find your GIMP plugin directory on Windows/Mac/Linux
- ✅ Screenshots and visual examples
- ✅ How to get and configure your OpenAI API key
- ✅ Troubleshooting for "Filter → AI not found" issues

### Quick Reference

**Plugin folder location by OS** (replace `3.0` with your GIMP version):

- **Windows**: `%APPDATA%\GIMP\3.0\plug-ins\gimp-ai-plugin\`
- **macOS**: `~/Library/Application Support/GIMP/3.0/plug-ins/gimp-ai-plugin/`
- **Linux**: `~/.config/GIMP/3.0/plug-ins/gimp-ai-plugin/`

> **Note**: The automated installer will detect all compatible GIMP versions (3.0.4+) and let you choose which one to install to.

**Required folder structure:**

```
plug-ins/
└── gimp-ai-plugin/          ← Create this folder
    ├── gimp-ai-plugin.py    ← Required file #1
    ├── coordinate_utils.py  ← Required file #2
    └── bedrock_client.py    ← Required file #3
```

**After copying files:**

- Linux/macOS: Run `chmod +x gimp-ai-plugin.py` in the folder
- Restart GIMP completely
- Look for `Filters → AI` in the menu

## ⚙️ Configuration

### OpenAI

1. **Get an OpenAI API key** from [platform.openai.com](https://platform.openai.com)
2. In GIMP: go to `Filters → AI → Settings...`
3. **Paste your API key** (starts with `sk-`)
4. **Pick the image model**:
   - **GPT-Image-2.5 Flare** - fast, the default
   - **GPT-Image-2.5 Sunburst** - slower, more precise
   - **GPT-Image-1** - legacy; only three fixed sizes (1024x1024, 1536x1024, 1024x1536)
5. Click Save - it's stored automatically!

The GPT-Image-2.5 models accept any size in multiples of 16, so inpainting
works at the native resolution of your image instead of being scaled to a fixed
shape. They also tend to paint *around* a masked hole rather than into it, so
the plugin sends the selection once more as a white-on-black image and tells the
model to work inside it. Set `"mask_hint": false` in the `openai` section of
`config.json` to turn that off.

> **First time?** See [INSTALL.md](INSTALL.md) for detailed API key instructions.

### Amazon Bedrock

Bedrock serves models on demand - there is no model-access request to submit
first, so this is just credentials and a region.

1. In GIMP: go to `Filters → AI → Settings...`
2. Set **Inpainting Provider** to *Amazon Bedrock*
3. Set the **AWS region** (default `us-east-1`)
4. Provide credentials one of these ways:
   - Type an **access key ID** and **secret access key** into the settings dialog.
     They are saved to `config.json`, which is written with mode 600. Use the
     *Clear Stored AWS Credentials* button to remove them again
   - Or paste a [Bedrock API key](https://docs.aws.amazon.com/bedrock/latest/userguide/getting-started-api-keys.html)
     instead of the two keys above
   - Or leave every credential field blank and let the plugin read your existing
     AWS credentials from the environment or `~/.aws` (`aws configure`)

> **Sandboxed GIMP**: snap and flatpak builds cannot read `~/.aws` at all - only
> `~/.config/GIMP` is visible to them. Either fill in the credential fields, or
> launch GIMP from a terminal with `AWS_ACCESS_KEY_ID` and
> `AWS_SECRET_ACCESS_KEY` exported.

#### Which model each feature uses

| Menu item | Model | Region | Override key |
|---|---|---|---|
| Inpainting | `stability.stable-image-inpaint-v1:0` | `region` | `model_id` |
| Layer Composite | `stability.stable-image-inpaint-v1:0` | `region` | `model_id` |
| Erase Object | `stability.stable-image-erase-object-v1:0` | `region` | `erase_model_id` |
| Generative Upscale - Fast | `stability.stable-fast-upscale-v1:0` | `region` | — |
| Generative Upscale - Conservative | `stability.stable-conservative-upscale-v1:0` | `region` | — |
| Generative Upscale - Creative | `stability.stable-creative-upscale-v1:0` | `region` | — |
| Generate Layer / Generate New Image | `stability.sd3-5-large-v1:0` | `generation_region` (`us-west-2`) | `generation_model_id` |

Everything except generation runs in the region you set in Settings
(default `us-east-1`). Generation is separate because Stability's text-to-image
models are not offered there; the same credentials work in both regions and
there is nothing extra to enable.

Every model above **except generation** is invoked through the cross-region
inference profile for your region, so the plugin prefixes the id with `us.`,
`eu.` or `apac.` automatically - `us.stability.stable-image-inpaint-v1:0` and so
on. Generation is invoked on-demand and rejects a prefixed id, so it is sent
bare. You do not need to do anything about this; it is only worth knowing if you
override a model id and get "The provided model identifier is invalid."

Stability's older SDXL 1.0 model (`stability.stable-diffusion-xl-v1`) is no
longer offered on Bedrock, which is why none of these use it.

Layer composite behaves differently on the two providers. OpenAI receives each
selected layer as a separate image and can use one as a reference for another.
Bedrock's inpainting model takes a single image, so the selected layers are
flattened by GIMP first and the model only ever sees the combined pixels; a
selection mask is required to tell it which region to work on.

## 🎨 Usage

### AI Inpainting

**Fill selected areas with AI-generated content using text prompts**

1. **Open image** in GIMP
2. **Make a selection** of the area to inpaint (required)
3. **Go to** `Filters → AI → Inpainting`
4. **Enter prompt** (e.g., "blue sky with clouds", "remove the object")
5. **Choose processing mode**:
   - **🔍 Focused (High Detail)**: Best for small edits - the model sees a window around the selection
   - **🖼️ Full Image (Consistent)**: Best for large changes - the model sees the whole image
6. **Clip result to selection** (on by default): untick to keep the edges the
   model drew outside the selection. The layer mask is still created, just
   disabled, so the clip is one click away (`Layer → Mask → Disable Layer Mask`) and
   the mask can be painted to allow only part of the overflow
7. **Reference images** (OpenAI only, optional): pick other open images for the
   model to draw on. Two slots are shown; **Add reference** adds more
8. **Result**: New layer with AI-generated content, masked to the selection

**Selection Mask Behavior:**

- **Soft masks**: AI can redraw content _outside_ the selection to maintain visual coherence
- **With selection**: Final result is masked to show only within selected area, but AI considers surrounding context
- **Smart feathering**: Automatic edge blending for seamless integration

⚠️ **Important**: The AI model may modify areas outside your selection to create coherent results. Only the final output is masked to your selection.

**On Amazon Bedrock** you get **three alternative results** as three layers
(`AI Inpaint 1 of 3` ...), only the first visible, generated concurrently. Each
is the whole context window pasted back without a selection mask: Stability only
changes the masked region, so this blends without a seam. Set
`inpaint_variations` in the `bedrock` section of `config.json` to change the
count (1-4).

If a request fails, the dialog stays open with the error and a **Try Again**
button, keeping your prompt and options.

### AI Erase Object

**Remove an unwanted object and fill the gap from the surrounding image**

1. **Select** the thing you want gone (any selection tool)
2. **Go to** `Filters → AI → Erase Object`

That is the whole workflow: no prompt, no options.

You get **three alternative erasures** as three layers, named `AI Erase 1 of 3`
through `3 of 3`, with only the first visible. Toggle layer visibility to compare
and keep the one you prefer. They are worth comparing: on a test photo one
variation invented a garbled bit of text on a background sign while the other two
were clean. The three run concurrently, so all three take about as long as one
(measured: 17 seconds).

Set `erase_variations` in the `bedrock` section of `config.json` to change how
many are generated (1-4). Removal is unambiguous, and
describing it only invites the model to invent replacement content instead of
removing.

Requires Amazon Bedrock as the provider - it uses Stability's purpose-built
object-erase model, which has no OpenAI equivalent.

Two things it does differently from Inpainting, both of which matter for
quality:

- The context window is taken at **native resolution**, sized to roughly one
  megapixel around the selection. Inpainting with gpt-image-1 snaps to its
  three fixed shapes, which for a small selection means upscaling a tight crop: a 160px selection became a
  288px crop blown up 3.56x, so 92% of what the model saw was interpolated.
- The whole window is pasted back rather than masked to the selection, so the
  blending the model already did across the mask edge is preserved instead of
  being cut off at the selection rectangle.

Tip: include a little of the object's shadow or contact points in the selection.
A mask that stops exactly at the outline tends to leave a ghost behind.

### AI Generative Upscale

**Enlarge a layer with a model rather than plain resampling**

1. **Select the layer** you want enlarged in the Layers dialog
2. **Go to** `Filters → AI → Generative Upscale`
3. **Pick a method** from the dropdown and optionally describe the image

It works on the **active layer**, not the whole image, so you can upscale one
layer and keep the original beside it for comparison.

| Method | Result | Time | Prompt |
|---|---|---|---|
| **Fast** | exactly 4x, but the input is capped at 1536px per side, so a large layer is reduced first | ~6s | ignored |
| **Conservative** | to about 4K, staying close to the original | ~32s | used |
| **Creative** | to about 4K, inventing extra detail | ~68s | used |

Measured on a 2048x1536 photo: Fast reduced it to 1182x886 and returned
4728x3544; Conservative and Creative took the full size and returned 3592x2696
and 3632x2728. So Fast gives the largest output but from a reduced input, while
the other two preserve what was there.

Conservative and Creative require a prompt; if you leave the box empty a neutral
one is used. The result is added as a new layer larger than the canvas - use
`Image → Fit Canvas to Layers` to see all of it.

Requires Amazon Bedrock as the provider.

### AI Image Generation

There are two entries:

- **`Filters → AI → Generate Layer`** - adds the result as a layer covering the
  canvas of the open image
- **`Filters → AI → Generate New Image`** (also `File → Create → Generate New
  Image`) - works with no image open; asks for a size and opens the result as a
  new image

1. **Go to** one of the entries above
2. **Enter prompt** (e.g., "a red dragon on mountain")
3. **Pick a size** (Generate New Image only). With GPT-Image-2.5 there are
   presets up to 4K and a custom size, rounded to multiples of 16; sizes above
   2560x1440 are marked experimental by OpenAI. gpt-image-1 and Bedrock offer
   the shapes their models support
4. **Reference images** (OpenAI only, optional): pick open images to guide the
   result - the request then goes to OpenAI's edits endpoint without a mask

Generation always produces a **whole-canvas layer** - it does not read your
selection. To generate into a selected area, use **Inpainting**, which is exactly
that operation: a prompt plus a selection mask.

Generation models return their own resolution (Stability gives about a megapixel
at the nearest aspect ratio it supports; gpt-image-1 one of three fixed sizes;
GPT-Image-2.5 the canvas aspect ratio, up to 2560x1440), which rarely matches
your canvas exactly. The generated layer is therefore scaled to **cover**
the canvas and centred: the tighter axis matches exactly and the other overhangs,
so there is no gap and the excess can be cropped or repositioned.

GIMP 3 has no Lanczos filter - it was replaced by NoHalo and LoHalo. The plugin
uses NoHalo when enlarging and LoHalo when reducing, which are the intended
modern equivalents.

On Bedrock, generation runs in its own region (`us-west-2` by default) because
Stability's generation models are not offered in the region the editing models
use. See [Configuration](#-configuration).

### AI Layer Composite

**Intelligently combines multiple visible layers using AI guidance**

1. **Set up your layers**:

   - **Bottom layer** = base/background (what gets modified)
   - **Upper layers** = elements to integrate (people, objects, etc.)
   - Make sure desired layers are **visible**

2. **Go to** `Filters → AI → Layer Composite`

3. **Enter integration prompt** (e.g., "blend the person naturally into the forest scene")

4. **Choose mode**:

   - **✅ Include selection mask**: Uses selection on base layer to limit where changes occur
   - **❌ No mask**: AI can modify the entire base layer to integrate upper layers

5. **Result**: A new layer is created, taking the base layer and intelligently modifying it to incorporate all visible layers

## 🐛 Find Issues?

- Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md) first
- Report at: [GitHub Issues](https://github.com/lukaso/gimp-ai/issues)

## 📚 Documentation

- **[INSTALL.md](INSTALL.md)** - Complete installation guide for users
- **[BEDROCK.md](BEDROCK.md)** - Amazon Bedrock support: models, API quirks, bugs found and how each was verified
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** - Common issues and solutions
- **[CHANGELOG.md](CHANGELOG.md)** - What's new and known issues
- **[TODO.md](TODO.md)** - Development roadmap
- **[RELEASE.md](RELEASE.md)** - Release process for maintainers

## 🔧 For Developers

### Creating Releases

Releases are automated via GitHub Actions. See **[RELEASE.md](RELEASE.md)** for details.

**Quick overview:**

- Label PRs with `major`, `minor`, or `patch` for version bumps
- Merge to `main` triggers automated release creation
- The workflow builds the package and creates a GitHub release

### Tools

- **`build_release.py`** - Creates distributable ZIP packages (used by workflow)
- **`tools/bump_version.py`** - Bumps version in `gimp-ai-plugin.py` (used by workflow)

### Tests

```bash
python3 bedrock_client.py          # signing, credentials, request bodies
python3 tests/run_tests.py          # coordinate transformations
python3 tests/test_native_context.py

# These run inside GIMP; some make live Bedrock calls
gimp -i --batch-interpreter=python-fu-eval \
     -b "import runpy; runpy.run_path('$PWD/tests/test_gimp_pipeline.py')"
```

See [BEDROCK.md](BEDROCK.md) for the full list and what each covers.

## ⚖️ License

MIT License - see [LICENSE](LICENSE) file for details
