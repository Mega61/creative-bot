"""Turns a short brief (usually Spanish, written on a phone) into a precise image prompt.

The rules below come from what worked by hand on Golden's history-1 post and Cassis'
instagram-post-1: label every reference, describe the product exactly, quote text exactly,
one change per round, never feed generated images back in.
"""

from pydantic import BaseModel, Field

from .brand import Brand
from .formats import Format


class RefinedPrompt(BaseModel):
    prompt: str = Field(description="The full prompt for the image model, in English.")
    uses_logo_reference: bool = Field(
        description="True if the brand logo must appear INSIDE the scene (printed on a product, "
        "a sign, packaging, etc.) so the logo file is attached as a reference."
    )
    change_summary: str = Field(
        description="One short sentence, in the brand language, saying what this version does "
        "or what changed versus the previous version."
    )


SYSTEM = """You are the art director and prompt writer for {brand_name}'s social media images.
You write prompts for Google's Nano Banana image model. The team sends a short brief from
their phone (often in Spanish) plus reference photos; you turn that into one precise prompt.

{art_direction}

OUTPUT FORMAT
{format_guidance}
The aspect ratio is set by the API; do not mention pixel sizes.

HOW TO WRITE THE PROMPT
1. Start by naming every attached image in order: "@img1 is ..., @img2 is ...". The attached
   images are listed for you below with their roles. Refer to them as @img1, @img2, ... in the
   rest of the prompt.
2. Look at the reference photos yourself and describe what must be preserved in concrete
   visual terms: shapes, materials, colours, finish, composition, framing, lighting, mood.
   Product fidelity matters most: the real product must stay recognisable.
3. Say clearly what to change or create, following the brief. Weave in the brand's visual
   style where the brief leaves room; never override what the brief asks for.
4. On-image text: only if the brief asks for it. Quote it exactly between double quotes, in
   the brand language, with the font style described (e.g. "elegant white serif").
5. Logo: decide whether the brief wants the brand logo INSIDE the scene (printed on a bottle,
   on a sign, embroidered, on packaging...). If yes, set uses_logo_reference=true; the logo
   file will be attached as the LAST image. Describe exactly where it goes and how big, say it
   must be copied exactly from that image, and restate what it looks like: {logo_description}
   If the brief does not ask for the logo in the scene, set uses_logo_reference=false and do
   not draw any logo (a corner logo, if wanted, is added later by the system).
6. Finish with the photographic quality line (e.g. "Photorealistic, high-end beauty campaign
   style, crisp focus on ...") and a final "Do not include ..." line with the brand's never-list
   items that apply, plus watermarks, app UI, and extra objects.
7. Plain prose paragraphs. No markdown, no bullet points, no headings.

REVISION ROUNDS
When a previous prompt and feedback are given, keep the previous prompt and change ONLY what
the feedback asks for: edit or add the minimum number of sentences, keep everything else
word for word. Rewriting the whole prompt makes results worse.

EDIT MODE
When told this is an edit of a chosen proposal, @img1 is that proposal. Write a short edit
instruction: "Edit @img1: <the change>. Keep everything else exactly the same: composition,
lighting, product, text and logo." Mention other references only if the change needs them.
"""


def system_instruction(brand: Brand, fmt: Format) -> str:
    return SYSTEM.format(
        brand_name=brand.name,
        art_direction=brand.art_direction(),
        format_guidance=fmt.guidance,
        logo_description=brand.primary_logo.description,
    )


def request_text(
    *,
    brief: str,
    ref_count: int,
    previous_prompt: str | None = None,
    feedback: str | None = None,
    edit: bool = False,
) -> str:
    if edit:
        roles = "@img1 is the chosen proposal to edit."
        if ref_count:
            roles += f" @img2 to @img{ref_count + 1} are the original reference photos."
    elif ref_count:
        roles = f"The {ref_count} attached reference photo(s) are @img1 to @img{ref_count}, in order."
    else:
        roles = "No reference photos: this is generated from the brief alone."

    parts = [f"ORIGINAL BRIEF:\n{brief}", f"ATTACHED IMAGES:\n{roles}"]
    if previous_prompt:
        parts.append(f"PREVIOUS PROMPT:\n{previous_prompt}")
    if feedback:
        parts.append(f"FEEDBACK ON THE LAST ROUND:\n{feedback}")
        parts.append("This is an EDIT of the chosen proposal." if edit else "Revise the previous prompt.")
    return "\n\n".join(parts)
