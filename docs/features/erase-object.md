---
title: Erase Object
parent: Features
nav_order: 2
---

# Erase Object

**Remove an unwanted object and fill the gap from the surrounding image.**
Works best with Amazon Bedrock, which has Stability's purpose-built
object-erase model. With OpenAI it works too, as an approximation - see
[With OpenAI](#with-openai).

<!-- Screenshot: docs/assets/screenshots/erase-object.png -->

## How to use it

1. **Select** the thing you want gone (any selection tool)
2. **Go to** `Filters → AI → Erase Object`

That is the whole workflow: no prompt, no options.

{: .tip }
Include a little of the object's shadow or contact points in the selection.
A mask that stops exactly at the outline tends to leave a ghost behind.

## Three alternatives (Bedrock)

You get **three alternative erasures** as three layers, named `AI Erase 1 of 3`
through `3 of 3`, with only the first visible. Toggle layer visibility to compare
and keep the one you prefer. They are worth comparing: on a test photo one
variation invented a garbled bit of text on a background sign while the other two
were clean. The three run concurrently, so all three take about as long as one
(measured: 17 seconds).

Set `erase_variations` in the `bedrock` section of `config.json` to change how
many are generated (1-4). There is deliberately no prompt: removal is
unambiguous, and describing it only invites the model to invent replacement
content instead of removing.

## With OpenAI

OpenAI has no dedicated erase model, so with the OpenAI provider Erase Object
is inpainting with a fixed prompt: remove the object and its shadow, rebuild
only the background behind it, add nothing new. Still no prompt for you to
write. What differs from Bedrock:

- **It can invent things.** A generative model sometimes fills the gap with a
  new object or text in spite of the prompt. Run it again, or switch to Bedrock
- **The selection is grown first**, by 8px by default, so the object's edge
  and halo go too. The OpenAI model redraws the whole window, so the result is
  masked back to that grown selection with a feathered edge instead of pasted
  whole
- **One result per run** by default, because each one is a separate billed
  request that can take a minute or more with the GPT-Image-2.5 models

Set `erase_variations` (1-4) and `erase_grow_mask` (0-50) in the `openai`
section of `config.json` to change these. See [Configuration](../configuration.md#openai).

## Why it beats prompted inpainting

Two things it does differently from Inpainting, both of which matter for
quality:

- The context window is taken at **native resolution**, sized to roughly one
  megapixel around the selection. Inpainting with gpt-image-1 snaps to its
  three fixed shapes, which for a small selection means upscaling a tight crop: a 160px selection became a
  288px crop blown up 3.56x, so 92% of what the model saw was interpolated.
- The whole window is pasted back rather than masked to the selection, so the
  blending the model already did across the mask edge is preserved instead of
  being cut off at the selection rectangle.

More detail in [Amazon Bedrock internals](../developers/bedrock.md#erase-object).
