"""Resolving a contact / group name (Hebrew or English, nickname, partial,
phone number) to a WhatsApp chat id on one user's WhatsApp: their saved
contacts, their preferred contacts, their WAHA chats and contacts, a
transliteration match, recency, the chat model, a number check."""
import datetime
import logging
from typing import Dict, Optional

import httpx

from core import clock, llm
from integrations.framework import Connection
from integrations.whatsapp import contacts as saved_contacts
from integrations.whatsapp.client import api_url, config, get_chat_id_to_name_map, normalize_to_international

logger = logging.getLogger(__name__)

_HE_EN: Dict[str, str] = {
    'א': 'a', 'ב': 'b', 'ג': 'g', 'ד': 'd', 'ה': 'h', 'ו': 'v', 'ז': 'z', 'ח': 'kh',
    'ט': 't', 'י': 'y', 'כ': 'k', 'ל': 'l', 'מ': 'm', 'נ': 'n', 'ס': 's', 'ע': 'a',
    'פ': 'p', 'צ': 'ts', 'ק': 'k', 'ר': 'r', 'ש': 'sh', 'ת': 't',
    'ך': 'k', 'ם': 'm', 'ן': 'n', 'ף': 'p', 'ץ': 'ts',
}
_EN_HE: Dict[str, str] = {v: k for k, v in _HE_EN.items()}


def transliterate_he_to_en(text: str) -> str:
    """Convert Hebrew characters to English phonetic equivalent."""
    result = []
    for ch in text:
        if ch in _HE_EN:
            result.append(_HE_EN[ch])
        else:
            result.append(ch)
    return "".join(result).lower()


def _transliterate_en_to_he(text: str) -> str:
    """Attempt a rough English-to-Hebrew transliteration."""
    result = []
    text_lower = text.lower()
    # Handle multi-character mappings first (longest match)
    i = 0
    while i < len(text_lower):
        # Try 2-char match
        if i + 1 < len(text_lower) and text_lower[i:i+2] in ('sh', 'ts', 'kh'):
            for en, he in _EN_HE.items():
                if en == text_lower[i:i+2]:
                    result.append(he)
                    break
            i += 2
        else:
            ch = text_lower[i]
            result.append(_EN_HE.get(ch, ch))
            i += 1
    return "".join(result)


def _display_name(candidate: dict) -> str:
    """Build a display name showing both Hebrew and English for a candidate."""
    name = candidate["name"]
    en = transliterate_he_to_en(name)
    if en and en != name.lower() and name != candidate["id"]:
        return f"{name} ({en})"
    return name



async def _resolve_name_with_llm(query: str, candidates: list[dict]) -> Optional[dict]:
    """Uses the chat model to match Hebrew/English/nickname searches to actual contacts.

    Returns the best matching candidate, or None if ambiguous/no match.
    Never raises ValueError — ambiguity is handled by the caller.
    """
    if not candidates:
        return None

    if not llm.configured("chat"):
        logger.warning("No API key for the chat model; cannot run the LLM name matcher.")
        return None

    candidates_str = ""
    for idx, c in enumerate(candidates):
        ts_info = ""
        if c.get("last_message_ts"):
            try:
                dt = clock.from_timestamp(c["last_message_ts"])
                ts_info = f" [last message: {dt.strftime('%Y-%m-%d %H:%M')}]"
            except Exception:
                pass
        candidates_str += f"{idx}: {c['name']} (ID: {c['id']}){ts_info}\n"

    prompt = (
        "You are an assistant that resolves contact names for WhatsApp.\n"
        f"The user is searching for a contact named: '{query}'\n"
        "This query could be in English or Hebrew, a nickname, or a partial name.\n\n"
        "Here is the list of candidates:\n"
        f"{candidates_str}\n"
        "Instructions:\n"
        "1. Identify if one of the candidates is a clear match for the user's query.\n"
        "2. Keep in mind that Hebrew and English names match phonetically or by translation (e.g. 'ענבל' matches 'Inbal Cohen', 'דן' matches 'Dan', 'נוה' matches 'Nave', etc.).\n"
        "3. Nicknames or partial name matches are allowed (e.g. 'Dan' matches 'Dan Haimov', 'Yaffa' matches 'יפה כהן').\n"
        "4. If there is a single clear best match, output only the index number of that candidate. Example: 'Index: 3'.\n"
        "5. If there are multiple close matches and you are not absolutely sure which one is correct (ambiguous), output 'AMBIGUOUS: <index1>, <index2>, ...'. Example: 'AMBIGUOUS: 1, 4'.\n"
        "6. If there is absolutely no reasonable match, output 'NO_MATCH'.\n"
        "7. When multiple candidates are equally valid by name, PREFER the one with the most recent message activity — the user typically asks about contacts they recently communicated with.\n\n"
        "Output:"
    )

    try:
        content = await llm.complete([{"role": "user", "content": prompt}], temperature=0.0,
                                     purpose="whatsapp_match")
        logger.info(f"LLM name resolution response for query '{query}': {content}")

        if "Index:" in content:
            try:
                idx_part = content.split("Index:")[-1].strip()
                idx_digits = "".join(ch for ch in idx_part if ch.isdigit())
                if idx_digits:
                    idx = int(idx_digits)
                    if 0 <= idx < len(candidates):
                        return candidates[idx]
            except Exception as e:
                logger.warning(f"Error parsing index from LLM match: {e}")
        elif "AMBIGUOUS:" in content:
            indices_str = content.split("AMBIGUOUS:")[-1].strip().split(",")
            ambiguous_matches = []
            for idx_str in indices_str:
                try:
                    idx_digits = "".join(ch for ch in idx_str if ch.isdigit())
                    if idx_digits:
                        idx = int(idx_digits)
                        if 0 <= idx < len(candidates):
                            ambiguous_matches.append(candidates[idx])
                except Exception:
                    pass
            if ambiguous_matches:
                logger.info(f"LLM returned {len(ambiguous_matches)} ambiguous candidates for '{query}'; will use recency to pick best")
                return None
    except Exception as e:
        logger.warning(f"Error calling LLM for name resolution: {e}")

    return None


async def _pick_best_by_recency(candidates: list[dict]) -> Optional[dict]:
    """Pick the best candidate by recency from a list of candidates.

    Rules:
    - If exactly one candidate has a recent timestamp (< 48h) and no other has one < 7d → pick it
    - If one candidate has a strictly most-recent timestamp → pick it
    - Otherwise returns None (caller should pick first candidate)
    """
    if not candidates:
        return None

    now_ts = datetime.datetime.now().timestamp()

    with_ts = sorted(
        [c for c in candidates if c.get("last_message_ts")],
        key=lambda c: c["last_message_ts"], reverse=True
    )

    if not with_ts:
        return None

    newest = with_ts[0]
    newest_age_hours = (now_ts - newest["last_message_ts"]) / 3600

    # Single candidate with a timestamp — auto-resolve if reasonably recent
    if len(with_ts) < 2:
        if newest_age_hours < 168:  # within 7 days
            logger.info(f"Recency pick: '{newest['name']}' (only contact with activity, {newest_age_hours:.1f}h ago)")
            return newest
        return None

    # Multiple candidates with timestamps — check gap
    second = with_ts[1]
    second_age_hours = (now_ts - second["last_message_ts"]) / 3600

    # Newest is significantly more recent than the second
    if newest_age_hours < 48 and second_age_hours > 168:
        logger.info(f"Recency pick: '{newest['name']}' ({newest_age_hours:.1f}h ago) beats second '{second['name']}' ({second_age_hours:.1f}h ago)")
        return newest

    # Newest is more than 2x more recent than second
    if second_age_hours > 0 and newest_age_hours < second_age_hours / 2 and newest_age_hours < 72:
        logger.info(f"Recency pick: '{newest['name']}' ({newest_age_hours:.1f}h ago) is2x+ more recent than '{second['name']}' ({second_age_hours:.1f}h ago)")
        return newest

    return None


def _pick_best_alphabetical(candidates: list[dict]) -> Optional[dict]:
    """Pick the best candidate when no timestamp info is available.

    Prefer exact name matches over substring matches.
    """
    if not candidates:
        return None
    # Already sorted by relevance from the caller — just return the first
    return candidates[0]



async def resolve_chat_id_and_name(conn: Connection, chat_id_or_name: str) -> tuple[str, str]:
    """Helper to resolve a chat ID or contact/group name to a valid WhatsApp chat ID and name.

    Supports Hebrew <-> English phonetic matches, nickname resolution, and recency-based auto-resolution.

    NEVER raises ValueError for ambiguous names — always picks the best available candidate.
    Only raises ValueError for truly unresolvable cases (empty input, no candidates at all).
    """
    chat_id_or_name = chat_id_or_name.strip()
    if not chat_id_or_name:
        raise ValueError("Recipient chat ID or name cannot be empty.")

    url, headers, session = config(conn)

    # 1. Resolve from the user's saved contacts first
    query_lower = chat_id_or_name.lower()
    try:
        query_normalized = query_lower.replace(" ", "_").replace("-", "_")
        rows = saved_contacts.resolve_rows(conn.user, query_lower, query_normalized)
        if rows:
            exact = [row for row in rows if row["key"] == query_normalized]
            best_row = exact[0] if exact else rows[0]
            resolved_val = best_row["value"].strip()
            resolved_key_name = best_row["key"].replace("_", " ").title()
            logger.info(f"Resolved '{chat_id_or_name}' to '{resolved_val}' from saved contact '{best_row['key']}'")
            return resolved_val, resolved_key_name
    except Exception as e:
        logger.error(f"Error querying saved contacts in resolve: {e}")

    # 2. If it is already a chat ID, look up its name if possible, or fall back to the ID
    if "@" in chat_id_or_name:
        chat_name = chat_id_or_name
        try:
            mapping = await get_chat_id_to_name_map(conn)
            if chat_id_or_name in mapping:
                chat_name = mapping[chat_id_or_name]
        except Exception:
            pass
        return chat_id_or_name, chat_name

    # 2b. Check preferred contacts mapping
    preferred_id = saved_contacts.preferred(conn.user, query_lower)
    if preferred_id:
        preferred_name = chat_id_or_name
        try:
            mapping = await get_chat_id_to_name_map(conn)
            if preferred_id in mapping:
                preferred_name = mapping[preferred_id]
        except Exception:
            pass
        logger.info(f"Resolved '{chat_id_or_name}' to preferred contact '{preferred_name}' (ID: {preferred_id})")
        return preferred_id, preferred_name

    # 3. Retrieve candidates from chats overview + contacts
    candidates = []
    seen_ids = set()

    async with httpx.AsyncClient() as client:
        # Chats overview (ordered by recent activity)
        try:
            r = await client.get(api_url(conn, session, "chats", "overview"), headers=headers, params={"limit": 100}, timeout=20.0)
            if r.status_code == 200:
                chats = r.json()
                for chat in chats:
                    cid = chat.get("id")
                    cname = chat.get("name")
                    if cid and cid not in seen_ids:
                        if not cname:
                            last_msg = chat.get("lastMessage")
                            if last_msg:
                                _d = last_msg.get("_data") or {}
                                push_name = _d.get("pushName")
                                if push_name:
                                    cname = push_name.lstrip("~")
                                else:
                                    cname = _d.get("verifiedBizName")
                        cname = cname or cid
                        last_ts = (chat.get("lastMessage") or {}).get("timestamp")
                        candidates.append({"id": cid, "name": cname, "last_message_ts": last_ts})
                        seen_ids.add(cid)
        except Exception as e:
            logger.warning(f"Error fetching chats overview in resolve: {e}")

        # Contacts
        try:
            r = await client.get(api_url(conn, "contacts", "all"), headers=headers, params={"session": session}, timeout=20.0)
            if r.status_code == 200:
                contacts = r.json()
                for contact in contacts:
                    cid = contact.get("id")
                    cname = contact.get("name")
                    if cid and cid not in seen_ids:
                        cname = cname or cid
                        candidates.append({"id": cid, "name": cname})
                        seen_ids.add(cid)
        except Exception as e:
            logger.warning(f"Error fetching contacts in resolve: {e}")

    if not candidates:
        raise ValueError(f"Could not find any contacts or chats in WhatsApp to match '{chat_id_or_name}'.")

    # 4. Filter matches locally (exact, substring, transliteration)
    query_lower = chat_id_or_name.lower()
    query_he = _transliterate_en_to_he(chat_id_or_name)
    query_en = transliterate_he_to_en(chat_id_or_name)

    matches = []
    for c in candidates:
        name_lower = c["name"].lower()
        if name_lower == query_lower:
            matches.append(c)
            continue
        if query_lower in name_lower:
            matches.append(c)
            continue
        if query_en and query_en in name_lower:
            matches.append(c)
            continue
        if query_he and query_he in name_lower:
            matches.append(c)
            continue

    # Also try matching as phone number against candidate IDs
    if not matches and query_lower.isdigit():
        normalized_phone = normalize_to_international(query_lower)
        for c in candidates:
            cid_clean = c["id"].split("@")[0]
            if normalized_phone in cid_clean or cid_clean in normalized_phone:
                matches.append(c)

    # Single match — return immediately
    if len(matches) == 1:
        logger.info(f"Resolved '{chat_id_or_name}' to single match: '{matches[0]['name']}' (ID: {matches[0]['id']})")
        return matches[0]["id"], matches[0]["name"]

    # 4b. Recency heuristic on local matches
    if len(matches) > 1:
        recency_pick = await _pick_best_by_recency(matches)
        if recency_pick:
            logger.info(f"Auto-resolved '{chat_id_or_name}' to '{recency_pick['name']}' via recency (ID: {recency_pick['id']})")
            return recency_pick["id"], recency_pick["name"]

    # 5. LLM fallback — never throws, returns None on ambiguity
    llm_candidates = matches if matches else candidates
    if llm_candidates:
        try:
            best_match = await _resolve_name_with_llm(chat_id_or_name, llm_candidates)
            if best_match:
                logger.info(f"LLM resolved '{chat_id_or_name}' to '{best_match['name']}' (ID: {best_match['id']})")
                return best_match["id"], best_match["name"]
        except Exception as e:
            logger.warning(f"LLM resolution failed: {e}")

    # 5b. After LLM returned None (ambiguous or no match), try recency on the FULL candidate set
    #     This covers the case where local matching found no matches or LLM was uncertain
    recency_pick = await _pick_best_by_recency(llm_candidates)
    if recency_pick:
        logger.info(f"Post-LLM recency resolved '{chat_id_or_name}' to '{recency_pick['name']}' (ID: {recency_pick['id']})")
        return recency_pick["id"], recency_pick["name"]

    # 5c. If still not resolved and we have multiple matches, try recency on ALL candidates
    if len(matches) > 1:
        recency_pick = await _pick_best_by_recency(candidates)
        if recency_pick:
            logger.info(f"Full-candidate recency resolved '{chat_id_or_name}' to '{recency_pick['name']}' (ID: {recency_pick['id']})")
            return recency_pick["id"], recency_pick["name"]

    # 6. Phone number fallback: normalize to international and try check-exists API
    phones_to_try = []
    cleaned_raw = "".join(c for c in chat_id_or_name if c.isdigit())
    if len(cleaned_raw) >= 7:
        phones_to_try.append(cleaned_raw)
        normalized = normalize_to_international(chat_id_or_name)
        if normalized != cleaned_raw:
            phones_to_try.append(normalized)
    for phone in phones_to_try:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    api_url(conn, "contacts", "check-exists"),
                    headers=headers,
                    params={"phone": phone, "session": session},
                    timeout=10.0
                )
                if r.status_code == 200:
                    res = r.json()
                    if res.get("exists"):
                        chat_id = res.get("id") or f"{phone}@c.us"
                        logger.info(f"Phone number fallback resolved '{chat_id_or_name}' to '{chat_id}' (tried: {phone})")
                        return chat_id, chat_id_or_name
        except Exception as e:
            logger.warning(f"Phone number fallback check failed for '{phone}': {e}")

    # 7. Final fallback: return a list of candidates with Hebrew + English names
    #    Never ask the user blindly — give them the full list to choose from
    if matches:
        list_items = "\n".join(
            f"  {i+1}. {_display_name(m)} (ID: `{m['id']}`)"
            for i, m in enumerate(matches[:10])
        )
        raise ValueError(
            f"Found {len(matches)} contacts matching '{chat_id_or_name}' but couldn't determine which one by recency:\n{list_items}\n"
            f"Please specify which one you mean."
        )

    # 8. Only raise if we truly found nothing at all
    raise ValueError(f"Could not find any WhatsApp chat or contact matching '{chat_id_or_name}'. Please verify the name or provide the chat ID (e.g. '972500000302@c.us'). For a new number not in your chats yet, first use check_whatsapp_number to validate it, then pass the chat ID in the format 'XXXXX@c.us'.")

