#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GIMP AI Plugin - Simplified version to fix crash
"""

VERSION = "0.15.0"

import sys
import os
import gi
import json
import urllib.request
import urllib.parse
import urllib.error
import ssl
import base64
import tempfile
import threading
import uuid

gi.require_version("Gimp", "3.0")
gi.require_version("GimpUi", "3.0")
gi.require_version("Gegl", "0.4")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gimp, GimpUi, GLib, Gegl, Gio, Gtk, Gdk, GdkPixbuf

# Import pure coordinate transformation functions
from coordinate_utils import (
    calculate_mask_coordinates,
    calculate_placement_coordinates,
    validate_context_info,
    get_optimal_openai_shape,
    get_gpt_image_shape,
    GPT_IMAGE_MAX_PIXELS,
    GPT_IMAGE_LIMIT_PIXELS,
    calculate_padding_for_shape,
    extract_context_with_selection,
    calculate_result_placement,
    calculate_scale_from_shape,
)

# Amazon Bedrock support is optional - the plugin still works with OpenAI alone
# if this module is missing (e.g. an older install that only copied two files).
try:
    import bedrock_client
except ImportError as e:
    bedrock_client = None
    print(f"DEBUG: Bedrock support unavailable: {e}")

# OpenAI image models offered in Settings, as (model id, label)
OPENAI_IMAGE_MODELS = [
    ("gpt-image-2.5-flare", "GPT-Image-2.5 Flare (fast, recommended)"),
    ("gpt-image-2.5-sunburst", "GPT-Image-2.5 Sunburst (slower, more precise)"),
    ("gpt-image-1", "GPT-Image-1 (legacy)"),
]
DEFAULT_OPENAI_MODEL = "gpt-image-2.5-flare"

# Newer models are slower at high quality, so allow them more time
OPENAI_TIMEOUT = 300

# Reference images picked from other open images. Each one costs input tokens
# at high fidelity, so they are reduced in size. The dialog starts with a few
# slots and more can be added, up to what the OpenAI API accepts in one call
# (the edited image, when there is one, counts towards that limit).
REFERENCE_INITIAL_SLOTS = 2
OPENAI_MAX_INPUT_IMAGES = 16
REFERENCE_MAX_EDGE = 1536

# Dialog styling, scoped to .ai-dialog so GIMP's own widgets are untouched.
# Colours come from the active theme where possible, so light themes work too.
DIALOG_CSS = b"""
.ai-dialog .ai-section-icon { color: @theme_selected_bg_color; }
.ai-dialog .ai-section-title { font-weight: bold; font-size: 1.2em; }
.ai-dialog .ai-section-subtitle { opacity: 0.7; }
.ai-dialog .ai-badge {
    border: 1px solid alpha(@theme_fg_color, 0.35);
    border-radius: 10px;
    padding: 1px 9px;
    font-size: 0.85em;
}
.ai-dialog .ai-prompt-frame { border-radius: 6px; }
.ai-dialog .ai-status {
    border: 1px solid alpha(@theme_fg_color, 0.15);
    border-radius: 8px;
    background-color: alpha(@theme_fg_color, 0.04);
    padding: 10px 14px;
}
.ai-dialog .ai-status-headline { font-weight: bold; }
.ai-dialog .ai-status.error {
    border-color: #e8912d;
    background-color: alpha(#e8912d, 0.12);
}
.ai-dialog .ai-status.error .ai-status-headline { color: #f0a040; }
.ai-dialog .ai-status.success {
    border-color: #4caf50;
    background-color: alpha(#4caf50, 0.10);
}
.ai-dialog .ai-status.success .ai-status-headline { color: #66bb6a; }
.ai-dialog button.suggested-action {
    background-image: none;
    background-color: @theme_selected_bg_color;
    border-color: shade(@theme_selected_bg_color, 0.8);
    color: @theme_selected_fg_color;
}
.ai-dialog button.suggested-action:hover {
    background-color: shade(@theme_selected_bg_color, 1.15);
}
.ai-dialog button.suggested-action:active {
    background-color: shade(@theme_selected_bg_color, 0.9);
}
.ai-dialog button.suggested-action:disabled {
    background-color: alpha(@theme_selected_bg_color, 0.4);
    color: alpha(@theme_selected_fg_color, 0.6);
}
.ai-dialog button.suggested-action label { color: inherit; }
"""

# Icon candidates per section; the first one the icon theme has is used
SECTION_ICONS = {
    "prompt": ["gtk-edit", "gimp-tool-pencil", "document-edit-symbolic"],
    "mode": ["gimp-tool-rect-select", "edit-select-all-symbolic"],
    "size": ["gimp-resize", "image-x-generic-symbolic"],
    "references": ["gimp-images", "insert-image-symbolic", "image-x-generic-symbolic"],
    "status": ["gimp-info", "dialog-information-symbolic"],
    "layers": ["gimp-layers", "view-list-symbolic"],
    "mask": ["gimp-layer-mask", "gimp-selection", "edit-select-all-symbolic"],
    "upscale": ["gimp-tool-scale", "gimp-scale", "zoom-in-symbolic"],
    "erase": ["gimp-tool-eraser", "edit-clear-symbolic"],
    "provider": ["preferences-system", "preferences-system-symbolic"],
    "openai": ["gimp-prefs-folders-plug-ins", "network-server-symbolic"],
    "bedrock": ["gimp-prefs-system-resources", "network-server-symbolic"],
    "history": ["gimp-undo-history", "document-open-recent-symbolic"],
    "debug": ["gimp-prefs-playground", "dialog-warning-symbolic"],
}


class GimpAIPlugin(Gimp.PlugIn):
    """Simplified AI Plugin"""

    def __init__(self):
        super().__init__()
        self.config = self._load_config()
        self._cancel_requested = False
        self._dialog_size = None
        self._dialog_references = []

    def _load_config(self):
        """Load configuration from various locations"""
        # Use GIMP API for primary config location
        try:
            plugin_dir = Gimp.PlugIn.directory()
            gimp_config_path = os.path.join(plugin_dir, "gimp-ai-plugin", "config.json")
        except:
            gimp_config_path = None

        config_paths = []

        # Try GIMP preferences directory first (where we want to save)
        try:
            gimp_user_dir = Gimp.directory()
            gimp_prefs_path = os.path.join(
                gimp_user_dir, "gimp-ai-plugin", "config.json"
            )
            config_paths.append(gimp_prefs_path)
        except:
            pass

        # Then try user config directory (migration path)
        config_paths.append(os.path.expanduser("~/.config/gimp-ai/config.json"))

        # Then try GIMP plugin directory
        if gimp_config_path:
            config_paths.append(gimp_config_path)

        # Fallback paths for backward compatibility
        config_paths.extend(
            [
                os.path.join(os.path.dirname(__file__), "config.json"),
                os.path.expanduser("~/.gimp-ai-config.json"),
            ]
        )

        for config_path in config_paths:
            try:
                if os.path.exists(config_path):
                    with open(config_path, "r") as f:
                        config = json.load(f)
                        print(f"DEBUG: Loaded config from {config_path}")
                        return config
            except Exception as e:
                print(f"DEBUG: Failed to load config from {config_path}: {e}")
                continue

        # Default config with prompt history support
        print("DEBUG: Using default config (no config file found)")
        return {
            "openai": {"api_key": None},
            "settings": {"max_image_size": 512, "timeout": 30},
            "prompt_history": [],
            "last_prompt": "",
        }

    def _save_config(self):
        """Save configuration to GIMP preferences directory"""
        try:
            # Use GIMP's user directory (where preferences are stored)
            gimp_user_dir = Gimp.directory()
            config_dir = os.path.join(gimp_user_dir, "gimp-ai-plugin")
            config_path = os.path.join(config_dir, "config.json")

            # Create directory if it doesn't exist
            os.makedirs(config_dir, exist_ok=True)

            with open(config_path, "w") as f:
                json.dump(self.config, f, indent=4)
            self._restrict_config_permissions(config_path)
            print(f"DEBUG: Saved config to GIMP preferences: {config_path}")
            return True
        except Exception as e:
            print(f"DEBUG: Failed to save config to GIMP preferences: {e}")
            # Fallback to user config directory
            try:
                config_dir = os.path.expanduser("~/.config/gimp-ai")
                config_path = os.path.join(config_dir, "config.json")
                os.makedirs(config_dir, exist_ok=True)
                with open(config_path, "w") as f:
                    json.dump(self.config, f, indent=4)
                self._restrict_config_permissions(config_path)
                print(f"DEBUG: Saved config to fallback location: {config_path}")
                return True
            except Exception as e2:
                print(f"DEBUG: All config save attempts failed: {e2}")
                return False

    def _restrict_config_permissions(self, config_path):
        """The config holds API keys and AWS secrets, so keep it owner-only"""
        try:
            os.chmod(config_path, 0o600)
        except Exception as e:
            # Windows and some filesystems do not support this
            print(f"DEBUG: Could not restrict config permissions: {e}")

    def _make_url_request(self, req_or_url, timeout=60, headers=None):
        """
        Make URL request with automatic SSL fallback for certificate errors.

        Args:
            req_or_url: Either a urllib.request.Request object or URL string
            timeout: Request timeout in seconds (default: 60)
            headers: Optional dict of headers to add (only if req_or_url is string)

        Returns:
            urllib response object

        Raises:
            urllib.error.URLError: If both normal and SSL-bypassed requests fail
        """
        try:
            # First attempt with normal SSL verification
            if isinstance(req_or_url, str):
                # Create Request object from URL string
                req = urllib.request.Request(req_or_url)
                if headers:
                    for key, value in headers.items():
                        req.add_header(key, value)
            else:
                req = req_or_url

            return urllib.request.urlopen(req, timeout=timeout)

        except (ssl.SSLError, urllib.error.URLError) as ssl_err:
            # Check if it's an SSL-related error
            if "SSL" in str(ssl_err) or "CERTIFICATE" in str(ssl_err):
                print(
                    f"DEBUG: SSL verification failed, trying with SSL bypass: {ssl_err}"
                )
            else:
                # Not an SSL error, re-raise it
                raise ssl_err

            # Fallback to unverified SSL if certificate fails
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            try:
                return urllib.request.urlopen(req, context=ctx, timeout=timeout)
            except Exception as fallback_err:
                print(f"DEBUG: SSL bypass also failed: {fallback_err}")
                raise fallback_err

    def _add_to_prompt_history(self, prompt):
        """Add prompt to history, keeping last 10 unique prompts"""
        if not prompt.strip():
            return

        # Remove if already exists to avoid duplicates
        history = self.config.get("prompt_history", [])
        if prompt in history:
            history.remove(prompt)

        # Add to beginning
        history.insert(0, prompt)

        # Keep only last 10
        history = history[:10]

        self.config["prompt_history"] = history
        self.config["last_prompt"] = prompt
        self._save_config()

    def _get_prompt_history(self):
        """Get prompt history list"""
        return self.config.get("prompt_history", [])

    def _get_last_prompt(self):
        """Get the last used prompt"""
        return self.config.get("last_prompt", "")

    def _get_api_key(self):
        """Get OpenAI API key from config or environment"""
        # Try config file first
        if (
            self.config
            and "openai" in self.config
            and self.config["openai"].get("api_key")
        ):
            return self.config["openai"]["api_key"]

        # Try environment variable
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            return api_key

        # No API key found
        return None

    def _get_openai_model(self):
        """OpenAI image model id from config, defaulting to GPT-Image-2.5 Flare"""
        model = self.config.get("openai", {}).get("model")
        return model or DEFAULT_OPENAI_MODEL

    def _openai_accepts_any_size(self):
        """gpt-image-1 only takes three fixed shapes; the 2.x models take any size"""
        return self._get_openai_model() != "gpt-image-1"

    def _openai_generation_size(self, width, height, exact=False):
        """
        OpenAI generation size string for a target of width x height.

        exact: the user picked this size for a new image, so honour it up to
        the API limit; otherwise (fitting an existing canvas) stay within the
        non-experimental size.
        """
        if self._openai_accepts_any_size():
            max_pixels = GPT_IMAGE_LIMIT_PIXELS if exact else GPT_IMAGE_MAX_PIXELS
            target_w, target_h = get_gpt_image_shape(width, height, max_pixels)
        else:
            target_w, target_h = get_optimal_openai_shape(width, height)
        return f"{target_w}x{target_h}"

    def _generation_size_presets(self):
        """
        Size choices for generating a new image: ([(label, (w, h))], allow_custom).

        gpt-image-1 has three fixed shapes and Stability picks its own size from
        an aspect ratio, so only GPT-Image-2.x offers larger and custom sizes.
        """
        if self._get_provider() == "bedrock":
            return [
                ("Square 1:1", (1024, 1024)),
                ("Landscape 3:2", (1536, 1024)),
                ("Portrait 2:3", (1024, 1536)),
                ("Widescreen 16:9", (1920, 1080)),
                ("Tall 9:16", (1080, 1920)),
            ], False
        if not self._openai_accepts_any_size():
            return [
                ("Square 1024x1024", (1024, 1024)),
                ("Landscape 1536x1024", (1536, 1024)),
                ("Portrait 1024x1536", (1024, 1536)),
            ], False
        return [
            ("Square 1024x1024", (1024, 1024)),
            ("Square 2048x2048", (2048, 2048)),
            ("Landscape 3:2 1536x1024", (1536, 1024)),
            ("Portrait 2:3 1024x1536", (1024, 1536)),
            ("Widescreen 16:9 2048x1152", (2048, 1152)),
            ("Tall 9:16 1152x2048", (1152, 2048)),
            ("4K landscape 3840x2160 (experimental)", (3840, 2160)),
            ("4K portrait 2160x3840 (experimental)", (2160, 3840)),
        ], True

    def _add_reference_pickers(self, content_area, exclude_image=None,
                               title="Reference Images",
                               max_slots=OPENAI_MAX_INPUT_IMAGES):
        """
        Add combo boxes listing the other open images with thumbnails, starting
        with REFERENCE_INITIAL_SLOTS and a button that adds more, up to
        max_slots. Returns the list of combo boxes for _read_reference_pickers();
        slots added later are appended to that same list.
        """
        exclude_id = exclude_image.get_id() if exclude_image else None
        candidates = [
            img for img in Gimp.get_images() if img.get_id() != exclude_id
        ]

        # The add button sits in the section header to keep the dialog short
        add_button = None
        if candidates:
            add_button = Gtk.Button(label="Add reference")
            add_button.set_image(
                Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON)
            )
            add_button.set_always_show_image(True)

        box = self._add_section(
            content_area, title,
            "Use other open images as references for the result. Refer to "
            "them in the prompt as reference 1, reference 2, and so on.",
            icon="references", badge="Optional", header_widget=add_button,
        )

        if not candidates:
            note = Gtk.Label(label="Open other images in GIMP to use them as references")
            note.set_halign(Gtk.Align.START)
            note.get_style_context().add_class("dim-label")
            box.pack_start(note, False, False, 0)
            return []

        # One shared model: thumbnail, label, image id (-1 = none)
        store = Gtk.ListStore(GdkPixbuf.Pixbuf, str, int)
        store.append([None, "(none)", -1])
        for img in candidates:
            try:
                thumbnail = img.get_thumbnail(
                    32, 32, Gimp.PixbufTransparency.SMALL_CHECKS
                )
            except Exception as e:
                print(f"DEBUG: No thumbnail for image {img.get_id()}: {e}")
                thumbnail = None
            store.append([thumbnail, self._image_label(img), img.get_id()])
        ids = [row[2] for row in store]
        # More slots than images to choose from would only ever stay empty
        max_slots = min(max_slots, len(candidates))

        # Preselect what was used last time, if the same image is still open
        last = self.config.get("last_reference_images", [])
        combos = []
        rows_box = Gtk.VBox(spacing=4)
        box.pack_start(rows_box, False, False, 0)

        def taken_elsewhere(combo, image_id):
            """True if another slot already has this image selected"""
            if image_id < 0:
                return False
            for other in combos:
                if other is combo:
                    continue
                other_iter = other.get_active_iter()
                if other_iter and store[other_iter][2] == image_id:
                    return True
            return False

        def grey_out_taken(combo, cell, model, tree_iter, _data):
            cell.set_property(
                "sensitive", not taken_elsewhere(combo, model[tree_iter][2])
            )

        def remembered_index(slot, combo):
            """Store row of the image used in this slot last time, or 0"""
            if slot < len(last) and last[slot]:
                image_id = last[slot].get("id")
                if image_id in ids and not taken_elsewhere(combo, image_id):
                    match = Gimp.Image.get_by_id(image_id)
                    if match and match.get_name() == last[slot].get("name"):
                        return ids.index(image_id)
            return 0

        def add_slot():
            slot = len(combos)
            row = Gtk.HBox(spacing=8)
            label = Gtk.Label(label=f"Reference {slot + 1}:")
            row.pack_start(label, False, False, 0)

            combo = Gtk.ComboBox.new_with_model(store)
            pixbuf_cell = Gtk.CellRendererPixbuf()
            combo.pack_start(pixbuf_cell, False)
            combo.add_attribute(pixbuf_cell, "pixbuf", 0)
            text_cell = Gtk.CellRendererText()
            combo.pack_start(text_cell, True)
            combo.add_attribute(text_cell, "text", 1)
            # An image picked in one slot can't be picked again in another
            combo.set_cell_data_func(pixbuf_cell, grey_out_taken, None)
            combo.set_cell_data_func(text_cell, grey_out_taken, None)
            combos.append(combo)
            combo.set_active(remembered_index(slot, combo))

            row.pack_start(combo, True, True, 0)
            rows_box.pack_start(row, False, False, 0)
            row.show_all()

        # Start with the usual slots, or enough to restore last time's choices
        initial = REFERENCE_INITIAL_SLOTS
        for slot in range(len(last)):
            if remembered_index(slot, None):
                initial = max(initial, slot + 1)
        for _ in range(min(initial, max_slots)):
            add_slot()

        def on_add_clicked(_button):
            add_slot()
            add_button.set_sensitive(len(combos) < max_slots)

        add_button.connect("clicked", on_add_clicked)
        add_button.set_sensitive(len(combos) < max_slots)
        return combos

    def _read_reference_pickers(self, combos):
        """Selected reference images, in slot order, without duplicates"""
        if not combos:
            return []

        selected = []
        remembered = []
        for combo in combos:
            tree_iter = combo.get_active_iter()
            image_id = combo.get_model()[tree_iter][2] if tree_iter else -1
            ref = Gimp.Image.get_by_id(image_id) if image_id >= 0 else None
            if ref and ref.is_valid():
                remembered.append({"id": image_id, "name": ref.get_name()})
                if image_id not in [img.get_id() for img in selected]:
                    selected.append(ref)
            else:
                remembered.append(None)

        self.config["last_reference_images"] = remembered
        return selected

    def _image_label(self, img):
        """Name as GIMP shows it, plus id and size to tell copies apart"""
        return f"{img.get_name()}-{img.get_id()}  ({img.get_width()}x{img.get_height()})"

    def _export_references(self, references):
        """
        Flatten each reference image (on a copy) to PNG bytes, reduced to
        REFERENCE_MAX_EDGE. Images that cannot be exported are skipped.
        """
        pngs = []
        for ref in references:
            if not ref or not ref.is_valid():
                print("DEBUG: Reference image was closed, skipping")
                continue
            temp_image = ref.duplicate()
            try:
                if temp_image.get_base_type() != Gimp.ImageBaseType.RGB:
                    temp_image.convert_rgb()
                temp_image.merge_visible_layers(Gimp.MergeType.CLIP_TO_IMAGE)

                width, height = temp_image.get_width(), temp_image.get_height()
                longest = max(width, height)
                if longest > REFERENCE_MAX_EDGE:
                    scale = REFERENCE_MAX_EDGE / longest
                    Gimp.context_push()
                    try:
                        Gimp.context_set_interpolation(Gimp.InterpolationType.LOHALO)
                        temp_image.scale(
                            max(1, round(width * scale)), max(1, round(height * scale))
                        )
                    finally:
                        Gimp.context_pop()

                png = self._export_layer_to_png(temp_image)
                if png:
                    pngs.append(png)
                    print(
                        f"DEBUG: Reference {ref.get_name()} exported "
                        f"{temp_image.get_width()}x{temp_image.get_height()}, {len(png)} bytes"
                    )
            except Exception as e:
                print(f"DEBUG: Could not export reference {ref.get_name()}: {e}")
            finally:
                temp_image.delete()
        return pngs

    def _prompt_with_reference_note(self, prompt, count):
        """
        Tell the model which input image is which when the edited image comes
        first and the references follow it.
        """
        refs = ", ".join(f"reference {i + 1} is image {i + 2}" for i in range(count))
        return f"{prompt}\n\n(Image 1 is the picture being edited; {refs}.)"

    def _openai_mask_hint(self):
        """
        Also show OpenAI the selection as a white-on-black image?

        The 2.x models see the edits mask as a blacked-out hole in the input
        and paint around it instead of into it, so the selection is sent once
        more as a picture the model can read. Config: openai.mask_hint.
        """
        default = self._get_openai_model() != "gpt-image-1"
        return bool(self.config.get("openai", {}).get("mask_hint", default))

    def _prompt_with_mask_hint(self, prompt, reference_count):
        """Point the model at the mask image (image 2); references follow it"""
        refs = "".join(
            f" Image {i + 3} is reference {i + 1}." for i in range(reference_count)
        )
        return (
            "Edit image 1 only inside the region shown in white in image 2. "
            "In image 1 that region may look empty or black: that is not part "
            "of the picture, it is the area to work on. Fill it completely, "
            "blend it seamlessly with its surroundings and leave everything "
            f"outside it unchanged.{refs}\n\n"
            f"Instruction for the region: {prompt}"
        )

    def _get_provider(self):
        """Which API provider handles inpainting: 'openai' or 'bedrock'"""
        provider = self.config.get("api_provider", "openai")
        if provider == "bedrock" and bedrock_client is None:
            print("DEBUG: Bedrock configured but module missing, falling back to OpenAI")
            return "openai"
        return provider

    def _credentials_ready(self, provider="openai"):
        """
        Is the given provider usable? Every credential gate routes through here,
        so an OpenAI key is never demanded for a Bedrock operation.
        """
        if provider == "bedrock":
            try:
                creds = bedrock_client.resolve_credentials(self.config.get("bedrock", {}))
                print(
                    f"DEBUG: Bedrock credentials ready via {creds['source']} "
                    f"(region {creds['region']}, model {creds['model_id']})"
                )
                return True
            except bedrock_client.BedrockError as e:
                print(f"DEBUG: Bedrock credentials not ready: {e}")
                self._log_bedrock_environment()
                return False
        return bool(self._get_api_key())

    def _log_bedrock_environment(self):
        """Print what the plugin can actually see, for diagnosing credential problems"""
        print("DEBUG: --- Bedrock environment ---")
        for name in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "AWS_BEARER_TOKEN_BEDROCK",
            "AWS_PROFILE",
            "AWS_REGION",
            "AWS_DEFAULT_REGION",
        ):
            value = os.environ.get(name)
            if not value:
                print(f"DEBUG:   {name}: not set")
            elif "SECRET" in name or "TOKEN" in name:
                print(f"DEBUG:   {name}: set ({len(value)} chars)")
            else:
                print(f"DEBUG:   {name}: {value}")

        aws_dir = os.path.expanduser("~/.aws")
        creds_file = os.path.join(aws_dir, "credentials")
        if not os.path.exists(aws_dir):
            print(f"DEBUG:   {aws_dir}: not present")
        elif not os.access(creds_file, os.R_OK):
            print(
                f"DEBUG:   {creds_file}: exists but unreadable - a sandboxed GIMP "
                "(snap/flatpak) cannot read hidden directories outside ~/.config/GIMP"
            )
        else:
            print(f"DEBUG:   {creds_file}: readable")

        config_keys = sorted(self.config.get("bedrock", {}).keys())
        print(f"DEBUG:   config 'bedrock' keys: {config_keys or 'none'}")
        print("DEBUG: ---------------------------")

    def _get_processing_mode(self, dialog_mode=None):
        """Determine processing mode based on dialog selection or fallback to config"""
        if dialog_mode:
            return dialog_mode

        # Fallback to last used mode from config
        return self.config.get("last_mode", "contextual")

    def _update_progress(self, progress_label, message, gimp_progress=None):
        """Update progress message in dialog with proper emoji encoding, optionally update GIMP progress bar"""
        if progress_label:
            try:
                # Ensure the message is properly encoded for GTK
                # GTK should handle UTF-8 properly, but let's be explicit
                if isinstance(message, str):
                    encoded_message = message.encode("utf-8").decode("utf-8")
                else:
                    encoded_message = str(message)

                # Use GLib.idle_add to ensure the update happens on the main thread
                def update_ui():
                    try:
                        print(
                            f"DEBUG: Actually updating progress label to: {encoded_message}"
                        )
                        self._set_status_text(progress_label, encoded_message)
                        return False  # Remove from idle queue after running once
                    except Exception as e:
                        print(f"DEBUG: UI update failed: {e}")
                        return False

                # Queue the update on the main thread
                GLib.idle_add(update_ui)

                # On the main thread, paint it now: the caller is usually about
                # to do blocking work (exporting images) before the API thread
                if threading.current_thread() is threading.main_thread():
                    self._flush_ui()

            except Exception as e:
                print(f"DEBUG: Progress update failed: {e}")
                # Fallback without emojis if there's encoding issue
                fallback = (
                    message.encode("ascii", "ignore").decode("ascii")
                    if message
                    else "Processing..."
                )
                try:
                    progress_label.set_text(fallback)
                except:
                    pass

        # Update GIMP progress bar if fraction provided
        if gimp_progress is not None:
            try:
                Gimp.progress_set_text(message)
                Gimp.progress_update(gimp_progress)
                Gimp.displays_flush()
            except:
                pass  # Ignore if not in right context

        return False  # Return False for GLib.idle_add compatibility

    def _create_progress_callback(self, progress_label):
        """Create a reusable progress callback for threading"""

        def progress_callback(message):
            def update_ui():
                self._update_progress(progress_label, message)
                return False

            GLib.idle_add(update_ui)

        return progress_callback

    def _create_progress_widget(self):
        """
        Status box for dialogs: a bold headline (the first line of a message)
        over the details. Returns (box to pack, headline label); the label is
        what _update_progress() takes.
        """
        box = Gtk.VBox(spacing=4)
        box.get_style_context().add_class("ai-status")

        headline = Gtk.Label()
        headline.get_style_context().add_class("ai-status-headline")
        detail = Gtk.Label()
        detail.set_no_show_all(True)
        # Error messages from the APIs can be long; wrap instead of widening.
        # Selectable so an error can be copied.
        for label in (headline, detail):
            label.set_xalign(0.0)
            label.set_line_wrap(True)
            label.set_max_width_chars(70)
            label.set_selectable(True)
            label.set_can_focus(False)
            box.pack_start(label, False, False, 0)

        headline._ai_detail = detail
        headline._ai_box = box
        self._set_status_text(headline, "Ready to start...")
        return box, headline

    def _offer_retry(
        self, progress_label, headline, details="",
        hint="Adjust the settings and press OK to try again.",
    ):
        """
        Show a failed attempt in the dialog's status box and hand the dialog
        back to the user through self._retry_dialog, which the dialog that
        is open sets up. Returns its new result, or None once the user has
        closed it (the dialog has then destroyed itself).
        """
        message = f"❌ {headline}\n" + (f"{details}\n\n" if details else "") + hint
        self._update_progress(progress_label, message)
        return self._retry_dialog()

    def _set_status_text(self, progress_label, message):
        """Show message in a status box, styled by its leading emoji"""
        detail = getattr(progress_label, "_ai_detail", None)
        box = getattr(progress_label, "_ai_box", None)
        if detail is None or box is None:
            progress_label.set_text(message)
            return

        first, _, rest = message.partition("\n")
        progress_label.set_text(first)
        detail.set_text(rest.strip())
        detail.set_visible(bool(rest.strip()))

        style = box.get_style_context()
        style.remove_class("error")
        style.remove_class("success")
        if first.startswith(("❌", "⚠")):
            style.add_class("error")
        elif first.startswith(("✅", "✓")):
            style.add_class("success")

    def _init_gimp_ui(self):
        """Initialize GIMP UI system if not already done"""
        if not hasattr(self, "_ui_initialized"):
            GimpUi.init("gimp-ai-plugin")
            self._ui_initialized = True
            try:
                provider = Gtk.CssProvider()
                try:
                    provider.load_from_data(DIALOG_CSS)
                except GLib.Error:
                    # Theme without the standard named colours
                    provider.load_from_data(
                        DIALOG_CSS.replace(b"@theme_selected_bg_color", b"#4a90d9")
                        .replace(b"@theme_fg_color", b"#888888")
                    )
                Gtk.StyleContext.add_provider_for_screen(
                    Gdk.Screen.get_default(),
                    provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )
            except Exception as e:
                print(f"DEBUG: Dialog CSS not applied: {e}")

    def _section_icon(self, key):
        """Image for a section header, or None if the theme has no candidate"""
        theme = Gtk.IconTheme.get_default()
        for name in SECTION_ICONS.get(key, []):
            if theme.has_icon(name):
                image = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.DND)
                image.set_valign(Gtk.Align.START)
                image.get_style_context().add_class("ai-section-icon")
                return image
        return None

    def _add_section(self, parent, title, subtitle=None, icon=None, badge=None,
                     separator=True, header_widget=None):
        """
        Add a section header (icon, bold title, optional badge and subtitle),
        preceded by a separator, and return the box for the section's widgets.
        header_widget (e.g. a button) is placed at the right end of the header.
        """
        if separator:
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            sep.set_margin_top(4)
            sep.set_margin_bottom(4)
            parent.pack_start(sep, False, False, 0)

        section = Gtk.VBox(spacing=8)
        parent.pack_start(section, False, False, 0)

        header = Gtk.HBox(spacing=12)
        section.pack_start(header, False, False, 0)
        image = self._section_icon(icon) if icon else None
        if image:
            header.pack_start(image, False, False, 0)

        text = Gtk.VBox(spacing=2)
        header.pack_start(text, True, True, 0)
        title_row = Gtk.HBox(spacing=10)
        text.pack_start(title_row, False, False, 0)
        title_label = Gtk.Label(label=title)
        title_label.set_xalign(0.0)
        title_label.get_style_context().add_class("ai-section-title")
        title_row.pack_start(title_label, False, False, 0)
        if badge:
            badge_label = Gtk.Label(label=badge)
            badge_label.set_valign(Gtk.Align.CENTER)
            badge_label.get_style_context().add_class("ai-badge")
            title_row.pack_start(badge_label, False, False, 0)
        if subtitle:
            subtitle_label = Gtk.Label(label=subtitle)
            subtitle_label.set_xalign(0.0)
            subtitle_label.set_line_wrap(True)
            subtitle_label.get_style_context().add_class("ai-section-subtitle")
            text.pack_start(subtitle_label, False, False, 0)
        if header_widget:
            header_widget.set_valign(Gtk.Align.CENTER)
            header.pack_end(header_widget, False, False, 0)

        body = Gtk.VBox(spacing=8)
        section.pack_start(body, False, False, 0)
        return body

    def _create_dialog_base(self, title="Dialog", size=(500, 400)):
        """Create a standard GIMP dialog with consistent styling"""
        self._init_gimp_ui()

        # Create dialog with header bar detection
        use_header_bar = Gtk.Settings.get_default().get_property(
            "gtk-dialogs-use-header"
        )
        dialog = GimpUi.Dialog(use_header_bar=use_header_bar, title=title)
        dialog.get_style_context().add_class("ai-dialog")

        # Set up dialog properties
        dialog.set_default_size(size[0], size[1])
        dialog.set_resizable(True)

        return dialog

    def _setup_dialog_content_area(self, dialog, spacing=15, margin=20):
        """Set up dialog content area with consistent styling"""
        content_area = dialog.get_content_area()
        content_area.set_spacing(spacing)
        content_area.set_margin_start(margin)
        content_area.set_margin_end(margin)
        content_area.set_margin_top(margin)
        content_area.set_margin_bottom(margin)
        return content_area

    def _add_api_warning_bar(self, content_area, dialog, provider="openai"):
        """Add API key warning info bar if needed, returns (warning_bar, ok_button_needs_config)"""
        if self._credentials_ready(provider):
            return None, False

        warning_text = (
            "⚠️ AWS credentials for Bedrock not configured"
            if provider == "bedrock"
            else "⚠️ OpenAI API key not configured"
        )

        # Create warning info bar
        api_warning_bar = Gtk.InfoBar()
        api_warning_bar.set_message_type(Gtk.MessageType.WARNING)
        api_warning_bar.set_show_close_button(False)

        # Warning message
        warning_label = Gtk.Label()
        warning_label.set_markup(warning_text)
        warning_label.set_halign(Gtk.Align.START)

        # Configure button - connect to main dialog response
        configure_button = api_warning_bar.add_button(
            "Configure Now", Gtk.ResponseType.APPLY
        )

        # Connect the InfoBar response to the main dialog
        def on_configure_clicked(infobar, response_id):
            if response_id == Gtk.ResponseType.APPLY:
                dialog.response(Gtk.ResponseType.APPLY)

        api_warning_bar.connect("response", on_configure_clicked)

        # Add label to info bar content area
        info_content = api_warning_bar.get_content_area()
        info_content.pack_start(warning_label, False, False, 0)

        content_area.pack_start(api_warning_bar, False, False, 5)

        return api_warning_bar, True

    def _model_info_rows(self, operation, variant=None):
        """
        (label, value) rows naming the model and main parameters an operation
        will send: 'generate', 'inpaint', 'composite', 'erase' or 'upscale'.
        Read from config the same way the API calls read it, so the box shows
        what is actually sent.
        """
        if self._get_provider() != "bedrock":
            model = self._get_openai_model()
            labels = dict(OPENAI_IMAGE_MODELS)
            params = ["quality high", "1 image"]
            if operation in ("inpaint", "composite"):
                params.append("moderation low")
                params.append(
                    "input fidelity high"
                    if model == "gpt-image-1"
                    else "high fidelity (built in)"
                )
            return [
                ("Provider", "OpenAI"),
                ("Model", labels.get(model, model)),
                ("Parameters", ", ".join(params)),
            ]

        cfg = self.config.get("bedrock", {})
        if operation == "upscale":
            variant = variant or cfg.get("upscale_variant", "conservative")
            if variant not in bedrock_client.UPSCALE_VARIANTS:
                variant = "conservative"
            model_id = bedrock_client.UPSCALE_VARIANTS[variant]["model_id"]
            get_creds = lambda c: bedrock_client.upscale_credentials(c, variant)
        elif operation == "erase":
            model_id = cfg.get("erase_model_id") or bedrock_client.DEFAULT_ERASE_MODEL_ID
            get_creds = bedrock_client.erase_credentials
        elif operation == "generate":
            model_id = (
                cfg.get("generation_model_id")
                or bedrock_client.DEFAULT_GENERATION_MODEL_ID
            )
            get_creds = bedrock_client.generation_credentials
        else:
            model_id = cfg.get("model_id") or bedrock_client.DEFAULT_MODEL_ID
            get_creds = bedrock_client.resolve_credentials

        # Credentials also settle the region and inference-profile prefix;
        # without them the warning bar already says what is missing
        region = "not configured"
        try:
            creds = get_creds(cfg)
            model_id, region = creds["model_id"], creds["region"]
        except bedrock_client.BedrockError:
            pass

        seed = int(cfg.get("seed", 0))
        seed_text = f"seed {seed}" if seed else "random seed"
        grow_text = f"grow mask {cfg.get('grow_mask', 5)}px"
        params = []
        if operation in ("inpaint", "erase"):
            count = cfg.get(f"{operation}_variations", 3)
            count = max(1, min(4, int(count)))
            params += [f"{count} variations (random seeds)", grow_text]
        elif operation == "composite":
            params += [seed_text, grow_text]
        elif operation == "generate":
            params += [seed_text, "aspect ratio from size"]
        elif operation == "upscale":
            if bedrock_client.UPSCALE_VARIANTS[variant]["needs_prompt"]:
                creativity = max(0.1, min(0.5, float(cfg.get("upscale_creativity", 0.35))))
                params += [f"creativity {creativity:.2f}", seed_text]
            else:
                params.append("exactly 4x")
            params.append("WebP output")

        rows = [
            ("Provider", "Amazon Bedrock"),
            ("Model", model_id),
            ("Region", region),
            ("Parameters", ", ".join(params)),
        ]
        negative = cfg.get("negative_prompt", "")
        if negative and operation in ("inpaint", "composite", "generate"):
            short = negative if len(negative) <= 50 else negative[:50] + "..."
            rows.append(("Negative prompt", short))
        return rows

    def _add_model_info_box(self, content_area, operation, variant=None):
        """
        Add one dim line naming the model and main parameters the operation
        will use. Returns refresh(variant=None) to call after Settings closes or
        the upscale variant changes.
        """
        label = Gtk.Label()
        label.set_xalign(0.0)
        label.set_line_wrap(True)
        label.set_selectable(True)
        label.get_style_context().add_class("dim-label")
        content_area.pack_start(label, False, False, 0)

        # The upscale variant changes with its combo; Settings refreshes keep it
        state = {"variant": variant}

        def refresh(new_variant=None):
            if new_variant:
                state["variant"] = new_variant
            try:
                rows = self._model_info_rows(operation, state["variant"])
            except Exception as e:
                print(f"DEBUG: Model info unavailable: {e}")
                rows = [("Model", "unknown")]
            # Provider, model and parameters read on their own; the rest
            # need their name to make sense
            parts = [
                value if key in ("Provider", "Model", "Parameters")
                else f"{key.lower()}: {value}"
                for key, value in rows
            ]
            label.set_text("Uses: " + " · ".join(parts))

        refresh()
        return refresh

    def _is_debug_mode(self):
        """Check if debug mode is enabled (saves temp files to system temp directory)"""
        # Check config first
        debug = self.config.get("debug_mode", False)
        # Allow environment variable override
        if os.environ.get("GIMP_AI_DEBUG") == "1":
            debug = True
        return debug

    def _flush_ui(self):
        """Process all pending GTK events so queued widget changes get painted"""
        if getattr(self, "_flushing_ui", False):
            return  # Already inside a flush (e.g. called from an idle callback)
        self._flushing_ui = True
        try:
            event_count = 0
            while Gtk.events_pending() and event_count < 100:
                Gtk.main_iteration_do(False)
                event_count += 1
        except Exception as e:
            print(f"DEBUG: GTK flush warning (non-fatal): {e}")
        finally:
            self._flushing_ui = False

    def _check_cancel_and_process_events(self):
        """Check if cancel was requested and process minimal UI events"""
        # Process GTK events more aggressively for better UI responsiveness
        try:
            # Process multiple pending events to improve responsiveness
            if hasattr(Gtk, "events_pending"):
                event_count = 0
                while (
                    Gtk.events_pending() and event_count < 5
                ):  # Process up to 5 events
                    Gtk.main_iteration_do(False)  # Non-blocking iteration
                    event_count += 1
        except Exception as e:
            print(f"DEBUG: GTK event processing warning (non-fatal): {e}")

        return self._cancel_requested

    def _show_prompt_dialog(
        self,
        title="AI Prompt",
        default_text="",
        show_mode_selection=True,
        image=None,
        provider="openai",
        size_presets=None,
        allow_custom_size=False,
        show_references=False,
        operation="generate",
    ):
        """
        Show a GIMP UI dialog to get user input for AI prompt.

        operation: 'generate' or 'inpaint', for the model & parameters box.

        size_presets: [(label, (w, h))] adds an image size picker (used when
        generating a new image); the choice is left in self._dialog_size.
        show_references: adds pickers for reference images taken from the
        other open images (not `image`); the choice is left in
        self._dialog_references as a list of Gimp.Image.
        """
        # Use last prompt as default if available, otherwise use provided default
        if not default_text:
            default_text = self._get_last_prompt()
        if not default_text:
            if title == "AI Inpaint":
                default_text = "Describe the area to inpaint (e.g. 'remove object', 'fix background')"
            else:
                default_text = "Describe what you want to generate..."
        try:
            # Create dialog using helper methods
            dialog = self._create_dialog_base(title, (600, 300))

            # Add buttons using GIMP's standard approach
            dialog.add_button(
                "Settings", Gtk.ResponseType.HELP
            )  # Use HELP for Settings
            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
            ok_button = dialog.add_button("OK", Gtk.ResponseType.OK)
            ok_button.set_can_default(True)
            ok_button.grab_default()
            ok_button.get_style_context().add_class("suggested-action")

            # Set up content area using helper
            content_area = self._setup_dialog_content_area(dialog, spacing=10)

            # Add API warning bar using helper
            api_warning_bar, needs_config = self._add_api_warning_bar(
                content_area, dialog, provider
            )
            if needs_config:
                # Disable OK button when no API key
                ok_button.set_sensitive(False)
                ok_button.set_label("Configure & Continue")

            # Sections are numbered in the order they appear
            section_number = [0]

            def numbered(text):
                section_number[0] += 1
                return f"{section_number[0]}. {text}"

            prompt_box = self._add_section(
                content_area, numbered("Prompt"),
                "Describe the change you want in the selected area."
                if operation == "inpaint"
                else "Describe the image you want to generate.",
                icon="prompt", separator=False,
            )

            # Prompt history dropdown
            history = self._get_prompt_history()
            history_combo = None
            if history:
                history_label = Gtk.Label(label="Recent prompts:")
                history_label.set_halign(Gtk.Align.START)
                prompt_box.pack_start(history_label, False, False, 0)

                history_combo = Gtk.ComboBoxText()
                history_combo.append_text("Select from recent prompts...")
                for prompt in history:
                    # Truncate long prompts for display
                    display_prompt = prompt[:60] + "..." if len(prompt) > 60 else prompt
                    history_combo.append_text(display_prompt)
                history_combo.set_active(0)
                prompt_box.pack_start(history_combo, False, False, 0)

            # Multiline text view for prompts
            scrolled_window = Gtk.ScrolledWindow()
            scrolled_window.set_policy(
                Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
            )
            scrolled_window.set_shadow_type(Gtk.ShadowType.IN)
            scrolled_window.get_style_context().add_class("ai-prompt-frame")
            scrolled_window.set_size_request(560, 120)

            text_view = Gtk.TextView()
            text_view.set_wrap_mode(Gtk.WrapMode.WORD)
            text_view.set_border_width(8)

            # Set default text
            text_buffer = text_view.get_buffer()
            text_buffer.set_text(default_text)

            scrolled_window.add(text_view)
            prompt_box.pack_start(scrolled_window, True, True, 0)

            # Add mode selection (only for inpainting)
            focused_radio = None
            full_radio = None
            if show_mode_selection:
                mode_box = self._add_section(
                    content_area, numbered("Processing Mode"),
                    "How much of the image the model gets to see.",
                    icon="mode",
                )

                # Get last used mode from config
                config = self._load_config()
                last_mode = config.get("last_mode", "contextual")

                # Radio buttons for mode selection
                focused_radio = Gtk.RadioButton.new_with_label(
                    None,
                    "Focused (High Detail) - Best for small edits, maximum resolution",
                )
                focused_radio.set_name("contextual")
                mode_box.pack_start(focused_radio, False, False, 2)

                full_radio = Gtk.RadioButton.new_with_label_from_widget(
                    focused_radio,
                    "Full Image (Consistent) - Best for large changes, visual consistency",
                )
                full_radio.set_name("full_image")
                mode_box.pack_start(full_radio, False, False, 2)

                # Set active radio based on last used mode
                if last_mode == "full_image":
                    full_radio.set_active(True)
                else:
                    focused_radio.set_active(True)

                # The selection always goes to the API as the mask; this only
                # decides whether the result layer's mask is applied afterwards
                clip_checkbox = Gtk.CheckButton()
                clip_checkbox.set_label(
                    "Clip result to selection (untick to keep edges the model drew outside)"
                )
                clip_checkbox.set_active(self.config.get("clip_to_selection", True))
                mode_box.pack_start(clip_checkbox, False, False, 6)

            # Image size picker (only when generating a new image)
            size_combo = None
            if size_presets:
                size_vbox = self._add_section(
                    content_area, numbered("Image Size"),
                    "Choose the size of the generated image.",
                    icon="size",
                )

                size_row = Gtk.HBox(spacing=8)
                size_vbox.pack_start(size_row, False, False, 0)

                size_combo = Gtk.ComboBoxText()
                for size_label, _ in size_presets:
                    size_combo.append_text(size_label)
                if allow_custom_size:
                    size_combo.append_text("Custom")
                size_row.pack_start(size_combo, True, True, 0)

                width_spin = Gtk.SpinButton.new_with_range(16, 3840, 16)
                height_spin = Gtk.SpinButton.new_with_range(16, 3840, 16)
                size_row.pack_start(width_spin, False, False, 0)
                size_row.pack_start(Gtk.Label(label="×"), False, False, 0)
                size_row.pack_start(height_spin, False, False, 0)

                size_note = Gtk.Label(
                    label="Sizes are rounded to what the model accepts"
                    if allow_custom_size
                    else "The model picks the exact size for this shape"
                )
                size_note.set_halign(Gtk.Align.START)
                size_note.get_style_context().add_class("dim-label")
                size_vbox.pack_start(size_note, False, False, 0)

                preset_sizes = [tuple(size) for _, size in size_presets]
                last_size = tuple(
                    self.config.get("last_generate_size", preset_sizes[0])
                )
                width_spin.set_value(last_size[0])
                height_spin.set_value(last_size[1])

                def on_size_changed(combo):
                    index = combo.get_active()
                    custom = index >= len(size_presets)
                    if 0 <= index < len(size_presets):
                        preset_w, preset_h = size_presets[index][1]
                        width_spin.set_value(preset_w)
                        height_spin.set_value(preset_h)
                    width_spin.set_sensitive(custom)
                    height_spin.set_sensitive(custom)

                size_combo.connect("changed", on_size_changed)
                if last_size in preset_sizes:
                    size_combo.set_active(preset_sizes.index(last_size))
                elif allow_custom_size:
                    size_combo.set_active(len(size_presets))
                else:
                    size_combo.set_active(0)

            # Reference image pickers (OpenAI only)
            reference_combos = []
            if show_references:
                # When inpainting, the edited image (and the mask hint, if
                # sent) go first and count towards the API's input image limit
                reserved = 0
                if operation == "inpaint":
                    reserved = 2 if self._openai_mask_hint() else 1
                reference_combos = self._add_reference_pickers(
                    content_area, image, numbered("Reference Images"),
                    max_slots=OPENAI_MAX_INPUT_IMAGES - reserved,
                )

            # Connect Enter to activate OK button, Shift+Enter for new line
            def on_key_press(widget, event):
                if event.keyval == Gdk.KEY_Return:
                    # Shift+Enter: Allow new line (default behavior)
                    if event.state & Gdk.ModifierType.SHIFT_MASK:
                        return False  # Let default behavior handle it
                    # Plain Enter or Ctrl+Enter: Submit dialog
                    else:
                        dialog.response(Gtk.ResponseType.OK)
                        return True
                return False

            text_view.connect("key-press-event", on_key_press)

            # Connect history selection to populate text view
            if history_combo:

                def on_history_changed(combo):
                    active = combo.get_active()
                    if active > 0:  # Skip the placeholder item
                        selected_prompt = history[
                            active - 1
                        ]  # -1 because of placeholder
                        text_buffer.set_text(selected_prompt)
                        text_view.grab_focus()
                        text_buffer.select_range(
                            text_buffer.get_start_iter(), text_buffer.get_end_iter()
                        )

                history_combo.connect("changed", on_history_changed)

            status_box = self._add_section(
                content_area, "Status", icon="status"
            )
            refresh_model_info = self._add_model_info_box(status_box, operation)

            # Add progress widget
            progress_frame, progress_label = self._create_progress_widget()
            status_box.pack_start(progress_frame, False, False, 0)

            # Show all widgets
            content_area.show_all()

            # Focus the text view and select all text for easy editing
            text_view.grab_focus()
            text_buffer.select_range(
                text_buffer.get_start_iter(), text_buffer.get_end_iter()
            )

            # Keeps the dialog responsive to Cancel while the request runs
            def on_dialog_response(dialog, response_id):
                if response_id == Gtk.ResponseType.CANCEL:
                    print("DEBUG: Cancel button clicked during processing")
                    self._cancel_requested = True
                    return True  # Keep dialog open
                return False

            dialog.connect("response", on_dialog_response)

            # Run dialog in loop to handle Settings button
            def wait_for_prompt():
                nonlocal provider
                while True:
                    response = dialog.run()
                    print(f"DEBUG: Dialog response: {response}")

                    if response == Gtk.ResponseType.OK:
                        # First check credentials are configured (for "Configure & Continue" button)
                        if not self._credentials_ready(provider):
                            print(
                                f"DEBUG: OK clicked but {provider} not configured, opening settings"
                            )
                            self._show_settings_dialog(dialog)

                            # The settings dialog can change the provider itself
                            provider = self._get_provider()
                            refresh_model_info()

                            # Re-check credentials after settings dialog
                            if self._credentials_ready(provider):
                                # API key now configured - update UI
                                if api_warning_bar:
                                    api_warning_bar.hide()
                                ok_button.set_sensitive(True)
                                ok_button.set_label("OK")
                                print(f"DEBUG: {provider} configured, enabled OK button")
                            else:
                                print(f"DEBUG: {provider} still not configured")
                                continue  # Keep dialog open

                        # Now validate the prompt
                        start_iter = text_buffer.get_start_iter()
                        end_iter = text_buffer.get_end_iter()
                        prompt = text_buffer.get_text(start_iter, end_iter, False).strip()

                        # Check if user entered actual content (not just placeholder)
                        placeholder_texts = [
                            "Describe what you want to generate...",
                            "Describe the area to inpaint (e.g. 'remove object', 'fix background')",
                        ]

                        is_placeholder = prompt in placeholder_texts or not prompt.strip()

                        if is_placeholder:
                            # Show error message and keep dialog open
                            error_dialog = Gtk.MessageDialog(
                                parent=dialog,
                                flags=Gtk.DialogFlags.MODAL,
                                message_type=Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.OK,
                                text="Please enter a prompt description",
                            )
                            error_dialog.format_secondary_text(
                                "You need to describe what you want to generate or change before proceeding."
                            )
                            error_dialog.run()
                            error_dialog.destroy()
                            continue  # Keep the main dialog open

                        # Get selected mode
                        selected_mode = "contextual"  # default
                        if show_mode_selection and full_radio and full_radio.get_active():
                            selected_mode = "full_image"
                        elif (
                            show_mode_selection
                            and focused_radio
                            and focused_radio.get_active()
                        ):
                            selected_mode = "contextual"
                        # If no mode selection UI, use default "contextual" (for image generator)

                        print(
                            f"DEBUG: Got prompt text: '{prompt}', mode: '{selected_mode}', disabling OK button..."
                        )
                        # Disable OK button to prevent multiple clicks
                        ok_button.set_sensitive(False)
                        ok_button.set_label("Processing...")

                        # Update progress
                        self._update_progress(progress_label, "Validating API key...")

                        if size_combo:
                            self._dialog_size = (
                                int(width_spin.get_value()),
                                int(height_spin.get_value()),
                            )
                            self.config["last_generate_size"] = list(self._dialog_size)

                        self._dialog_references = self._read_reference_pickers(
                            reference_combos
                        )

                        if prompt:
                            self._add_to_prompt_history(prompt)
                            # Save the selected mode to config
                            self.config["last_mode"] = selected_mode
                            if show_mode_selection:
                                self.config["clip_to_selection"] = clip_checkbox.get_active()
                            self._save_config()

                        # Reset cancel flag for new operation
                        self._cancel_requested = False

                        # Return dialog, progress_label, and prompt data for processing
                        return (
                            (dialog, progress_label, prompt, selected_mode)
                            if prompt
                            else None
                        )
                    elif response == Gtk.ResponseType.APPLY:  # Configure Now button
                        print("DEBUG: Configure Now button clicked")
                        self._show_settings_dialog(dialog)

                        # Re-check credentials after settings dialog
                        provider = self._get_provider()
                        refresh_model_info()
                        if self._credentials_ready(provider):
                            # Credentials now configured - update UI
                            if api_warning_bar:
                                api_warning_bar.hide()
                            ok_button.set_sensitive(True)
                            ok_button.set_label("OK")
                            print(f"DEBUG: {provider} configured, enabled OK button")
                        else:
                            print(f"DEBUG: {provider} still not configured")
                        # Continue loop to keep main dialog open
                    elif response == Gtk.ResponseType.HELP:  # Settings button
                        print("DEBUG: Settings button clicked")
                        self._show_settings_dialog(dialog)
                        provider = self._get_provider()
                        refresh_model_info()
                        # Continue loop to keep main dialog open
                    else:
                        print("DEBUG: Dialog cancelled, destroying...")
                        dialog.destroy()
                        return None

            # Lets the caller hand the dialog back to the user after a failed
            # attempt, keeping the prompt and options they entered
            def retry_dialog():
                ok_button.set_sensitive(True)
                ok_button.set_label("OK")
                text_view.grab_focus()
                return wait_for_prompt()

            self._retry_dialog = retry_dialog

            print("DEBUG: About to call dialog.run()...")
            return wait_for_prompt()

        except Exception as e:
            print(f"DEBUG: Dialog error: {e}")
            # Fallback to default prompt if dialog fails
            return default_text if default_text else "fill this area naturally"

    def _show_composite_dialog(self, image, provider="openai"):
        """Show dedicated dialog for Layer Composite with visible layers info"""
        try:
            print("DEBUG: Creating Layer Composite dialog")

            # Get visible layers (image.get_layers() returns top-to-bottom order)
            all_layers = image.get_layers()
            visible_layers = [layer for layer in all_layers if layer.get_visible()]

            print(
                f"DEBUG: Found {len(all_layers)} total layers, {len(visible_layers)} visible"
            )
            print(
                f"DEBUG: Layer order (top-to-bottom): {[layer.get_name() for layer in visible_layers]}"
            )

            if len(visible_layers) < 2:
                print("DEBUG: Insufficient visible layers for composite")
                Gimp.message(
                    "❌ Layer Composite requires at least 2 visible layers.\n\nPlease make sure at least 2 layers are visible (eye icon shown) and try again."
                )
                return None

            if len(visible_layers) > 16:
                print(
                    f"DEBUG: Too many visible layers ({len(visible_layers)}), will use first 16"
                )
                visible_layers = visible_layers[:16]

            # Create dialog using helper methods
            dialog = self._create_dialog_base("Layer Composite")

            # Add buttons using GIMP's standard approach
            dialog.add_button(
                "Settings", Gtk.ResponseType.HELP
            )  # Use HELP for Settings
            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
            ok_button = dialog.add_button("Composite Layers", Gtk.ResponseType.OK)
            ok_button.set_can_default(True)
            ok_button.grab_default()
            ok_button.get_style_context().add_class("suggested-action")

            # Set up content area using helper
            content_area = self._setup_dialog_content_area(dialog, spacing=10)

            # Add API warning bar using helper
            api_warning_bar, needs_config = self._add_api_warning_bar(
                content_area, dialog, provider
            )
            if needs_config:
                # Disable OK button when no API key
                ok_button.set_sensitive(False)
                ok_button.set_label("Configure & Continue")

            # Layer list (read-only, just for user info)
            layer_box = self._add_section(
                content_area, "1. Layers",
                f"{len(visible_layers)} visible layers will be combined by AI.",
                icon="layers", separator=False,
            )
            layer_box.set_spacing(2)

            for i, layer in enumerate(visible_layers):
                layer_label = Gtk.Label()
                if (
                    i == len(visible_layers) - 1
                ):  # Last layer is the base (bottom) layer
                    layer_label.set_text(f"Base: {layer.get_name()} (primary layer)")
                else:
                    layer_label.set_text(
                        f"Layer {len(visible_layers) - 1 - i}: {layer.get_name()}"
                    )
                layer_label.set_halign(Gtk.Align.START)
                layer_box.pack_start(layer_label, False, False, 0)

            prompt_box = self._add_section(
                content_area, "2. Prompt",
                "Describe how to combine these layers.",
                icon="prompt",
            )

            # Prompt history dropdown
            history = self._get_prompt_history()
            history_combo = None
            if history:
                history_label = Gtk.Label(label="Recent prompts:")
                history_label.set_halign(Gtk.Align.START)
                prompt_box.pack_start(history_label, False, False, 0)

                history_combo = Gtk.ComboBoxText()
                history_combo.append_text("Select from recent prompts...")
                for prompt in history:
                    # Truncate long prompts for display
                    display_prompt = prompt[:60] + "..." if len(prompt) > 60 else prompt
                    history_combo.append_text(display_prompt)
                history_combo.set_active(0)
                prompt_box.pack_start(history_combo, False, False, 0)

            scrolled_window = Gtk.ScrolledWindow()
            scrolled_window.set_policy(
                Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
            )
            scrolled_window.set_shadow_type(Gtk.ShadowType.IN)
            scrolled_window.get_style_context().add_class("ai-prompt-frame")
            scrolled_window.set_size_request(520, 100)

            text_view = Gtk.TextView()
            text_view.set_wrap_mode(Gtk.WrapMode.WORD)
            text_view.set_border_width(8)

            # Set default prompt - use last prompt if available
            default_prompt = self._get_last_prompt()
            if not default_prompt:
                default_prompt = "Combine these layers naturally into a cohesive image"
            text_buffer = text_view.get_buffer()
            text_buffer.set_text(default_prompt)

            scrolled_window.add(text_view)
            prompt_box.pack_start(scrolled_window, True, True, 0)

            # Connect history selection to populate text view
            if history_combo:

                def on_history_changed(combo):
                    active = combo.get_active()
                    if active > 0:  # Skip the placeholder item
                        selected_prompt = history[
                            active - 1
                        ]  # -1 because of placeholder
                        text_buffer.set_text(selected_prompt)
                        text_view.grab_focus()
                        text_buffer.select_range(
                            text_buffer.get_start_iter(), text_buffer.get_end_iter()
                        )

                history_combo.connect("changed", on_history_changed)

            # Mask option - restore from config
            mask_box = self._add_section(
                content_area, "3. Selection Mask",
                "Limit the change to the selected area.",
                icon="mask", badge="Optional",
            )
            mask_checkbox = Gtk.CheckButton()
            mask_checkbox.set_label("Include selection mask (applies to base layer)")
            last_use_mask = self.config.get("last_use_mask", False)
            mask_checkbox.set_active(last_use_mask)
            mask_box.pack_start(mask_checkbox, False, False, 0)

            status_box = self._add_section(content_area, "Status", icon="status")
            refresh_model_info = self._add_model_info_box(status_box, "composite")

            # Add progress widget
            progress_frame, progress_label = self._create_progress_widget()
            status_box.pack_start(progress_frame, False, False, 0)

            # Show dialog
            content_area.show_all()
            text_view.grab_focus()

            # Keeps the dialog responsive to Cancel while the request runs
            def on_dialog_response(dialog, response_id):
                if response_id == Gtk.ResponseType.CANCEL:
                    print("DEBUG: Cancel button clicked during processing")
                    self._cancel_requested = True
                    return True  # Keep dialog open
                return False

            dialog.connect("response", on_dialog_response)

            # Run dialog loop
            def wait_for_prompt():
                nonlocal provider
                while True:
                    response = dialog.run()

                    if response == Gtk.ResponseType.OK:
                        # Get prompt text
                        start_iter = text_buffer.get_start_iter()
                        end_iter = text_buffer.get_end_iter()
                        prompt = text_buffer.get_text(start_iter, end_iter, False)

                        # Check if user entered actual content (not just placeholder)
                        placeholder_texts = [
                            "Combine these layers naturally into a cohesive image",
                            "Describe what you want to generate...",
                        ]

                        is_placeholder = prompt in placeholder_texts or not prompt.strip()

                        if is_placeholder:
                            # Show error
                            error_dialog = Gtk.MessageDialog(
                                parent=dialog,
                                flags=Gtk.DialogFlags.MODAL,
                                message_type=Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.OK,
                                text="Please enter a prompt description",
                            )
                            error_dialog.run()
                            error_dialog.destroy()
                            continue

                        use_mask = mask_checkbox.get_active()
                        print(
                            f"DEBUG: Composite dialog OK - {len(visible_layers)} layers, mask: {use_mask}"
                        )

                        # Save mask checkbox state to config
                        self.config["last_use_mask"] = use_mask
                        self._save_config()

                        # Disable OK button to prevent multiple clicks
                        ok_button.set_sensitive(False)
                        ok_button.set_label("Processing...")

                        # Update progress
                        self._update_progress(progress_label, "Validating API key...")

                        # Save prompt to history
                        self._add_to_prompt_history(prompt.strip())

                        # Reset cancel flag for new operation
                        self._cancel_requested = False

                        # Return dialog, progress_label, and data for processing
                        return (
                            dialog,
                            progress_label,
                            prompt.strip(),
                            visible_layers,
                            use_mask,
                        )

                    elif response == Gtk.ResponseType.HELP:
                        print("DEBUG: Settings button clicked")
                        # Show settings dialog
                        self._show_settings_dialog(dialog)
                        # Check if credentials were configured
                        provider = self._get_provider()
                        refresh_model_info()
                        if self._credentials_ready(provider):
                            print(f"DEBUG: {provider} configured, enabled OK button")
                            ok_button.set_sensitive(True)
                            ok_button.set_label("Composite Layers")
                            if api_warning_bar:
                                api_warning_bar.hide()
                        else:
                            print(f"DEBUG: {provider} still not configured")

                    elif response == Gtk.ResponseType.APPLY:
                        print("DEBUG: Configure Now button clicked")
                        # Show settings dialog when "Configure Now" is clicked
                        self._show_settings_dialog(dialog)
                        # Check if credentials were configured
                        provider = self._get_provider()
                        refresh_model_info()
                        if self._credentials_ready(provider):
                            print(f"DEBUG: {provider} configured, enabled OK button")
                            ok_button.set_sensitive(True)
                            ok_button.set_label("Composite Layers")
                            if api_warning_bar:
                                api_warning_bar.hide()
                        else:
                            print(f"DEBUG: {provider} still not configured")

                    else:
                        dialog.destroy()
                        return None

            # Lets the caller hand the dialog back to the user after a failed
            # attempt, keeping the prompt and options they entered
            def retry_dialog():
                ok_button.set_sensitive(True)
                ok_button.set_label("Composite Layers")
                text_view.grab_focus()
                return wait_for_prompt()

            self._retry_dialog = retry_dialog

            return wait_for_prompt()

        except Exception as e:
            print(f"DEBUG: Composite dialog error: {e}")
            return None

    def _show_settings_dialog(self, parent_dialog):
        """Show settings dialog with write-only API key field"""
        try:
            self._init_gimp_ui()
            dialog = Gtk.Dialog(
                title="AI Plugin Settings",
                parent=parent_dialog,
                flags=Gtk.DialogFlags.MODAL,
            )
            dialog.get_style_context().add_class("ai-dialog")

            # Two columns keep the dialog short enough that the buttons stay on
            # screen at 1080p; the scrolled window covers smaller displays.
            dialog.set_default_size(900, 560)
            dialog.set_resizable(True)

            # Add buttons
            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
            save_button = dialog.add_button("Save", Gtk.ResponseType.OK)
            save_button.set_can_default(True)
            save_button.grab_default()
            save_button.get_style_context().add_class("suggested-action")

            # Add content
            dialog_content = dialog.get_content_area()

            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            dialog_content.pack_start(scroller, True, True, 0)

            columns = Gtk.HBox(spacing=24)
            columns.set_margin_start(20)
            columns.set_margin_end(20)
            columns.set_margin_top(20)
            columns.set_margin_bottom(20)
            scroller.add(columns)

            content_area = Gtk.VBox(spacing=10)
            right_column = Gtk.VBox(spacing=10)
            columns.pack_start(content_area, True, True, 0)
            columns.pack_start(
                Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 0
            )
            columns.pack_start(right_column, True, True, 0)

            # Provider selection
            provider_box = self._add_section(
                content_area, "Inpainting Provider",
                "Service used for inpainting, erase and upscale.",
                icon="provider", separator=False,
            )

            provider_combo = Gtk.ComboBoxText()
            provider_ids = ["openai", "bedrock"]
            provider_combo.append_text("OpenAI (GPT-Image)")
            provider_combo.append_text("Amazon Bedrock (Stability inpainting)")
            current_provider = self.config.get("api_provider", "openai")
            provider_combo.set_active(
                provider_ids.index(current_provider)
                if current_provider in provider_ids
                else 0
            )
            provider_box.pack_start(provider_combo, False, False, 0)

            provider_info = Gtk.Label()
            provider_info.set_text(
                "Image generation and layer composite always use OpenAI"
            )
            provider_info.set_halign(Gtk.Align.START)
            provider_info.get_style_context().add_class("dim-label")
            provider_box.pack_start(provider_info, False, False, 0)

            if bedrock_client is None:
                provider_combo.set_sensitive(False)
                provider_info.set_text("bedrock_client.py is missing - reinstall the plugin")

            # API Key section
            api_box = self._add_section(
                content_area, "OpenAI", "API key and image model.", icon="openai"
            )

            # Current API key status
            current_key = self.config.get("openai", {}).get("api_key")
            if current_key:
                status_label = Gtk.Label(label="✓ API key is configured")
                status_label.set_halign(Gtk.Align.START)
            else:
                status_label = Gtk.Label(label="✗ No API key configured")
                status_label.set_halign(Gtk.Align.START)
            api_box.pack_start(status_label, False, False, 0)

            # API key input (write-only)
            key_label = Gtk.Label(label="Enter new API key (write-only):")
            key_label.set_halign(Gtk.Align.START)
            api_box.pack_start(key_label, False, False, 0)

            key_entry = Gtk.Entry()
            key_entry.set_placeholder_text("sk-proj-...")
            key_entry.set_visibility(False)  # Hide the text for security
            api_box.pack_start(key_entry, False, False, 0)

            # Image model
            openai_model_label = Gtk.Label(label="Image model:")
            openai_model_label.set_halign(Gtk.Align.START)
            api_box.pack_start(openai_model_label, False, False, 0)

            openai_model_ids = [model_id for model_id, _ in OPENAI_IMAGE_MODELS]
            openai_model_combo = Gtk.ComboBoxText()
            for _, label in OPENAI_IMAGE_MODELS:
                openai_model_combo.append_text(label)
            current_model = self._get_openai_model()
            if current_model not in openai_model_ids:
                # Keep a hand-edited model id selectable instead of losing it
                openai_model_ids.append(current_model)
                openai_model_combo.append_text(current_model)
            openai_model_combo.set_active(openai_model_ids.index(current_model))
            api_box.pack_start(openai_model_combo, False, False, 0)

            # Bedrock section
            bedrock_config = self.config.get("bedrock", {})
            bedrock_box = self._add_section(
                right_column, "Amazon Bedrock",
                "Region, model and AWS credentials.",
                icon="bedrock", separator=False,
            )

            # A grid keeps each setting on one row instead of two
            bedrock_grid = Gtk.Grid()
            bedrock_grid.set_row_spacing(6)
            bedrock_grid.set_column_spacing(10)

            def add_bedrock_row(row, text, entry):
                label = Gtk.Label(label=text)
                label.set_halign(Gtk.Align.START)
                entry.set_hexpand(True)
                bedrock_grid.attach(label, 0, row, 1, 1)
                bedrock_grid.attach(entry, 1, row, 1, 1)

            region_entry = Gtk.Entry()
            region_entry.set_placeholder_text(bedrock_client.DEFAULT_REGION if bedrock_client else "us-east-1")
            region_entry.set_text(bedrock_config.get("region", ""))
            add_bedrock_row(0, "AWS region:", region_entry)

            model_entry = Gtk.Entry()
            model_entry.set_placeholder_text(
                bedrock_client.DEFAULT_MODEL_ID if bedrock_client else "stability.stable-image-inpaint-v1:0"
            )
            model_entry.set_text(bedrock_config.get("model_id", ""))
            add_bedrock_row(1, "Model ID:", model_entry)

            access_key_entry = Gtk.Entry()
            access_key_entry.set_placeholder_text("AKIA... (blank to use environment)")
            access_key_entry.set_text(bedrock_config.get("access_key_id", ""))
            add_bedrock_row(2, "Access key ID:", access_key_entry)

            secret_key_entry = Gtk.Entry()
            secret_key_entry.set_visibility(False)
            secret_key_entry.set_placeholder_text(
                "stored - type to replace"
                if bedrock_config.get("secret_access_key")
                else "blank to use environment"
            )
            add_bedrock_row(3, "Secret access key:", secret_key_entry)

            bedrock_key_entry = Gtk.Entry()
            bedrock_key_entry.set_visibility(False)
            bedrock_key_entry.set_placeholder_text(
                "stored - type to replace"
                if bedrock_config.get("api_key")
                else "optional, instead of the keys above"
            )
            add_bedrock_row(4, "Bedrock API key:", bedrock_key_entry)

            bedrock_box.pack_start(bedrock_grid, False, False, 0)

            clear_creds_button = Gtk.Button(label="Clear Stored AWS Credentials")
            clear_creds_button.connect(
                "clicked",
                lambda _b: self._on_clear_bedrock_credentials(
                    access_key_entry, secret_key_entry, bedrock_key_entry
                ),
            )
            bedrock_box.pack_start(clear_creds_button, False, False, 0)

            bedrock_info = Gtk.Label()
            bedrock_info.set_text(
                "Leave the credential fields blank to read AWS_ACCESS_KEY_ID and "
                "AWS_SECRET_ACCESS_KEY from the environment, or ~/.aws. A sandboxed "
                "GIMP (snap, flatpak) cannot read ~/.aws at all, so fill these in if "
                "you launch GIMP from the desktop. Secrets are stored in plain text "
                "in config.json (mode 600)."
            )
            # Wrap rather than force the whole dialog wider
            bedrock_info.set_line_wrap(True)
            bedrock_info.set_max_width_chars(46)
            bedrock_info.set_xalign(0.0)
            bedrock_info.set_halign(Gtk.Align.START)
            bedrock_info.get_style_context().add_class("dim-label")
            bedrock_box.pack_start(bedrock_info, False, False, 0)

            # Prompt History section
            history_box = self._add_section(
                content_area, "Prompt History", icon="history"
            )

            # History count
            history_count = len(self._get_prompt_history())
            count_label = Gtk.Label(label=f"Stored prompts: {history_count}")
            count_label.set_halign(Gtk.Align.START)
            history_box.pack_start(count_label, False, False, 0)

            # Clear history button
            clear_button = Gtk.Button(label="Clear Prompt History")
            clear_button.connect("clicked", self._on_clear_history_clicked)
            history_box.pack_start(clear_button, False, False, 0)

            # Debug Settings section
            debug_box = self._add_section(content_area, "Debug", icon="debug")

            # Debug mode checkbox
            debug_checkbox = Gtk.CheckButton()
            debug_dir = tempfile.gettempdir()
            debug_checkbox.set_label(f"Save debug images to {debug_dir}")
            debug_checkbox.set_active(self.config.get("debug_mode", False))
            debug_box.pack_start(debug_checkbox, False, False, 0)

            # Debug explanation
            debug_info = Gtk.Label()
            debug_info.set_text(
                "Saves intermediate AI processing images for troubleshooting"
            )
            debug_info.set_halign(Gtk.Align.START)
            debug_info.get_style_context().add_class("dim-label")
            debug_box.pack_start(debug_info, False, False, 0)

            # Show all widgets - must start from the dialog's own content area,
            # since content_area is only the left column here
            dialog_content.show_all()

            # Run dialog
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                # Save new API key if provided
                new_key = key_entry.get_text().strip()
                if new_key:
                    if "openai" not in self.config:
                        self.config["openai"] = {}
                    self.config["openai"]["api_key"] = new_key
                    print("DEBUG: API key updated")

                # Save OpenAI image model
                active_model = openai_model_combo.get_active()
                if active_model >= 0:
                    self.config.setdefault("openai", {})["model"] = openai_model_ids[
                        active_model
                    ]
                    print(f"DEBUG: OpenAI model set to {openai_model_ids[active_model]}")

                # Save provider selection
                active = provider_combo.get_active()
                if active >= 0:
                    self.config["api_provider"] = provider_ids[active]
                    print(f"DEBUG: Provider set to {provider_ids[active]}")

                # Save Bedrock settings (blank entries clear the override)
                bedrock = self.config.setdefault("bedrock", {})
                bedrock["region"] = region_entry.get_text().strip()
                bedrock["model_id"] = model_entry.get_text().strip()
                bedrock["access_key_id"] = access_key_entry.get_text().strip()

                # Secrets are write-only: a blank field keeps what is stored,
                # so they are cleared through the Clear button instead.
                new_secret = secret_key_entry.get_text().strip()
                if new_secret:
                    bedrock["secret_access_key"] = new_secret
                    print("DEBUG: Bedrock secret access key updated")

                new_bedrock_key = bedrock_key_entry.get_text().strip()
                if new_bedrock_key:
                    bedrock["api_key"] = new_bedrock_key
                    print("DEBUG: Bedrock API key updated")

                # Save debug mode setting
                debug_mode = debug_checkbox.get_active()
                self.config["debug_mode"] = debug_mode
                self._save_config()
                print(f"DEBUG: Debug mode set to {debug_mode}")

            dialog.destroy()

        except Exception as e:
            print(f"DEBUG: Settings dialog error: {e}")

    def _on_clear_bedrock_credentials(self, access_entry, secret_entry, api_key_entry):
        """Remove stored AWS credentials from the config and clear the fields"""
        bedrock = self.config.setdefault("bedrock", {})
        for key in ("access_key_id", "secret_access_key", "api_key", "session_token"):
            bedrock.pop(key, None)
        self._save_config()

        for entry in (access_entry, secret_entry, api_key_entry):
            entry.set_text("")
        secret_entry.set_placeholder_text("blank to use environment")
        api_key_entry.set_placeholder_text("optional, instead of the keys above")
        print("DEBUG: Cleared stored Bedrock credentials")

    def _on_clear_history_clicked(self, button):
        """Handle clear history button click"""
        self.config["prompt_history"] = []
        self._save_config()
        print("DEBUG: Prompt history cleared")

    def _extract_context_region(self, image, context_info):
        """Extract context region and scale to optimal OpenAI shape"""
        try:
            print("DEBUG: Extracting context region for AI with optimal shape")

            # Get parameters for the extract region
            ctx_x1, ctx_y1, ctx_width, ctx_height = context_info["extract_region"]
            target_shape = context_info["target_shape"]
            target_width, target_height = target_shape
            orig_width = image.get_width()
            orig_height = image.get_height()

            print(
                f"DEBUG: Extract region: ({ctx_x1},{ctx_y1}) to ({ctx_x1+ctx_width},{ctx_y1+ctx_height}) size={ctx_width}x{ctx_height}"
            )
            print(f"DEBUG: Original image: {orig_width}x{orig_height}")
            print(f"DEBUG: Target shape: {target_width}x{target_height}")

            # Create a new canvas with the extract region size
            extract_image = Gimp.Image.new(ctx_width, ctx_height, image.get_base_type())
            if not extract_image:
                return False, "Failed to create extract canvas", None

            # Calculate what part of the original image intersects with our extract region
            intersect_x1 = max(0, ctx_x1)
            intersect_y1 = max(0, ctx_y1)
            intersect_x2 = min(orig_width, ctx_x1 + ctx_width)
            intersect_y2 = min(orig_height, ctx_y1 + ctx_height)

            intersect_width = intersect_x2 - intersect_x1
            intersect_height = intersect_y2 - intersect_y1

            print(
                f"DEBUG: Image intersection: ({intersect_x1},{intersect_y1}) to ({intersect_x2},{intersect_y2})"
            )
            print(f"DEBUG: Intersection size: {intersect_width}x{intersect_height}")

            if intersect_width > 0 and intersect_height > 0:
                # Create a temporary image with just the intersecting region
                temp_image = image.duplicate()
                temp_image.crop(
                    intersect_width, intersect_height, intersect_x1, intersect_y1
                )

                # Create a layer from this region
                merged_layer = temp_image.merge_visible_layers(
                    Gimp.MergeType.CLIP_TO_IMAGE
                )
                if not merged_layer:
                    temp_image.delete()
                    extract_image.delete()
                    return False, "Failed to merge layers", None

                # Copy this layer to our extract canvas at the correct position
                layer_copy = Gimp.Layer.new_from_drawable(merged_layer, extract_image)
                extract_image.insert_layer(layer_copy, None, 0)

                # Position the layer correctly within the extract region
                # The layer should be at the same relative position as in the extract region
                paste_x = intersect_x1 - ctx_x1  # Offset within the extract region
                paste_y = intersect_y1 - ctx_y1  # Offset within the extract region
                layer_copy.set_offsets(paste_x, paste_y)

                print(
                    f"DEBUG: Placed image content at offset ({paste_x},{paste_y}) within extract region"
                )

                # Clean up temp image
                temp_image.delete()
            else:
                print(
                    "DEBUG: No intersection with original image - creating empty extract region"
                )

            # Scale and pad to target shape for OpenAI (preserve aspect ratio)
            if ctx_width != target_width or ctx_height != target_height:
                # Get padding info to preserve aspect ratio
                if "padding_info" in context_info:
                    padding_info = context_info["padding_info"]
                    scale_factor = padding_info["scale_factor"]
                    scaled_w, scaled_h = padding_info["scaled_size"]
                    pad_left, pad_top, pad_right, pad_bottom = padding_info["padding"]

                    print(f"DEBUG: Using aspect-ratio preserving scaling:")
                    print(f"  Scale factor: {scale_factor}")
                    print(f"  Scaled size: {scaled_w}x{scaled_h}")
                    print(
                        f"  Padding: left={pad_left}, top={pad_top}, right={pad_right}, bottom={pad_bottom}"
                    )

                    # First scale preserving aspect ratio
                    if scale_factor != 1.0:
                        extract_image.scale(scaled_w, scaled_h)
                        print(
                            f"DEBUG: Scaled to {scaled_w}x{scaled_h} preserving aspect ratio"
                        )

                    # Then add padding to reach target dimensions
                    if pad_left > 0 or pad_top > 0 or pad_right > 0 or pad_bottom > 0:
                        # Resize canvas to add padding
                        extract_image.resize(
                            target_width, target_height, pad_left, pad_top
                        )
                        print(
                            f"DEBUG: Added padding to reach {target_width}x{target_height}"
                        )
                else:
                    # Fallback: calculate padding on the fly
                    padding_info = calculate_padding_for_shape(
                        ctx_width, ctx_height, target_width, target_height
                    )
                    scale_factor = padding_info["scale_factor"]
                    scaled_w, scaled_h = padding_info["scaled_size"]
                    pad_left, pad_top, pad_right, pad_bottom = padding_info["padding"]

                    print(f"DEBUG: Calculating padding on the fly:")
                    print(f"  Scale factor: {scale_factor}")
                    print(
                        f"  Padding: left={pad_left}, top={pad_top}, right={pad_right}, bottom={pad_bottom}"
                    )

                    # First scale preserving aspect ratio
                    extract_image.scale(scaled_w, scaled_h)

                    # Then add padding
                    extract_image.resize(target_width, target_height, pad_left, pad_top)

                print(
                    f"DEBUG: Final extract image size: {target_width}x{target_height} (aspect ratio preserved)"
                )

            # Export to PNG
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                temp_filename = temp_file.name

            try:
                # Export using GIMP's PNG export
                file = Gio.File.new_for_path(temp_filename)

                pdb_proc = Gimp.get_pdb().lookup_procedure("file-png-export")
                pdb_config = pdb_proc.create_config()
                pdb_config.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
                pdb_config.set_property("image", extract_image)
                pdb_config.set_property("file", file)
                pdb_config.set_property("options", None)

                result = pdb_proc.run(pdb_config)
                if result.index(0) != Gimp.PDBStatusType.SUCCESS:
                    print(f"DEBUG: PNG export failed: {result.index(0)}")
                    extract_image.delete()
                    return False, "PNG export failed", None

                # Read the exported file and encode to base64
                with open(temp_filename, "rb") as f:
                    png_data = f.read()

                base64_data = base64.b64encode(png_data).decode("utf-8")

                # Clean up
                os.unlink(temp_filename)
                extract_image.delete()

                info = f"Extracted context region: {len(png_data)} bytes as PNG, base64 length: {len(base64_data)}"
                print(f"DEBUG: {info}")
                return True, info, base64_data

            except Exception as e:
                print(f"DEBUG: Context extraction export failed: {e}")
                if os.path.exists(temp_filename):
                    os.unlink(temp_filename)
                extract_image.delete()
                return False, f"Export failed: {str(e)}", None

        except Exception as e:
            print(f"DEBUG: Context extraction failed: {e}")
            return False, f"Context extraction error: {str(e)}", None


    def _calculate_full_image_context_extraction(self, image, any_size=False):
        """
        Calculate context extraction for the full image.

        any_size: the provider takes arbitrary sizes (GPT-Image-2.x), so the
        image keeps its own aspect ratio and is only scaled down to the size
        limit instead of being squeezed into one of three fixed shapes.
        """
        try:
            print("DEBUG: Calculating full image context extraction")

            # Get full image dimensions
            orig_width = image.get_width()
            orig_height = image.get_height()
            print(f"DEBUG: Original full image size: {orig_width}x{orig_height}")

            # Use full image bounds as "selection"
            full_x1, full_y1 = 0, 0
            full_x2, full_y2 = orig_width, orig_height

            print(
                f"DEBUG: Full image bounds: ({full_x1},{full_y1}) to ({full_x2},{full_y2})"
            )

            if any_size:
                target_shape = get_gpt_image_shape(orig_width, orig_height)
            else:
                target_shape = get_optimal_openai_shape(orig_width, orig_height)
            target_width, target_height = target_shape
            target_size = max(target_width, target_height)  # For backward compatibility

            print(f"DEBUG: Target OpenAI shape: {target_width}x{target_height}")

            # For full image, the context covers the entire original image
            ctx_x1 = 0
            ctx_y1 = 0

            print(
                f"DEBUG: Context region covers entire image: {orig_width}x{orig_height}"
            )

            # Check if there's actually a selection - if not, use full image for transformation
            selection_bounds = Gimp.Selection.bounds(image)
            has_real_selection = (
                selection_bounds[0] if len(selection_bounds) > 0 else False
            )

            if has_real_selection:
                # Use actual selection bounds
                sel_bounds = (
                    selection_bounds[2],
                    selection_bounds[3],
                    selection_bounds[4],
                    selection_bounds[5],
                )
            else:
                # No selection - transform entire image ("Image to Image" mode)
                sel_bounds = (full_x1, full_y1, full_x2, full_y2)

            return {
                "mode": "full",
                "selection_bounds": sel_bounds,
                "extract_region": (
                    0,
                    0,
                    orig_width,
                    orig_height,
                ),  # Extract entire image
                "target_shape": target_shape,
                "target_size": max(target_shape),  # For backward compatibility
                "needs_padding": True,
                "padding_info": calculate_padding_for_shape(
                    orig_width, orig_height, target_shape[0], target_shape[1]
                ),
                "has_selection": has_real_selection,
                "original_bounds": (full_x1, full_y1, full_x2, full_y2),
            }

        except Exception as e:
            print(f"DEBUG: Failed to calculate full image context extraction: {e}")
            return None

    def _calculate_context_extraction(self, image):
        """Calculate smart context extraction area around selection using optimal shapes"""
        try:
            print("DEBUG: Calculating smart context extraction with optimal shapes")

            # Get image dimensions
            img_width = image.get_width()
            img_height = image.get_height()
            print(f"DEBUG: Original image size: {img_width}x{img_height}")

            # Check for selection
            selection_bounds = Gimp.Selection.bounds(image)
            print(f"DEBUG: Selection bounds raw: {selection_bounds}")

            if len(selection_bounds) < 5 or not selection_bounds[0]:
                print("DEBUG: No selection found, using center area")
                # Use new shape-aware function with no selection
                return extract_context_with_selection(
                    img_width,
                    img_height,
                    0,
                    0,
                    0,
                    0,
                    mode="focused",
                    has_selection=False,
                )

            # Extract selection bounds
            sel_x1 = selection_bounds[2] if len(selection_bounds) > 2 else 0
            sel_y1 = selection_bounds[3] if len(selection_bounds) > 3 else 0
            sel_x2 = selection_bounds[4] if len(selection_bounds) > 4 else 0
            sel_y2 = selection_bounds[5] if len(selection_bounds) > 5 else 0

            sel_width = sel_x2 - sel_x1
            sel_height = sel_y2 - sel_y1
            print(
                f"DEBUG: Selection: ({sel_x1},{sel_y1}) to ({sel_x2},{sel_y2}), size: {sel_width}x{sel_height}"
            )

            # Use new shape-aware function for calculation
            context_info = extract_context_with_selection(
                img_width,
                img_height,
                sel_x1,
                sel_y1,
                sel_x2,
                sel_y2,
                mode="focused",
                has_selection=True,
            )

            # Log the optimal shape selected
            print(f"DEBUG: Optimal shape selected: {context_info['target_shape']}")

            # Extract dimensions for any code that still expects target_size
            target_w, target_h = context_info["target_shape"]
            context_info["target_size"] = max(target_w, target_h)

            # Validate still works but now with shape support
            is_valid, error_msg = validate_context_info(context_info)
            if not is_valid:
                print(f"DEBUG: Context validation failed: {error_msg}")
                # Fallback to center extraction
                return extract_context_with_selection(
                    img_width,
                    img_height,
                    0,
                    0,
                    0,
                    0,
                    mode="focused",
                    has_selection=False,
                )

            # Add debug output for the calculated values
            extract_x1, extract_y1, extract_width, extract_height = context_info[
                "extract_region"
            ]
            target_w, target_h = context_info["target_shape"]

            print(
                f"DEBUG: Extract region: ({extract_x1},{extract_y1}) to ({extract_x1+extract_width},{extract_y1+extract_height}), size: {extract_width}x{extract_height}"
            )
            print(f"DEBUG: Target shape for OpenAI: {target_w}x{target_h}")

            if "padding_info" in context_info:
                padding_info = context_info["padding_info"]
                print(f"DEBUG: Scale factor: {padding_info['scale_factor']}")
                print(f"DEBUG: Padding: {padding_info['padding']}")

            return context_info

        except Exception as e:
            print(f"DEBUG: Context calculation failed: {e}")
            # Fallback to simple center extraction
            return extract_context_with_selection(
                img_width, img_height, 0, 0, 0, 0, mode="focused", has_selection=False
            )

    def _prepare_full_image(self, image):
        """Prepare full image for GPT-Image-1 processing with optimal shape"""
        try:
            print("DEBUG: Preparing full image for transformation with optimal shape")

            width = image.get_width()
            height = image.get_height()

            print(f"DEBUG: Original image size: {width}x{height}")

            # Get optimal OpenAI shape for this image
            target_shape = get_optimal_openai_shape(width, height)
            target_width, target_height = target_shape

            print(
                f"DEBUG: Optimal OpenAI shape selected: {target_width}x{target_height}"
            )

            # Calculate padding info for this shape
            padding_info = calculate_padding_for_shape(
                width, height, target_width, target_height
            )
            scale = padding_info["scale_factor"]
            scaled_width, scaled_height = padding_info["scaled_size"]

            print(f"DEBUG: Scale factor: {scale:.3f}")
            print(f"DEBUG: Scaled size: {scaled_width}x{scaled_height}")

            # Create context_info with both old and new format for compatibility
            context_info = {
                "mode": "full_image",
                "original_size": (width, height),
                "scaled_size": (scaled_width, scaled_height),
                "scale_factor": scale,
                "target_shape": target_shape,  # New: optimal shape tuple
                "target_size": (
                    target_width
                    if target_width == target_height
                    else max(target_width, target_height)
                ),  # Old format fallback
                "padding_info": padding_info,
                "has_selection": True,  # Always true for this mode
            }

            return context_info

        except Exception as e:
            print(f"DEBUG: Full image preparation failed: {e}")
            # Fallback to square
            return {
                "mode": "full_image",
                "original_size": (1024, 1024),
                "scaled_size": (1024, 1024),
                "scale_factor": 1.0,
                "target_shape": (1024, 1024),
                "target_size": 1024,
                "has_selection": True,
            }

    def _extract_full_image(self, image, context_info):
        """Extract and scale the full image for GPT-Image-1"""
        try:
            target_width, target_height = context_info["scaled_size"]
            print(
                f"DEBUG: Extracting full image, scaling to {target_width}x{target_height}"
            )

            # Create a copy of the image
            original_width = image.get_width()
            original_height = image.get_height()

            # Create image copy for processing
            temp_image = image.duplicate()

            # Flatten the image to get composite result
            if len(temp_image.get_layers()) > 1:
                temp_image.flatten()

            # Get the flattened layer
            layer = temp_image.get_layers()[0]

            # Scale the layer to target size
            layer.scale(target_width, target_height, False)

            # Scale the image canvas to match
            temp_image.scale(target_width, target_height)

            # Export to PNG in memory
            print("DEBUG: Exporting full image as PNG...")
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                temp_path = temp_file.name

            # Use GIMP's export function like the existing code
            file = Gio.File.new_for_path(temp_path)
            pdb_proc = Gimp.get_pdb().lookup_procedure("file-png-export")
            pdb_config = pdb_proc.create_config()
            pdb_config.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
            pdb_config.set_property("image", temp_image)
            pdb_config.set_property("file", file)
            pdb_config.set_property("options", None)
            result = pdb_proc.run(pdb_config)

            if result.index(0) != Gimp.PDBStatusType.SUCCESS:
                temp_image.delete()
                raise Exception("Failed to export full image")

            # Read the exported PNG
            with open(temp_path, "rb") as f:
                image_bytes = f.read()

            # Clean up
            os.unlink(temp_path)
            temp_image.delete()

            # Convert to base64 for API
            import base64

            image_data = base64.b64encode(image_bytes).decode("utf-8")

            print(
                f"DEBUG: Full image extracted: {len(image_bytes)} bytes, base64 length: {len(image_data)}"
            )
            return (
                True,
                f"Extracted full image: {len(image_bytes)} bytes as PNG, base64 length: {len(image_data)}",
                image_data,
            )

        except Exception as e:
            print(f"DEBUG: Full image extraction failed: {e}")
            return False, f"Full image extraction failed: {str(e)}", None

    def _run_threaded_operation(self, operation_func, operation_name, progress_label=None, max_wait_time=300):
        """Generic threaded wrapper for any operation to keep UI responsive"""
        import threading
        import time

        print(f"DEBUG: Starting threaded {operation_name}...")

        # Shared storage for results
        result = {
            "success": False,
            "message": "",
            "data": None,  # Generic data field
            "completed": False,
        }

        def operation_thread():
            try:
                result.update(operation_func())
            except Exception as e:
                print(f"DEBUG: [THREAD] {operation_name} exception: {e}")
                result["success"] = False
                result["message"] = str(e)
                result["data"] = None
            finally:
                result["completed"] = True

        # Start thread
        thread = threading.Thread(target=operation_thread)
        thread.daemon = True
        thread.start()

        # Keep UI responsive while waiting
        start_time = time.time()
        last_update_time = start_time

        print(f"DEBUG: Starting wait loop for {operation_name}, progress_label={progress_label is not None}")
        while not result["completed"]:
            current_time = time.time()
            elapsed = current_time - start_time

            # Update progress every second
            if progress_label and current_time - last_update_time >= 1:
                minutes, seconds = divmod(int(elapsed), 60)
                if minutes > 0:
                    self._update_progress(
                        progress_label,
                        f"Still processing... ({minutes}m {seconds:02d}s elapsed)",
                    )
                else:
                    self._update_progress(
                        progress_label, f"Processing... ({seconds}s elapsed)"
                    )
                last_update_time = current_time

            # Check for cancellation
            if self._check_cancel_and_process_events():
                print(f"DEBUG: {operation_name} cancelled by user")
                if progress_label:
                    self._update_progress(progress_label, "❌ Operation cancelled")
                result["success"] = False
                result["message"] = "Operation cancelled by user"
                break

            # Check for timeout
            if elapsed > max_wait_time:
                print(f"DEBUG: {operation_name} timeout after {max_wait_time} seconds")
                if progress_label:
                    self._update_progress(progress_label, "❌ Request timed out")
                result["success"] = False
                result["message"] = "Request timed out - check internet connection"
                break

            time.sleep(0.1)  # Small sleep to prevent busy waiting

        return result["success"], result["message"], result["data"]

    def _show_upscale_dialog(self):
        """Dialog for Generative Upscale: variant dropdown plus optional prompt"""
        try:
            self._init_gimp_ui()
            dialog = self._create_dialog_base("AI Generative Upscale", size=(520, 300))
            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
            ok_button = dialog.add_button("Upscale", Gtk.ResponseType.OK)
            ok_button.set_can_default(True)
            ok_button.grab_default()
            ok_button.get_style_context().add_class("suggested-action")

            content_area = self._setup_dialog_content_area(dialog, spacing=10)

            api_warning_bar, needs_config = self._add_api_warning_bar(
                content_area, dialog, self._get_provider()
            )
            if needs_config:
                ok_button.set_sensitive(False)
                ok_button.set_label("Configure & Continue")

            method_box = self._add_section(
                content_area, "1. Method",
                "Upscales the active layer, not the whole image.",
                icon="upscale", separator=False,
            )

            variants = ["fast", "conservative", "creative"]
            combo = Gtk.ComboBoxText()
            combo.append_text("Fast - 4x, a few seconds, no prompt")
            combo.append_text("Conservative - to ~4K, preserves the original closely")
            combo.append_text("Creative - to ~4K, invents detail, slowest")
            last = self.config.get("bedrock", {}).get("upscale_variant", "conservative")
            combo.set_active(variants.index(last) if last in variants else 1)
            method_box.pack_start(combo, False, False, 0)

            info = Gtk.Label()
            info.set_text(
                "Fast only accepts layers up to 1536px per side; larger layers "
                "are reduced to fit before upscaling."
            )
            info.set_line_wrap(True)
            info.set_max_width_chars(56)
            info.set_xalign(0.0)
            info.get_style_context().add_class("dim-label")
            method_box.pack_start(info, False, False, 0)

            prompt_box = self._add_section(
                content_area, "2. Prompt",
                "Describe the image (used by Conservative and Creative).",
                icon="prompt",
            )

            prompt_entry = Gtk.Entry()
            prompt_entry.set_placeholder_text(bedrock_client.DEFAULT_UPSCALE_PROMPT)
            prompt_entry.set_text(self.config.get("last_upscale_prompt", ""))
            prompt_box.pack_start(prompt_entry, False, False, 0)

            status_box = self._add_section(content_area, "Status", icon="status")
            refresh_model_info = self._add_model_info_box(
                status_box, "upscale", variants[max(0, combo.get_active())]
            )
            combo.connect(
                "changed",
                lambda c: refresh_model_info(variants[max(0, c.get_active())]),
            )

            progress_frame, progress_label = self._create_progress_widget()
            status_box.pack_start(progress_frame, False, False, 0)

            content_area.show_all()

            # Keeps the dialog responsive to Cancel while the request runs
            def on_dialog_response(dialog, response_id):
                if response_id == Gtk.ResponseType.CANCEL:
                    print("DEBUG: Cancel button clicked during processing")
                    self._cancel_requested = True
                return False

            dialog.connect("response", on_dialog_response)

            def wait_for_options():
                while True:
                    response = dialog.run()
                    if response == Gtk.ResponseType.OK:
                        provider = self._get_provider()
                        if not self._credentials_ready(provider):
                            self._show_settings_dialog(dialog)
                            provider = self._get_provider()
                            refresh_model_info()
                            if not self._credentials_ready(provider):
                                continue
                            if api_warning_bar:
                                api_warning_bar.hide()
                            ok_button.set_sensitive(True)
                            ok_button.set_label("Upscale")
                            continue

                        variant = variants[max(0, combo.get_active())]
                        prompt = prompt_entry.get_text().strip()
                        self.config.setdefault("bedrock", {})["upscale_variant"] = variant
                        self.config["last_upscale_prompt"] = prompt
                        self._save_config()

                        # Disable OK button to prevent multiple clicks
                        ok_button.set_sensitive(False)
                        ok_button.set_label("Processing...")
                        self._flush_ui()
                        self._cancel_requested = False
                        return dialog, progress_label, variant, prompt

                    if response in (Gtk.ResponseType.APPLY, Gtk.ResponseType.HELP):
                        self._show_settings_dialog(dialog)
                        refresh_model_info()
                        if self._credentials_ready(self._get_provider()):
                            if api_warning_bar:
                                api_warning_bar.hide()
                            ok_button.set_sensitive(True)
                            ok_button.set_label("Upscale")
                        continue

                    dialog.destroy()
                    return None

            # Lets the caller hand the dialog back to the user after a failed
            # attempt, keeping the options they chose
            def retry_dialog():
                ok_button.set_sensitive(True)
                ok_button.set_label("Upscale")
                return wait_for_options()

            self._retry_dialog = retry_dialog

            return wait_for_options()

        except Exception as e:
            print(f"DEBUG: Upscale dialog error: {e}")
            return None

    def _call_bedrock_upscale(self, variant, image_data, prompt, progress_label=None):
        """Call one of the Bedrock upscale models. Returns (ok, message, bytes)."""
        try:
            bedrock_config = self.config.get("bedrock", {})
            creds = bedrock_client.upscale_credentials(bedrock_config, variant)
            spec = bedrock_client.UPSCALE_VARIANTS[variant]
            print(f"DEBUG: Upscaling with {creds['model_id']} in {creds['region']}")

            body = bedrock_client.build_upscale_body(
                variant,
                image_data,
                prompt=prompt,
                creativity=bedrock_config.get("upscale_creativity", 0.35),
                seed=bedrock_config.get("seed", 0),
            )
            url, headers = bedrock_client.build_invoke_request(creds, body)
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")

            # Creative takes about a minute; the others are quicker
            with self._make_url_request(req, timeout=300) as response:
                response_json = json.loads(response.read().decode("utf-8"))

            finish_reasons = response_json.get("finish_reasons") or [None]
            if finish_reasons[0]:
                return False, f"Bedrock refused the request: {finish_reasons[0]}", None
            if not response_json.get("images"):
                return False, "Bedrock returned no image", None

            data = base64.b64decode(response_json["images"][0])
            print(f"DEBUG: Upscale returned {len(data)} bytes ({spec['output_format']})")
            return True, "Upscale successful", data

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
            print(f"DEBUG: Upscale HTTP error: {e.code} - {error_body}")
            return False, f"Bedrock API error {e.code}: {error_body[:300]}", None
        except Exception as e:
            print(f"DEBUG: Upscale failed: {e}")
            return False, f"Upscale failed: {str(e)}", None

    def _call_upscale_threaded(self, variant, image_data, prompt, progress_label=None):
        """Threaded wrapper so the UI stays responsive during the upscale"""
        def operation():
            success, message, data = self._call_bedrock_upscale(
                variant, image_data, prompt, progress_label
            )
            return {"success": success, "message": message, "data": data}

        return self._run_threaded_operation(
            operation, f"Bedrock {variant} upscale", progress_label, max_wait_time=420
        )

    def _export_layer_for_upscale(self, layer, variant):
        """
        Export the active layer as base64 PNG, reduced first if the variant
        cannot accept it at full size.
        """
        width, height = layer.get_width(), layer.get_height()
        send_width, send_height = bedrock_client.upscale_input_size(
            variant, width, height
        )

        temp_image = Gimp.Image.new(width, height, Gimp.ImageBaseType.RGB)
        try:
            copy = Gimp.Layer.new_from_drawable(layer, temp_image)
            temp_image.insert_layer(copy, None, 0)
            copy.set_offsets(0, 0)
            temp_image.flatten()

            if (send_width, send_height) != (width, height):
                Gimp.context_push()
                try:
                    Gimp.context_set_interpolation(Gimp.InterpolationType.LOHALO)
                    temp_image.scale(send_width, send_height)
                finally:
                    Gimp.context_pop()
                print(
                    f"DEBUG: Reduced layer {width}x{height} -> {send_width}x{send_height} "
                    f"to fit the {variant} upscale input limit"
                )

            png = self._export_layer_to_png(temp_image)
            if not png:
                return None, (width, height), (send_width, send_height)
            return (
                base64.b64encode(png).decode("utf-8"),
                (width, height),
                (send_width, send_height),
            )
        finally:
            temp_image.delete()

    def _call_bedrock_generation(self, prompt, width, height, progress_label=None):
        """
        Text-to-image through Bedrock. Returns raw PNG bytes.

        Stability takes an aspect ratio rather than pixel dimensions and returns
        roughly a megapixel at that ratio, so the canvas shape is mapped to the
        nearest ratio it accepts.
        """
        try:
            if not prompt or not prompt.strip():
                return False, "Error: Empty prompt provided", None

            bedrock_config = self.config.get("bedrock", {})
            creds = bedrock_client.generation_credentials(bedrock_config)
            ratio = bedrock_client.aspect_ratio_for(width, height)
            print(
                f"DEBUG: Bedrock generation with {creds['model_id']} in "
                f"{creds['region']}, canvas {width}x{height} -> aspect {ratio}"
            )

            body = bedrock_client.build_generation_body(
                prompt,
                aspect_ratio=ratio,
                negative_prompt=bedrock_config.get("negative_prompt", ""),
                seed=bedrock_config.get("seed", 0),
            )
            url, headers = bedrock_client.build_invoke_request(creds, body)
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")

            if progress_label:
                self._update_progress(progress_label, "Generating image...", 0.5)

            with self._make_url_request(req, timeout=180) as response:
                response_json = json.loads(response.read().decode("utf-8"))

            finish_reasons = response_json.get("finish_reasons") or [None]
            if finish_reasons[0]:
                return False, f"Bedrock refused the request: {finish_reasons[0]}", None
            if not response_json.get("images"):
                return False, "Bedrock returned no image", None

            image_data = base64.b64decode(response_json["images"][0])
            print(f"DEBUG: Bedrock generated {len(image_data)} bytes")
            return True, "Image generation successful", image_data

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
            print(f"DEBUG: Bedrock generation HTTP error: {e.code} - {error_body}")
            return False, f"Bedrock API error {e.code}: {error_body[:300]}", None
        except Exception as e:
            print(f"DEBUG: Bedrock generation failed: {e}")
            return False, f"Bedrock generation failed: {str(e)}", None

    def _call_openai_generation(
        self, prompt, api_key, size="auto", progress_label=None
    ):
        """Call OpenAI GPT-Image API for image generation with progress updates"""
        try:
            import json
            import urllib.request

            model = self._get_openai_model()
            print(f"DEBUG: Calling {model} generation API with prompt: {prompt}")

            # Determine optimal size
            if size == "auto":
                optimal_size = "1536x1024"  # Default landscape
            else:
                optimal_size = size

            print(f"DEBUG: Using size {optimal_size} for generation")

            # Prepare the request data
            data = {
                "model": model,
                "prompt": prompt,
                "n": 1,
                "size": optimal_size,
                "quality": "high",
            }

            # Create the request
            json_data = json.dumps(data).encode("utf-8")
            url = "https://api.openai.com/v1/images/generations"
            req = urllib.request.Request(url, data=json_data)
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {api_key}")

            print(f"DEBUG: Sending real {model} generation request...")

            # Progress during network operation (same pattern as _call_openai_edit)
            if progress_label:
                self._update_progress(
                    progress_label, f"🚀 Sending request to {model}..."
                )

            # Make the API call with progress updates during the call
            with self._make_url_request(req, timeout=OPENAI_TIMEOUT) as response:
                response_data = json.loads(response.read().decode("utf-8"))

            print("DEBUG: GPT-Image-1 generation response received")

            if progress_label:
                self._update_progress(progress_label, "✅ Processing AI response...")

            # Process the response
            if "data" in response_data and len(response_data["data"]) > 0:
                result_data = response_data["data"][0]

                if "b64_json" in result_data:
                    print(f"DEBUG: Processing base64 image data from {model}")
                    import base64

                    image_data = base64.b64decode(result_data["b64_json"])
                    print(f"DEBUG: Decoded {len(image_data)} bytes of image data")

                    return True, "Image generation successful", image_data
                else:
                    print(f"ERROR: No b64_json in {model} response")
                    return False, "No image data in response", None
            else:
                print(f"ERROR: No data in {model} response")
                return False, "No data in API response", None

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
            print(f"ERROR: OpenAI generation HTTP error: {e.code} - {error_body}")
            return False, f"OpenAI image API error {e.code}: {error_body[:200]}", None
        except Exception as e:
            print(f"ERROR: OpenAI generation API call failed: {str(e)}")
            return False, str(e), None

    def _call_openai_reference_generation(
        self, prompt, api_key, references, size, progress_label=None
    ):
        """
        Generate from a prompt plus reference images (PNG bytes). Text-only
        generation has no image input, so this goes through the edits endpoint
        without a mask. Returns raw image bytes like _call_openai_generation.
        """
        success, message, response = self._call_openai_edit(
            list(references), None, prompt, api_key, size, progress_label
        )
        if not success:
            return False, message, None
        try:
            image_data = base64.b64decode(response["data"][0]["b64_json"])
        except (KeyError, IndexError, TypeError):
            return False, "No image data in response", None
        return True, "Image generation successful", image_data

    def _call_generation_threaded(
        self, prompt, api_key, width, height, progress_label=None, exact_size=False,
        references=None,
    ):
        """
        Threaded wrapper dispatching generation to the configured provider.

        exact_size: width x height was picked by the user for a new image,
        rather than taken from an existing canvas.
        references: reference images as PNG bytes (OpenAI only).
        """
        provider = self._get_provider()
        openai_size = self._openai_generation_size(width, height, exact_size)

        def operation():
            if provider == "bedrock":
                success, message, data = self._call_bedrock_generation(
                    prompt, width, height, progress_label
                )
            elif references:
                success, message, data = self._call_openai_reference_generation(
                    prompt, api_key, references, openai_size, progress_label
                )
            else:
                success, message, data = self._call_openai_generation(
                    prompt, api_key, openai_size, progress_label
                )
            return {"success": success, "message": message, "data": data}

        return self._run_threaded_operation(
            operation, f"{provider} generation API call", progress_label
        )

    def _call_openai_generation_threaded(
        self, prompt, api_key, size="auto", progress_label=None
    ):
        """Threaded wrapper for OpenAI image generation API call to keep UI responsive"""
        def operation():
            success, message, image_data = self._call_openai_generation(
                prompt, api_key, size, progress_label
            )
            return {"success": success, "message": message, "data": image_data}

        return self._run_threaded_operation(
            operation, "OpenAI generation API call", progress_label
        )

    def _prepare_layers_for_composite(self, selected_layers):
        """Prepare multiple layers for OpenAI composite API - each layer as separate PNG"""
        try:
            print(f"DEBUG: Preparing {len(selected_layers)} layers for composite API")

            layer_data_list = []

            # Import coordinate utilities for optimal sizing
            from coordinate_utils import get_optimal_openai_shape

            # Process primary layer (bottom/first) with full optimization
            primary_layer = selected_layers[0]
            print(f"DEBUG: Processing primary layer: {primary_layer.get_name()}")

            # Create temporary image with just the primary layer
            primary_temp_image = Gimp.Image.new(
                primary_layer.get_width(),
                primary_layer.get_height(),
                Gimp.ImageBaseType.RGB,
            )

            # Use GIMP's built-in layer copying - much more reliable than manual buffer operations
            print(f"DEBUG: Copying primary layer using new_from_drawable method")
            primary_layer_copy = Gimp.Layer.new_from_drawable(
                primary_layer, primary_temp_image
            )
            primary_layer_copy.set_name("primary_copy")
            primary_temp_image.insert_layer(primary_layer_copy, None, 0)
            print("DEBUG: Primary layer copy completed via new_from_drawable")

            # Get optimal shape for primary image (using existing logic)
            primary_width = primary_temp_image.get_width()
            primary_height = primary_temp_image.get_height()
            optimal_shape = get_optimal_openai_shape(primary_width, primary_height)
            target_width, target_height = optimal_shape

            print(
                f"DEBUG: Primary layer optimal shape: {primary_width}x{primary_height} -> {target_width}x{target_height}"
            )

            # Scale primary image to optimal shape
            primary_temp_image.scale(target_width, target_height)
            primary_layer_copy.scale(target_width, target_height, False)

            # Export primary layer to PNG
            primary_png_data = self._export_layer_to_png(primary_temp_image)
            if primary_png_data:
                layer_data_list.append(primary_png_data)
                print(f"DEBUG: Primary layer exported: {len(primary_png_data)} bytes")

            primary_temp_image.delete()

            # Process additional layers - scale proportionally to match primary
            for i, layer in enumerate(selected_layers[1:], 1):
                print(f"DEBUG: Processing additional layer {i}: {layer.get_name()}")

                # Create temporary image for this layer
                temp_image = Gimp.Image.new(
                    layer.get_width(), layer.get_height(), Gimp.ImageBaseType.RGB
                )

                # Use GIMP's built-in layer copying - much more reliable than manual buffer operations
                print(
                    f"DEBUG: Copying additional layer {i} using new_from_drawable method"
                )
                layer_copy = Gimp.Layer.new_from_drawable(layer, temp_image)
                layer_copy.set_name(f"layer_copy_{i}")
                temp_image.insert_layer(layer_copy, None, 0)
                print(
                    f"DEBUG: Additional layer {i} copy completed via new_from_drawable"
                )

                # Scale to match primary dimensions (proportional scaling)
                scale_x = target_width / layer.get_width()
                scale_y = target_height / layer.get_height()
                scale_factor = min(scale_x, scale_y)  # Maintain aspect ratio

                new_width = int(layer.get_width() * scale_factor)
                new_height = int(layer.get_height() * scale_factor)

                print(
                    f"DEBUG: Scaling layer {i}: {layer.get_width()}x{layer.get_height()} -> {new_width}x{new_height}"
                )

                temp_image.scale(new_width, new_height)
                layer_copy.scale(new_width, new_height, False)

                # If smaller than target, pad with transparency
                if new_width < target_width or new_height < target_height:
                    offset_x = (target_width - new_width) // 2
                    offset_y = (target_height - new_height) // 2
                    temp_image.resize(target_width, target_height, offset_x, offset_y)

                # Export layer to PNG
                layer_png_data = self._export_layer_to_png(temp_image)
                if layer_png_data:
                    layer_data_list.append(layer_png_data)
                    print(
                        f"DEBUG: Additional layer {i} exported: {len(layer_png_data)} bytes"
                    )

                temp_image.delete()

            print(
                f"DEBUG: Successfully prepared {len(layer_data_list)} layers for composite"
            )
            return (
                True,
                f"Prepared {len(layer_data_list)} layers",
                layer_data_list,
                optimal_shape,
            )

        except Exception as e:
            print(f"DEBUG: Layer preparation failed: {e}")
            return False, f"Layer preparation failed: {str(e)}", None, None

    def _flatten_layers_for_bedrock(self, image, selected_layers, context_info):
        """
        Flatten the selected layers into a single image for Bedrock.

        Bedrock's inpainting model takes one image plus a mask, not the array of
        separate layers gpt-image-1 accepts, so the layers are composited here
        (by GIMP, which is what layers are for) and the model only ever sees the
        combined pixels. Returns the same (success, message, base64) tuple as
        _extract_context_region, whose scaling and padding it reuses.
        """
        temp_image = None
        try:
            temp_image = Gimp.Image.new(
                image.get_width(), image.get_height(), Gimp.ImageBaseType.RGB
            )

            # selected_layers arrives base-first, and each copy is inserted at
            # the top of the stack, so the last one ends up on top.
            for layer in selected_layers:
                layer_copy = Gimp.Layer.new_from_drawable(layer, temp_image)
                layer_copy.set_name(layer.get_name())
                temp_image.insert_layer(layer_copy, None, 0)
                layer_copy.set_offsets(*layer.get_offsets()[1:])

            if len(temp_image.get_layers()) > 1:
                temp_image.flatten()

            print(
                f"DEBUG: Flattened {len(selected_layers)} layers into a single "
                f"{temp_image.get_width()}x{temp_image.get_height()} image for Bedrock"
            )

            return self._extract_context_region(temp_image, context_info)

        except Exception as e:
            print(f"DEBUG: Layer flattening failed: {e}")
            return False, f"Layer flattening failed: {str(e)}", None
        finally:
            if temp_image:
                temp_image.delete()

    def _export_layer_to_png(self, temp_image):
        """Helper function to export a GIMP image to PNG bytes"""
        try:
            import tempfile
            import os

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                temp_path = temp_file.name

            # Export using GIMP's PNG export
            file = Gio.File.new_for_path(temp_path)
            pdb_proc = Gimp.get_pdb().lookup_procedure("file-png-export")
            pdb_config = pdb_proc.create_config()
            pdb_config.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
            pdb_config.set_property("image", temp_image)
            pdb_config.set_property("file", file)
            pdb_config.set_property("options", None)
            result = pdb_proc.run(pdb_config)

            if result.index(0) != Gimp.PDBStatusType.SUCCESS:
                os.unlink(temp_path)
                return None

            # Read the PNG data
            with open(temp_path, "rb") as f:
                png_data = f.read()

            # Debug the exported PNG
            print(f"DEBUG: Exported PNG size: {len(png_data)} bytes")
            print(
                f"DEBUG: Image dimensions: {temp_image.get_width()}x{temp_image.get_height()}"
            )
            print(
                f"DEBUG: Expected raw size: {temp_image.get_width() * temp_image.get_height() * 4} bytes (RGBA)"
            )
            print(
                f"DEBUG: Compression ratio: {len(png_data) / (temp_image.get_width() * temp_image.get_height() * 4):.4f}"
            )

            os.unlink(temp_path)
            return png_data

        except Exception as e:
            print(f"DEBUG: PNG export failed: {e}")
            return None

    def _create_full_size_mask_then_scale(self, image, selection_channel, context_info):
        """Create mask at full original size, then scale/pad using same operations as image"""
        try:
            target_shape = context_info["target_shape"]
            target_width, target_height = target_shape
            padding_info = context_info["padding_info"]
            scale_factor = padding_info["scale_factor"]
            scaled_w, scaled_h = padding_info["scaled_size"]
            pad_left, pad_top, pad_right, pad_bottom = padding_info["padding"]

            # Determine the correct base size for mask creation
            if context_info.get("mode") == "full":
                # Full image mode: create mask at full image size
                mask_base_width = image.get_width()
                mask_base_height = image.get_height()
                print(
                    f"DEBUG: Creating mask at full image size {mask_base_width}x{mask_base_height}, then scaling like image"
                )
            else:
                # Focused/contextual mode: create mask at extract region size
                extract_region = context_info["extract_region"]
                mask_base_width = extract_region[2]
                mask_base_height = extract_region[3]
                print(
                    f"DEBUG: Creating mask at extract region size {mask_base_width}x{mask_base_height}, then scaling like image"
                )

            from gi.repository import Gegl

            # Build the mask with plain GIMP fills. Duplicating the image carries
            # the selection along with it, so the mask starts at full image size
            # and is cropped to the extract region afterwards. Compositing the
            # selection channel through a Gegl graph instead put the selection at
            # the image origin rather than the extract region, which produced an
            # all-black "inpaint nothing" mask whenever the selection was not in
            # the top-left corner.
            full_width = image.get_width()
            full_height = image.get_height()

            Gimp.context_push()
            mask_image = image.duplicate()
            mask_layer = Gimp.Layer.new(
                mask_image,
                "selection_mask",
                full_width,
                full_height,
                Gimp.ImageType.RGBA_IMAGE,
                100.0,
                Gimp.LayerMode.NORMAL,
            )
            mask_image.insert_layer(mask_layer, None, 0)

            try:
                # Everything outside the selection is black (preserve)
                duplicated_selection = Gimp.Selection.save(mask_image)
                Gimp.Selection.none(mask_image)
                Gimp.context_set_foreground(Gegl.Color.new("black"))
                mask_layer.edit_fill(Gimp.FillType.FOREGROUND)

                # The selection itself becomes transparent (inpaint), which is
                # the convention the OpenAI edits endpoint expects
                mask_image.select_item(
                    Gimp.ChannelOps.REPLACE, duplicated_selection
                )
                mask_layer.edit_clear()
                Gimp.Selection.none(mask_image)

                # Discard the duplicated image content, keep only the mask
                for other_layer in mask_image.get_layers():
                    if other_layer != mask_layer:
                        mask_image.remove_layer(other_layer)

                if (mask_base_width, mask_base_height) != (full_width, full_height):
                    extract_x, extract_y = context_info["extract_region"][:2]
                    mask_image.crop(
                        mask_base_width, mask_base_height, extract_x, extract_y
                    )
                    print(
                        f"DEBUG: Cropped mask to extract region {mask_base_width}x"
                        f"{mask_base_height} at ({extract_x},{extract_y})"
                    )

                # NOW scale using SAME operations as image
                if scale_factor != 1.0:
                    mask_image.scale(scaled_w, scaled_h)
                    print(f"DEBUG: Scaled mask to {scaled_w}x{scaled_h}")

                if pad_left > 0 or pad_top > 0 or pad_right > 0 or pad_bottom > 0:
                    mask_image.resize(target_width, target_height, pad_left, pad_top)
                    mask_layer = mask_image.get_layers()[0]
                    mask_layer.resize_to_image_size()

                    # Padding arrives transparent, which would read as "inpaint
                    # this". Paint just the padding border black so only the real
                    # selection stays transparent.
                    mask_image.select_rectangle(
                        Gimp.ChannelOps.REPLACE, 0, 0, target_width, target_height
                    )
                    mask_image.select_rectangle(
                        Gimp.ChannelOps.SUBTRACT, pad_left, pad_top, scaled_w, scaled_h
                    )
                    Gimp.context_set_foreground(Gegl.Color.new("black"))
                    mask_layer.edit_fill(Gimp.FillType.FOREGROUND)
                    Gimp.Selection.none(mask_image)
                    print(
                        f"DEBUG: Added padding to mask to reach {target_width}x{target_height}"
                    )
            finally:
                Gimp.context_pop()

            print("DEBUG: Mask built - selection transparent, everything else black")

            # Export (same as working code)
            import tempfile
            import os

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                temp_filename = temp_file.name

            file = Gio.File.new_for_path(temp_filename)
            pdb_proc = Gimp.get_pdb().lookup_procedure("file-png-export")
            pdb_config = pdb_proc.create_config()
            pdb_config.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
            pdb_config.set_property("image", mask_image)
            pdb_config.set_property("file", file)
            pdb_config.set_property("options", None)

            result = pdb_proc.run(pdb_config)

            if result.index(0) != Gimp.PDBStatusType.SUCCESS:
                mask_image.delete()
                image.remove_channel(selection_channel)
                os.unlink(temp_filename)
                raise Exception("PNG export failed")

            with open(temp_filename, "rb") as f:
                png_data = f.read()

            os.unlink(temp_filename)
            mask_image.delete()
            image.remove_channel(selection_channel)

            print(f"DEBUG: Created full-size-then-scaled mask: {len(png_data)} bytes")
            return png_data

        except Exception as e:
            print(f"DEBUG: Full size mask creation failed: {e}")
            if "mask_image" in locals():
                mask_image.delete()
            if "selection_channel" in locals():
                image.remove_channel(selection_channel)
            raise Exception(f"Full size mask creation failed: {e}")

    def _create_context_mask(self, image, context_info, target_size):
        """Create mask from actual selection shape using pixel-by-pixel copying"""
        try:
            target_shape = context_info.get("target_shape", (target_size, target_size))
            target_width, target_height = target_shape
            print(
                f"DEBUG: Creating pixel-perfect selection mask {target_width}x{target_height}"
            )

            if not context_info["has_selection"]:
                raise Exception(
                    "No selection available - selection-shaped mask requires an active selection"
                )

            # Get extract region info
            ctx_x1, ctx_y1, ctx_width, ctx_height = context_info["extract_region"]
            print(
                f"DEBUG: Extract region: ({ctx_x1},{ctx_y1}) size {ctx_width}x{ctx_height}"
            )

            # Step 1: Save original selection as channel to preserve its exact shape
            selection_channel = Gimp.Selection.save(image)
            if not selection_channel:
                raise Exception("Failed to save selection as channel")
            print("DEBUG: Saved selection as channel for pixel copying")

            # For any mode with padding, use simplified approach that mirrors image processing
            if "padding_info" in context_info:
                return self._create_full_size_mask_then_scale(
                    image, selection_channel, context_info
                )

            # Step 2: Create target-shaped mask image (RGBA for transparency)
            mask_image = Gimp.Image.new(
                target_width, target_height, Gimp.ImageBaseType.RGB
            )
            if not mask_image:
                image.remove_channel(selection_channel)
                raise Exception("Failed to create mask image")

            mask_layer = Gimp.Layer.new(
                mask_image,
                "selection_mask",
                target_width,
                target_height,
                Gimp.ImageType.RGBA_IMAGE,
                100.0,
                Gimp.LayerMode.NORMAL,
            )
            if not mask_layer:
                mask_image.delete()
                image.remove_channel(selection_channel)
                raise Exception("Failed to create mask layer")

            mask_image.insert_layer(mask_layer, None, 0)

            # Fill with black background (preserve all areas initially)
            from gi.repository import Gegl

            black_color = Gegl.Color.new("black")
            Gimp.context_set_foreground(black_color)
            mask_layer.edit_fill(Gimp.FillType.FOREGROUND)
            print("DEBUG: Created black background mask (preserve all areas)")

            # Force layer update to make sure black fill is committed
            mask_layer.update(0, 0, target_width, target_height)

            # Explicitly ensure extension areas stay black by filling the entire target area
            print(
                f"DEBUG: Ensuring all extension areas are black in {target_width}x{target_height} mask"
            )

            # Step 3: Copy only the original image area, leave extended context white

            # Calculate where original image appears in context square
            orig_width, orig_height = image.get_width(), image.get_height()
            img_offset_x = max(
                0, -ctx_x1
            )  # where original image starts in context square
            img_offset_y = max(
                0, -ctx_y1
            )  # where original image starts in context square
            # Calculate where the original image content appears in the final padded target shape
            # Account for both extract region and padding
            if "padding_info" in context_info:
                padding_info = context_info["padding_info"]
                scale_factor = padding_info["scale_factor"]
                pad_left, pad_top, pad_right, pad_bottom = padding_info["padding"]

                # Original content is scaled and then padded
                img_end_x = min(
                    target_width - pad_left - pad_right, int(orig_width * scale_factor)
                )
                img_end_y = min(
                    target_height - pad_top - pad_bottom,
                    int(orig_height * scale_factor),
                )

                print(
                    f"DEBUG: Accounting for padding in mask - scale={scale_factor}, padding=({pad_left},{pad_top},{pad_right},{pad_bottom})"
                )
            else:
                # Fallback to simple calculation
                img_end_x = min(
                    ctx_width, orig_width - ctx_x1 if ctx_x1 >= 0 else orig_width
                )
                img_end_y = min(
                    ctx_height, orig_height - ctx_y1 if ctx_y1 >= 0 else orig_height
                )

            print(
                f"DEBUG: Original image appears at ({img_offset_x},{img_offset_y}) to ({img_end_x},{img_end_y}) in context square"
            )

            # Only process if there's an intersection
            if img_end_x > img_offset_x and img_end_y > img_offset_y:
                # Get buffers for pixel-level operations
                selection_buffer = selection_channel.get_buffer()
                if not selection_buffer:
                    mask_image.delete()
                    image.remove_channel(selection_channel)
                    raise Exception("Failed to get selection channel buffer")

                mask_shadow_buffer = mask_layer.get_shadow_buffer()
                if not mask_shadow_buffer:
                    mask_image.delete()
                    image.remove_channel(selection_channel)
                    raise Exception("Failed to get mask shadow buffer")

                print("DEBUG: Starting Gegl pixel copying from selection channel")

                # Create Gegl processing graph for selection shape copying
                graph = Gegl.Node()

                # Source 1: Current mask buffer (black background)
                mask_source = graph.create_child("gegl:buffer-source")
                mask_source.set_property("buffer", mask_layer.get_buffer())

                # Source 2: Selection channel buffer (contains exact selection shape)
                selection_source = graph.create_child("gegl:buffer-source")
                selection_source.set_property("buffer", selection_buffer)

                # Scale selection if needed to match the final image scaling
                if "padding_info" in context_info:
                    padding_info = context_info["padding_info"]
                    scale_factor = padding_info["scale_factor"]

                    if abs(scale_factor - 1.0) > 0.001:  # Need scaling
                        print(
                            f"DEBUG: Scaling selection channel by factor {scale_factor}"
                        )
                        scale_op = graph.create_child("gegl:scale-ratio")
                        scale_op.set_property("x", float(scale_factor))
                        scale_op.set_property("y", float(scale_factor))
                        selection_source.link(scale_op)
                        selection_input = scale_op
                    else:
                        selection_input = selection_source
                else:
                    selection_input = selection_source

                # Translate selection to correct position in padded target shape
                # For full image with padding, the selection has been scaled and needs padding offset
                if "padding_info" in context_info:
                    padding_info = context_info["padding_info"]
                    pad_left, pad_top, pad_right, pad_bottom = padding_info["padding"]

                    # Selection has already been scaled, just add padding offset
                    translate_x = pad_left
                    translate_y = pad_top

                    print(
                        f"DEBUG: Mask translation for padded image: translate by ({translate_x},{translate_y})"
                    )
                else:
                    # Original logic for non-padded extracts
                    translate_x = -ctx_x1
                    translate_y = -ctx_y1

                translate = graph.create_child("gegl:translate")
                translate.set_property("x", float(translate_x))
                translate.set_property("y", float(translate_y))

                # Connect scaled selection through translate to composite
                selection_input.link(translate)

                # Composite the translated selection over the black background
                # This preserves the black background in extension areas
                composite = graph.create_child("gegl:over")

                # Write to mask shadow buffer
                output = graph.create_child("gegl:write-buffer")
                output.set_property("buffer", mask_shadow_buffer)

                # Link the processing chain:
                # mask_source (black bg) + translated_selection → composite → output
                selection_source.link(translate)
                mask_source.link(composite)
                translate.connect_to("output", composite, "aux")
                composite.link(output)

                print(
                    f"DEBUG: Compositing selection over black background: translate by ({translate_x},{translate_y})"
                )

                # Process the graph to composite selection shape over black background
                output.process()
                print(
                    "DEBUG: Successfully composited selection shape over black background preserving extension areas"
                )

                # Flush and merge shadow buffer to make changes visible
                mask_shadow_buffer.flush()
                mask_layer.merge_shadow(True)
                print("DEBUG: Merged shadow buffer with base layer")
            else:
                print("DEBUG: No intersection - mask remains fully white")

            # Force complete layer update
            mask_layer.update(0, 0, target_width, target_height)

            # Force flush all changes to ensure PNG export sees the correct data
            Gimp.displays_flush()

            print("DEBUG: Successfully copied exact selection shape to mask using Gegl")

            # Step 4: Mask is already at target shape, no scaling needed
            # (Previous version scaled square masks, but we now create masks at target shape)
            print(f"DEBUG: Mask created at target shape {target_width}x{target_height}")

            # Step 4.5: Make selection areas transparent (the one simple change requested)
            # Current state: black background, white selection copied from channel
            # Needed: black background (preserve), transparent selection (inpaint)
            print("DEBUG: Making selection areas transparent for inpainting")
            scaled_mask_layer = mask_image.get_layers()[0]

            # Create a simple color-to-alpha operation to make selection areas transparent
            from gi.repository import Gegl

            transparency_graph = Gegl.Node()

            # Get layer buffer
            layer_buffer = scaled_mask_layer.get_buffer()
            shadow_buffer = scaled_mask_layer.get_shadow_buffer()

            # Source buffer
            buffer_source = transparency_graph.create_child("gegl:buffer-source")
            buffer_source.set_property("buffer", layer_buffer)

            # Convert white (selection) to transparent, keep everything else as-is
            color_to_alpha = transparency_graph.create_child("gegl:color-to-alpha")
            white_color = Gegl.Color.new("white")
            color_to_alpha.set_property("color", white_color)

            # Output buffer
            buffer_write = transparency_graph.create_child("gegl:write-buffer")
            buffer_write.set_property("buffer", shadow_buffer)

            # Process: source → color-to-alpha → output
            buffer_source.link(color_to_alpha)
            color_to_alpha.link(buffer_write)
            buffer_write.process()

            # Merge changes
            shadow_buffer.flush()
            scaled_mask_layer.merge_shadow(True)
            scaled_mask_layer.update(0, 0, target_size, target_size)

            print(
                "DEBUG: Selection areas are now transparent (inpaint), context/extension areas are black (preserved)"
            )

            # Step 5: Export as PNG for OpenAI
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                temp_filename = temp_file.name

            try:
                file = Gio.File.new_for_path(temp_filename)

                pdb_proc = Gimp.get_pdb().lookup_procedure("file-png-export")
                pdb_config = pdb_proc.create_config()
                pdb_config.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
                pdb_config.set_property("image", mask_image)
                pdb_config.set_property("file", file)
                pdb_config.set_property("options", None)

                result = pdb_proc.run(pdb_config)
                if result.index(0) != Gimp.PDBStatusType.SUCCESS:
                    mask_image.delete()
                    image.remove_channel(selection_channel)
                    raise Exception(f"PNG export failed with status: {result.index(0)}")

                # Read the exported mask PNG
                with open(temp_filename, "rb") as f:
                    png_data = f.read()

                if len(png_data) == 0:
                    raise Exception("Exported PNG file is empty")

                # Clean up
                os.unlink(temp_filename)
                mask_image.delete()
                image.remove_channel(selection_channel)

                print(
                    f"DEBUG: Created pixel-perfect selection mask PNG: {len(png_data)} bytes"
                )
                return png_data

            except Exception as e:
                print(f"DEBUG: Mask export failed: {e}")
                if os.path.exists(temp_filename):
                    os.unlink(temp_filename)
                mask_image.delete()
                image.remove_channel(selection_channel)
                raise Exception(f"Mask export failed: {str(e)}")

        except Exception as e:
            print(f"DEBUG: Context mask creation failed: {e}")
            raise Exception(f"Selection-shaped mask creation failed: {str(e)}")

    def _apply_smart_mask_feathering(self, mask, image):
        """Apply smart feathering to mask edges for better blending while preserving selection size"""
        try:
            print("DEBUG: Applying smart mask feathering for enhanced edge blending")

            from gi.repository import Gegl

            # Get mask dimensions and buffer
            mask_width = mask.get_width()
            mask_height = mask.get_height()
            mask_buffer = mask.get_buffer()
            shadow_buffer = mask.get_shadow_buffer()

            print(f"DEBUG: Processing mask {mask_width}x{mask_height}")

            # Simplified approach: Apply graduated gaussian blur
            # This softens edges without changing the overall selection area
            graph = Gegl.Node()

            # Source: Current mask buffer
            source = graph.create_child("gegl:buffer-source")
            source.set_property("buffer", mask_buffer)

            # Apply moderate gaussian blur to soften edges
            # Use smaller blur to maintain selection size while softening transitions
            blur = graph.create_child("gegl:gaussian-blur")
            blur.set_property("std-dev-x", 4.0)  # Moderate blur for edge softening
            blur.set_property("std-dev-y", 4.0)

            # Output to shadow buffer
            output = graph.create_child("gegl:write-buffer")
            output.set_property("buffer", shadow_buffer)

            # Link the chain: source -> blur -> output
            source.link(blur)
            blur.link(output)

            # Process the graph
            print("DEBUG: Processing edge feathering...")
            output.process()

            # Merge changes
            shadow_buffer.flush()
            mask.merge_shadow(True)
            mask.update(0, 0, mask_width, mask_height)

            print(
                "DEBUG: Smart edge feathering applied - edges softened while preserving selection area"
            )

        except Exception as e:
            print(f"DEBUG: Smart mask feathering failed (using simple feathering): {e}")
            # Fallback: apply light gaussian blur to entire mask
            try:
                from gi.repository import Gegl

                mask_buffer = mask.get_buffer()
                shadow_buffer = mask.get_shadow_buffer()

                # Simple fallback: light gaussian blur on entire mask
                graph = Gegl.Node()

                source = graph.create_child("gegl:buffer-source")
                source.set_property("buffer", mask_buffer)

                blur = graph.create_child("gegl:gaussian-blur")
                blur.set_property("std-dev-x", 2.0)
                blur.set_property("std-dev-y", 2.0)

                output = graph.create_child("gegl:write-buffer")
                output.set_property("buffer", shadow_buffer)

                source.link(blur)
                blur.link(output)
                output.process()

                shadow_buffer.flush()
                mask.merge_shadow(True)
                mask.update(0, 0, mask.get_width(), mask.get_height())

                print("DEBUG: Applied fallback simple feathering")

            except Exception as e2:
                print(
                    f"DEBUG: Both smart and simple feathering failed, using original mask: {e2}"
                )

    def _sample_boundary_colors(self, image, context_info):
        """Sample colors around selection boundary for color matching"""
        try:
            print("DEBUG: Sampling boundary colors for color matching")

            if not context_info.get("has_selection", False):
                return None

            # Get selection bounds
            sel_x1, sel_y1, sel_x2, sel_y2 = context_info["selection_bounds"]

            # Sample from a ring around the selection edge
            # Inner ring: just inside selection
            # Outer ring: just outside selection
            sample_width = min(10, (sel_x2 - sel_x1) // 10)  # Adaptive sample width

            # Get the flattened image for color sampling
            merged_layer = None
            try:
                # Create a temporary flattened copy for sampling
                temp_image = image.duplicate()
                merged_layer = temp_image.flatten()

                # Sample colors using GEGL buffer operations
                from gi.repository import Gegl

                layer_buffer = merged_layer.get_buffer()

                # Sample pixels around selection boundary
                inner_samples = []
                outer_samples = []

                # Sample points along the selection perimeter
                sample_points = 20  # Number of sample points

                for i in range(sample_points):
                    # Calculate position along selection perimeter
                    t = i / sample_points

                    # Sample along top and bottom edges
                    if i < sample_points // 2:
                        x = int(sel_x1 + t * 2 * (sel_x2 - sel_x1))
                        y_inner = sel_y1 + sample_width // 2
                        y_outer = sel_y1 - sample_width // 2
                    else:
                        x = int(sel_x2 - (t - 0.5) * 2 * (sel_x2 - sel_x1))
                        y_inner = sel_y2 - sample_width // 2
                        y_outer = sel_y2 + sample_width // 2

                    # Ensure coordinates are within image bounds
                    x = max(0, min(x, image.get_width() - 1))
                    y_inner = max(0, min(y_inner, image.get_height() - 1))
                    y_outer = max(0, min(y_outer, image.get_height() - 1))

                    try:
                        # Sample inner color (inside selection)
                        inner_rect = Gegl.Rectangle.new(x, y_inner, 1, 1)
                        inner_pixel = layer_buffer.get(
                            inner_rect, 1.0, "R'G'B'A u8", Gegl.AbyssPolicy.CLAMP
                        )
                        if len(inner_pixel) >= 3:
                            inner_samples.append(
                                (inner_pixel[0], inner_pixel[1], inner_pixel[2])
                            )

                        # Sample outer color (outside selection)
                        outer_rect = Gegl.Rectangle.new(x, y_outer, 1, 1)
                        outer_pixel = layer_buffer.get(
                            outer_rect, 1.0, "R'G'B'A u8", Gegl.AbyssPolicy.CLAMP
                        )
                        if len(outer_pixel) >= 3:
                            outer_samples.append(
                                (outer_pixel[0], outer_pixel[1], outer_pixel[2])
                            )

                    except Exception as sample_e:
                        print(f"DEBUG: Sample point {i} failed: {sample_e}")
                        continue

                # Calculate average colors
                if inner_samples and outer_samples:
                    # Calculate averages
                    inner_avg = tuple(
                        sum(channel) // len(inner_samples)
                        for channel in zip(*inner_samples)
                    )
                    outer_avg = tuple(
                        sum(channel) // len(outer_samples)
                        for channel in zip(*outer_samples)
                    )

                    # Calculate differences for color correction
                    hue_diff = 0  # Simplified - could calculate actual hue difference
                    brightness_diff = (sum(outer_avg) // 3) - (sum(inner_avg) // 3)

                    color_info = {
                        "inner_avg": inner_avg,
                        "outer_avg": outer_avg,
                        "brightness_diff": brightness_diff,
                        "hue_diff": hue_diff,
                    }

                    print(
                        f"DEBUG: Sampled colors - Inner: {inner_avg}, Outer: {outer_avg}"
                    )
                    print(f"DEBUG: Brightness difference: {brightness_diff}")

                    return color_info
                else:
                    print("DEBUG: No valid color samples collected")
                    return None

            finally:
                # Clean up temporary image
                if merged_layer and hasattr(merged_layer, "get_image"):
                    temp_image = merged_layer.get_image()
                    if temp_image:
                        temp_image.delete()

        except Exception as e:
            print(f"DEBUG: Color sampling failed: {e}")
            return None

    def _apply_color_matching(self, result_layer, color_info):
        """Apply color correction to match sampled boundary colors"""
        if not color_info:
            print("DEBUG: No color info available - skipping color matching")
            return

        try:
            print("DEBUG: Applying color matching based on boundary samples")

            from gi.repository import Gegl

            # Get layer buffer
            layer_buffer = result_layer.get_buffer()
            shadow_buffer = result_layer.get_shadow_buffer()

            # Create color correction graph
            graph = Gegl.Node()

            # Source buffer
            source = graph.create_child("gegl:buffer-source")
            source.set_property("buffer", layer_buffer)

            # Apply brightness/levels adjustment if significant difference
            brightness_diff = color_info.get("brightness_diff", 0)
            if abs(brightness_diff) > 10:  # Only apply if difference is noticeable
                levels = graph.create_child("gegl:levels")

                # Adjust gamma based on brightness difference
                gamma_adjust = 1.0 + (brightness_diff / 255.0)
                gamma_adjust = max(0.5, min(2.0, gamma_adjust))  # Clamp gamma

                levels.set_property("in-low", 0.0)
                levels.set_property("in-high", 1.0)
                levels.set_property("gamma", gamma_adjust)
                levels.set_property("out-low", 0.0)
                levels.set_property("out-high", 1.0)

                source.link(levels)
                current_node = levels

                print(f"DEBUG: Applied gamma correction: {gamma_adjust}")
            else:
                current_node = source
                print(
                    "DEBUG: No significant brightness difference - skipping levels adjustment"
                )

            # Output buffer
            output = graph.create_child("gegl:write-buffer")
            output.set_property("buffer", shadow_buffer)

            current_node.link(output)

            # Process color correction
            output.process()

            # Merge changes
            shadow_buffer.flush()
            result_layer.merge_shadow(True)
            result_layer.update(
                0, 0, result_layer.get_width(), result_layer.get_height()
            )

            print("DEBUG: Color matching applied successfully")

        except Exception as e:
            print(f"DEBUG: Color matching failed: {e}")

    def _create_multipart_data(self, fields, files):
        """Create multipart form data for file upload - supports image arrays"""
        import email.mime.multipart
        import email.mime.text
        import email.mime.application
        import uuid

        boundary = uuid.uuid4().hex
        body = b""

        # Add text fields
        for key, value in fields.items():
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
            body += f"{value}\r\n".encode()

        # Add file fields - handle both single files and arrays
        for key, file_data in files.items():
            if key == "image" and isinstance(file_data, list):
                # Handle image array for composite mode - use image[] array syntax
                for i, (filename, data, content_type) in enumerate(file_data):
                    body += f"--{boundary}\r\n".encode()
                    body += f'Content-Disposition: form-data; name="image[]"; filename="{filename}"\r\n'.encode()
                    body += f"Content-Type: {content_type}\r\n\r\n".encode()
                    body += data
                    body += b"\r\n"
                print(f"DEBUG: Added {len(file_data)} images to multipart data")
            else:
                # Handle single file (like mask or single image)
                filename, data, content_type = file_data
                body += f"--{boundary}\r\n".encode()
                body += f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode()
                body += f"Content-Type: {content_type}\r\n\r\n".encode()
                body += data
                body += b"\r\n"

        # End boundary
        body += f"--{boundary}--\r\n".encode()

        return body, boundary

    def _call_openai_edit(
        self,
        image_data,
        mask_data,
        prompt,
        api_key,
        size="1024x1024",
        progress_label=None,
    ):
        """Call OpenAI GPT-Image-1 API for image editing (supports single image or array)"""
        try:
            print(f"DEBUG: Calling GPT-Image-1 API with prompt: {prompt}")

            # Validate inputs
            if not prompt or not prompt.strip():
                return False, "Error: Empty prompt provided", None

            if not image_data:
                return False, "Error: No image data provided", None

            # Support both single image and array of images
            is_array_mode = isinstance(image_data, list)

            if is_array_mode:
                print(f"DEBUG: Array mode: {len(image_data)} images provided")
                # A single listed image is generation from references, with
                # no mask; layer composite checks its own minimum of two
                if len(image_data) < 1:
                    return False, "Error: No images provided", None
                if len(image_data) > 16:
                    return False, "Error: Maximum 16 layers supported", None
            else:
                print("DEBUG: Single image mode")
                if not mask_data:
                    return (
                        False,
                        "Error: No mask data provided for single image mode",
                        None,
                    )

            # The edits endpoint rejects masks of 4 MB or more
            if mask_data and len(mask_data) >= 4 * 1024 * 1024:
                return (
                    False,
                    f"Error: Mask is {len(mask_data) / (1024 * 1024):.1f} MB, "
                    "over the 4 MB OpenAI accepts",
                    None,
                )

            if not api_key or api_key == "test-api-key":
                print("DEBUG: No valid API key provided, returning mock response")
                mock_response = {
                    "data": [
                        {
                            "url": "https://picsum.photos/512/512",
                            "revised_prompt": prompt,
                        }
                    ]
                }
                return True, "API call successful (mock - no API key)", mock_response

            url = "https://api.openai.com/v1/images/edits"

            # Prepare multipart form data
            model = self._get_openai_model()
            fields = {
                "model": model,
                "prompt": prompt,
                "n": "1",
                "quality": "high",
                "size": size if size else "1024x1024",  # Use provided size or default
                "moderation": "low",  # Less restrictive filtering
            }
            # Only gpt-image-1 takes input_fidelity; the 2.x models always use
            # high fidelity and may reject the parameter
            if model == "gpt-image-1":
                fields["input_fidelity"] = "high"

            # Prepare files for API based on mode
            import base64

            files = {}

            if is_array_mode:
                # Array mode - multiple images for composite (with same validation as single mode)
                image_files = []

                for i, layer_data in enumerate(image_data):
                    # Debug the input data format
                    print(
                        f"DEBUG: Array input {i} - type: {type(layer_data)}, size: {len(layer_data) if hasattr(layer_data, '__len__') else 'N/A'}"
                    )
                    if hasattr(layer_data, "startswith"):
                        png_header = b"\x89PNG"
                        has_png_header = (
                            layer_data.startswith(png_header)
                            if isinstance(layer_data, bytes)
                            else "not bytes"
                        )
                        print(
                            f"DEBUG: Array input {i} - starts with PNG header: {has_png_header}"
                        )

                    # Apply same validation as single image mode
                    if isinstance(layer_data, str):
                        # Base64 encoded data - decode it
                        print(f"DEBUG: Array input {i} - decoding base64 string")
                        layer_bytes = base64.b64decode(layer_data)
                    else:
                        # Already binary data
                        print(f"DEBUG: Array input {i} - using binary data as-is")
                        layer_bytes = layer_data

                    # Create debug file for inspection (same as single mode)
                    if self._is_debug_mode():
                        debug_dir = tempfile.gettempdir()
                        debug_filename = os.path.join(debug_dir, f"gpt-image-1_array_image_{i}_{len(layer_bytes)}_bytes.png")
                        try:
                            with open(debug_filename, "wb") as debug_file:
                                debug_file.write(layer_bytes)
                            print(f"DEBUG: Saved array image {i} to {debug_filename}")
                        except Exception as e:
                            print(f"DEBUG: Could not save debug file: {e}")

                    # Validate PNG format (same as single mode)
                    if layer_bytes.startswith(b"\x89PNG"):
                        if len(layer_bytes) > 25:
                            # Extract dimensions and format info
                            img_width = int.from_bytes(layer_bytes[16:20], "big")
                            img_height = int.from_bytes(layer_bytes[20:24], "big")
                            color_type = layer_bytes[25]
                            format_names = {
                                0: "L",
                                2: "RGB",
                                3: "P",
                                4: "LA",
                                6: "RGBA",
                            }
                            format_name = format_names.get(
                                color_type, f"Unknown({color_type})"
                            )
                            print(
                                f"DEBUG: Array image {i} format: {format_name} (color type {color_type}) dimensions: {img_width}x{img_height}"
                            )
                        else:
                            print(f"DEBUG: Array image {i} PNG header too short")
                    else:
                        print(f"DEBUG: Array image {i} is not PNG format!")

                    image_files.append((f"image_{i}.png", layer_bytes, "image/png"))
                    print(f"DEBUG: Added validated layer {i}: {len(layer_bytes)} bytes")

                files["image"] = image_files

                # Add mask if provided (applies to first image) - with same validation
                if mask_data:
                    # Create debug file for mask
                    if self._is_debug_mode():
                        debug_dir = tempfile.gettempdir()
                        debug_mask_filename = os.path.join(debug_dir, f"gpt-image-1_array_mask_{len(mask_data)}_bytes.png")
                        try:
                            with open(debug_mask_filename, "wb") as debug_file:
                                debug_file.write(mask_data)
                            print(f"DEBUG: Saved array mask to {debug_mask_filename}")
                        except Exception as e:
                            print(f"DEBUG: Could not save debug file: {e}")

                    # Validate mask format (same as single mode)
                    if mask_data.startswith(b"\x89PNG"):
                        if len(mask_data) > 25:
                            mask_width = int.from_bytes(mask_data[16:20], "big")
                            mask_height = int.from_bytes(mask_data[20:24], "big")
                            color_type = mask_data[25]
                            format_names = {
                                0: "L",
                                2: "RGB",
                                3: "P",
                                4: "LA",
                                6: "RGBA",
                            }
                            format_name = format_names.get(
                                color_type, f"Unknown({color_type})"
                            )
                            print(
                                f"DEBUG: Array mask format: {format_name} (color type {color_type}) dimensions: {mask_width}x{mask_height}"
                            )
                            print(f"DEBUG: Array mask size: {len(mask_data)} bytes")

                            # Check dimensions against first image (if available)
                            if image_files:
                                first_image_bytes = image_files[0][1]
                                if (
                                    first_image_bytes.startswith(b"\x89PNG")
                                    and len(first_image_bytes) > 25
                                ):
                                    first_img_width = int.from_bytes(
                                        first_image_bytes[16:20], "big"
                                    )
                                    first_img_height = int.from_bytes(
                                        first_image_bytes[20:24], "big"
                                    )
                                    if (
                                        first_img_width == mask_width
                                        and first_img_height == mask_height
                                    ):
                                        print(
                                            "DEBUG: ✅ Array mask and first image dimensions match!"
                                        )
                                    else:
                                        print(
                                            f"DEBUG: ❌ DIMENSION MISMATCH! First image: {first_img_width}x{first_img_height}, Mask: {mask_width}x{mask_height}"
                                        )
                        else:
                            print("DEBUG: Array mask PNG header too short")
                    else:
                        print("DEBUG: Array mask is not PNG format!")

                    files["mask"] = ("mask.png", mask_data, "image/png")
                    print(
                        f"DEBUG: Added validated mask: {len(mask_data)} bytes (applies to first image)"
                    )

                print(
                    f"DEBUG: Prepared {len(image_files)} validated images for composite mode"
                )

            else:
                # Single image mode - traditional inpainting
                image_bytes = base64.b64decode(image_data)

                # Save debug copies of what we're sending to GPT-Image-1
                if self._is_debug_mode():
                    debug_dir = tempfile.gettempdir()
                    debug_input_filename = os.path.join(debug_dir, f"gpt-image-1_input_{len(image_bytes)}_bytes.png")
                    try:
                        with open(debug_input_filename, "wb") as debug_file:
                            debug_file.write(image_bytes)
                        print(f"DEBUG: Saved input image to {debug_input_filename}")
                    except Exception as e:
                        print(f"DEBUG: Could not save debug file: {e}")

                    debug_mask_filename = os.path.join(debug_dir, f"gpt-image-1_mask_{len(mask_data)}_bytes.png")
                    try:
                        with open(debug_mask_filename, "wb") as debug_file:
                            debug_file.write(mask_data)
                        print(f"DEBUG: Saved mask to {debug_mask_filename}")
                    except Exception as e:
                        print(f"DEBUG: Could not save debug file: {e}")

                # Analyze both image formats by examining PNG headers
                if image_bytes.startswith(b"\x89PNG"):
                    # Check color type in IHDR chunk (byte 25) and dimensions
                    if len(image_bytes) > 25:
                        # Extract width and height from IHDR (bytes 16-23)
                        img_width = int.from_bytes(image_bytes[16:20], "big")
                        img_height = int.from_bytes(image_bytes[20:24], "big")
                        color_type = image_bytes[25]
                        format_names = {0: "L", 2: "RGB", 3: "P", 4: "LA", 6: "RGBA"}
                        format_name = format_names.get(
                            color_type, f"Unknown({color_type})"
                        )
                        print(
                            f"DEBUG: Input image format: {format_name} (color type {color_type}) dimensions: {img_width}x{img_height}"
                        )
                    else:
                        print("DEBUG: Input image PNG header too short")
                else:
                    print("DEBUG: Input image is not PNG format!")

                if mask_data.startswith(b"\x89PNG"):
                    # Check mask format and dimensions
                    if len(mask_data) > 25:
                        # Extract width and height from IHDR (bytes 16-23)
                        mask_width = int.from_bytes(mask_data[16:20], "big")
                        mask_height = int.from_bytes(mask_data[20:24], "big")
                        color_type = mask_data[25]
                        format_names = {0: "L", 2: "RGB", 3: "P", 4: "LA", 6: "RGBA"}
                        format_name = format_names.get(
                            color_type, f"Unknown({color_type})"
                        )
                        print(
                            f"DEBUG: Mask format: {format_name} (color type {color_type}) dimensions: {mask_width}x{mask_height}"
                        )
                        print(f"DEBUG: Mask size: {len(mask_data)} bytes")

                        # Check if dimensions match
                        if img_width == mask_width and img_height == mask_height:
                            print("DEBUG: ✅ Image and mask dimensions match!")
                        else:
                            print(
                                f"DEBUG: ❌ DIMENSION MISMATCH! Image: {img_width}x{img_height}, Mask: {mask_width}x{mask_height}"
                            )
                    else:
                        print("DEBUG: Mask PNG header too short")
                else:
                    print("DEBUG: Mask is not PNG format!")

                files = {
                    "image": ("image.png", image_bytes, "image/png"),
                    "mask": ("mask.png", mask_data, "image/png"),
                }

            body, boundary = self._create_multipart_data(fields, files)

            # Create request
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "GIMP-AI-Plugin/1.0",
            }

            req = urllib.request.Request(url, data=body, headers=headers, method="POST")

            print(f"DEBUG: Sending real {model} API request...")

            # Progress during network operation
            print("DEBUG: Setting progress text to 'Sending request to GPT-Image-1...'")
            # if progress_label:
            #     self._update_dual_progress(progress_label, "Sending request to GPT-Image-1...", 0.65)
            # else:
            #     # Fallback to old system if no dialog progress
            #     Gimp.progress_set_text("Sending request to GPT-Image-1...")
            #     Gimp.progress_update(0.65)  # 65% - API request started (after 60% mask)
            #     Gimp.displays_flush()  # Force UI update before blocking network call

            with self._make_url_request(req, timeout=OPENAI_TIMEOUT) as response:
                # More progress during data reading
                if progress_label:
                    self._update_progress(
                        progress_label, "Processing AI response...", 0.7
                    )
                else:
                    Gimp.progress_set_text("Processing AI response...")
                    Gimp.progress_update(0.7)  # 70% - Reading response

                response_data = response.read().decode("utf-8")

                if progress_label:
                    self._update_progress(progress_label, "Parsing AI result...", 0.75)
                else:
                    Gimp.progress_set_text("Parsing AI result...")
                    Gimp.progress_update(0.75)  # 75% - Parsing JSON

                response_json = json.loads(response_data)
                print(
                    f"DEBUG: {model} API response received: {len(response_data)} bytes"
                )
                return True, f"{model} API call successful", response_json

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
            print(f"DEBUG: OpenAI image API HTTP error: {e.code} - {error_body}")
            return False, f"OpenAI image API error {e.code}: {error_body[:200]}", None
        except Exception as e:
            print(f"DEBUG: OpenAI image API call failed: {e}")
            return False, f"OpenAI image API call failed: {str(e)}", None

    def _convert_mask_to_luminance(self, mask_data):
        """
        Convert an OpenAI-convention mask to a white-on-black luminance mask.

        OpenAI marks the area to inpaint with transparency; Stability marks it
        with white pixels (black = preserve), and the OpenAI mask hint shows the
        model the same white-on-black picture. Flattening the mask onto a white
        background converts one to the other, and keeps the feathered edges as
        greyscale values, which Stability reads as partial inpaint strength.

        Must run on the main thread - it uses GIMP image operations.
        """
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            temp_path = temp_file.name
            temp_file.write(mask_data)

        mask_image = None
        # Push a context so setting the background colour does not clobber the
        # user's own background colour.
        Gimp.context_push()
        try:
            mask_image = Gimp.file_load(
                run_mode=Gimp.RunMode.NONINTERACTIVE,
                file=Gio.File.new_for_path(temp_path),
            )
            if not mask_image:
                raise Exception("Failed to load mask for conversion")

            # Flatten fills transparent pixels with the background colour.
            Gimp.context_set_background(Gegl.Color.new("white"))
            mask_image.flatten()

            converted = self._export_layer_to_png(mask_image)
            if not converted:
                raise Exception("Failed to export converted mask")

            if self._is_debug_mode():
                debug_path = os.path.join(
                    tempfile.gettempdir(), f"luminance_mask_{len(converted)}_bytes.png"
                )
                try:
                    with open(debug_path, "wb") as debug_file:
                        debug_file.write(converted)
                    print(f"DEBUG: Saved luminance mask to {debug_path}")
                except Exception as e:
                    print(f"DEBUG: Could not save debug file: {e}")

            print(f"DEBUG: Converted mask to luminance convention: {len(converted)} bytes")
            return converted

        finally:
            Gimp.context_pop()
            if mask_image:
                mask_image.delete()
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def _call_bedrock_edit(self, image_data, mask_data, prompt, progress_label=None,
                           seed=None):
        """
        Call Amazon Bedrock (Stability inpainting) and return an OpenAI-shaped response.

        Returns the same {"data": [{"b64_json": ...}]} structure the OpenAI path
        returns, so the compositing pipeline needs no provider-specific handling.
        """
        try:
            if not prompt or not prompt.strip():
                return False, "Error: Empty prompt provided", None
            if not image_data:
                return False, "Error: No image data provided", None
            if not mask_data:
                return False, "Error: No mask data provided", None
            if isinstance(image_data, list):
                return (
                    False,
                    "Error: Bedrock inpainting does not support multiple images",
                    None,
                )

            bedrock_config = self.config.get("bedrock", {})
            creds = bedrock_client.resolve_credentials(bedrock_config)
            print(
                f"DEBUG: Calling Bedrock model {creds['model_id']} in {creds['region']}"
            )

            # The image arrives base64-encoded, the mask as raw PNG bytes.
            image_b64 = (
                image_data if isinstance(image_data, str)
                else base64.b64encode(image_data).decode("utf-8")
            )
            mask_b64 = base64.b64encode(mask_data).decode("utf-8")

            body = bedrock_client.build_inpaint_body(
                image_b64,
                mask_b64,
                prompt,
                negative_prompt=bedrock_config.get("negative_prompt", ""),
                seed=bedrock_config.get("seed", 0) if seed is None else seed,
                grow_mask=bedrock_config.get("grow_mask", 5),
            )
            url, headers = bedrock_client.build_invoke_request(creds, body)
            req = urllib.request.Request(
                url, data=body, headers=headers, method="POST"
            )

            print("DEBUG: Sending Bedrock InvokeModel request...")
            with self._make_url_request(req, timeout=180) as response:
                if progress_label:
                    self._update_progress(
                        progress_label, "Processing AI response...", 0.7
                    )
                else:
                    Gimp.progress_set_text("Processing AI response...")
                    Gimp.progress_update(0.7)

                response_json = json.loads(response.read().decode("utf-8"))

            images = response_json.get("images") or []
            if not images:
                reasons = response_json.get("finish_reasons")
                return False, f"Bedrock returned no image (finish: {reasons})", None

            # Stability reports content filtering through finish_reasons.
            finish_reasons = response_json.get("finish_reasons") or [None]
            if finish_reasons[0]:
                return (
                    False,
                    f"Bedrock refused the request: {finish_reasons[0]}",
                    None,
                )

            print(f"DEBUG: Bedrock returned image, seed {response_json.get('seeds')}")
            return (
                True,
                "Bedrock API call successful",
                {"data": [{"b64_json": images[0]}]},
            )

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
            print(f"DEBUG: Bedrock HTTP error: {e.code} - {error_body}")
            return False, f"Bedrock API error {e.code}: {error_body[:300]}", None
        except Exception as e:
            print(f"DEBUG: Bedrock API call failed: {e}")
            return False, f"Bedrock API call failed: {str(e)}", None

    def _calculate_native_context_extraction(self, image):
        """
        Context window for Bedrock at native resolution.

        The OpenAI-shaped path snaps to 1024x1024 / 1536x1024 / 1024x1536, which
        for a small selection means upscaling a tight crop - a 160px selection
        became a 288px crop blown up 3.56x, so 92% of what the model saw was
        interpolated. Bedrock accepts arbitrary sizes, so the window is taken at
        native resolution instead and only ever scaled down.
        """
        from coordinate_utils import calculate_native_context

        bounds = Gimp.Selection.bounds(image)
        sel_x1, sel_y1, sel_x2, sel_y2 = bounds[2], bounds[3], bounds[4], bounds[5]

        native = calculate_native_context(
            image.get_width(), image.get_height(), (sel_x1, sel_y1, sel_x2, sel_y2)
        )
        x, y, width, height = native["extract_region"]
        print(
            f"DEBUG: Native context window ({x},{y}) {width}x{height}, "
            f"scale {native['padding_info']['scale_factor']:.3f} "
            f"(selection {sel_x2 - sel_x1}x{sel_y2 - sel_y1})"
        )

        return {
            "mode": "focused",
            "selection_bounds": (sel_x1, sel_y1, sel_x2, sel_y2),
            "extract_region": native["extract_region"],
            "target_shape": native["target_shape"],
            "target_size": max(native["target_shape"]),
            "needs_padding": False,
            "padding_info": native["padding_info"],
            "has_selection": True,
        }

    def _calculate_gpt_image_context_extraction(self, image):
        """
        Context window for GPT-Image-2.x at native resolution.

        Same window as Bedrock's native path - a small selection gets a large
        window of real pixels instead of a tight crop blown up to a fixed
        shape - then fitted to the 2.x size rules (multiples of 16, area
        limits). Only rounding leaves a few pixels of padding.
        """
        from coordinate_utils import calculate_native_context

        bounds = Gimp.Selection.bounds(image)
        sel_x1, sel_y1, sel_x2, sel_y2 = bounds[2], bounds[3], bounds[4], bounds[5]

        native = calculate_native_context(
            image.get_width(), image.get_height(), (sel_x1, sel_y1, sel_x2, sel_y2)
        )
        x, y, width, height = native["extract_region"]
        target_shape = get_gpt_image_shape(width, height)
        padding_info = calculate_padding_for_shape(width, height, *target_shape)
        print(
            f"DEBUG: GPT-Image context window ({x},{y}) {width}x{height} -> "
            f"{target_shape[0]}x{target_shape[1]}, "
            f"scale {padding_info['scale_factor']:.3f} "
            f"(selection {sel_x2 - sel_x1}x{sel_y2 - sel_y1})"
        )

        return {
            "mode": "focused",
            "selection_bounds": (sel_x1, sel_y1, sel_x2, sel_y2),
            "extract_region": native["extract_region"],
            "target_shape": target_shape,
            "target_size": max(target_shape),
            "needs_padding": any(padding_info["padding"]),
            "padding_info": padding_info,
            "has_selection": True,
        }

    def _call_bedrock_erase(self, image_data, mask_data, progress_label=None, seed=None):
        """
        Call Bedrock's object-erase model. Same response shape as the others.

        There is no prompt: the model fills the masked region from surrounding
        context. Prompted inpainting invents new content instead of removing.
        """
        try:
            if not image_data or not mask_data:
                return False, "Error: Missing image or mask data", None

            bedrock_config = self.config.get("bedrock", {})
            creds = bedrock_client.erase_credentials(bedrock_config)
            print(f"DEBUG: Calling Bedrock erase model {creds['model_id']}")

            image_b64 = (
                image_data if isinstance(image_data, str)
                else base64.b64encode(image_data).decode("utf-8")
            )
            body = bedrock_client.build_erase_body(
                image_b64,
                base64.b64encode(mask_data).decode("utf-8"),
                seed=bedrock_config.get("seed", 0) if seed is None else seed,
                grow_mask=bedrock_config.get("grow_mask", 5),
            )
            url, headers = bedrock_client.build_invoke_request(creds, body)
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")

            with self._make_url_request(req, timeout=180) as response:
                if progress_label:
                    self._update_progress(progress_label, "Processing result...", 0.7)
                response_json = json.loads(response.read().decode("utf-8"))

            images = response_json.get("images") or []
            if not images:
                return False, "Bedrock returned no image", None

            finish_reasons = response_json.get("finish_reasons") or [None]
            if finish_reasons[0]:
                return False, f"Bedrock refused the request: {finish_reasons[0]}", None

            return True, "Erase successful", {"data": [{"b64_json": images[0]}]}

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
            print(f"DEBUG: Bedrock erase HTTP error: {e.code} - {error_body}")
            return False, f"Bedrock API error {e.code}: {error_body[:300]}", None
        except Exception as e:
            print(f"DEBUG: Bedrock erase failed: {e}")
            return False, f"Bedrock erase failed: {str(e)}", None

    def _run_bedrock_variations(self, count, make_call, progress_label=None,
                                name="Bedrock variations"):
        """
        Produce `count` alternative results and return those that succeeded.

        Each call gets its own random seed, which is what makes the results
        differ. The calls run concurrently: they are network-bound at roughly 15
        seconds each, so running them in sequence would multiply the wait for no
        benefit. A variation that fails (throttling, say) is dropped and the rest
        are still used.

        make_call(seed) must return the usual (success, message, response).
        """
        import concurrent.futures
        import random

        seeds = [random.randint(1, 4294967294) for _ in range(count)]
        print(f"DEBUG: Requesting {count} variations for {name}, seeds {seeds}")

        def operation():
            results = []
            errors = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
                futures = {pool.submit(make_call, seed): seed for seed in seeds}
                for future in concurrent.futures.as_completed(futures):
                    seed = futures[future]
                    try:
                        success, message, response = future.result()
                    except Exception as e:
                        success, message, response = False, str(e), None
                    if success:
                        results.append((seed, response))
                    else:
                        errors.append(f"seed {seed}: {message}")
                        print(f"DEBUG: Variation {seed} failed: {message}")

            if not results:
                return {
                    "success": False,
                    "message": "; ".join(errors) or "All variations failed",
                    "data": None,
                }
            return {
                "success": True,
                "message": f"{len(results)} of {count} variations succeeded",
                "data": results,
            }

        return self._run_threaded_operation(operation, name, progress_label)

    def _place_variation_layers(self, image, results, context_info, label):
        """
        Composite each variation as its own layer, newest at the bottom of the
        batch so variation 1 ends on top, then name them and show only the first.
        """
        placed = 0
        last_message = ""
        for seed, response in reversed(results):
            ok, last_message = self._download_and_composite_result(
                image, response, context_info, "full"
            )
            if ok:
                placed += 1
                print(f"DEBUG: Placed {label} variation (seed {seed})")
            else:
                print(f"DEBUG: Failed to place variation {seed}: {last_message}")

        for index, layer in enumerate(image.get_layers()[:placed], start=1):
            layer.set_name(
                f"AI {label} Result" if placed == 1
                else f"AI {label} {index} of {placed}"
            )
            layer.set_visible(index == 1)
        if placed:
            Gimp.displays_flush()
        return placed, last_message

    def _call_edit_threaded(
        self,
        image_data,
        mask_data,
        prompt,
        api_key,
        size="1024x1024",
        progress_label=None,
    ):
        """Threaded wrapper for the configured provider's edit call"""
        provider = self._get_provider()

        def operation():
            if provider == "bedrock":
                success, message, response = self._call_bedrock_edit(
                    image_data, mask_data, prompt, progress_label
                )
            else:
                success, message, response = self._call_openai_edit(
                    image_data, mask_data, prompt, api_key, size, progress_label
                )
            return {"success": success, "message": message, "data": response}

        return self._run_threaded_operation(
            operation, f"{provider} edit API call", progress_label
        )

    def _call_openai_edit_threaded(
        self,
        image_data,
        mask_data,
        prompt,
        api_key,
        size="1024x1024",
        progress_label=None,
    ):
        """Threaded wrapper for OpenAI API call to keep UI responsive"""
        def operation():
            success, message, response = self._call_openai_edit(
                image_data, mask_data, prompt, api_key, size, progress_label
            )
            return {"success": success, "message": message, "data": response}

        return self._run_threaded_operation(
            operation, "OpenAI edit API call", progress_label
        )

    def _download_and_composite_result(
        self, image, api_response, context_info, mode, color_info=None,
        clip_to_selection=True,
    ):
        """Download AI result and composite it back to original image with proper masking"""
        try:
            print("DEBUG: Downloading and compositing AI result")

            # Validate inputs
            if not image:
                return False, "Error: No GIMP image provided"
            if not api_response or "data" not in api_response:
                return False, "Invalid API response - no data"
            if not api_response["data"] or len(api_response["data"]) == 0:
                return False, "Invalid API response - empty data array"

            result_data = api_response["data"][0]

            # Handle both URL and base64 response formats
            if "url" in result_data:
                # URL format (DALL-E 2 style)
                image_url = result_data["url"]
                print(f"DEBUG: Downloading result from: {image_url}")

                # Update progress for download phase
                Gimp.progress_set_text("Downloading AI result...")
                Gimp.progress_update(0.8)  # 80% - Starting download
                Gimp.displays_flush()

                # Download from URL
                with self._make_url_request(image_url, timeout=60) as response:
                    image_data = response.read()

            elif "b64_json" in result_data:
                # Base64 format (GPT-Image-1 style)
                print("DEBUG: Processing base64 image data from GPT-Image-1")

                # Update progress for processing phase
                Gimp.progress_set_text("Processing AI result...")
                Gimp.progress_update(0.8)  # 80% - Processing data
                Gimp.displays_flush()

                # Decode base64 data
                import base64

                image_data = base64.b64decode(result_data["b64_json"])

            else:
                return False, "Invalid API response - no image URL or base64 data"

            print(f"DEBUG: Processed {len(image_data)} bytes")

            # Save to temporary file
            Gimp.progress_set_text("Processing AI result...")
            Gimp.progress_update(0.9)  # 90% - Processing
            Gimp.displays_flush()

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False, mode='wb') as temp_file:
                temp_filename = temp_file.name
                temp_file.write(image_data)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            # Save debug copy
            if self._is_debug_mode():
                debug_dir = tempfile.gettempdir()
                debug_filename = os.path.join(debug_dir, f"gpt-image-1_result_{len(image_data)}_bytes.png")
                try:
                    with open(debug_filename, "wb") as debug_file:
                        debug_file.write(image_data)
                    print(f"DEBUG: Saved GPT-Image-1 result to {debug_filename} for inspection")
                except Exception as e:
                    print(f"DEBUG: Could not save debug file: {e}")

            try:
                # Load the AI result into a temporary image
                Gimp.progress_set_text("Loading AI result...")
                Gimp.progress_update(0.95)  # 95% - Loading
                Gimp.displays_flush()

                file = Gio.File.new_for_path(temp_filename)
                ai_result_img = Gimp.file_load(
                    run_mode=Gimp.RunMode.NONINTERACTIVE, file=file
                )

                if not ai_result_img:
                    return False, "Failed to load AI result image"

                ai_layers = ai_result_img.get_layers()
                if not ai_layers or len(ai_layers) == 0:
                    ai_result_img.delete()
                    return False, "No layers found in AI result"

                ai_layer = ai_layers[0]
                print(
                    f"DEBUG: AI result dimensions: {ai_layer.get_width()}x{ai_layer.get_height()}"
                )

                # Get original image dimensions
                orig_width = image.get_width()
                orig_height = image.get_height()

                # Get context info for compositing
                sel_x1, sel_y1, sel_x2, sel_y2 = context_info["selection_bounds"]
                ctx_x1, ctx_y1, ctx_width, ctx_height = context_info["extract_region"]
                target_shape = context_info["target_shape"]

                print(f"DEBUG: Original image: {orig_width}x{orig_height}")
                print(
                    f"DEBUG: Selection bounds: ({sel_x1},{sel_y1}) to ({sel_x2},{sel_y2})"
                )
                print(
                    f"DEBUG: Extract region: ({ctx_x1},{ctx_y1}), size {ctx_width}x{ctx_height}"
                )

                # The padding crop below uses offsets that are only valid at the
                # size we asked the API for, so normalise first if the provider
                # returned something else.
                if target_shape and (
                    ai_result_img.get_width(),
                    ai_result_img.get_height(),
                ) != tuple(target_shape):
                    print(
                        f"DEBUG: AI result {ai_result_img.get_width()}x{ai_result_img.get_height()} "
                        f"differs from requested {target_shape}, rescaling"
                    )
                    ai_result_img.scale(target_shape[0], target_shape[1])
                    ai_layer = ai_result_img.get_layers()[0]

                # Scale AI result back to extract region size if needed
                if (
                    ai_layer.get_width() != ctx_width
                    or ai_layer.get_height() != ctx_height
                ):
                    scaled_img = ai_result_img.duplicate()

                    # For any mode with padding, remove padding first, then scale
                    if "padding_info" in context_info:
                        padding_info = context_info["padding_info"]
                        pad_left, pad_top, pad_right, pad_bottom = padding_info[
                            "padding"
                        ]
                        scaled_w, scaled_h = padding_info["scaled_size"]

                        print(
                            f"DEBUG: Removing padding from AI result: crop to {scaled_w}x{scaled_h}"
                        )
                        print(
                            f"DEBUG: Padding to remove: left={pad_left}, top={pad_top}, right={pad_right}, bottom={pad_bottom}"
                        )

                        # Crop to remove padding (get the actual content without black bars)
                        scaled_img.crop(scaled_w, scaled_h, pad_left, pad_top)
                        print(
                            f"DEBUG: Cropped AI result to {scaled_w}x{scaled_h} (removed padding)"
                        )

                        # Now scale the unpadded result to original size
                        scaled_img.scale(ctx_width, ctx_height)
                        print(
                            f"DEBUG: Scaled unpadded result to original size: {ctx_width}x{ctx_height}"
                        )
                    else:
                        # Normal scaling for non-padded results
                        scaled_img.scale(ctx_width, ctx_height)
                        print(
                            f"DEBUG: Scaled AI result to extract region size: {ctx_width}x{ctx_height}"
                        )

                    scaled_layers = scaled_img.get_layers()
                    if scaled_layers:
                        ai_layer = scaled_layers[0]

                # USE PURE COORDINATE FUNCTION FOR PLACEMENT
                placement = calculate_placement_coordinates(context_info)
                paste_x = placement["paste_x"]
                paste_y = placement["paste_y"]
                result_width = placement["result_width"]
                result_height = placement["result_height"]

                # Place the AI result by copying it as a layer and offsetting
                # it. The Gegl buffer-source -> translate -> write-buffer chain
                # this replaced writes nothing at all under GIMP 3.2 (measured:
                # zero pixels landed), so the result layer stayed empty and every
                # inpaint looked like it had done nothing.
                print(f"DEBUG: Placing {ctx_width}x{ctx_height} AI result at ({paste_x},{paste_y})")

                selection_channel = Gimp.Selection.save(image)

                result_layer = Gimp.Layer.new_from_drawable(ai_layer, image)
                result_layer.set_name("AI Inpaint Result")
                image.insert_layer(result_layer, None, 0)
                result_layer.set_offsets(paste_x, paste_y)

                # Alpha first: without it, growing the layer to canvas size
                # fills the new area with opaque background instead of
                # transparency, hiding the rest of the image.
                result_layer.add_alpha()
                result_layer.resize_to_image_size()

                placed_x, placed_y = result_layer.get_offsets()[1:]
                print(
                    f"DEBUG: Result layer placed at ({placed_x},{placed_y}), "
                    f"size {result_layer.get_width()}x{result_layer.get_height()}"
                )

                # Restore the original selection
                print("DEBUG: Restoring original selection")
                try:
                    pdb = Gimp.get_pdb()
                    select_proc = pdb.lookup_procedure("gimp-image-select-item")
                    select_config = select_proc.create_config()
                    select_config.set_property("image", image)
                    select_config.set_property("operation", Gimp.ChannelOps.REPLACE)
                    select_config.set_property("item", selection_channel)
                    select_proc.run(select_config)
                    print("DEBUG: Selection successfully restored")
                except Exception as e:
                    print(f"DEBUG: Could not restore selection: {e}")
                # Clean up the temporary selection channel
                image.remove_channel(selection_channel)

                # Apply color matching for contextual mode (before masking)
                if mode == "contextual" and color_info:
                    print("DEBUG: Applying color matching to result layer...")
                    self._apply_color_matching(result_layer, color_info)

                # Restrict the result layer to the selection in contextual and full-image modes
                if mode in ("contextual", "full_image") and context_info["has_selection"]:
                    print(
                        "DEBUG: Creating selection-based mask for contextual mode while preserving full AI result in layer"
                    )

                    # Use GIMP's built-in selection mask type to automatically create properly shaped mask
                    # This preserves the full AI content in the layer but masks visibility to selection area
                    mask = result_layer.create_mask(Gimp.AddMaskType.SELECTION)
                    result_layer.add_mask(mask)

                    # Apply smart feathering to the mask for better blending
                    self._apply_smart_mask_feathering(mask, image)

                    # Unclipped: keep the mask but leave it off, so the clip is
                    # one click away (Layers > Disable Layer Mask) and the mask
                    # can be painted to allow only some of the overflow
                    if not clip_to_selection:
                        result_layer.set_apply_mask(False)
                        print("DEBUG: Clip to selection off - layer mask kept but disabled")

                    print(
                        "DEBUG: Applied selection-based layer mask with smart feathering - enhanced blending at edges"
                    )
                    print(
                        "DEBUG: Core subject preserved at 100%, edges feathered for seamless integration"
                    )
                else:
                    print(
                        "DEBUG: No active selection - layer shows full AI result without mask"
                    )

                # VALIDATION CHECKS
                print(f"DEBUG: === SIMPLIFIED ALIGNMENT VALIDATION ===")
                print(
                    f"DEBUG: Context square positioned at: ({paste_x},{paste_y}) with size {result_width}x{result_height}"
                )
                print(
                    f"DEBUG: Original selection was: ({sel_x1},{sel_y1}) to ({sel_x2},{sel_y2})"
                )
                print(
                    f"DEBUG: Since we work with true squares, alignment should be perfect"
                )
                print(
                    f"DEBUG: Selection coordinates within context square: ({sel_x1-paste_x},{sel_y1-paste_y}) to ({sel_x2-paste_x},{sel_y2-paste_y})"
                )

                # Clean up temporary image
                ai_result_img.delete()
                os.unlink(temp_filename)

                # Force display update
                Gimp.displays_flush()

                layer_count = len(image.get_layers())
                print(
                    f"DEBUG: Successfully composited AI result. Total layers: {layer_count}"
                )

                return (
                    True,
                    f"AI result composited as masked layer: '{result_layer.get_name()}' (total layers: {layer_count})",
                )

            except Exception as e:
                print(f"DEBUG: Compositing failed: {e}")
                if os.path.exists(temp_filename):
                    os.unlink(temp_filename)
                return False, f"Failed to composite result: {str(e)}"

        except Exception as e:
            print(f"DEBUG: Download and composite failed: {e}")
            return False, f"Failed to download result: {str(e)}"

    def do_query_procedures(self):
        return [
            "gimp-ai-inpaint",
            "gimp-ai-erase-object",
            "gimp-ai-upscale",
            "gimp-ai-layer-generator",
            "gimp-ai-image-generator",
            "gimp-ai-layer-composite",
            "gimp-ai-settings",
        ]

    def do_create_procedure(self, name):
        if name == "gimp-ai-inpaint":
            procedure = Gimp.ImageProcedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, self.run_inpaint, None
            )
            procedure.set_menu_label("Inpainting")
            procedure.add_menu_path("<Image>/Filters/AI/")
            return procedure

        elif name == "gimp-ai-erase-object":
            procedure = Gimp.ImageProcedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, self.run_erase_object, None
            )
            procedure.set_menu_label("Erase Object")
            procedure.add_menu_path("<Image>/Filters/AI/")
            return procedure

        elif name == "gimp-ai-upscale":
            procedure = Gimp.ImageProcedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, self.run_upscale, None
            )
            procedure.set_menu_label("Generative Upscale")
            procedure.add_menu_path("<Image>/Filters/AI/")
            return procedure

        elif name == "gimp-ai-layer-generator":
            procedure = Gimp.ImageProcedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, self.run_layer_generator, None
            )
            procedure.set_menu_label("Generate Layer")
            procedure.add_menu_path("<Image>/Filters/AI/")
            # Needs an open image, but not a selected drawable
            procedure.set_sensitivity_mask(
                Gimp.ProcedureSensitivityMask.DRAWABLE
                | Gimp.ProcedureSensitivityMask.DRAWABLES
                | Gimp.ProcedureSensitivityMask.NO_DRAWABLES
            )
            return procedure

        elif name == "gimp-ai-image-generator":
            procedure = Gimp.ImageProcedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, self.run_image_generator, None
            )
            procedure.set_menu_label("Generate New Image")
            procedure.add_menu_path("<Image>/Filters/AI/")
            procedure.add_menu_path("<Image>/File/Create/")
            # Ignores any open image, so it is available with or without one
            procedure.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.ALWAYS)
            return procedure

        elif name == "gimp-ai-layer-composite":
            procedure = Gimp.ImageProcedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, self.run_layer_composite, None
            )
            procedure.set_menu_label("Layer Composite")
            procedure.add_menu_path("<Image>/Filters/AI/")
            return procedure

        elif name == "gimp-ai-settings":
            procedure = Gimp.ImageProcedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, self.run_settings, None
            )
            procedure.set_menu_label("Settings...")
            procedure.add_menu_path("<Image>/Filters/AI/")
            # Settings do not touch the image, so allow them with none open
            procedure.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.ALWAYS)
            return procedure

        return None

    def run_upscale(self, procedure, run_mode, image, drawables, config, run_data):
        """
        Generative Upscale - enlarge the active layer using a Stability model.

        Works on the active layer rather than the whole image, so an upscaled
        result can be dropped alongside the original for comparison.
        """
        print("DEBUG: AI Generative Upscale called!")
        original_selected_layers = image.get_selected_layers()

        if not original_selected_layers:
            Gimp.message(
                "❌ No Active Layer\n\n"
                "Generative Upscale works on the active layer.\n"
                "Select a layer in the Layers dialog and try again."
            )
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

        source_layer = original_selected_layers[0]

        if self._get_provider() != "bedrock" or bedrock_client is None:
            Gimp.message(
                "❌ Generative Upscale requires Amazon Bedrock\n\n"
                "It uses Stability's upscale models, which have no OpenAI "
                "equivalent.\n\n"
                "Set the inpainting provider to Amazon Bedrock in "
                "Filters → AI → Settings."
            )
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

        dialog_result = self._show_upscale_dialog()
        if not dialog_result:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
        dialog, progress_label, variant, prompt = dialog_result

        def attempt():
            """One try at the upscale; returns (headline, details) on failure"""
            self._log_bedrock_environment()
            try:
                bedrock_client.upscale_credentials(
                    self.config.get("bedrock", {}), variant
                )
            except bedrock_client.BedrockError as e:
                return "Amazon Bedrock is not configured", str(e)

            self._update_progress(progress_label, "🔍 Preparing layer...")
            image_data, original_size, sent_size = self._export_layer_for_upscale(
                source_layer, variant
            )
            if not image_data:
                return "Could not export the active layer", ""

            note = ""
            if sent_size != original_size:
                note = (
                    f" (reduced from {original_size[0]}x{original_size[1]} to fit "
                    f"the {variant} input limit)"
                )
            self._update_progress(
                progress_label,
                f"🚀 Upscaling {sent_size[0]}x{sent_size[1]} with {variant}...{note}",
            )

            ok, message, data = self._call_upscale_threaded(
                variant, image_data, prompt, progress_label
            )
            if not ok:
                if "cancelled" not in message.lower():
                    return "Upscale failed", message
                self._update_progress(progress_label, "❌ Cancelled")
                Gimp.message("❌ Operation cancelled by user")
                return None

            self._update_progress(progress_label, "Placing result...")
            suffix = "." + bedrock_client.UPSCALE_VARIANTS[variant]["output_format"]
            new_layer = self._add_layer_from_bytes(
                image, data, suffix, f"AI Upscale ({variant})",
                offsets=source_layer.get_offsets()[1:],
            )
            if not new_layer:
                return "Upscale returned an image GIMP could not load", ""

            factor = new_layer.get_width() / max(1, original_size[0])
            self._update_progress(
                progress_label,
                f"✅ Upscaled to {new_layer.get_width()}x{new_layer.get_height()} "
                f"({factor:.1f}x)",
            )
            Gimp.message(
                f"✅ Upscaled to {new_layer.get_width()}x{new_layer.get_height()} "
                f"({factor:.1f}x the original layer).\n\n"
                "The new layer is larger than the canvas. Use "
                "Image → Fit Canvas to Layers to see all of it."
            )
            Gimp.displays_flush()
            return None

        try:
            # On failure the dialog stays open with the error shown, so the
            # user can pick another method or prompt and try again
            while True:
                try:
                    failure = attempt()
                except Exception as e:
                    print(f"ERROR: Upscale failed: {e}")
                    failure = ("Upscale failed", str(e))
                if not failure:
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.SUCCESS, GLib.Error()
                    )

                # API calls can clear the layer selection
                image.set_selected_layers(original_selected_layers)
                dialog_result = self._offer_retry(progress_label, *failure)
                if not dialog_result:
                    # Closed or cancelled; the dialog has already destroyed itself
                    dialog = None
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.CANCEL, GLib.Error()
                    )
                dialog, progress_label, variant, prompt = dialog_result

        finally:
            if dialog:
                dialog.destroy()
            if original_selected_layers:
                image.set_selected_layers(original_selected_layers)

    def run_erase_object(self, procedure, run_mode, image, drawables, config, run_data):
        """
        Erase Object - remove what is selected and fill from the surroundings.

        Deliberately has no prompt and no options: select the thing, run this,
        done. Removal is a single unambiguous operation, so asking the user to
        describe it only invites the model to invent content instead.
        """
        print("DEBUG: AI Erase Object called!")
        original_selected_layers = image.get_selected_layers()

        selection_bounds = Gimp.Selection.bounds(image)
        if not (len(selection_bounds) >= 5 and selection_bounds[0]):
            Gimp.message(
                "❌ No Selection Found!\n\n"
                "Erase Object removes whatever is selected.\n\n"
                "Please:\n"
                "1. Select the object you want gone\n"
                "2. Run Filters → AI → Erase Object again"
            )
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

        provider = self._get_provider()
        if provider != "bedrock" or bedrock_client is None:
            Gimp.message(
                "❌ Erase Object requires Amazon Bedrock\n\n"
                "It uses Stability's object-erase model, which has no OpenAI "
                "equivalent.\n\n"
                "Set the inpainting provider to Amazon Bedrock in "
                "Filters → AI → Settings."
            )
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

        self._log_bedrock_environment()
        try:
            bedrock_client.resolve_credentials(self.config.get("bedrock", {}))
        except bedrock_client.BedrockError as e:
            Gimp.message(f"❌ Amazon Bedrock is not configured!\n\n{e}")
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

        self._init_gimp_ui()
        dialog = self._create_dialog_base("AI Erase Object", size=(460, 140))
        content_area = self._setup_dialog_content_area(dialog)
        status_box = self._add_section(
            content_area, "Erasing Object",
            "Removing the selected object and filling in the background.",
            icon="erase", separator=False,
        )
        self._add_model_info_box(status_box, "erase")
        progress_frame, progress_label = self._create_progress_widget()
        status_box.pack_start(progress_frame, False, False, 0)

        # Only shown after a failed attempt; there is nothing to adjust here,
        # but transient errors such as throttling are worth another try
        close_button = dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        retry_button = dialog.add_button("Try Again", Gtk.ResponseType.OK)
        retry_button.get_style_context().add_class("suggested-action")

        content_area.show_all()
        dialog.show_all()
        close_button.hide()
        retry_button.hide()

        def retry_dialog():
            close_button.show()
            retry_button.show()
            retry_button.grab_focus()
            response = dialog.run()
            close_button.hide()
            retry_button.hide()
            if response != Gtk.ResponseType.OK:
                dialog.destroy()
                return None
            return True

        self._retry_dialog = retry_dialog

        def attempt():
            """One try at the erase; returns (headline, details) on failure"""
            # The selection can change between attempts
            selection_bounds = Gimp.Selection.bounds(image)
            if not (len(selection_bounds) >= 5 and selection_bounds[0]):
                return "No selection", "Select the object you want gone."

            self._update_progress(progress_label, "🔍 Reading image...")
            context_info = self._calculate_native_context_extraction(image)

            success, message, image_data = self._extract_context_region(
                image, context_info
            )
            if not success:
                return "Context extraction failed", message

            self._update_progress(progress_label, "🎭 Creating mask...")
            mask_data = self._convert_mask_to_luminance(
                self._create_context_mask(
                    image, context_info, context_info["target_size"]
                )
            )

            variations = self.config.get("bedrock", {}).get("erase_variations", 3)
            variations = max(1, min(4, int(variations)))
            self._update_progress(
                progress_label, f"🚀 Erasing ({variations} variations)..."
            )
            api_success, api_message, results = self._run_bedrock_variations(
                variations,
                lambda seed: self._call_bedrock_erase(image_data, mask_data, None, seed),
                progress_label,
                name="Bedrock erase API call",
            )

            if not api_success:
                if "cancelled" not in api_message.lower():
                    return "Erase failed", api_message
                self._update_progress(progress_label, "❌ Cancelled")
                Gimp.message("❌ Operation cancelled by user")
                return None

            self._update_progress(progress_label, "Placing results...")
            # Paste the whole context window rather than masking back to the
            # selection. The model returns the window with only the masked area
            # changed (measured: ~3% of pixels) and has already blended across
            # the mask edge, so re-masking to the selection rectangle would
            # discard that blending and leave a visible rectangular seam.
            placed, last_message = self._place_variation_layers(
                image, results, context_info, "Erase"
            )

            if placed:
                self._update_progress(
                    progress_label,
                    "✅ Object erased!" if placed == 1
                    else f"✅ {placed} variations - toggle layer visibility to compare",
                )
                print(f"DEBUG: Erase complete - {placed} variation(s) placed")
            else:
                return "Erased but import failed", last_message

            return None

        try:
            while True:
                try:
                    failure = attempt()
                except Exception as e:
                    print(f"ERROR: Erase failed: {e}")
                    failure = ("Erase failed", str(e))
                if not failure:
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.SUCCESS, GLib.Error()
                    )

                # API calls can clear the layer selection
                if original_selected_layers:
                    image.set_selected_layers(original_selected_layers)
                if not self._offer_retry(
                    progress_label, *failure,
                    "Press Try Again to retry, or Close.",
                ):
                    # Closed; the dialog has already destroyed itself
                    dialog = None
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.CANCEL, GLib.Error()
                    )

        finally:
            if dialog:
                dialog.destroy()
            if original_selected_layers:
                image.set_selected_layers(original_selected_layers)

    def run_inpaint(self, procedure, run_mode, image, drawables, config, run_data):
        print("DEBUG: AI Inpaint Selection called!")

        # Save the currently selected layers before any API calls that might clear them
        original_selected_layers = image.get_selected_layers()
        print(f"DEBUG: Saved {len(original_selected_layers)} originally selected layers")

        # Step 1: Check for active selection FIRST
        print("DEBUG: Checking for active selection...")
        selection_bounds = Gimp.Selection.bounds(image)
        has_selection = len(selection_bounds) >= 5 and selection_bounds[0]

        if not has_selection:
            print("DEBUG: No selection found - showing error message")
            Gimp.message(
                "❌ No Selection Found!\n\n"
                "AI Inpainting requires an active selection to define the area to modify.\n\n"
                "Please:\n"
                "1. Use selection tools (Rectangle, Ellipse, Free Select, etc.)\n"
                "2. Select the area you want to inpaint\n"
                "3. Run AI Inpaint Selection again"
            )
            # Restore layer selection before returning
            if original_selected_layers:
                image.set_selected_layers(original_selected_layers)
                print("DEBUG: Restored layer selection after no canvas selection error")
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

        print("DEBUG: Selection found - proceeding with inpainting")

        # Step 2: Get user prompt
        print("DEBUG: About to show prompt dialog...")
        dialog_result = self._show_prompt_dialog(
            "AI Inpaint",
            "",
            show_mode_selection=True,
            image=image,
            provider=self._get_provider(),
            # Bedrock's inpainting takes a single image
            show_references=self._get_provider() == "openai",
            operation="inpaint",
        )
        print(f"DEBUG: Dialog returned: {repr(dialog_result)}")

        if not dialog_result:
            print("DEBUG: User cancelled prompt dialog")
            # Restore layer selection before returning
            if original_selected_layers:
                image.set_selected_layers(original_selected_layers)
                print("DEBUG: Restored layer selection after dialog cancel")
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

        # Extract dialog, progress_label, prompt and mode from dialog result
        dialog, progress_label, prompt, selected_mode = dialog_result
        print(f"DEBUG: Extracted prompt: '{prompt}', mode: '{selected_mode}'")

        def attempt():
            """One try at the inpaint; returns (headline, details) on failure"""
            # The selection can change between attempts
            selection_bounds = Gimp.Selection.bounds(image)
            if not (len(selection_bounds) >= 5 and selection_bounds[0]):
                return "No selection", "Select the area you want to inpaint."

            # Step 3: Get credentials for the configured provider; read on
            # every attempt since Settings may have changed them
            provider = self._get_provider()
            print(f"DEBUG: Inpainting provider: {provider}")
            api_key = None
            if provider == "bedrock":
                self._log_bedrock_environment()
                try:
                    bedrock_client.resolve_credentials(self.config.get("bedrock", {}))
                except bedrock_client.BedrockError as e:
                    return "Amazon Bedrock is not configured", str(e)
            else:
                api_key = self._get_api_key()
                if not api_key:
                    return (
                        "No OpenAI API key found",
                        "Set your API key in Settings, config.json or the "
                        "OPENAI_API_KEY environment variable.",
                    )

            # Create progress callback for thread-to-UI communication
            progress_callback = self._create_progress_callback(progress_label)

            # Do GIMP operations on main thread, only thread the API call
            mode = self._get_processing_mode(selected_mode)
            print(f"DEBUG: Using processing mode: {mode}")

            self._update_progress(progress_label, "🔍 Processing image...")

            any_size = provider == "openai" and self._openai_accepts_any_size()

            if mode == "full_image":
                print("DEBUG: Calculating full-image context extraction...")
                context_info = self._calculate_full_image_context_extraction(
                    image, any_size=any_size
                )
            elif mode == "contextual" and provider == "bedrock":
                # Native resolution, so the window round-trips without
                # resampling and can be pasted back whole
                print("DEBUG: Calculating native-resolution context extraction...")
                context_info = self._calculate_native_context_extraction(image)
            elif mode == "contextual" and any_size:
                print("DEBUG: Calculating GPT-Image native context extraction...")
                context_info = self._calculate_gpt_image_context_extraction(image)
            elif mode == "contextual":
                print("DEBUG: Calculating contextual selection-based extraction...")
                context_info = self._calculate_context_extraction(image)
            else:
                print("DEBUG: Unknown mode, defaulting to contextual extraction...")
                context_info = self._calculate_context_extraction(image)

            self._update_progress(progress_label, "🔍 Analyzing image context...")

            # Sample boundary colors for contextual mode (before inpainting)
            color_info = None
            if (
                mode == "contextual"
                and context_info
                and context_info.get("has_selection", False)
            ):
                print("DEBUG: Sampling boundary colors for color matching...")
                color_info = self._sample_boundary_colors(image, context_info)

            # Extract context region with padding (works for both modes)
            print("DEBUG: Extracting context region...")
            success, message, image_data = self._extract_context_region(
                image, context_info
            )
            if not success:
                print(f"DEBUG: Context extraction failed: {message}")
                return "Context extraction failed", message
            print(f"DEBUG: Context extraction succeeded: {message}")

            self._update_progress(progress_label, "🎭 Creating selection mask...")

            # Create smart mask that respects selection within context
            print("DEBUG: Creating context-aware mask...")
            if not context_info:
                return "Context info not available", ""

            mask_data = self._create_context_mask(
                image, context_info, context_info["target_size"]
            )

            # Bedrock reads the mask from luminance, not alpha. Convert here, on
            # the main thread, before the API call is handed to a worker thread.
            if provider == "bedrock":
                mask_data = self._convert_mask_to_luminance(mask_data)

            self._update_progress(progress_label, "🚀 Starting AI processing...")

            # Determine the optimal size for OpenAI API
            if context_info and "target_shape" in context_info:
                target_w, target_h = context_info["target_shape"]
                api_size = f"{target_w}x{target_h}"
            elif context_info and "target_size" in context_info:
                # Fallback to square for old format
                size = context_info["target_size"]
                api_size = f"{size}x{size}"
            else:
                api_size = "1024x1024"  # Default

            print(f"DEBUG: Using OpenAI API size: {api_size}")

            if provider == "bedrock":
                # Bedrock returns the context window with only the masked region
                # changed, so several seeds give genuine alternatives and the
                # whole window can be pasted back without a seam. The OpenAI
                # edits endpoint regenerates the whole window instead, so it
                # keeps the single call and the selection-shaped layer mask.
                variations = self.config.get("bedrock", {}).get("inpaint_variations", 3)
                variations = max(1, min(4, int(variations)))
                self._update_progress(
                    progress_label, f"🚀 Inpainting ({variations} variations)..."
                )
                api_success, api_message, results = self._run_bedrock_variations(
                    variations,
                    lambda seed: self._call_bedrock_edit(
                        image_data, mask_data, prompt, None, seed
                    ),
                    progress_label,
                    name="Bedrock inpaint API call",
                )
            else:
                # References follow the edited image; the mask applies to the
                # first image only. Exported here, on the main thread.
                api_image_data, api_prompt = image_data, prompt
                reference_pngs = self._export_references(self._dialog_references)
                if self._openai_mask_hint():
                    print("DEBUG: Sending selection as mask hint image")
                    hint_png = self._convert_mask_to_luminance(mask_data)
                    api_image_data = [image_data, hint_png] + reference_pngs
                    api_prompt = self._prompt_with_mask_hint(
                        prompt, len(reference_pngs)
                    )
                elif reference_pngs:
                    print(f"DEBUG: Sending {len(reference_pngs)} reference image(s)")
                    api_image_data = [image_data] + reference_pngs
                    api_prompt = self._prompt_with_reference_note(
                        prompt, len(reference_pngs)
                    )

                api_success, api_message, api_response = self._call_edit_threaded(
                    api_image_data,
                    mask_data,
                    api_prompt,
                    api_key,
                    size=api_size,
                    progress_label=progress_label,
                )
                results = [(0, api_response)] if api_success else None

            if api_success:
                print(f"DEBUG: AI API succeeded: {api_message}")
                self._update_progress(progress_label, "Processing AI response...")

                # Pasting the whole window replaces the unmasked ring around the
                # selection too, which is only harmless when that ring comes back
                # unresampled. Full-image mode scales the whole canvas down to
                # reach the API, so it keeps the selection mask.
                scale = context_info.get("padding_info", {}).get("scale_factor", 1.0)
                whole_window = provider == "bedrock" and abs(scale - 1.0) < 0.005
                clip_to_selection = self.config.get("clip_to_selection", True)
                print(
                    f"DEBUG: Composite scale {scale:.3f} -> "
                    f"{'whole window' if whole_window else 'masked to selection'}"
                )

                if whole_window:
                    placed, import_message = self._place_variation_layers(
                        image, results, context_info, "Inpaint"
                    )
                    import_success = placed > 0
                    if import_success:
                        self._update_progress(
                            progress_label,
                            "✅ AI Inpaint Complete!" if placed == 1
                            else f"✅ {placed} variations - toggle layer visibility to compare",
                        )
                elif provider == "bedrock":
                    # Resampled window: mask each variation to the selection
                    placed = 0
                    for seed, response in reversed(results):
                        ok_one, import_message = self._download_and_composite_result(
                            image, response, context_info, mode, color_info,
                            clip_to_selection=clip_to_selection,
                        )
                        placed += 1 if ok_one else 0
                    for index, layer in enumerate(image.get_layers()[:placed], start=1):
                        layer.set_name(
                            "AI Inpaint Result" if placed == 1
                            else f"AI Inpaint {index} of {placed}"
                        )
                        layer.set_visible(index == 1)
                    import_success = placed > 0
                    if import_success:
                        self._update_progress(
                            progress_label,
                            "✅ AI Inpaint Complete!" if placed == 1
                            else f"✅ {placed} variations - toggle layer visibility to compare",
                        )
                else:
                    # Masked to the selection: the OpenAI model alters pixels
                    # outside it, and only the mask keeps those out of the result
                    import_success, import_message = self._download_and_composite_result(
                        image, api_response, context_info, mode, color_info,
                        clip_to_selection=clip_to_selection,
                    )
                    if import_success:
                        self._update_progress(progress_label, "✅ AI Inpaint Complete!")

                if import_success:
                    print(f"DEBUG: AI Inpaint Complete - {import_message}")
                else:
                    print(f"DEBUG: Import failed: {import_message}")
                    return (
                        "AI generated but import failed",
                        f"API: {api_message}\nImport error: {import_message}",
                    )
            else:
                print(f"DEBUG: AI API failed: {api_message}")
                # Check if this was a cancellation vs actual API failure
                if "cancelled" not in api_message.lower():
                    return "AI API failed", api_message
                self._update_progress(
                    progress_label, "❌ Operation cancelled by user"
                )
                Gimp.message("❌ Operation cancelled by user")

            return None

        try:
            # On failure the dialog stays open with the error shown, so the
            # user can adjust the prompt, selection or settings and try again
            while True:
                try:
                    failure = attempt()
                except Exception as e:
                    print(f"ERROR: Inpaint failed: {e}")
                    failure = ("Inpaint failed", str(e))
                if not failure:
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.SUCCESS, GLib.Error()
                    )

                # API calls can clear the layer selection
                if original_selected_layers:
                    image.set_selected_layers(original_selected_layers)
                dialog_result = self._offer_retry(
                    progress_label, *failure,
                    "Adjust the prompt or selection and press OK to try again.",
                )
                if not dialog_result:
                    # Closed or cancelled; the dialog has already destroyed itself
                    dialog = None
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.CANCEL, GLib.Error()
                    )
                dialog, progress_label, prompt, selected_mode = dialog_result

        finally:
            # Always destroy the dialog
            if dialog:
                dialog.destroy()
            # Always restore original layer selection after any operation outcome
            if original_selected_layers:
                image.set_selected_layers(original_selected_layers)
                print("DEBUG: Restored layer selection after inpaint operation")

    def run_layer_composite(
        self, procedure, run_mode, image, drawables, config, run_data
    ):
        """Layer Composite - combine multiple layers using OpenAI API"""
        print("DEBUG: Layer Composite called!")

        # Save the currently selected layers before showing dialog (which queries layers and might clear selection)
        original_selected_layers = image.get_selected_layers()
        print(f"DEBUG: Saved {len(original_selected_layers)} originally selected layers")

        # Step 1: Show prompt dialog with layer selection
        print("DEBUG: Showing layer composite dialog...")
        provider = self._get_provider()
        print(f"DEBUG: Layer composite provider: {provider}")
        dialog_result = self._show_composite_dialog(image, provider)
        print(f"DEBUG: Dialog returned: {repr(dialog_result)}")

        if not dialog_result:
            print("DEBUG: User cancelled prompt dialog")
            # Restore layer selection before returning
            if original_selected_layers:
                image.set_selected_layers(original_selected_layers)
                print("DEBUG: Restored layer selection after dialog cancel")
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

        # Handle composite dialog result: (dialog, progress_label, prompt, layers, use_mask)
        dialog, progress_label, prompt, selected_layers, use_mask = dialog_result
        print(
            f"DEBUG: Layer composite mode: {len(selected_layers)} layers, mask: {use_mask}"
        )

        def attempt():
            """One try at the composite; returns (headline, details) on failure"""
            # Step 2: Get credentials for the configured provider; read on
            # every attempt since Settings may have changed them
            provider = self._get_provider()
            api_key = None
            if provider == "bedrock":
                self._log_bedrock_environment()
                try:
                    bedrock_client.resolve_credentials(self.config.get("bedrock", {}))
                except bedrock_client.BedrockError as e:
                    return "Amazon Bedrock is not configured", str(e)

                # Bedrock inpainting always needs a mask to know what to change
                if not use_mask:
                    return (
                        "Selection required for Bedrock",
                        "Amazon Bedrock composites into a masked region, so it "
                        "needs a selection to work on. Make a selection and tick "
                        "'Include selection mask', or switch the provider back "
                        "to OpenAI in Settings.",
                    )
            else:
                api_key = self._get_api_key()
                if not api_key:
                    return (
                        "No OpenAI API key found",
                        "Set your API key in Settings, config.json or the "
                        "OPENAI_API_KEY environment variable.",
                    )

            # Use existing layer preparation method
            self._update_progress(progress_label, "🔧 Preparing layers...")
            print("DEBUG: Preparing layers for composite...")

            # Reverse layer order so base layer (last in dialog list) is first for API
            layers_for_api = list(reversed(selected_layers))

            # Use the existing preparation method
            success, message, layer_data_list, optimal_shape = (
                self._prepare_layers_for_composite(layers_for_api)
            )
            if not success:
                print(f"DEBUG: Layer preparation failed: {message}")
                return "Layer preparation failed", message

            print(f"DEBUG: Layer preparation succeeded: {message}")

            # Always create context_info for result processing (padding removal and scaling)
            img_width = image.get_width()
            img_height = image.get_height()
            target_width, target_height = optimal_shape

            # Import the padding calculation function
            from coordinate_utils import calculate_padding_for_shape

            # Create context_info for result processing
            context_info = {
                "mode": "full",
                "selection_bounds": (
                    0,
                    0,
                    img_width,
                    img_height,
                ),  # Default to full image
                "extract_region": (0, 0, img_width, img_height),  # Full image
                "target_shape": (target_width, target_height),
                "target_size": max(target_width, target_height),
                "needs_padding": True,
                "padding_info": calculate_padding_for_shape(
                    img_width, img_height, target_width, target_height
                ),
                "has_selection": False,  # Will be updated if mask is used
            }

            self._update_progress(progress_label, "Creating mask...")

            # Prepare mask if requested
            mask_data = None
            if use_mask:
                print("DEBUG: Preparing mask for primary layer...")
                # Use the same context-based mask approach as inpainting
                selection_bounds = Gimp.Selection.bounds(image)
                if len(selection_bounds) >= 5 and selection_bounds[0]:
                    print("DEBUG: Creating context-aware mask for layer composite...")

                    # Get selection bounds
                    sel_x1 = selection_bounds[2] if len(selection_bounds) > 2 else 0
                    sel_y1 = selection_bounds[3] if len(selection_bounds) > 3 else 0
                    sel_x2 = (
                        selection_bounds[4] if len(selection_bounds) > 4 else img_width
                    )
                    sel_y2 = (
                        selection_bounds[5] if len(selection_bounds) > 5 else img_height
                    )

                    # Update context_info with actual selection bounds
                    context_info["selection_bounds"] = (sel_x1, sel_y1, sel_x2, sel_y2)
                    context_info["has_selection"] = True

                    # Create mask using the same function as inpainting
                    mask_data = self._create_context_mask(
                        image, context_info, context_info["target_size"]
                    )
                    if provider == "bedrock":
                        mask_data = self._convert_mask_to_luminance(mask_data)
                    print(
                        f"DEBUG: Created context-aware selection mask for composite {target_width}x{target_height}"
                    )
                else:
                    # ERROR: User checked the mask box but there's no selection
                    print("DEBUG: ERROR - Use mask checked but no selection found")
                    return (
                        "Selection required for mask",
                        "'Include selection mask' is ticked but there is no "
                        "selection. Make a selection on your image, or untick "
                        "'Include selection mask'.",
                    )

            self._update_progress(progress_label, "🚀 Starting AI processing...")

            target_width, target_height = optimal_shape
            api_size = f"{target_width}x{target_height}"

            if provider == "bedrock":
                # Bedrock takes a single image, so composite the layers first
                self._update_progress(progress_label, "🔧 Flattening layers...")
                flatten_ok, flatten_message, api_image_data = (
                    self._flatten_layers_for_bedrock(
                        image, layers_for_api, context_info
                    )
                )
                if not flatten_ok:
                    return "Layer flattening failed", flatten_message
                print(f"DEBUG: Calling Bedrock with flattened layers, size={api_size}")
            else:
                api_image_data = layer_data_list
                print(
                    f"DEBUG: Calling OpenAI API with {len(layer_data_list)} layers, size={api_size}..."
                )

            api_success, api_message, api_response = self._call_edit_threaded(
                api_image_data,
                mask_data,
                prompt,
                api_key,
                size=api_size,
                progress_label=progress_label,
            )

            if api_success:
                print(f"DEBUG: AI API succeeded: {api_message}")
                self._update_progress(progress_label, "Processing AI response...")

                # Create result layer in GIMP
                if (
                    api_response
                    and "data" in api_response
                    and len(api_response["data"]) > 0
                ):
                    result_data = api_response["data"][0]

                    # Handle both URL and base64 response formats
                    if "b64_json" in result_data:
                        # Base64 format (gpt-image-1)
                        print("DEBUG: Processing base64 composite result...")

                        # Use the same result processing as inpainting to handle padding removal and scaling
                        print(
                            "DEBUG: Using inpainting result processing to handle padding and scaling..."
                        )
                        success, message = self._download_and_composite_result(
                            image, api_response, context_info, "full"
                        )

                        if success:
                            # Rename the layer to indicate it's a composite
                            new_layer = image.get_layers()[0]
                            new_layer.set_name("Layer Composite")

                            self._update_progress(
                                progress_label,
                                "✅ Layer Composite completed successfully!",
                            )
                            Gimp.message("✅ Layer Composite completed successfully!")
                            print("DEBUG: Layer composite creation successful")
                        else:
                            raise Exception(
                                f"Failed to process composite result: {message}"
                            )

                    elif "url" in result_data:
                        # URL format (fallback)
                        print("DEBUG: Downloading composite result from URL...")

                        import urllib.request

                        with urllib.request.urlopen(result_data["url"]) as response:
                            image_data = response.read()

                        # Create new layer with result
                        temp_image = self._create_image_from_data(image_data)
                        if temp_image:
                            new_layer = temp_image.get_layers()[0].copy()
                            new_layer.set_name("Layer Composite")
                            image.insert_layer(new_layer, None, 0)
                            temp_image.delete()

                            Gimp.progress_update(1.0)  # 100% - Complete
                            Gimp.message("✅ Layer Composite completed successfully!")
                        else:
                            raise Exception("Failed to create image from result data")
                    else:
                        raise Exception(
                            "No image data (b64_json or url) in API response"
                        )
                else:
                    return "No data in API response", ""
            else:
                print(f"DEBUG: AI API failed: {api_message}")
                # Check if this was a cancellation vs actual API failure
                if "cancelled" not in api_message.lower():
                    return "AI API failed", api_message
                self._update_progress(
                    progress_label, "❌ Operation cancelled by user"
                )
                Gimp.message("❌ Operation cancelled by user")

            return None

        try:
            # On failure the dialog stays open with the error shown, so the
            # user can adjust the prompt, mask or settings and try again
            while True:
                try:
                    failure = attempt()
                except Exception as e:
                    print(f"ERROR: Layer composite failed: {e}")
                    failure = ("Layer composite failed", str(e))
                if not failure:
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.SUCCESS, GLib.Error()
                    )

                # API calls can clear the layer selection
                if original_selected_layers:
                    image.set_selected_layers(original_selected_layers)
                dialog_result = self._offer_retry(progress_label, *failure)
                if not dialog_result:
                    # Closed or cancelled; the dialog has already destroyed itself
                    dialog = None
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.CANCEL, GLib.Error()
                    )
                dialog, progress_label, prompt, selected_layers, use_mask = (
                    dialog_result
                )

        finally:
            # Always destroy the dialog
            if dialog:
                dialog.destroy()
            # Always restore original layer selection after any operation outcome
            if original_selected_layers:
                image.set_selected_layers(original_selected_layers)
                print("DEBUG: Restored layer selection after layer composite operation")

    def _create_image_from_data(self, image_data):
        """Helper function to create GIMP image from binary data"""
        try:
            import tempfile
            import os

            print(f"DEBUG: Writing {len(image_data)} bytes to temp file...")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False, mode='wb') as temp_file:
                temp_file.write(image_data)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_path = temp_file.name
            print(f"DEBUG: Temp file created: {temp_path}")

            # Load image using GIMP
            print("DEBUG: Creating Gio.File...")
            file = Gio.File.new_for_path(temp_path)
            print("DEBUG: Calling Gimp.file_load()...")
            temp_image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, file)
            print("DEBUG: Gimp.file_load() completed")

            print("DEBUG: Cleaning up temp file...")
            os.unlink(temp_path)
            print("DEBUG: Image creation successful")
            return temp_image

        except Exception as e:
            print(f"DEBUG: Failed to create image from data: {e}")
            return None

    def _generate_gpt_image_layer_threaded(
        self, image, prompt, api_key, size="auto", progress_label=None
    ):
        """Threaded wrapper for GPT image generation to keep UI responsive"""
        import threading
        import time

        print("DEBUG: Starting threaded GPT-Image-1 generation...")

        # Shared storage for results
        result = {"success": False, "completed": False}

        def generation_thread():
            try:
                # Call the blocking API directly with progress updates
                import json
                import urllib.request
                import ssl

                # Determine optimal size based on image dimensions or user preference
                if size == "auto":
                    img_width = image.get_width()
                    img_height = image.get_height()
                    aspect_ratio = img_width / img_height

                    if aspect_ratio > 1.2:  # Landscape
                        optimal_size = "1536x1024"
                    elif aspect_ratio < 0.83:  # Portrait
                        optimal_size = "1024x1536"
                    else:  # Square or close to square
                        optimal_size = "1024x1024"
                else:
                    optimal_size = size

                print(f"DEBUG: [THREAD] Using size {optimal_size} for generation")

                # Prepare the request data
                model = self._get_openai_model()
                data = {
                    "model": model,
                    "prompt": prompt,
                    "n": 1,
                    "size": optimal_size,
                    "quality": "high",
                }

                # Create the request
                json_data = json.dumps(data).encode("utf-8")
                url = "https://api.openai.com/v1/images/generations"
                req = urllib.request.Request(url, data=json_data)
                req.add_header("Content-Type", "application/json")
                req.add_header("Authorization", f"Bearer {api_key}")

                print(f"DEBUG: [THREAD] Sending {model} generation request...")
                if progress_label:
                    update_progress = self._create_progress_callback(progress_label)
                    update_progress(f"🚀 Sending request to {model}...")

                # Send request
                with urllib.request.urlopen(req, timeout=OPENAI_TIMEOUT) as response:
                    response_data = response.read().decode("utf-8")

                # Parse response
                response_json = json.loads(response_data)
                print("DEBUG: [THREAD] GPT-Image-1 API response received")

                if progress_label:
                    update_progress("✅ Processing AI response...")

                # Process the response
                if "data" in response_json and len(response_json["data"]) > 0:
                    result_data = response_json["data"][0]

                    if "b64_json" in result_data:
                        print(
                            "DEBUG: [THREAD] Processing base64 image data from GPT-Image-1"
                        )
                        import base64

                        image_data = base64.b64decode(result_data["b64_json"])
                        print(
                            f"DEBUG: [THREAD] Decoded {len(image_data)} bytes of image data"
                        )

                        # Create layer on main thread via GLib.idle_add
                        layer_created = {"success": False}

                        def create_layer():
                            try:
                                success = self._add_layer_from_data(image, image_data)
                                layer_created["success"] = success
                                return False
                            except Exception as e:
                                print(f"ERROR: [MAIN] Failed to create layer: {e}")
                                layer_created["success"] = False
                                return False

                        GLib.idle_add(create_layer)

                        # Wait for layer creation to complete
                        import time

                        while "success" not in layer_created:
                            time.sleep(0.01)

                        result["success"] = layer_created["success"]
                    else:
                        print("ERROR: [THREAD] No b64_json in GPT-Image-1 response")
                        result["success"] = False
                else:
                    print("ERROR: [THREAD] No data in GPT-Image-1 response")
                    result["success"] = False

            except Exception as e:
                print(f"ERROR: [THREAD] Image generation failed: {e}")
                result["success"] = False
            finally:
                result["completed"] = True

        # Start thread
        thread = threading.Thread(target=generation_thread)
        thread.daemon = True
        thread.start()

        # Keep UI responsive while waiting
        max_wait_time = 400  # 6.7 minutes maximum wait (longer for image generation)
        start_time = time.time()
        last_update_time = start_time

        while not result["completed"]:
            current_time = time.time()
            elapsed = current_time - start_time

            # Update progress every second
            if progress_label and current_time - last_update_time >= 1:
                minutes, seconds = divmod(int(elapsed), 60)
                if minutes > 0:
                    self._update_progress(
                        progress_label,
                        f"🎨 Still generating... ({minutes}m {seconds:02d}s elapsed)",
                    )
                else:
                    self._update_progress(
                        progress_label, f"🎨 Generating image... ({seconds}s elapsed)"
                    )
                last_update_time = current_time

            # Check for cancellation
            if self._check_cancel_and_process_events():
                print("DEBUG: Image generation cancelled by user")
                if progress_label:
                    self._update_progress(
                        progress_label, "❌ Generation cancelled by user"
                    )
                result["success"] = False
                break

            # Check for timeout
            if elapsed > max_wait_time:
                print(
                    f"DEBUG: Image generation thread timeout after {max_wait_time} seconds"
                )
                if progress_label:
                    self._update_progress(progress_label, "❌ Generation timed out")
                result["success"] = False
                break

            # Small sleep to prevent CPU spinning
            time.sleep(0.1)

        # Thread completed, return results
        print(
            f"DEBUG: Threaded image generation completed: success={result['success']}"
        )
        return result["success"]

    def _fit_generated_layer(self, image, layer):
        """
        Scale a generated layer to cover the canvas, centred.

        Generation models return their own resolution - Stability gives roughly a
        megapixel at the nearest aspect ratio it supports, OpenAI one of three
        fixed sizes - which rarely matches the canvas. Scaling to *cover* means
        the tighter axis matches exactly and the other overhangs, so there is no
        gap and the excess can be cropped. Scaling to fit instead would leave
        empty canvas.

        GIMP 3 has no Lanczos filter; it was replaced by NoHalo and LoHalo.
        NoHalo is the one intended for enlarging, LoHalo for reducing.
        """
        canvas_width, canvas_height = image.get_width(), image.get_height()
        layer_width, layer_height = layer.get_width(), layer.get_height()
        if not layer_width or not layer_height:
            return

        scale = max(canvas_width / layer_width, canvas_height / layer_height)
        if abs(scale - 1.0) < 0.005:
            layer.set_offsets(
                (canvas_width - layer_width) // 2, (canvas_height - layer_height) // 2
            )
            return

        new_width = max(1, round(layer_width * scale))
        new_height = max(1, round(layer_height * scale))

        Gimp.context_push()
        try:
            Gimp.context_set_interpolation(
                Gimp.InterpolationType.NOHALO if scale > 1.0
                else Gimp.InterpolationType.LOHALO
            )
            layer.scale(new_width, new_height, False)
        finally:
            Gimp.context_pop()

        layer.set_offsets(
            (canvas_width - new_width) // 2, (canvas_height - new_height) // 2
        )
        print(
            f"DEBUG: Fitted generated layer {layer_width}x{layer_height} -> "
            f"{new_width}x{new_height} (scale {scale:.3f}) to cover "
            f"{canvas_width}x{canvas_height} canvas"
        )

    def _add_layer_from_bytes(self, image, data, suffix, name, offsets=(0, 0)):
        """
        Add encoded image bytes as a new layer, without fitting to the canvas.

        Separate from _add_layer_from_data because upscale results are meant to
        exceed the canvas, and because they arrive as WebP or JPEG rather than
        PNG - the suffix matters, since GIMP picks its loader from the filename.
        """
        temp_path = None
        loaded = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = handle.name

            loaded = Gimp.file_load(
                Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(temp_path)
            )
            if not loaded or not loaded.get_layers():
                return None

            new_layer = Gimp.Layer.new_from_drawable(loaded.get_layers()[0], image)
            new_layer.set_name(name)
            image.insert_layer(new_layer, None, 0)
            new_layer.set_offsets(int(offsets[0]), int(offsets[1]))
            print(
                f"DEBUG: Added {name}: {new_layer.get_width()}x{new_layer.get_height()} "
                f"at {offsets}"
            )
            return new_layer
        except Exception as e:
            print(f"DEBUG: Failed to add layer from bytes: {e}")
            return None
        finally:
            if loaded:
                loaded.delete()
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def _add_layer_from_data(self, image, image_data):
        """Add image from raw data as a new layer"""
        try:
            import tempfile
            import os

            # Create temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png", mode='wb') as temp_file:
                temp_file.write(image_data)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_file_path = temp_file.name

            print(f"DEBUG: Saved image data to: {temp_file_path}")

            try:
                # Load the image as a new layer
                loaded_image = Gimp.file_load(
                    Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(temp_file_path)
                )
                source_layer = loaded_image.get_layers()[0]

                # Copy the layer to the current image
                new_layer = Gimp.Layer.new_from_drawable(source_layer, image)
                new_layer.set_name("GPT-Image Generated")

                # Add the layer to the image
                image.insert_layer(new_layer, None, 0)

                # Generated sizes rarely match the canvas, so scale to cover it
                self._fit_generated_layer(image, new_layer)

                # Clean up
                loaded_image.delete()

                print("DEBUG: Successfully added generated layer")
                return True

            finally:
                # Clean up temporary file
                try:
                    os.unlink(temp_file_path)
                except:
                    pass

        except Exception as e:
            print(f"ERROR: Failed to add layer from data: {str(e)}")
            return False

    def _open_image_from_data(self, image_data, run_mode):
        """
        Open generated image data as a new, untitled image in its own window.

        The layer is copied into a fresh image rather than opening the loaded
        file directly, which would tie the image to the deleted temp file and
        make Save/Export default to the temp folder.
        """
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png", mode="wb") as temp_file:
                temp_file.write(image_data)
                temp_file_path = temp_file.name

            try:
                loaded_image = Gimp.file_load(
                    Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(temp_file_path)
                )
            finally:
                try:
                    os.unlink(temp_file_path)
                except OSError:
                    pass

            if not loaded_image or not loaded_image.get_layers():
                print("ERROR: Generated image could not be loaded")
                return None

            source_layer = loaded_image.get_layers()[0]
            width, height = source_layer.get_width(), source_layer.get_height()

            new_image = Gimp.Image.new(width, height, Gimp.ImageBaseType.RGB)
            # Creating the image is not an edit the user should be able to undo
            new_image.undo_disable()
            new_layer = Gimp.Layer.new_from_drawable(source_layer, new_image)
            new_layer.set_name("AI Generated")
            new_image.insert_layer(new_layer, None, 0)
            new_layer.set_offsets(0, 0)
            new_image.undo_enable()
            loaded_image.delete()

            if run_mode == Gimp.RunMode.INTERACTIVE:
                Gimp.Display.new(new_image)
                Gimp.displays_flush()

            print(f"DEBUG: Opened generated image as new {width}x{height} image")
            return new_image

        except Exception as e:
            print(f"ERROR: Failed to open generated image: {str(e)}")
            return None

    def _download_and_add_layer(self, image, image_url):
        """Download image from URL and add as new layer"""
        try:
            import urllib.request
            import tempfile
            import os
            import ssl

            # Download the image to a temporary file
            with self._make_url_request(image_url) as response:
                image_data = response.read()

            # Create temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png", mode='wb') as temp_file:
                temp_file.write(image_data)
                temp_file.flush()
                os.fsync(temp_file.fileno())
                temp_file_path = temp_file.name

            print(f"DEBUG: Downloaded image to: {temp_file_path}")

            try:
                # Load the image as a new layer
                loaded_image = Gimp.file_load(
                    Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(temp_file_path)
                )
                source_layer = loaded_image.get_layers()[0]

                # Copy the layer to the current image
                new_layer = Gimp.Layer.new_from_drawable(source_layer, image)
                new_layer.set_name("GPT-Image Generated")

                # Add the layer to the image
                image.insert_layer(new_layer, None, 0)

                # Generated sizes rarely match the canvas, so scale to cover it
                self._fit_generated_layer(image, new_layer)

                # Clean up
                loaded_image.delete()

                print("DEBUG: Successfully added generated layer")
                return True

            finally:
                # Clean up temporary file
                try:
                    os.unlink(temp_file_path)
                except:
                    pass

        except Exception as e:
            print(f"ERROR: Failed to download and add layer: {str(e)}")
            return False

    def run_image_generator(
        self, procedure, run_mode, image, drawables, config, run_data
    ):
        # Always opens the result as a new image, even when one is open
        return self.run_layer_generator(
            procedure, run_mode, None, drawables, config, run_data
        )

    def run_layer_generator(
        self, procedure, run_mode, image, drawables, config, run_data
    ):
        print("DEBUG: Image Generator called!")

        # Without an image there is no canvas to fill, so ask for a size
        # and open the result as a new image instead of adding a layer
        new_image_mode = image is None
        size_presets, allow_custom_size = (
            self._generation_size_presets() if new_image_mode else (None, False)
        )

        # Show prompt dialog with API key checking (no mode selection for image generator)
        dialog_result = self._show_prompt_dialog(
            "Generate New Image" if new_image_mode else "Generate Layer",
            "", show_mode_selection=False,
            image=image,
            provider=self._get_provider(),
            size_presets=size_presets,
            allow_custom_size=allow_custom_size,
            # Stability's text-to-image takes no input images
            show_references=self._get_provider() == "openai",
        )
        if not dialog_result:
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

        # Extract dialog, progress_label, prompt and mode from dialog result
        dialog, progress_label, prompt, _ = (
            dialog_result  # Ignore mode for layer generator
        )

        try:
            done_text = "image" if new_image_mode else "layer"

            # On failure the dialog stays open with the error shown, so the
            # user can adjust the prompt (or the settings) and try again
            while True:
                # Update dialog immediately when processing starts
                self._update_progress(progress_label, "🎨 Generating image with AI...")

                try:
                    # Credentials for whichever provider is configured; read
                    # on every attempt since Settings may have changed them
                    provider = self._get_provider()
                    api_key = None
                    if provider == "bedrock":
                        self._log_bedrock_environment()
                        bedrock_client.generation_credentials(
                            self.config.get("bedrock", {})
                        )
                    else:
                        api_key = self._get_api_key()
                        if not api_key:
                            raise RuntimeError("API key not available")

                    # Use threaded generation to keep UI responsive like other functions
                    self._update_progress(
                        progress_label, "🚀 Starting image generation..."
                    )

                    if new_image_mode:
                        width, height = self._dialog_size
                    else:
                        width, height = image.get_width(), image.get_height()

                    reference_pngs = []
                    if provider == "openai":
                        reference_pngs = self._export_references(
                            self._dialog_references
                        )
                        if reference_pngs:
                            print(
                                f"DEBUG: Generating with {len(reference_pngs)} reference image(s)"
                            )

                    success, message, image_data = self._call_generation_threaded(
                        prompt,
                        api_key,
                        width,
                        height,
                        progress_label=progress_label,
                        exact_size=new_image_mode,
                        references=reference_pngs,
                    )
                    if success and image_data:
                        if new_image_mode:
                            result = (
                                self._open_image_from_data(image_data, run_mode)
                                is not None
                            )
                        else:
                            # Create layer from the generated image data
                            result = self._add_layer_from_data(image, image_data)
                        if not result:
                            message = f"Failed to add the generated {done_text}"
                    else:
                        result = False
                except bedrock_client.BedrockError as e:
                    result, message = False, f"Amazon Bedrock is not configured: {e}"
                except Exception as e:
                    print(f"ERROR: Error generating AI image: {e}")
                    result, message = False, str(e)

                if result:
                    self._update_progress(
                        progress_label, f"✅ AI {done_text} generated successfully!"
                    )
                    Gimp.message(f"✅ AI {done_text} generated successfully!")
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.SUCCESS, GLib.Error()
                    )

                # Check if this was a cancellation vs actual failure
                if "cancelled" in message.lower():
                    self._update_progress(progress_label, "❌ Operation cancelled")
                    Gimp.message("❌ Operation cancelled by user")
                    # For cancellation, return SUCCESS status (user action, not an error)
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.SUCCESS, GLib.Error()
                    )

                dialog_result = self._offer_retry(
                    progress_label, "Generation failed", message,
                    "Adjust the prompt and press OK to try again.",
                )
                if not dialog_result:
                    # Closed or cancelled; the dialog has already destroyed itself
                    dialog = None
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.CANCEL, GLib.Error()
                    )
                dialog, progress_label, prompt, _ = dialog_result
        except Exception as e:
            error_msg = f"Error generating AI image: {str(e)}"
            self._update_progress(progress_label, f"❌ Error: {str(e)}")
            print(f"ERROR: {error_msg}")
            Gimp.message(f"❌ {error_msg}")
            return procedure.new_return_values(
                Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error()
            )
        finally:
            # Always destroy the dialog
            if dialog:
                dialog.destroy()

    def run_settings(self, procedure, run_mode, image, drawables, config, run_data):
        """Open the settings dialog straight from the Filters > AI menu"""
        print("DEBUG: Settings called from menu")
        if run_mode != Gimp.RunMode.INTERACTIVE:
            return procedure.new_return_values(
                Gimp.PDBStatusType.CALLING_ERROR, GLib.Error()
            )

        self._init_gimp_ui()
        self._show_settings_dialog(None)
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


# Entry point
if __name__ == "__main__":
    Gimp.main(GimpAIPlugin.__gtype__, sys.argv)
