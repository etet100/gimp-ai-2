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

**Inpainting Provider** in Settings picks OpenAI, Amazon Bedrock, DeepSeek or
Custom (OpenAI-compatible). The choice applies to every feature. Generative
Upscale uses Bedrock by default; Custom also has an experimental, disabled-by-
default edits-based path. Erase Object works on all four, but Bedrock's
dedicated erase model gives cleaner results.

**DeepSeek caveat:** the public DeepSeek API serves text and vision only - it
has no image endpoint. Selecting *DeepSeek* therefore calls an
OpenAI-compatible image API at the URL in the `deepseek` section, which only
works if you point it at a gateway that offers one. On the official API, use
DeepSeek for prompt refinement ([below](#deepseek-prompt-assistant)) and keep
OpenAI or Bedrock as the image provider.

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

## DeepSeek prompt assistant

DeepSeek is a text model, not an image generator: its API has no equivalent of
the GPT-Image or Stability endpoints. The plugin therefore uses it to **rewrite
the prompt** you type, then sends the improved prompt to whichever image
provider you selected above. Every feature that asks for a prompt gets an
**Enhance with DeepSeek** button, and Settings can do it automatically.

The provider dropdown also offers **DeepSeek (OpenAI-compatible images)**. That
entry sends `/images/generations` and `/images/edits` requests to
`deepseek.image_base_url` using your DeepSeek key. The official
`api.deepseek.com` does not implement those endpoints today; it only works if
you point the URL at a compatible gateway. If you just want DeepSeek to improve
prompts, leave the provider on OpenAI or Bedrock.

1. **Get a DeepSeek API key** from [platform.deepseek.com](https://platform.deepseek.com/api_keys)
2. In GIMP: go to `Filters → AI → Settings...`
3. Under **DeepSeek Prompt Assistant**, paste the key
4. Pick the prompt model:
   - **DeepSeek Flash** - fast and cheap, the default (`deepseek-flash`)
   - **DeepSeek V4 Pro** - slower, more detailed rewrites (`deepseek-v4-pro`)
5. Optionally tick **Refine prompts automatically before generating**
6. Click Save

Press **Enhance with DeepSeek** in a prompt dialog to rewrite the text in place
for review; nothing is sent to the image provider until you press OK. With
automatic refinement on, the prompt is rewritten when you press OK (unless you
already used the button), and the original prompt is kept if DeepSeek cannot be
reached.

The API key can also come from the `DEEPSEEK_API_KEY` environment variable.
The base URL defaults to `https://api.deepseek.com` and can be changed for a
compatible proxy.

## Custom (OpenAI-compatible)

Use this provider for stable-diffusion.cpp `sd-server.exe` or another partial
OpenAI-compatible image server on your machine or LAN.

1. Start the server with its OpenAI-compatible API enabled.
2. In GIMP Settings, choose **Custom (OpenAI-compatible)**.
3. Set **Base URL** to the server root, for example
   `http://192.168.1.50:8080`. The plugin appends `/v1/images/generations` or
   `/v1/images/edits`; do not include `/v1` yourself. Trailing slashes are
   normalized, and only HTTP(S) URLs without traversal segments are accepted.
4. Leave **API key** blank unless your server requires one. Keys are stored like
   the other provider secrets in plain-text `config.json` with mode 600 attempted.
5. Leave **Timeout seconds** at 600 on slow hardware (30-3600 is accepted).
6. Optionally set **Extra parameters (JSON)** to an object such as
   `{"steps": 20, "cfg_scale": 7, "sampling_method": "euler"}`. These fields are
   merged into generation and edit requests. Invalid JSON or a non-object shows
   a validation error. OpenAI-only `quality`, `moderation` and `background` are
   never sent and are rejected if included here.

**Test** runs a minimal generation (64x64 with one step, falling back to 256x256
when the small request is rejected), reports latency, and probes the edits
endpoint with OPTIONS. An edits probe can be reported as unknown on servers
that do not answer OPTIONS; an actual edit returning 404/501 disables Custom
inpainting and erase for the current configuration. Generation remains available.

The backend must accept the multipart image/mask edit format and return either
`b64_json` or a URL in OpenAI's `data` array. The plugin does not send `model`,
`quality`, `moderation`, `background`, `input_fidelity`, or mask-hint images to
this provider. Reference-image generation is not available. Upscale and
composite are disabled by default; enabling them is experimental and uses the
edits endpoint (4x image-to-image for upscale), so results depend on the build.

### Prompt enhancement

The separate **Prompt enhancement** section calls an OpenAI-compatible
`/v1/chat/completions` endpoint, for example a local KoboldCpp server. Set the
full endpoint URL, model name, optional API key and timeout (default 120s), then
enable the toggle. It is **off by default** and only runs when Custom is the
image provider. The enhanced description replaces the prompt for generation,
inpainting and any experimentally enabled custom operations. Any failure keeps
the original prompt and notes the fallback in the status line without an error
dialog. Your prompt is sent to this endpoint only while the toggle is enabled.

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

### `deepseek` section

| Key | Default | Meaning |
|---|---|---|
| `api_key` | — | DeepSeek API key (falls back to `DEEPSEEK_API_KEY`) |
| `model` | `deepseek-flash` | Prompt model: `deepseek-flash` or `deepseek-v4-pro` |
| `base_url` | `https://api.deepseek.com` | Prompt API base URL |
| `auto_enhance` | `false` | Rewrite prompts automatically before sending them to the image provider |
| `timeout` | `60` | Seconds to wait for a prompt rewrite (5-300) |
| `image_model` | `model` | Model sent to the OpenAI-compatible image endpoint |
| `image_base_url` | `https://api.deepseek.com/v1` | Base URL for `/images/generations` and `/images/edits` when DeepSeek is the provider |
| `accepts_any_size` | `true` | Let the image endpoint choose from free sizes instead of the three gpt-image-1 shapes |
| `mask_hint` | `true` | Send the selection as an extra hint image to the image endpoint |
| `erase_variations`, `erase_grow_mask` | `1`, `8` | Erase Object tuning for the DeepSeek-compatible image endpoint |

### `custom` section

| Key | Default | Meaning |
|---|---|---|
| `base_url` | empty | Required HTTP(S) server root; `/v1/images/...` is appended automatically |
| `api_key` | empty | Optional bearer token; blank omits the Authorization header |
| `timeout` | `600` | Image request timeout in seconds (30-3600) |
| `extra_parameters` | empty string | JSON object merged into generation and edit requests |
| `erase_variations` | `1` | Number of erase results (1-4); each is a separate request |
| `erase_grow_mask` | `8` | Pixels to grow the selection before erase (0-50) |
| `capabilities.t2i` | `true` | Generate Layer / Generate New Image |
| `capabilities.inpaint` | `true` | Inpainting; set false automatically after edits 404/501 |
| `capabilities.erase` | `true` | Erase Object; set false automatically after edits 404/501 |
| `capabilities.upscale` | `false` | Experimental 4x image-to-image edits request |
| `capabilities.composite` | `false` | Experimental multi-image edits request |

The same five `capabilities` keys can be overridden in the `openai`, `bedrock`
or `deepseek` sections. Their defaults preserve existing behavior: all support
text-to-image, inpainting, erase and composite; only Bedrock supports upscale.
Automatic edits-endpoint detection changes the in-memory Custom configuration;
saving Settings persists it. Re-enable the flags after upgrading to a native
stable-diffusion.cpp API build with edits support.

### `prompt_enhancement` section

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Enhance prompts only when the Custom provider is selected |
| `endpoint` | empty | Full HTTP(S) `/v1/chat/completions` URL |
| `model` | empty | Chat model name required when enhancement is enabled |
| `api_key` | empty | Optional bearer token; blank omits Authorization |
| `timeout` | `120` | Enhancement request timeout in seconds (30-3600) |

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
