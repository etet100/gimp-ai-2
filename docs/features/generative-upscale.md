---
title: Generative Upscale
parent: Features
nav_order: 3
---

# Generative Upscale

**Enlarge a layer with a model rather than plain resampling.**
Requires Amazon Bedrock.

<!-- Screenshot: docs/assets/screenshots/generative-upscale.png -->

## How to use it

1. **Select the layer** you want enlarged in the Layers dialog
2. **Go to** `Filters → AI → Generative Upscale`
3. **Pick a method** from the dropdown and optionally describe the image

It works on the **active layer**, not the whole image, so you can upscale one
layer and keep the original beside it for comparison.

The result is added as a new layer larger than the canvas - use
`Image → Fit Canvas to Layers` to see all of it.

## Methods

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
one is used.
