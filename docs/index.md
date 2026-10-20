---
title: Home
layout: home
nav_order: 1
description: AI inpainting, object removal, upscaling and image generation for GIMP 3.
permalink: /
---

# GIMP AI Plugin
{: .fs-9 }

AI inpainting, object removal, generative upscaling and image generation -
inside GIMP 3, as ordinary layers you can mask, compare and undo.
{: .fs-6 .fw-300 }

[Install](installation.md){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[Download latest release](https://github.com/etet100/gimp-ai-2/releases/latest){: .btn .fs-5 .mb-4 .mb-md-0 }

---

The plugin adds a `Filters → AI` menu to GIMP 3.0.4 and newer. It talks to
**OpenAI's GPT-Image models** (GPT-Image-2.5 Flare and Sunburst, plus the legacy
gpt-image-1) or to **Amazon Bedrock** (Stability AI), whichever you pick in
Settings. It is pure Python standard library: nothing to `pip install`, three
files to copy.

{: .note }
This is a **beta**. Bug reports from Windows and Linux are especially welcome -
see [Troubleshooting](troubleshooting.md#reporting-bugs-beta-testing).

## Features

| Feature | What it does | OpenAI | Bedrock |
|---|---|:-:|:-:|
| [Inpainting](features/inpainting.md) | Fill a selection from a text prompt | ✓ | ✓ |
| [Erase Object](features/erase-object.md) | Remove what is selected, no prompt, three alternatives | ✓ | ✓ |
| [Generative Upscale](features/generative-upscale.md) | Enlarge a layer with a model - Fast, Conservative or Creative | | ✓ |
| [Image Generation](features/image-generation.md) | Text to image, as a new layer or a new image; optional reference images | ✓ | ✓ |
| [Layer Composite](features/layer-composite.md) | Blend upper layers into the base layer | ✓ | ✓ |

Generative Upscale needs Bedrock because OpenAI has no equivalent model.
Erase Object works with OpenAI too, but Bedrock's dedicated erase model is
more reliable at removing without inventing something new.

## Where to go next

- **New here?** [Installation](installation.md) walks through every step,
  then [Configuration](configuration.md) sets up your API key or AWS credentials.
- **Something not working?** [Troubleshooting](troubleshooting.md).
- **Want to know how it works or contribute?** [Developers](developers/index.md).
