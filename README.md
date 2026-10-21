# GIMP AI Plugin

**AI inpainting, object removal, generative upscaling and image generation - right inside GIMP 3.**

Select something, type what you want, get a new layer. No exporting, no browser
tabs, no round trips: results land in your image as ordinary layers you can
mask, compare and undo.

[![Latest release](https://img.shields.io/github/v/release/etet100/gimp-ai-2?include_prereleases&label=download)](https://github.com/etet100/gimp-ai-2/releases/latest)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://etet100.github.io/gimp-ai-2/)
[![GIMP 3.0.4+](https://img.shields.io/badge/GIMP-3.0.4%2B-5C5543)](https://www.gimp.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

<p align="center">
  <img src="docs/assets/screenshots/hero-erase-inpaint.jpg" alt="Erase Object removes the astronaut, then Inpainting puts a cosmic cat in the same spot" width="900">
</p>
<p align="center"><em>Two clicks in GIMP: erase the astronaut, then ask for a cosmic cat in the same spot.</em></p>

## What it does

- 🎨 **Inpainting** - fill a selection from a text prompt, at your image's native resolution
- 🧹 **Erase Object** - select a photobomber, click once, pick the best of three clean results
- 🔍 **Generative Upscale** - enlarge a layer up to 4x with real detail instead of blur
- 🖼️ **Image Generation** - text to image as a new layer or a new file, optionally guided by reference images
- 🔄 **Layer Composite** - blend pasted-in elements naturally into a scene

Powered by **OpenAI GPT-Image** (2.5 Flare, 2.5 Sunburst, gpt-image-1) or
**Amazon Bedrock** (Stability AI) - your choice, your API key.

## See it in action

**Inpainting** - select an area, describe the change, pick how much of the image
the model gets to see. Other open images can be passed in as references.

<p align="center">
  <img src="docs/assets/screenshots/inpaint1.jpg" alt="The AI Inpaint dialog next to an image with a selection" width="700">
</p>

**Generate New Image** - type a prompt, choose a size, get a new GIMP image.
Works even with no image open.

| Prompt | Result |
|:---:|:---:|
| <img src="docs/assets/screenshots/newimage1.jpg" alt="The Generate New Image dialog with the prompt 'Lots of little horses'" width="400"> | <img src="docs/assets/screenshots/newimage2.jpg" alt="Generated image: a herd of miniature horses on a meadow" width="400"> |

## Why you might like it

- **Non-destructive** - every result is a new layer; your original stays untouched
- **Zero dependencies** - pure Python standard library, nothing to `pip install`
- **Three files, one installer** - works on Windows, macOS and Linux
- **Retries built in** - a failed request keeps the dialog open with a Try Again button

## Quick install

1. Download the [latest release ZIP](https://github.com/etet100/gimp-ai-2/releases/latest) and extract it
2. Run `python3 install_plugin.py`
3. Restart GIMP, open `Filters → AI → Settings...` and enter your OpenAI API key or AWS credentials

| Everything lives under `Filters → AI` | Pick a provider and model in Settings |
|:---:|:---:|
| <img src="docs/assets/screenshots/menu.jpg" alt="The Filters → AI menu" width="300"> | <img src="docs/assets/screenshots/settings1.jpg" alt="The AI Plugin Settings dialog" width="500"> |

Requires **GIMP 3.0.4 or newer**. Prefer to copy files by hand, or using a
Flatpak/Snap build? See the [installation guide](https://etet100.github.io/gimp-ai-2/installation.html).

## Documentation

Everything else lives at **[etet100.github.io/gimp-ai-2](https://etet100.github.io/gimp-ai-2/)**:

- [Installation](https://etet100.github.io/gimp-ai-2/installation.html) - step by step, every platform
- [Configuration](https://etet100.github.io/gimp-ai-2/configuration.html) - OpenAI, Amazon Bedrock, `config.json` reference
- [Features](https://etet100.github.io/gimp-ai-2/features/) - how to use each tool and get the best results
- [Troubleshooting](https://etet100.github.io/gimp-ai-2/troubleshooting.html) - when `Filters → AI` does not show up, and more
- [Developers](https://etet100.github.io/gimp-ai-2/developers/) - architecture, tests, Bedrock internals, release process

What's new: [CHANGELOG.md](CHANGELOG.md).

## Beta

This is a beta and testers on all platforms - especially Windows and Linux - are
very welcome. Found a problem? [Open an issue](https://github.com/etet100/gimp-ai-2/issues).

## License

MIT - see [LICENSE](LICENSE). Fork of [lukaso/gimp-ai](https://github.com/lukaso/gimp-ai).
