---
title: Image preparation algorithms
parent: Developers
nav_order: 2
---

# Image Preparation Algorithms

This document describes how images are prepared for the image APIs (OpenAI
GPT-Image and Amazon Bedrock / Stability AI) and how the results are placed
back into GIMP. All of the math lives in `coordinate_utils.py`, which has no
GIMP dependencies.

Which sizing path is used depends on the provider and model:

| Provider / model | Sizing | Section |
|---|---|---|
| OpenAI gpt-image-1 | Three fixed shapes, content scaled and padded | 1-6 |
| OpenAI GPT-Image-2.x | Any size in multiples of 16, native resolution | [8](#8-gpt-image-2x-sizing) |
| Amazon Bedrock (Stability) | Any size, ~1MP native-resolution window | [7](#7-native-resolution-context-window) |

## OpenAI gpt-image-1 Constraints

gpt-image-1 accepts three specific image dimensions:
- **Square**: 1024 × 1024
- **Landscape**: 1536 × 1024  
- **Portrait**: 1024 × 1536

When dimensions aren't specified, the API chooses based on input, making it difficult to match results back to the original image during inpainting.

## Current Implementation Status

### Implemented
- Shape selection for gpt-image-1 (square, landscape, portrait)
- Context extraction with padding and scaling to the target shape
- Mask generation from the GIMP selection (on a duplicate of the image)
- Native-resolution context windows for Bedrock and GPT-Image-2.x
- Free-size request shapes for GPT-Image-2.x

### Not Implemented
- Smart boundary-aware padding using image content
- Advanced mask feathering
- Tiling for images larger than the API limits

## Core Algorithms

### 1. Optimal Shape Selection

Given an image dimension, return the best matching OpenAI shape.

```python
def get_optimal_openai_shape(width, height):
    """
    Select optimal OpenAI shape based on image dimensions.
    
    Args:
        width: Image width in pixels
        height: Image height in pixels
        
    Returns:
        tuple: (target_width, target_height)
    """
    aspect_ratio = width / height
    
    if aspect_ratio > 1.3:
        # Landscape orientation
        return (1536, 1024)
    elif aspect_ratio < 0.77:
        # Portrait orientation  
        return (1024, 1536)
    else:
        # Square or near-square
        return (1024, 1024)
```

### 2. Image Padding to Match OpenAI Shape

Pad an image to match the target OpenAI dimensions while preserving content.

```python
def pad_image_to_shape(image, target_width, target_height):
    """
    Pad image to match OpenAI shape, centering the content.
    
    Args:
        image: Source image
        target_width: Target width (1024 or 1536)
        target_height: Target height (1024 or 1536)
        
    Returns:
        dict: {
            'padded_image': Padded image data,
            'padding': (left, top, right, bottom),
            'scale_factor': Applied scaling factor
        }
    """
    img_width = image.width
    img_height = image.height
    
    # Calculate scale to fit within target
    scale_x = target_width / img_width
    scale_y = target_height / img_height
    scale = min(scale_x, scale_y)
    
    # Scale image
    scaled_width = int(img_width * scale)
    scaled_height = int(img_height * scale)
    
    # Calculate padding to center
    pad_left = (target_width - scaled_width) // 2
    pad_top = (target_height - scaled_height) // 2
    pad_right = target_width - scaled_width - pad_left
    pad_bottom = target_height - scaled_height - pad_top
    
    return {
        'scale_factor': scale,
        'scaled_size': (scaled_width, scaled_height),
        'padding': (pad_left, pad_top, pad_right, pad_bottom)
    }
```

### 3. Context Extraction Around Selection

Extract a region around a selection with appropriate context for inpainting.

```python
def extract_context_with_selection(image, selection, mode='focused'):
    """
    Extract context region around selection for inpainting.
    
    Args:
        image: Source image dimensions (width, height)
        selection: Selection bounds (x1, y1, x2, y2)
        mode: 'focused' for partial extraction, 'full' for whole image
        
    Returns:
        dict: Context extraction parameters
    """
    img_width, img_height = image
    sel_x1, sel_y1, sel_x2, sel_y2 = selection
    sel_width = sel_x2 - sel_x1
    sel_height = sel_y2 - sel_y1
    
    if mode == 'full':
        # Send entire image with mask
        target_shape = get_optimal_openai_shape(img_width, img_height)
        return {
            'mode': 'full',
            'extract_region': (0, 0, img_width, img_height),
            'target_shape': target_shape,
            'needs_padding': True
        }
    
    # Focused mode: extract region around selection
    # Calculate context padding (30-50% of selection, min 50px, max 300px)
    context_pad = max(50, min(300, int(max(sel_width, sel_height) * 0.4)))
    
    # Initial context bounds
    ctx_x1 = sel_x1 - context_pad
    ctx_y1 = sel_y1 - context_pad
    ctx_x2 = sel_x2 + context_pad
    ctx_y2 = sel_y2 + context_pad
    
    # Smart boundary handling: prefer not to extend beyond image
    if ctx_x1 < 0:
        shift = -ctx_x1
        ctx_x1 = 0
        ctx_x2 = min(img_width, ctx_x2 + shift)
    if ctx_y1 < 0:
        shift = -ctx_y1
        ctx_y1 = 0
        ctx_y2 = min(img_height, ctx_y2 + shift)
    if ctx_x2 > img_width:
        shift = ctx_x2 - img_width
        ctx_x2 = img_width
        ctx_x1 = max(0, ctx_x1 - shift)
    if ctx_y2 > img_height:
        shift = ctx_y2 - img_height
        ctx_y2 = img_height
        ctx_y1 = max(0, ctx_y1 - shift)
    
    ctx_width = ctx_x2 - ctx_x1
    ctx_height = ctx_y2 - ctx_y1
    
    # Determine optimal shape for context
    target_shape = get_optimal_openai_shape(ctx_width, ctx_height)
    
    return {
        'mode': 'focused',
        'extract_region': (ctx_x1, ctx_y1, ctx_width, ctx_height),
        'selection_in_extract': (
            sel_x1 - ctx_x1,
            sel_y1 - ctx_y1,
            sel_x2 - ctx_x1,
            sel_y2 - ctx_y1
        ),
        'target_shape': target_shape,
        'needs_padding': ctx_width != target_shape[0] or ctx_height != target_shape[1]
    }
```

### 4. Mask Generation

Generate a mask for the selection area within the context.

```python
def generate_mask(context_info, feather_radius=10):
    """
    Generate mask for inpainting based on context extraction.
    
    Args:
        context_info: Context extraction info from extract_context_with_selection
        feather_radius: Pixels to feather the mask edges
        
    Returns:
        Binary mask array matching target_shape dimensions
    """
    target_width, target_height = context_info['target_shape']
    
    # Create black mask (areas to preserve)
    mask = create_black_image(target_width, target_height)
    
    if context_info['mode'] == 'full':
        # Full image mode: need to scale selection to target shape
        scale_x = target_width / context_info['extract_region'][2]
        scale_y = target_height / context_info['extract_region'][3]
        # ... scale and position selection
    else:
        # Focused mode: selection within extract region
        sel_in_extract = context_info['selection_in_extract']
        # Account for any padding/scaling to target shape
        # ... position selection in mask
    
    # Apply feathering for smooth blending
    if feather_radius > 0:
        mask = apply_gaussian_blur(mask, feather_radius)
    
    return mask
```

### 5. Result Scaling and Placement

Scale the AI-generated result back and place it correctly in the original image.

```python
def place_result_in_image(result, original_image, context_info):
    """
    Place AI result back into original image at correct position.
    
    Args:
        result: AI-generated image at target_shape dimensions
        original_image: Original image to composite onto
        context_info: Context extraction info used for generation
        
    Returns:
        dict: Placement parameters
    """
    if context_info['mode'] == 'full':
        # Full image mode: scale entire result to original size
        scale_x = original_image.width / result.width
        scale_y = original_image.height / result.height
        
        return {
            'placement_mode': 'replace',
            'scale': (scale_x, scale_y),
            'position': (0, 0)
        }
    else:
        # Focused mode: scale and position extract region
        extract_region = context_info['extract_region']
        target_shape = context_info['target_shape']
        
        # Calculate scale from result back to extract size
        scale_x = extract_region[2] / target_shape[0]
        scale_y = extract_region[3] / target_shape[1]
        
        return {
            'placement_mode': 'composite',
            'scale': (scale_x, scale_y),
            'position': (extract_region[0], extract_region[1]),
            'blend_mode': 'normal'
        }
```

### 6. Layer Mask Generation for Partial Inpainting

When placing a partial inpaint result, generate a layer mask to blend only the modified areas.

```python
def generate_layer_mask(context_info, selection_mask):
    """
    Generate layer mask for blending partial inpaint results.
    
    Args:
        context_info: Context extraction info
        selection_mask: Original selection as binary mask
        
    Returns:
        Layer mask for compositing
    """
    if context_info['mode'] == 'full':
        # Use selection mask directly, scaled to image size
        return scale_mask(selection_mask, context_info['original_size'])
    else:
        # Create mask for just the extracted region
        extract_region = context_info['extract_region']
        layer_mask = create_transparent_mask(original_size)
        
        # Fill only the extract region area
        place_mask_region(
            layer_mask,
            selection_mask,
            position=extract_region[:2],
            size=extract_region[2:]
        )
        
        return layer_mask
```

### 7. Native-Resolution Context Window

`calculate_native_context(img_width, img_height, sel_bounds)` is used for
Bedrock and for focused inpainting with GPT-Image-2.x. Instead of snapping a
tight crop to a fixed shape, it grows a window around the selection to about
one megapixel (`NATIVE_TARGET_PIXELS`) of real pixels:

1. Margin = max(64px, 25% of the selection's longer side)
2. Side = max(√target_pixels, selection + 2 × margin)
3. Centre the window on the selection and slide it back inside the image
   rather than shrinking it; clip it when the image is smaller
4. Trim to the accepted aspect range, then scale down only if the window
   exceeds the target area - never up

Stability downsamples inputs above about a megapixel, which measurably hurt
quality in testing, so the target is kept at ~1MP rather than at the 9.4MP the
API would accept. Padding is always zero on this path.

### 8. GPT-Image-2.x Sizing

The GPT-Image-2.x models accept any `WIDTHxHEIGHT` that satisfies
`is_valid_gpt_image_size()`:

- both sides multiples of 16 (`GPT_IMAGE_SIZE_STEP`)
- longest side ≤ 3840 (`GPT_IMAGE_MAX_EDGE`)
- aspect ratio ≤ 3:1 (`GPT_IMAGE_MAX_ASPECT`)
- 655,360 ≤ area ≤ 8,294,400 pixels

`get_gpt_image_shape(width, height, max_pixels)` picks the request size:

1. Content more extreme than 3:1 is widened to a 3:1 box; the extra becomes padding
2. Scale = min(1, √(max_pixels / area), 3840 / longest side) - content is only
   reduced, except when it is below the minimum area, where it is enlarged to
   reach it
3. Round both sides to multiples of 16, then nudge back inside the limits if
   rounding overshot one
4. The remainder is padded with `calculate_padding_for_shape()` as for gpt-image-1

`max_pixels` defaults to 2560×1440 (`GPT_IMAGE_MAX_PIXELS`): OpenAI marks larger
output as experimental. When the user explicitly picks a size for a new image,
the plugin passes the full API limit instead, which is how the 4K presets work.

Focused inpainting takes the window from section 7 and then fits it with
`get_gpt_image_shape()`, so only rounding leaves a few pixels of padding.
`validate_context_info()` accepts either a gpt-image-1 shape or any valid
GPT-Image-2.x size.

## Implementation Notes

### Current Limitations

1. **Upscaling with gpt-image-1**: a small selection's context is blown up to
   1024px or more, so most of what the model sees is interpolated. The
   native-resolution paths (sections 7 and 8) avoid this
2. **Padding**: padding is filled with flat colour rather than image content

### Recommended Improvements

1. **Boundary-Aware Context**
   - Prefer shifting context window over extending beyond image
   - Only extend beyond boundaries when selection is at edge
   - Use image content for padding when possible

2. **Progressive Mask Feathering**
   - Variable feather radius based on selection size
   - Directional feathering for edge selections
   - Preserve hard edges where appropriate

3. **Multi-Resolution Support**
   - Handle images larger than OpenAI max dimensions
   - Tile-based processing for very large images
   - Smart downsampling for quality preservation

## Usage Examples

### Example 1: Generate New Layer
```python
# No selection, generate at closest shape to image
image_dims = (1920, 1080)  # Landscape image
target_shape = get_optimal_openai_shape(*image_dims)  # Returns (1536, 1024)
padded = pad_image_to_shape(image, *target_shape)
# Send to OpenAI...
```

### Example 2: Focused Inpainting
```python
# Inpaint a selection with context
selection = (500, 300, 700, 500)  # 200×200 selection
context = extract_context_with_selection(
    image=(1920, 1080),
    selection=selection,
    mode='focused'
)
mask = generate_mask(context)
# Send context extract and mask to OpenAI...
result_placement = place_result_in_image(result, original, context)
```

### Example 3: Full Image Inpainting
```python
# Send entire image but only modify selection
context = extract_context_with_selection(
    image=(1920, 1080),
    selection=(500, 300, 700, 500),
    mode='full'
)
# Scales full image to optimal shape with selection mask
```

## Testing Considerations

Key properties to verify:
1. Context always contains full selection
2. Masks align correctly with selections after scaling
3. Results place back at exact selection position
4. No data loss during scale up/down cycles
5. Padding is symmetric and reversible