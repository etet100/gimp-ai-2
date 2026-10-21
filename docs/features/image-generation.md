---
title: Image Generation
parent: Features
nav_order: 4
---

# Image Generation

**Create images from a text description.** Works with OpenAI and Amazon Bedrock.

<!-- Screenshot: docs/assets/screenshots/image-generation.png -->

There are two entries:

- **`Filters → AI → Generate Layer`** - adds the result as a layer covering the
  canvas of the open image
- **`Filters → AI → Generate New Image`** (also `File → Create → Generate New
  Image`) - works with no image open; asks for a size and opens the result as a
  new image

## How to use it

1. **Go to** one of the entries above
2. **Enter prompt** (e.g., "a red dragon on mountain")
3. **Pick a size** (Generate New Image only). With GPT-Image-2.5 there are
   presets up to 4K and a custom size, rounded to multiples of 16; sizes above
   2560x1440 are marked experimental by OpenAI. gpt-image-1 and Bedrock offer
   the shapes their models support
4. **Reference images** (OpenAI only, optional): pick open images to guide the
   result - the request then goes to OpenAI's edits endpoint without a mask

{: .note }
Generation always produces a **whole-canvas layer** - it does not read your
selection. To generate into a selected area, use [Inpainting](inpainting.md),
which is exactly that operation: a prompt plus a selection mask.

## How the result is fitted

Generation models return their own resolution (Stability gives about a megapixel
at the nearest aspect ratio it supports; gpt-image-1 one of three fixed sizes;
GPT-Image-2.5 the canvas aspect ratio, up to 2560x1440), which rarely matches
your canvas exactly. The generated layer is therefore scaled to **cover**
the canvas and centred: the tighter axis matches exactly and the other overhangs,
so there is no gap and the excess can be cropped or repositioned.

GIMP 3 has no Lanczos filter - it was replaced by NoHalo and LoHalo. The plugin
uses NoHalo when enlarging and LoHalo when reducing, which are the intended
modern equivalents.

## On Amazon Bedrock

Generation runs in its own region (`us-west-2` by default) because Stability's
generation models are not offered in the region the editing models use. See
[Configuration](../configuration.md#which-model-each-feature-uses).
