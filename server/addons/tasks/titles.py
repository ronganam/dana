"""Task titles and lists picked for a new task: the leading emoji and the
list, from rules or one small model call each (``core/llm.py``, purposes
``task_emoji`` / ``task_group``). Shared by the tools, the HTTP handlers and
the call / notification suggestions."""

import logging
import re
from typing import Optional

from core import llm
from core.users import User
from core.addons import sharing
from addons.tasks import groups

logger = logging.getLogger(__name__)


def starts_with_emoji(s: str) -> bool:
    """Check if the string s starts with an emoji or common symbol."""
    if not s:
        return False
    first_char = s[0]
    ord_val = ord(first_char)
    # Dingbats & Misc Symbols: 0x2600 - 0x2BDF
    # Misc Technical & arrows: 0x2300 - 0x2BFF
    # Emoticons & Transport/Map/Pictographs: 0x1F000 - 0x1FAFF
    return (0x2300 <= ord_val <= 0x2BDF) or (0x1F000 <= ord_val <= 0x1FAFF)

def get_fitting_emoji(title: str) -> str:
    """Scan title for keywords and return a matching emoji."""
    title_lower = title.lower()
    
    rules = [
        # Specific health terms
        (r"\b(dentist|teeth|tooth|dental)\b", "🦷"),
        (r"\b(doctor|physician|clinic|hospital|medical)\b", "🩺"),
        (r"\b(pill|pills|medicine|meds|pharmacy|medication)\b", "💊"),
        (r"\b(gym|workout|exercise|training|fit|fitness|lift|weights)\b", "🏋️‍♂️"),
        (r"\b(run|jog|walk|cardio|sport|sports)\b", "🏃‍♂️"),
        (r"\b(sick|ill|pain|wound|hurt)\b", "🩹"),
        
        # Specific food & drink items
        (r"\b(milk|dairy|cheese|yogurt)\b", "🥛"),
        (r"\b(apple|apples|fruit|fruits|banana|bananas|orange|oranges)\b", "🍎"),
        (r"\b(vegetable|vegetables|veg|broccoli|salad|carrot|carrots)\b", "🥦"),
        (r"\b(meat|chicken|steak|beef|fish)\b", "🥩"),
        (r"\b(bread|bakery|toast)\b", "🍞"),
        (r"\b(coffee|tea|espresso|cafe)\b", "☕"),
        (r"\b(pizza)\b", "🍕"),
        (r"\b(burger|hamburger)\b", "🍔"),
        (r"\b(cake|cookie|cookies)\b", "🍰"),
        
        # Specific chores objects
        (r"\b(laundry|wash|clothes|dryer)\b", "🧺"),
        (r"\b(dish|dishes|dishwasher)\b", "🧼"),
        (r"\b(shower|bath|toilet|bathroom)\b", "🚿"),
        (r"\b(trash|garbage|rubbish|bin|recycle)\b", "🗑️"),
        
        # Specific study/work items
        (r"\b(email|mail|outlook|gmail)\b", "✉️"),
        (r"\b(text|message|whatsapp|chat|reply|msg)\b", "💬"),
        (r"\b(study|read|book|books|class|course|homework|lecture)\b", "📚"),
        (r"\b(code|coding|program|github|develop|bug|fix|pr|commit)\b", "💻"),
        
        # Specific financial items
        (r"\b(bank|transfer|deposit|save|savings)\b", "💰"),
        (r"\b(money|cash|dollar|dollars|euro|shekel|shekels|salary)\b", "💵"),
        
        # Specific home/garden items
        (r"\b(garden|plant|plants|water|seed|flower|flowers|yard|lawn)\b", "🌱"),
        (r"\b(home|house|apartment|flat)\b", "🏡"),
        (r"\b(light|bulb|electricity|power)\b", "💡"),
        
        # Specific travel/vehicle items
        (r"\b(car|drive|driving|parking|garage|fuel|gas|oil)\b", "🚗"),
        (r"\b(flight|fly|plane|airport|travel|trip|vacation|hotel)\b", "✈️"),
        (r"\b(pack|luggage|bag|backpack)\b", "🎒"),
        (r"\b(map|navigation|gps|directions)\b", "🗺️"),
        
        # Specific entertainment items
        (r"\b(movie|film|cinema|show|watch|netflix|youtube|disney)\b", "🎬"),
        (r"\b(tv|television|screen|chromecast|appletv)\b", "📺"),
        (r"\b(game|games|gaming|play|xbox|playstation|switch|steam)\b", "🎮"),
        (r"\b(music|song|songs|concert|spotify|listen|audio)\b", "🎵"),
        
        # Sleep & alarm
        (r"\b(sleep|bed|night|dream)\b", "🛌"),
        (r"\b(wake|morning|alarm|snooze)\b", "⏰"),
        
        # Generic action verbs (placed at the end to avoid premature matches)
        (r"\b(call|phone|dial|ring)\b", "📞"),
        (r"\b(buy|get|purchase|order|shop|shopping|store)\b", "🛒"),
        (r"\b(grocery|groceries|supermarket|market)\b", "🛍️"),
        (r"\b(clean|sweep|vacuum|dust|mop)\b", "🧹"),
        (r"\b(work|office|meeting|zoom|teams|calendar|schedule|sync)\b", "💻"),
        (r"\b(write|draft|note|notes|blog|post|paper)\b", "📝"),
        (r"\b(pay|bill|bills|rent|tax|taxes|invoice|credit|card|subscription)\b", "💳"),
        (r"\b(fix|repair|mend|broken|troubleshoot)\b", "🔧"),
        (r"\b(build|assemble|install|setup)\b", "🔨"),
        (r"\b(cook|dinner|lunch|breakfast|meal|food|eat|restaurant)\b", "🍽️"),
        (r"\b(bake|oven)\b", "🍰"),
    ]
    
    for pattern, emoji in rules:
        if re.search(pattern, title_lower):
            return emoji
            
    return "📝"

async def get_fitting_emoji_ai(title: str) -> str:
    """Ask the chat model for a single fitting emoji for the task description (any language)."""
    prompt = (
        f"Analyze the following task description and return exactly ONE single emoji character "
        f"that fits the task best. If the description is in Hebrew, understand its meaning first. "
        f"Do not include any text, reasoning, whitespace, punctuation or explanation. Only a single emoji.\n\n"
        f"Task: \"{title}\"\n"
        f"Emoji:"
    )
    
    try:
        emoji = await llm.complete([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=5,
                                 purpose="task_emoji")
        # Clean up any stray text/quotes/markdown/whitespace
        emoji = emoji.replace("\"", "").replace("'", "").strip()
        if emoji:
            # Check the first part of the string in case it has explanation text
            parts = emoji.split()
            first_part = parts[0] if parts else ""
            if starts_with_emoji(first_part):
                return first_part
            if starts_with_emoji(emoji):
                return emoji[0]
                
    except Exception as e:
        logger.warning(f"Failed to fetch emoji using AI: {e}. Falling back to rule-based matching.")
        
    # Fallback to the rule-based approach if AI fails or returns invalid response
    return get_fitting_emoji(title)

async def sanitize_task_title(title: str) -> str:
    """Ensure the title starts with a relevant emoji, fetching from AI with rule-based fallback."""
    cleaned = title.strip()
    if not cleaned:
        return cleaned
    if not starts_with_emoji(cleaned):
        emoji = await get_fitting_emoji_ai(cleaned)
        cleaned = f"{emoji} {cleaned}"
    return cleaned

async def get_fitting_group_ai(title: str, available_groups: list) -> str:
    """Ask the chat model for the best group for a task from the available groups."""
    groups_list = ", ".join(g["name"] for g in available_groups)

    prompt = (
        f"Analyze the following task description and choose the BEST matching group "
        f"from this list: {groups_list}. "
        f"If the description is in Hebrew, understand its meaning first. "
        f"Return ONLY the group name, nothing else. No punctuation, no explanation.\n\n"
        f"Task: \"{title}\"\n"
        f"Group:"
    )

    try:
        reply = await llm.complete([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=20,
                                 purpose="task_group")
        group = reply.strip("\"'.")
        if group:
            # Validate against available groups
            for g in available_groups:
                if g["name"].lower() == group.lower():
                    return g["name"]
    except Exception as e:
        logger.warning(f"Failed to detect group using AI: {e}. Assigning to 'General'.")

    return "General"


async def resolve_task_group(user: User, group: Optional[str], title: str) -> str:
    """The id of the list a new task of ``user`` goes to: the list called
    ``group`` (their own first), else the one the model picks among the lists
    they can add to, else their 'General' list."""
    if group:
        group_obj = groups.find_group(user, group)
        if group_obj:
            return group_obj["id"]
        logger.warning(f"Group '{group}' not found, auto-detecting.")

    available = [g for g in groups.list_groups(user) if g.get("access") in (None, sharing.EDIT)]
    if not available:
        return groups.default_group_id(user)

    detected_name = await get_fitting_group_ai(title, available)
    group_obj = groups.find_group(user, detected_name)
    if group_obj and group_obj.get("access") in (None, sharing.EDIT):
        return group_obj["id"]

    return groups.default_group_id(user)
