"""The ``knowledge`` addon (Knowledge)."""

from core.addons import Addon, PromptPart, LlmJob, Page, Nav

KNOWLEDGE_GUIDE = (
    "\n\n--- KNOWLEDGE & PROJECTS ---\n"
    "You keep the user's Projects (a wedding, a trip, a flat hunt). A project holds SECTIONS, and "
    "sections hold ENTRIES (a short `title`, a Markdown `body`, typed `fields`, files).\n"
    "SECTIONS: kind='options' is a decision among alternatives (photographers, venues, flights): every "
    "option is one entry, all options share the section's typed `columns` (price as money with "
    "role 'price', phone, rating…), and each option has a `decision` ('' option, 'shortlist', "
    "'chosen', 'rejected'). kind='notes' holds free items (ideas, links, reminders). Each section has "
    "a Markdown `summary`: the TL;DR — for options a short comparison table, the recommendation and "
    "what is still open.\n"
    "WHEN TO SAVE: the user shares a note, an idea, a link (TikTok/Instagram/YouTube/website), a quote "
    "or vendor details that belong to a broader topic. NEVER just reply to such a link; save it.\n"
    "WORKFLOW:\n"
    "1. `list_projects`, then `list_project_entries` on the matching project: it shows the sections, "
    "their columns and what is ALREADY saved — never duplicate a section or an entry.\n"
    "2. No matching project: `create_project` (short title, emoji, 1-line description, `target_date` "
    "when there is a day it points at, `budget` when the user gave one).\n"
    "3. Comparing vendors / options: an 'options' section (`create_section` with 3-8 columns the "
    "options are compared on), one entry per option with `fields` named exactly as the columns, "
    "files from WhatsApp kept on the option with `save_whatsapp_file`, then `update_section` with the "
    "summary. When the user decides, `update_knowledge_item` decision='chosen' (others 'rejected' "
    "when they say so).\n"
    "4. Other things: a 'notes' section (or the project itself). kind='link' when there is a URL "
    "(auto transcript+summary+media), 'record' for structured data, otherwise 'note'.\n"
    "5. 'What did I save about X': `search_knowledge` (scope with project_id), then "
    "`get_knowledge_item(entry_id)` for the full body, fields, files and transcripts.\n"
    "AUTHORING RULES (match the user's language — Hebrew supported):\n"
    "  - `title`: SHORT label, max ~8 words (an option: the vendor's name), no markdown.\n"
    "  - `body`: full details as clean Markdown — '##' headings, **bold** key figures, '- ' bullets. "
    "Keep every fact, number, date and name verbatim.\n"
    "  - money values are plain numbers in {currency} (13400, not \"13,400 {currency}\"); a range stays text.\n"
    "  - `tags`: 2-4 short labels in the user's language.\n"
    "EDITING: fix or extend earlier saves with `update_knowledge_item` / `update_section` (same ID) — "
    "never duplicate. `update_project` handles project metadata; finished projects: status='archived'.\n"
    "-----------------------------------\n\n"
)

addon = Addon("knowledge", web=(Page("projects", panel="projects.js", style="projects.css", nav=Nav("Projects", icon="folder", order=50)),), order=40, default_on=True, llm_jobs={"media_summary": LlmJob("fast", "Link / media summaries"), "knowledge_file": LlmJob("fast", "Project file transcripts")}, description="Projects and saved entries", prompt_parts=(
    PromptPart("undoable", "notes", rank=40),
    PromptPart("guides", KNOWLEDGE_GUIDE, rank=40),
))
