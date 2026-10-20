---
title: Configuration
nav_order: 3
---

# Configuration
{: .no_toc }

Everything is set in `Filters → AI → Settings...`. The plugin stores it in
`config.json` in its folder in your GIMP profile, so you only enter it once.

1. TOC
{:toc}

---

## Choosing a provider

**Inpainting Provider** in Settings picks OpenAI or Amazon Bedrock. The choice
applies to every feature. Generative Upscale exists only on Bedrock. Erase
Object works on both, but Bedrock's dedicated erase model gives cleaner results.

## OpenAI

1. **Get an OpenAI API key** from [platform.openai.com](https://platform.openai.com)
   (profile icon → API Keys → Create new secret key). You need credits on the
   account to use the API.
2. In GIMP: go to `Filters → AI → Settings...`
3. **Paste your API key** (starts with `sk-`)
4. **Pick the image model**:
   - **GPT-Image-2.5 Flare** - fast, the default
   - **GPT-Image-2.5 Sunburst** - slower, more precise
   - **GPT-Image-1** - legacy; only three fixed sizes (1024x1024, 1536x1024, 1024x1536)
5. Click Save

You can also start any AI feature without a key: the warning that appears has a
**Configure Now** button.

The GPT-Image-2.5 models accept any size in multiples of 16, so inpainting
works at the native resolution of your image instead of being scaled to a fixed
shape. They also tend to paint *around* a masked hole rather than into it, so
the plugin sends the selection once more as a white-on-black image and tells the
model to work inside it. Set `"mask_hint": false` in the `openai` section of
`config.json` to turn that off.

Erase Object on OpenAI reads two more keys from the `openai` section:

| Key | Default | Meaning |
|---|---|---|
| `erase_variations` | `1` | How many alternatives Erase Object produces (1-4). Each one is a separate, billed request |
| `erase_grow_mask` | `8` | Pixels the selection is grown by before erasing (0-50), so the object's edge and halo go too |

## Amazon Bedrock

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

{: .warning }
> **Sandboxed GIMP**: snap and flatpak builds cannot read `~/.aws` at all - only
> `~/.config/GIMP` is visible to them. Either fill in the credential fields, or
> launch GIMP from a terminal with `AWS_ACCESS_KEY_ID` and
> `AWS_SECRET_ACCESS_KEY` exported.

### Which model each feature uses

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

## config.json reference

The Settings dialog covers the common options. The rest can be edited in
`config.json` directly.

| OS | Location (replace `3.0` with your GIMP version) |
|---|---|
| Windows | `%APPDATA%\GIMP\3.0\gimp-ai-plugin\config.json` |
| macOS | `~/Library/Application Support/GIMP/3.0/gimp-ai-plugin/config.json` |
| Linux | `~/.config/GIMP/3.0/gimp-ai-plugin/config.json` |

A complete example is in
[`config.json.example`](https://github.com/etet100/gimp-ai-2/blob/main/config.json.example).

### `openai` section

| Key | Default | Meaning |
|---|---|---|
| `api_key` | — | OpenAI API key |
| `model` | `gpt-image-2.5-flare` | `gpt-image-2.5-flare`, `gpt-image-2.5-sunburst` or `gpt-image-1` |
| `mask_hint` | `true` | Send the selection as an extra hint image (2.x models only) |

### `bedrock` section

| Key | Default | Applies to |
|---|---|---|
| `region` | `us-east-1` | Inpainting, Layer Composite, Erase Object, Generative Upscale |
| `model_id` | inpaint model | Inpainting, Layer Composite |
| `erase_model_id` | erase model | Erase Object |
| `generation_region` | `us-west-2` | Generate Layer, Generate New Image |
| `generation_model_id` | SD 3.5 Large | Generate Layer, Generate New Image |
| `inpaint_variations` | `3` | How many alternatives Inpainting produces (1-4) |
| `erase_variations` | `3` | How many alternatives Erase Object produces (1-4) |
| `grow_mask` | `5` | Expands and softens the mask edge (0-20), Inpainting and Erase Object |
| `upscale_variant` | `conservative` | Default Generative Upscale method |
| `upscale_creativity` | `0.35` | Conservative and Creative upscale |
| `seed`, `negative_prompt` | `0`, empty | Most models |
| `api_key`, `access_key_id`, `secret_access_key`, `profile` | unset | Credentials |

`grow_mask` of 5 measured better than 20, which grew far enough to swallow
neighbouring subjects. Blank credential values fall through to the next source:
plugin config, then environment, then `~/.aws`.
