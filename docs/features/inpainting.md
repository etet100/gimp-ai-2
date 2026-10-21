---
title: Inpainting
parent: Features
nav_order: 1
---

# Inpainting

**Fill selected areas with AI-generated content using text prompts.**
Works with OpenAI and Amazon Bedrock.

<!-- Screenshot: docs/assets/screenshots/inpainting.png -->

## How to use it

1. **Open image** in GIMP
2. **Make a selection** of the area to inpaint (required)
3. **Go to** `Filters → AI → Inpainting`
4. **Enter prompt** (e.g., "blue sky with clouds", "remove the object")
5. **Choose processing mode**:
   - **Focused (High Detail)**: Best for small edits - the model sees a window around the selection
   - **Full Image (Consistent)**: Best for large changes - the model sees the whole image
6. **Clip result to selection** (on by default): untick to keep the edges the
   model drew outside the selection. The layer mask is still created, just
   disabled, so the clip is one click away (`Layer → Mask → Disable Layer Mask`) and
   the mask can be painted to allow only part of the overflow
7. **Reference images** (OpenAI only, optional): pick other open images for the
   model to draw on. Two slots are shown; **Add reference** adds more
8. **Result**: New layer with AI-generated content, masked to the selection

## How the selection is used

- **Soft masks**: AI can redraw content _outside_ the selection to maintain visual coherence
- **With selection**: Final result is masked to show only within selected area, but AI considers surrounding context
- **Smart feathering**: Automatic edge blending for seamless integration

{: .important }
The AI model may modify areas outside your selection to create coherent
results. Only the final output is masked to your selection.

## On Amazon Bedrock

You get **three alternative results** as three layers
(`AI Inpaint 1 of 3` ...), only the first visible, generated concurrently. Each
is the whole context window pasted back without a selection mask: Stability only
changes the masked region, so this blends without a seam. Set
`inpaint_variations` in the `bedrock` section of `config.json` to change the
count (1-4).

## On GPT-Image-2.5

The 2.5 models work at your image's native resolution instead of a fixed shape,
and receive the selection as an extra hint image so they paint inside it rather
than around it. See [Configuration](../configuration.md#openai).
