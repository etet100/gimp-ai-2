---
title: Layer Composite
parent: Features
nav_order: 5
---

# Layer Composite

**Intelligently combine multiple visible layers using AI guidance.**
Works with OpenAI and Amazon Bedrock.

<!-- Screenshot: docs/assets/screenshots/layer-composite.png -->

## How to use it

1. **Set up your layers**:
   - **Bottom layer** = base/background (what gets modified)
   - **Upper layers** = elements to integrate (people, objects, etc.)
   - Make sure desired layers are **visible**
2. **Go to** `Filters → AI → Layer Composite`
3. **Enter integration prompt** (e.g., "blend the person naturally into the forest scene")
4. **Choose mode**:
   - **Include selection mask**: Uses selection on base layer to limit where changes occur
   - **No mask**: AI can modify the entire base layer to integrate upper layers
5. **Result**: A new layer is created, taking the base layer and intelligently
   modifying it to incorporate all visible layers

## OpenAI vs Bedrock

Layer composite behaves differently on the two providers. OpenAI receives each
selected layer as a separate image and can use one as a reference for another.
Bedrock's inpainting model takes a single image, so the selected layers are
flattened by GIMP first and the model only ever sees the combined pixels; a
selection mask is required to tell it which region to work on.
