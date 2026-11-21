"""Agent tools for people: who someone is, what's going on with them, what's open between us."""
import json
from typing import Optional

from core.addons import Connection
from addons.people.addon import addon
from core.undoable import UndoConflict, safe_record
from core.users import User
from addons.people import cadence, detail, store
from addons.people.values import shown_phone
from addons.tasks import links as task_links, service as tasks_service

# What update_person can change, as returned by detail.get_person / store.to_api.
_PERSON_FIELDS = ("name", "aliases", "phones", "relation", "notes", "birthday", "pinned")


def _person_state(user: User, pid: str) -> Optional[dict]:
    p = detail.get_person(user, pid, schedule_brief=False)
    if not p:
        return None
    return {k: (sorted(p.get(k) or []) if k in ("aliases", "phones") else p.get(k)) for k in _PERSON_FIELDS}


def _record_person_restore(user: User, pid: str, before: dict, after: Optional[dict]) -> None:
    if not after or after == before:
        return

    def _undo():
        current = _person_state(user, pid)
        if current is None:
            raise UndoConflict("The person was deleted in the meantime.")
        if current != after:
            raise UndoConflict("Their details were changed again since, so they were left as they are.")
        store.update_person(user, pid, {k: before[k] for k in _PERSON_FIELDS})
        if before["name"] != after.get("name"):
            # A rename keeps the old name as an alias; put the aliases back exactly.
            store.update_person(user, pid, {"aliases": before["aliases"]})

    safe_record(f"Updated {after.get('name') or before.get('name')}", _undo, "update_person")


def _person_line(p: dict) -> str:
    s = p.get("stats") or {}
    bits = [p["name"]]
    if p.get("relation"):
        bits.append(p["relation"])
    if s.get("last_contact_ago"):
        bits.append(f"last contact {s['last_contact_ago']}")
    if s.get("drifting"):
        bits.append(f"usually every ~{s.get('typical_gap_days')} days")
    if p.get("open_tasks"):
        bits.append(f"{p['open_tasks']} open task(s)")
    return f"- [{p['id']}] " + " · ".join(bits)


@addon.tool()
async def list_people(conn: Connection, query: Optional[str] = None, sort: str = "recent", limit: int = 15) -> str:
    """List the people the user talks to (built from calls and messages), e.g. who he talked to lately or who he hasn't talked to in a while.

    Args:
        query: Optional name, relation or phone number to filter by (Hebrew or English).
        sort: 'recent' (last contact first), 'drifting' (only people he talks to regularly but not lately), 'pinned' or 'name'.
        limit: Max people to return (default 15).
    """
    items = cadence.list_people(conn.user, q=query or "", sort=sort if sort in ("recent", "drifting", "pinned", "name") else "recent")
    if not items:
        return "No people found."
    return "\n".join(_person_line(p) for p in items[:max(1, min(limit, 50))])


@addon.tool(untrusted_result=True)
async def get_person(conn: Connection, person: str) -> str:
    """Everything Dana knows about one person: relation, notes, birthday, how often they talk, the AI brief (what's going on, open loops), recent calls with summaries, recent messages from them and open tasks involving them.

    Args:
        person: The person's name (Hebrew or English, partial is fine), phone number or id.
    """
    pid = store.resolve(conn.user, person)
    if not pid:
        return f"No person matching '{person}'. Use list_people to search."
    p = detail.get_person(conn.user, pid, schedule_brief=True)
    if not p:
        return "Person not found."
    s = p["stats"]
    b = p.get("brief") or {}
    out = [f"{p['name']} [{p['id']}]" + (f" — {p['relation']}" if p["relation"] else "")]
    if p["aliases"]:
        out.append("Also known as: " + ", ".join(p["aliases"]))
    if p["phones"]:
        out.append("Phones: " + ", ".join(shown_phone(x) for x in p["phones"]))
    if p["emails"]:
        out.append("Emails: " + ", ".join(p["emails"]))
    if p["birthday"]:
        out.append(f"Birthday: {p['birthday']}")
    if p["notes"]:
        out.append(f"Notes: {p['notes']}")
    out.append(f"Last contact: {s['last_contact_ago'] or 'never'} ({s['calls']} calls, {s['messages']} messages on record)"
               + (f"; usually every ~{s['typical_gap_days']} days" if s.get("typical_gap_days") else ""))
    if b.get("points"):
        out.append("Lately: " + "; ".join(b["points"]))
    if b.get("open"):
        out.append("Open between them: " + "; ".join(b["open"]))
    calls = [e for e in p["timeline"] if e["type"] == "call"][:5]
    if calls:
        out.append("Recent calls:")
        out += [f"- {c['at'][:10]} [{c['id']}] {c['title']}: {c['summary'][:400]}" for c in calls]
    msgs = [e for e in p["timeline"] if e["type"] == "messages"][:5]
    if msgs:
        out.append("Recent messages from them:")
        out += [f"- {m['at'][:10]} {m['app']}{' in ' + m['chat'] if m['chat'] else ''} ({m['count']}): "
                + " / ".join(m["texts"]) for m in msgs]
    if p["tasks"]:
        out.append("Open tasks:")
        out += [f"- [{t['id']}] {t['title']}" + (f" (due {t['due_date']})" if t["due_date"] else "") for t in p["tasks"]]
    if p["suggestions"]:
        out.append("Suggestions waiting: " + "; ".join(f"[{x['id']}] {x['title']}" for x in p["suggestions"]))
    return "\n".join(out)


@addon.tool(is_write=True)
async def update_person(conn: Connection, person: str, relation: Optional[str] = None, notes: Optional[str] = None,
                        append_note: Optional[str] = None, birthday: Optional[str] = None,
                        add_alias: Optional[str] = None, add_phone: Optional[str] = None,
                        pinned: Optional[bool] = None, rename_to: Optional[str] = None) -> str:
    """Update what Dana knows about a person (creates them if nobody matches and rename_to is not given).

    Args:
        person: The person's name, phone number or id.
        relation: Who they are to the user, e.g. 'mother', 'colleague at Wix', 'plumber'.
        notes: Replace the free-text notes.
        append_note: Add a line to the notes (keeps what's there).
        birthday: YYYY-MM-DD, or MM-DD when the year is unknown.
        add_alias: Another name they go by (e.g. the Hebrew or English spelling, a nickname).
        add_phone: Another phone number for them.
        pinned: Pin (true) or unpin (false) them in the People list and the home-screen widget.
        rename_to: A new display name.
    """
    user = conn.user
    pid = store.resolve(user, person)
    try:
        if not pid:
            p = store.create_person(user, person, phones=[add_phone] if add_phone else [],
                                     aliases=[add_alias] if add_alias else [], relation=relation or "",
                                     notes=(notes or append_note or ""), birthday=birthday or "", pinned=bool(pinned))
            created = _person_state(user, p["id"])

            def _undo_create(new_id=p["id"]):
                current = _person_state(user, new_id)
                if current is None:
                    return
                if current != created:
                    raise UndoConflict("They were edited since, so they were kept.")
                store.delete_person(user, new_id)

            safe_record(f"Added {p['name']} to people", _undo_create, "update_person")
            return f"Created {p['name']} [{p['id']}]."
        before = _person_state(user, pid)
        changes = {"relation": relation, "birthday": birthday, "pinned": pinned, "add_alias": add_alias,
                   "add_phone": add_phone, "name": rename_to}
        if notes is not None:
            changes["notes"] = notes
        elif append_note:
            current = detail.get_person(user, pid, schedule_brief=False) or {}
            changes["notes"] = "\n".join(x for x in (current.get("notes") or "", append_note) if x)
        p = store.update_person(user, pid, {k: v for k, v in changes.items() if v is not None})
        if before:
            _record_person_restore(user, pid, before, _person_state(user, pid))
        return f"Updated {p['name']}: " + json.dumps({k: v for k, v in changes.items() if v is not None}, ensure_ascii=False)
    except ValueError as e:
        return f"Error: {e}"


@addon.tool(is_write=True, needs=("tasks",))
async def link_task_to_person(conn: Connection, task_id: str, person: str) -> str:
    """Attach a task to a person so it shows on their page and on the card when they call.

    Args:
        task_id: The task id.
        person: The person's name, phone number or id.
    """
    pid = store.resolve(conn.user, person)
    if not pid:
        return f"No person matching '{person}'."
    try:
        tasks_service.writable_task(conn.user, task_id)
    except LookupError:
        return f"No task with id {task_id}."
    except tasks_service.ReadOnly as e:
        return f"Error: {e}"
    already = task_links.is_linked(task_id, "person", pid)
    task_links.link_task(task_id, "person", pid, "related")
    if not already:
        user = conn.user

        def _undo() -> None:
            tasks_service.check_still_writable(user, task_id)  # the task's list may have been unshared since
            task_links.unlink_task(task_id, "person", pid)
        safe_record("Linked the task to the person", _undo, "link_task_to_person")
    return "Linked."
