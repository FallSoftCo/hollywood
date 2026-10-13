"""Indexed, bidirectional chat pages. Reading history never dispatches agent work."""

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .failures import present_event


class HistoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,64}$")
    visibility: str = "room"
    limit: int = Field(default=50, ge=1, le=100)
    before: int | None = Field(default=None, ge=1)
    after: int | None = Field(default=None, ge=1)
    around: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.visibility != "room" and not re.fullmatch(
            r"direct:[a-z0-9_-]{1,64}", self.visibility
        ):
            raise ValueError("Unknown conversation")
        if sum(cursor is not None for cursor in (self.before, self.after, self.around)) > 1:
            raise ValueError("Supply only one history cursor")
        return self


def history_page(store, query: HistoryQuery, *, team="default"):
    if query.visibility != "room" and query.visibility.split(":", 1)[1] not in store.roster(team):
        raise ValueError("Unknown conversation")
    if query.task and store.task_team(query.task) != team:
        raise ValueError("Task does not belong to this team")
    where = "visibility=? AND kind IN ('message','attention','approval','task','control','resolved','agentNote','artifact','dismissed')"
    where += " AND task IN (SELECT id FROM task WHERE team=?)"
    args = [query.visibility, team]
    if query.task:
        where += " AND task=?"
        args.append(query.task)

    def ids(condition, params, order, limit):
        return [
            row["id"]
            for row in store.rows(
                f"SELECT id FROM event WHERE {where} {condition} ORDER BY id {order} LIMIT ?",
                [*args, *params, limit],
            )
        ]

    if query.around is not None:
        newer = ids("AND id>=?", [query.around], "ASC", query.limit // 2 + 1)
        older = ids("AND id<?", [query.around], "DESC", query.limit - len(newer))
        selected = list(reversed(newer)) + older
    elif query.after is not None:
        selected = list(reversed(ids("AND id>?", [query.after], "ASC", query.limit)))
    else:
        selected = ids(
            "AND id<?" if query.before else "",
            [query.before] if query.before else [],
            "DESC",
            query.limit,
        )

    data = []
    for event_id in selected:
        event = present_event(store, store.get_event(event_id))
        event.update(store.attention_card_state(event))
        if event["kind"] in {"attention", "approval"}:
            attention = store.rows("SELECT state FROM attention WHERE id=?", (event_id,))
            event["open"] = bool(attention and attention[0]["state"] == "open")
        source = store.rows(
            "SELECT id,author,substr(body,1,240) AS body FROM event WHERE id=? AND visibility IN ('room',?)",
            (event["data"].get("replyTo"), query.visibility),
        )
        event["reply"] = source[0] if source else None
        data.append(event)
    return {
        "data": data,
        "olderCursor": selected[-1]
        if selected and ids("AND id<?", [selected[-1]], "DESC", 1)
        else None,
        "newerCursor": selected[0]
        if selected and ids("AND id>?", [selected[0]], "ASC", 1)
        else None,
    }
