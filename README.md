# GIMP AI Plugin Beta

A Python plugin for GIMP 3.0.4+ that integrates AI image generation capabilities directly into GIMP. This is a **beta release** seeking testers on all platforms.

Supports **OpenAI's gpt-image-1** models for inpainting and image generation, and **Amazon Bedrock** (Stability AI inpainting) as an alternative provider for inpainting and layer compositing.

## ✨ Features

- **🎨 AI Inpainting**: Fill selected areas with AI-generated content using text prompts and selection masks
- **🧹 AI Erase Object**: Remove something from a photo and fill the gap from its surroundings - select it, run it, no prompt, three alternatives to choose from
- **🖼️ AI Image Generation**: Create new images from text descriptions as new layers
- **🔄 AI Layer Composite**: Intelligently blend AI content into existing images
- **⚙️ Easy Configuration**: Built-in settings dialog, no external config files needed
- **☁️ Choice of provider**: Inpaint and composite through OpenAI or Amazon Bedrock

## 📋 Requirements

- **GIMP 3.0.4 or newer** (all GIMP 3.X versions from 3.0.4 onwards)
- **Internet connection** for AI API calls
- **OpenAI API key** (get one at [platform.openai.com](https://platform.openai.com)), or **AWS credentials with Amazon Bedrock access** if you inpaint through Bedrock
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
3. **Copy files**: Put both files in that folder, restart GIMP

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
2. In GIMP: go to `Filters → AI → Settings`
3. **Paste your API key** (starts with `sk-`)
4. Click OK - it's saved automatically!

> **First time?** See [INSTALL.md](INSTALL.md) for detailed API key instructions.

### Amazon Bedrock (inpainting only)

1. **Request model access** for Stability AI's inpainting model in the
   [Bedrock console](https://console.aws.amazon.com/bedrock/) under Model access
2. In GIMP: go to `Filters → AI → Settings`
3. Set **Inpainting Provider** to *Amazon Bedrock*
4. Set the **AWS region** you enabled the model in (default `us-east-1`)
5. Provide credentials one of these ways:
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

The default model is `stability.stable-image-inpaint-v1:0`, invoked through the
cross-region inference profile for your region (`us.`, `eu.` or `apac.`), which
the plugin adds automatically. Stability's older SDXL 1.0 model
(`stability.stable-diffusion-xl-v1`) is no longer offered on Bedrock.

Bedrock serves both **AI Inpainting** and **AI Layer Composite**. Image
generation is OpenAI-only, since it needs a text-to-image model rather than an
inpainting one.

Layer composite behaves differently on the two providers. OpenAI receives each
selected layer as a separate image and can use one as a reference for another.
Bedrock's inpainting model takes a single image, so the selected layers are
flattened by GIMP first and the model only ever sees the combined pixels; a
selection mask is required to tell it which region to work on.

## 🎨 Usage

### AI Inpainting

**Fill selected areas with AI-generated content using text prompts**

1. **Open image** in GIMP
2. **Make a selection** of area to inpaint (or no selection for full image)
3. **Go to** `Filters → AI → Inpainting`
4. **Choose processing mode**:
   - **🔍 Focused (High Detail)**: Best for small edits, maximum resolution, selection required
   - **🖼️ Full Image (Consistent)**: Best for large changes, works with or without selection
5. **Enter prompt** (e.g., "blue sky with clouds", "remove the object")
6. **Result**: New layer with AI-generated content, automatically masked to selection area

**Selection Mask Behavior:**

- **Soft masks**: AI can redraw content _outside_ the selection to maintain visual coherence
- **With selection**: Final result is masked to show only within selected area, but AI considers surrounding context
- **No selection** (Full Image mode): Entire image is processed and replaced
- **Smart feathering**: Automatic edge blending for seamless integration

⚠️ **Important**: The AI model may modify areas outside your selection to create coherent results. Only the final output is masked to your selection.

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
  megapixel around the selection. Inpainting snaps to the OpenAI shapes, which
  for a small selection means upscaling a tight crop: a 160px selection became a
  288px crop blown up 3.56x, so 92% of what the model saw was interpolated.
- The whole window is pasted back rather than masked to the selection, so the
  blending the model already did across the mask edge is preserved instead of
  being cut off at the selection rectangle.

Tip: include a little of the object's shadow or contact points in the selection.
A mask that stops exactly at the outline tends to leave a ghost behind.

### AI Image Generation

1. **Open or create** any GIMP document
2. **Go to** `Filters → AI → Image Generator`
3. **Enter prompt** (e.g., "a red dragon on mountain")
4. **New layer created** with generated image

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
- Report at: [GitHub Issues](https://github.com/yourusername/gimp-ai/issues)

## 📚 Documentation

- **[INSTALL.md](INSTALL.md)** - Complete installation guide for users
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

## ⚖️ License

MIT License - see [LICENSE](LICENSE) file for details
