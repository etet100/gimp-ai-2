"""
Pure coordinate transformation utilities for GIMP AI Plugin.

These functions contain no GIMP dependencies and can be unit tested independently.
All coordinate calculations for context extraction, masking, and placement are here.
"""


def get_optimal_openai_shape(width, height):
    """
    Select optimal OpenAI shape based on image dimensions.

    Args:
        width: Image width in pixels
        height: Image height in pixels

    Returns:
        tuple: (target_width, target_height) - one of (1024, 1024), (1536, 1024), (1024, 1536)
    """
    if width <= 0 or height <= 0:
        return (1024, 1024)  # Default to square for invalid dimensions

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


# GPT-Image-2.x takes any WIDTHxHEIGHT within these limits, unlike gpt-image-1's
# three fixed shapes.
GPT_IMAGE_SIZE_STEP = 16
GPT_IMAGE_MAX_EDGE = 3840
GPT_IMAGE_MAX_ASPECT = 3.0
GPT_IMAGE_MIN_PIXELS = 655360
GPT_IMAGE_LIMIT_PIXELS = 8294400
# OpenAI marks output above 2560x1440 as experimental, so stay at or below it
GPT_IMAGE_MAX_PIXELS = 2560 * 1440


def is_valid_gpt_image_size(width, height):
    """Whether (width, height) is a request size GPT-Image-2.x accepts."""
    step = GPT_IMAGE_SIZE_STEP
    return (
        width > 0 and height > 0
        and width % step == 0 and height % step == 0
        and max(width, height) <= GPT_IMAGE_MAX_EDGE
        and max(width, height) <= GPT_IMAGE_MAX_ASPECT * min(width, height)
        and GPT_IMAGE_MIN_PIXELS <= width * height <= GPT_IMAGE_LIMIT_PIXELS
    )


def get_gpt_image_shape(width, height, max_pixels=GPT_IMAGE_MAX_PIXELS):
    """
    Request size for GPT-Image-2.x that keeps content at native resolution.

    Content is only scaled down, to fit max_pixels and the edge limit, or up when
    it is below the API's minimum area. Whatever the content cannot fill - an
    aspect ratio beyond 3:1, or rounding to multiples of 16 - becomes padding
    through calculate_padding_for_shape().

    Args:
        width, height: Content size in pixels
        max_pixels: Largest request area to use

    Returns:
        tuple: (target_width, target_height) satisfying is_valid_gpt_image_size()
    """
    if width <= 0 or height <= 0:
        return (1024, 1024)

    step = GPT_IMAGE_SIZE_STEP
    max_pixels = max(GPT_IMAGE_MIN_PIXELS, min(max_pixels, GPT_IMAGE_LIMIT_PIXELS))

    # Widen content more extreme than 3:1 to a 3:1 box; the rest is padding
    box_w = max(width, height / GPT_IMAGE_MAX_ASPECT)
    box_h = max(height, width / GPT_IMAGE_MAX_ASPECT)

    scale = min(
        1.0,
        (max_pixels / (box_w * box_h)) ** 0.5,
        GPT_IMAGE_MAX_EDGE / max(box_w, box_h),
    )
    if box_w * box_h * scale * scale < GPT_IMAGE_MIN_PIXELS:
        scale = (GPT_IMAGE_MIN_PIXELS / (box_w * box_h)) ** 0.5

    target_w = max(step, int(round(box_w * scale / step)) * step)
    target_h = max(step, int(round(box_h * scale / step)) * step)

    # Rounding can overshoot a limit by a step; nudge back inside
    while target_w * target_h > max_pixels or max(target_w, target_h) > GPT_IMAGE_MAX_EDGE:
        if target_w >= target_h:
            target_w -= step
        else:
            target_h -= step
    if target_w > target_h * GPT_IMAGE_MAX_ASPECT:
        target_w = int(target_h * GPT_IMAGE_MAX_ASPECT) // step * step
    if target_h > target_w * GPT_IMAGE_MAX_ASPECT:
        target_h = int(target_w * GPT_IMAGE_MAX_ASPECT) // step * step
    while target_w * target_h < GPT_IMAGE_MIN_PIXELS:
        if target_w <= target_h:
            target_w += step
        else:
            target_h += step

    return (target_w, target_h)


def calculate_padding_for_shape(current_width, current_height, target_width, target_height):
    """
    Calculate padding needed to fit content into target OpenAI shape.

    Args:
        current_width: Current content width
        current_height: Current content height
        target_width: Target width (1024 or 1536)
        target_height: Target height (1024 or 1536)

    Returns:
        dict: {
            'scale_factor': Applied scaling factor,
            'scaled_size': (scaled_width, scaled_height),
            'padding': (left, top, right, bottom)
        }
    """
    # Calculate scale to fit within target
    scale_x = target_width / current_width
    scale_y = target_height / current_height
    scale = min(scale_x, scale_y)

    # Scale dimensions
    scaled_width = int(current_width * scale)
    scaled_height = int(current_height * scale)

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


def extract_context_with_selection(img_width, img_height, sel_x1, sel_y1, sel_x2, sel_y2,
                                  mode='focused', has_selection=True):
    """
    Extract context region around selection for inpainting with optimal shape.

    Args:
        img_width: Source image width
        img_height: Source image height
        sel_x1, sel_y1, sel_x2, sel_y2: Selection bounds
        mode: 'focused' for partial extraction, 'full' for whole image
        has_selection: Whether there's an active selection

    Returns:
        dict: Context extraction parameters with optimal shape
    """
    if not has_selection:
        # No selection - use center area
        target_shape = get_optimal_openai_shape(img_width, img_height)
        # Create a default selection in center
        size = min(img_width, img_height, 512)
        sel_x1 = (img_width - size) // 2
        sel_y1 = (img_height - size) // 2
        sel_x2 = sel_x1 + size
        sel_y2 = sel_y1 + size

    sel_width = sel_x2 - sel_x1
    sel_height = sel_y2 - sel_y1

    if mode == 'full':
        # Send entire image with mask
        target_shape = get_optimal_openai_shape(img_width, img_height)
        padding_info = calculate_padding_for_shape(img_width, img_height,
                                                  target_shape[0], target_shape[1])
        return {
            'mode': 'full',
            'selection_bounds': (sel_x1, sel_y1, sel_x2, sel_y2),
            'extract_region': (0, 0, img_width, img_height),
            'target_shape': target_shape,
            'needs_padding': True,
            'padding_info': padding_info,
            'has_selection': has_selection
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
    target_aspect = target_shape[0] / target_shape[1]
    current_aspect = ctx_width / ctx_height if ctx_height > 0 else 1.0

    # Try to extend extract region to match target aspect ratio
    # This avoids padding when possible by using more of the available image
    if abs(current_aspect - target_aspect) > 0.01:  # Only if aspect ratios differ significantly
        if target_aspect > current_aspect:
            # Need wider region: extend horizontally if possible
            target_width = int(ctx_height * target_aspect)
            width_diff = target_width - ctx_width

            # Try to extend equally on both sides
            left_extend = width_diff // 2
            right_extend = width_diff - left_extend

            new_ctx_x1 = max(0, ctx_x1 - left_extend)
            new_ctx_x2 = min(img_width, ctx_x2 + right_extend)

            # If we hit boundaries, try to extend more on the available side
            if new_ctx_x1 == 0 and new_ctx_x2 < img_width:
                # Hit left boundary, extend right more
                remaining = target_width - (new_ctx_x2 - new_ctx_x1)
                new_ctx_x2 = min(img_width, new_ctx_x2 + remaining)
            elif new_ctx_x2 == img_width and new_ctx_x1 > 0:
                # Hit right boundary, extend left more
                remaining = target_width - (new_ctx_x2 - new_ctx_x1)
                new_ctx_x1 = max(0, new_ctx_x1 - remaining)

            ctx_x1, ctx_x2 = new_ctx_x1, new_ctx_x2

        else:
            # Need taller region: extend vertically if possible
            target_height = int(ctx_width / target_aspect)
            height_diff = target_height - ctx_height

            # Try to extend equally on both sides
            top_extend = height_diff // 2
            bottom_extend = height_diff - top_extend

            new_ctx_y1 = max(0, ctx_y1 - top_extend)
            new_ctx_y2 = min(img_height, ctx_y2 + bottom_extend)

            # If we hit boundaries, try to extend more on the available side
            if new_ctx_y1 == 0 and new_ctx_y2 < img_height:
                # Hit top boundary, extend bottom more
                remaining = target_height - (new_ctx_y2 - new_ctx_y1)
                new_ctx_y2 = min(img_height, new_ctx_y2 + remaining)
            elif new_ctx_y2 == img_height and new_ctx_y1 > 0:
                # Hit bottom boundary, extend top more
                remaining = target_height - (new_ctx_y2 - new_ctx_y1)
                new_ctx_y1 = max(0, new_ctx_y1 - remaining)

            ctx_y1, ctx_y2 = new_ctx_y1, new_ctx_y2

    # Recalculate final dimensions
    ctx_width = ctx_x2 - ctx_x1
    ctx_height = ctx_y2 - ctx_y1

    padding_info = calculate_padding_for_shape(ctx_width, ctx_height,
                                              target_shape[0], target_shape[1])

    return {
        'mode': 'focused',
        'selection_bounds': (sel_x1, sel_y1, sel_x2, sel_y2),
        'extract_region': (ctx_x1, ctx_y1, ctx_width, ctx_height),
        'selection_in_extract': (
            sel_x1 - ctx_x1,
            sel_y1 - ctx_y1,
            sel_x2 - ctx_x1,
            sel_y2 - ctx_y1
        ),
        'target_shape': target_shape,
        'needs_padding': ctx_width != target_shape[0] or ctx_height != target_shape[1],
        'padding_info': padding_info,
        'has_selection': has_selection
    }


def calculate_result_placement(result_shape, original_shape, context_info):
    """
    Calculate placement for AI result back into original image.

    Args:
        result_shape: (width, height) of AI result
        original_shape: (width, height) of original image
        context_info: Context extraction info used for generation

    Returns:
        dict: Placement parameters
    """
    if context_info['mode'] == 'full':
        # Full image mode: scale entire result to original size
        scale_x = original_shape[0] / result_shape[0]
        scale_y = original_shape[1] / result_shape[1]

        return {
            'placement_mode': 'replace',
            'scale': (scale_x, scale_y),
            'position': (0, 0),
            'size': original_shape
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
            'size': (extract_region[2], extract_region[3])
        }


def calculate_scale_from_shape(source_shape, target_shape):
    """
    Calculate scaling factors between two shapes.

    Args:
        source_shape: (width, height) tuple
        target_shape: (width, height) tuple

    Returns:
        dict: {
            'scale_x': Horizontal scale factor,
            'scale_y': Vertical scale factor,
            'uniform_scale': Min of scale_x and scale_y (preserves aspect ratio)
        }
    """
    scale_x = target_shape[0] / source_shape[0] if source_shape[0] > 0 else 1.0
    scale_y = target_shape[1] / source_shape[1] if source_shape[1] > 0 else 1.0

    return {
        'scale_x': scale_x,
        'scale_y': scale_y,
        'uniform_scale': min(scale_x, scale_y)
    }




def calculate_mask_coordinates(context_info, target_size):
    """
    Calculate mask coordinates for selection within extract region.

    Args:
        context_info: Context extraction info from extract_context_with_selection()
        target_size: Target size for the mask (e.g. 1024)

    Returns:
        dict with mask coordinates
    """
    if not context_info['has_selection']:
        # Create center circle mask for no selection case
        center = target_size // 2
        radius = target_size // 4
        return {
            'mask_type': 'circle',
            'center_x': center,
            'center_y': center,
            'radius': radius,
            'target_size': target_size
        }

    # Get extract region info
    sel_x1, sel_y1, sel_x2, sel_y2 = context_info['selection_bounds']
    ext_x1, ext_y1, ext_width, ext_height = context_info['extract_region']

    # Calculate selection position within the extract region
    sel_in_ext_x1 = sel_x1 - ext_x1
    sel_in_ext_y1 = sel_y1 - ext_y1
    sel_in_ext_x2 = sel_x2 - ext_x1
    sel_in_ext_y2 = sel_y2 - ext_y1

    # Scale to target size (use the larger dimension for scale factor)
    scale = target_size / max(ext_width, ext_height)
    mask_sel_x1 = int(sel_in_ext_x1 * scale)
    mask_sel_y1 = int(sel_in_ext_y1 * scale)
    mask_sel_x2 = int(sel_in_ext_x2 * scale)
    mask_sel_y2 = int(sel_in_ext_y2 * scale)

    # Ensure coordinates are within bounds
    mask_sel_x1 = max(0, min(target_size - 1, mask_sel_x1))
    mask_sel_y1 = max(0, min(target_size - 1, mask_sel_y1))
    mask_sel_x2 = max(0, min(target_size, mask_sel_x2))
    mask_sel_y2 = max(0, min(target_size, mask_sel_y2))

    return {
        'mask_type': 'rectangle',
        'x1': mask_sel_x1,
        'y1': mask_sel_y1,
        'x2': mask_sel_x2,
        'y2': mask_sel_y2,
        'target_size': target_size,
        'scale_factor': scale
    }


def calculate_placement_coordinates(context_info):
    """
    Calculate where to place the AI result back in the original image.

    Args:
        context_info: Context extraction info from extract_context_with_selection()

    Returns:
        dict with placement coordinates
    """
    ctx_x1, ctx_y1, ctx_width, ctx_height = context_info['extract_region']

    return {
        'paste_x': ctx_x1,
        'paste_y': ctx_y1,
        'result_width': ctx_width,
        'result_height': ctx_height
    }


def validate_context_info(context_info):
    """
    Validate that context_info contains all required fields with valid values.

    Args:
        context_info: Context info dict to validate

    Returns:
        tuple: (is_valid: bool, error_message: str)
    """
    required_fields = [
        'selection_bounds', 'extract_region', 'target_shape', 'has_selection'
    ]

    for field in required_fields:
        if field not in context_info:
            return False, f"Missing required field: {field}"

    # Validate selection bounds
    sel_bounds = context_info['selection_bounds']
    if len(sel_bounds) != 4:
        return False, "selection_bounds must have 4 values (x1, y1, x2, y2)"

    sel_x1, sel_y1, sel_x2, sel_y2 = sel_bounds
    if sel_x2 <= sel_x1 or sel_y2 <= sel_y1:
        return False, "Invalid selection bounds: x2 <= x1 or y2 <= y1"

    # Validate extract region
    extract_region = context_info['extract_region']
    if len(extract_region) != 4:
        return False, "extract_region must have 4 values (x1, y1, width, height)"

    ext_x1, ext_y1, ext_width, ext_height = extract_region
    if ext_width <= 0 or ext_height <= 0:
        return False, "Extract region dimensions must be positive"

    # Validate that extract region contains selection (for focused mode)
    if context_info.get('mode') == 'focused':
        ext_x2 = ext_x1 + ext_width
        ext_y2 = ext_y1 + ext_height

        if not (ext_x1 <= sel_x1 and ext_y1 <= sel_y1 and ext_x2 >= sel_x2 and ext_y2 >= sel_y2):
            return False, "Extract region must contain the selection"

    # Validate target shape
    target_shape = context_info['target_shape']
    if not isinstance(target_shape, tuple) or len(target_shape) != 2:
        return False, "target_shape must be a tuple of (width, height)"
    valid_shapes = [(1024, 1024), (1536, 1024), (1024, 1536)]
    if target_shape not in valid_shapes and not is_valid_gpt_image_size(*target_shape):
        return False, (
            f"target_shape must be one of {valid_shapes} or a valid GPT-Image-2.x size"
        )

    return True, ""


def check_coordinate_properties(img_width, img_height, sel_x1, sel_y1, sel_x2, sel_y2):
    """
    Test that coordinate calculations satisfy expected mathematical properties.

    Returns:
        dict with test results
    """
    context_info = extract_context_with_selection(img_width, img_height, sel_x1, sel_y1, sel_x2, sel_y2)
    target_shape = context_info['target_shape']
    target_size = max(target_shape)
    mask_coords = calculate_mask_coordinates(context_info, target_size)
    placement = calculate_placement_coordinates(context_info)

    # Test validation
    is_valid, error_msg = validate_context_info(context_info)

    results = {
        'validation_passed': is_valid,
        'validation_error': error_msg,
        'context_contains_selection': True,  # Will be checked below
        'mask_coordinates_valid': True,
        'placement_covers_selection': True
    }

    if not is_valid:
        return results

    # Check that extract region contains selection
    ext_x1, ext_y1, ext_width, ext_height = context_info['extract_region']
    ext_x2, ext_y2 = ext_x1 + ext_width, ext_y1 + ext_height

    results['context_contains_selection'] = (
        ext_x1 <= sel_x1 and ext_y1 <= sel_y1 and
        ext_x2 >= sel_x2 and ext_y2 >= sel_y2
    )

    # Check mask coordinates are within bounds
    if mask_coords['mask_type'] == 'rectangle':
        results['mask_coordinates_valid'] = (
            0 <= mask_coords['x1'] < target_size and
            0 <= mask_coords['y1'] < target_size and
            0 < mask_coords['x2'] <= target_size and
            0 < mask_coords['y2'] <= target_size and
            mask_coords['x1'] < mask_coords['x2'] and
            mask_coords['y1'] < mask_coords['y2']
        )

    # Check that placement would cover selection
    paste_x, paste_y = placement['paste_x'], placement['paste_y']
    result_width, result_height = placement['result_width'], placement['result_height']

    results['placement_covers_selection'] = (
        paste_x <= sel_x1 and paste_y <= sel_y1 and
        paste_x + result_width >= sel_x2 and paste_y + result_height >= sel_y2
    )

    return results

# Stability's image models work internally at roughly one megapixel. Sending
# more than that gains nothing - the service downsamples and the extra detail is
# lost - and sending less means upscaling, which is worse: the model then sees
# interpolated pixels and generates content to match them.
NATIVE_TARGET_PIXELS = 1024 * 1024

# Stability rejects images outside this aspect range.
MIN_ASPECT = 1 / 2.5
MAX_ASPECT = 2.5


def calculate_native_context(img_width, img_height, sel_bounds,
                             target_pixels=NATIVE_TARGET_PIXELS, min_margin=64):
    """
    Context window around a selection at native resolution, for providers that
    accept arbitrary image sizes (Bedrock).

    Unlike get_optimal_openai_shape(), this never upscales. A small selection
    gets a large window of real pixels around it rather than a tight crop blown
    up to a fixed shape; only windows above target_pixels are scaled down.

    Args:
        img_width, img_height: Source image size
        sel_bounds: (x1, y1, x2, y2) selection bounds
        target_pixels: Preferred working area in pixels
        min_margin: Smallest context margin around the selection, in pixels

    Returns:
        dict with the same keys the rest of the pipeline expects:
            extract_region: (x, y, width, height) in source coordinates
            target_shape: (width, height) actually sent to the API
            padding_info: scale_factor, scaled_size, padding (always zero here)
    """
    sel_x1, sel_y1, sel_x2, sel_y2 = sel_bounds
    sel_width = max(1, sel_x2 - sel_x1)
    sel_height = max(1, sel_y2 - sel_y1)

    # Grow the selection outwards to about target_pixels, keeping it centred.
    margin = max(min_margin, int(max(sel_width, sel_height) * 0.25))
    side = max(int(target_pixels ** 0.5), sel_width + 2 * margin, sel_height + 2 * margin)

    centre_x = (sel_x1 + sel_x2) // 2
    centre_y = (sel_y1 + sel_y2) // 2
    half = side // 2

    # Clamp to the image, sliding the window back inside rather than shrinking it
    x1 = max(0, min(centre_x - half, img_width - side))
    y1 = max(0, min(centre_y - half, img_height - side))
    x2 = min(img_width, x1 + side)
    y2 = min(img_height, y1 + side)
    x1 = max(0, x2 - side)
    y1 = max(0, y2 - side)

    width = x2 - x1
    height = y2 - y1

    # Keep within the provider's accepted aspect range
    aspect = width / height
    if aspect > MAX_ASPECT:
        width = int(height * MAX_ASPECT)
        x2 = x1 + width
    elif aspect < MIN_ASPECT:
        height = int(width / MIN_ASPECT)
        y2 = y1 + height

    # Only ever scale down
    scale = 1.0
    if width * height > target_pixels:
        scale = (target_pixels / (width * height)) ** 0.5
    scaled_width = max(64, int(width * scale))
    scaled_height = max(64, int(height * scale))

    return {
        'extract_region': (x1, y1, width, height),
        'target_shape': (scaled_width, scaled_height),
        'padding_info': {
            'scale_factor': scale,
            'scaled_size': (scaled_width, scaled_height),
            'padding': (0, 0, 0, 0),
        },
    }
