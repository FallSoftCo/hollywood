"""Goal intent/strategy history on the existing Goal, Work and receipt authority."""
from psycopg.types.json import Jsonb
from .store import Conflict, Denied, digest, json_value

CAPABILITY="goal-definition-strategy/v1"

class ContractConflict(Conflict):
    """Actionable, authenticated contract preconditions; never SQL diagnostics."""
    def __init__(self, code, message, *, next_action=None):
        super().__init__(message)
        self.code=code
        self.next_action=next_action

def current(c, goal):
    definition=c.execute("SELECT * FROM hb_goal_definition WHERE goal=%s AND revision=%s",
                         (goal["id"],goal["definition_revision"])).fetchone()
    adopted=c.execute("SELECT * FROM hb_goal_definition WHERE goal=%s AND revision=%s",
                       (goal["id"],goal["adopted_definition_revision"])).fetchone()
    strategy=c.execute("SELECT * FROM hb_goal_strategy WHERE goal=%s AND revision=%s",
                        (goal["id"],goal["plan_revision"])).fetchone()
    return json_value({"schema":CAPABILITY,"required":goal["contract_required"],
        "definition":definition,"adoptedDefinition":adopted,"strategy":strategy,
        "definitionReady":bool(adopted),"strategyCurrent":bool(strategy and
          strategy["definition_revision"]==goal["adopted_definition_revision"]),
        "meaning":"Definitions and strategies are versioned intent. Adoption is attributable owner review, not evidence of achieved acceptance."})

def mutate(store,c,actor,goal,action,payload):
    source=store.readable_event(c,actor,payload["sourceEventId"])
    if source["room"]!=goal["room"] or source["audience"]!="room":
        raise Denied("Definition/strategy source must be room-visible in this Goal; publish selected scoped intent first")
    if action=="goal.definition":
        if payload["definitionRevision"]!=goal["definition_revision"]:
            raise ContractConflict('GOAL_DEFINITION_CHANGED','Goal definition revision changed')
        body=payload["definition"];number=goal["definition_revision"]+1
        c.execute("""INSERT INTO hb_goal_definition(goal,revision,definition,digest,actor,source)
            VALUES(%s,%s,%s,%s,%s,%s)""",
            (goal["id"],number,Jsonb(body),digest(body),actor["id"],source["id"]))
        c.execute("UPDATE hb_goal SET contract_required=true,definition_revision=%s WHERE id=%s",
                  (number,goal["id"]))
        return {"definitionRevision":number,"definitionSha256":digest(body),"adopted":False}
    if action=="goal.definition_adopt":
        body=c.execute("SELECT * FROM hb_goal_definition WHERE goal=%s AND revision=%s",
                       (goal["id"],payload["definitionRevision"])).fetchone()
        if not body or body["revision"]!=goal["definition_revision"] or body["digest"]!=payload["definitionSha256"]:
            raise ContractConflict('GOAL_DEFINITION_CHANGED','Adopt the exact latest proposed definition and digest')
        if body["definition"]["openQuestions"]:
            raise ContractConflict('GOAL_DEFINITION_UNRESOLVED','Resolve recorded open questions before adopting the definition')
        if goal["adopted_definition_revision"]==body["revision"]:
            raise ContractConflict('GOAL_DEFINITION_ALREADY_ADOPTED','Definition already adopted; inspect its original receipt')
        acceptance="\n".join(x["id"]+": "+x["requirement"]+" ["+x["method"]+"] "+x["verification"]
                              for x in body["definition"]["criteria"])
        if len(acceptance)>4000:
            raise ContractConflict('GOAL_DEFINITION_TOO_LARGE','Criterion text exceeds Goal acceptance limit; retain concise criteria with artifact references')
        c.execute("""UPDATE hb_goal SET contract_required=true,adopted_definition_revision=%s,
            objective=%s,acceptance=%s WHERE id=%s""",
            (body["revision"],body["definition"]["objective"],acceptance,goal["id"]))
        return {"definitionRevision":body["revision"],"definitionSha256":body["digest"],
                "adopted":True,"reviewerId":str(actor["id"]),"reviewerKind":actor["kind"],
                "sourceEventId":str(source["id"]),"achievementProved":False}
    if payload["planRevision"]!=goal["plan_revision"]:
        raise ContractConflict('GOAL_STRATEGY_CHANGED','Strategy revision changed')
    if payload["definitionRevision"]!=goal["adopted_definition_revision"] or not goal["adopted_definition_revision"]:
        raise ContractConflict('GOAL_DEFINITION_CHANGED','Strategy must use the currently adopted definition')
    strategy=payload["strategy"]
    for identity in strategy["frontierWorkIds"]:
        work=c.execute("SELECT * FROM hb_work WHERE id=%s AND goal=%s",
                       (identity,goal["id"])).fetchone()
        if not work or work["goal_definition_revision"]!=goal["adopted_definition_revision"]:
            raise ContractConflict('GOAL_FRONTIER_STALE','Strategy frontier requires Work for this adopted definition',
                next_action='Read the current Goal and original Work/delivery first. For obsolete unfinished intent, use hollywood_work_cancel, observe known cancellation settlement, then create current criterion-bound Work, select its strategy frontier and separately assign it. Retain the old cancelled attempt and any effects; do not replay it. hollywood_work_revise is for completed independently rejected attempts. Active or unknown effects remain fenced; cancellation is not proof they stopped.')
        store.room(c,actor,work["room"])
    number=goal["plan_revision"]+1
    c.execute("""INSERT INTO hb_goal_strategy(goal,revision,definition_revision,strategy,digest,actor,source)
        VALUES(%s,%s,%s,%s,%s,%s,%s)""",(goal["id"],number,payload["definitionRevision"],
        Jsonb(strategy),digest(strategy),actor["id"],source["id"]))
    c.execute("UPDATE hb_goal SET plan_revision=%s WHERE id=%s",(number,goal["id"]))
    return {"planRevision":number,"definitionRevision":payload["definitionRevision"],
            "strategySha256":digest(strategy),"executionAdmitted":False}

def bind_work(c,goal,work,criterion_ids=None,*,assign=False):
    if not goal["contract_required"]:
        return
    if not goal["adopted_definition_revision"]:
        raise ContractConflict('GOAL_DEFINITION_REQUIRED','Develop and adopt the Goal definition before creating or assigning Work')
    definition=c.execute("SELECT definition FROM hb_goal_definition WHERE goal=%s AND revision=%s",
                          (goal["id"],goal["adopted_definition_revision"])).fetchone()["definition"]
    if criterion_ids is not None:
        valid={x["id"] for x in definition["criteria"]}
        if not criterion_ids or len(set(criterion_ids))!=len(criterion_ids) or not set(criterion_ids)<=valid:
            raise ContractConflict('GOAL_CRITERIA_INVALID','Work must name its exact adopted acceptance criterion IDs')
        c.execute("UPDATE hb_work SET goal_definition_revision=%s,criterion_ids=%s WHERE id=%s",
                  (goal["adopted_definition_revision"],Jsonb(criterion_ids),work["id"]))
    elif work["goal_definition_revision"]!=goal["adopted_definition_revision"]:
        raise ContractConflict('WORK_DEFINITION_SUPERSEDED','Work belongs to a superseded Goal definition; explicitly prepare current work',
            next_action='Read the current Goal and original Work/delivery first. For obsolete unfinished intent, use hollywood_work_cancel, observe known cancellation settlement, then create current criterion-bound Work, select its strategy frontier and separately assign it. Retain the old cancelled attempt and any effects; do not replay it. hollywood_work_revise is for completed independently rejected attempts. Active or unknown effects remain fenced; cancellation is not proof they stopped.')
    if assign:
        plan=c.execute("SELECT * FROM hb_goal_strategy WHERE goal=%s AND revision=%s",
                       (goal["id"],goal["plan_revision"])).fetchone()
        if not plan or plan["definition_revision"]!=goal["adopted_definition_revision"]:
            raise ContractConflict('GOAL_STRATEGY_REQUIRED','Select a current strategy before assigning Work')
        if str(work["id"]) not in plan["strategy"]["frontierWorkIds"]:
            raise ContractConflict('WORK_OUTSIDE_FRONTIER','Work is outside the selected strategy frontier')
        c.execute("UPDATE hb_work SET goal_plan_revision=%s WHERE id=%s",
                  (plan["revision"],work["id"]))

def accept_goal(c,goal):
    if not goal["contract_required"]:
        return
    adopted=goal["adopted_definition_revision"]
    if not adopted:
        raise ContractConflict('GOAL_DEFINITION_REQUIRED','Goal has no adopted acceptance definition')
    contract=current(c,goal)
    if not contract["strategyCurrent"]:
        raise ContractConflict('GOAL_STRATEGY_REQUIRED','Goal strategy belongs to a superseded definition')
    all_work=c.execute("""SELECT w.id,w.state,d.state AS delivery_state FROM hb_work w
        LEFT JOIN hb_delivery d ON d.id=w.delivery WHERE w.goal=%s""",(goal["id"],)).fetchall()
    frontier=set(contract["strategy"]["strategy"]["frontierWorkIds"])
    if any(str(w["id"]) in frontier and w["state"]!="accepted" for w in all_work):
        raise ContractConflict("GOAL_FRONTIER_UNACCEPTED","Selected strategy frontier is not accepted; explicitly revise a retired path")
    valid={x["id"] for x in contract["adoptedDefinition"]["definition"]["criteria"]}
    rows=c.execute("""SELECT criterion_ids FROM hb_work
        WHERE goal=%s AND state='accepted' AND goal_definition_revision=%s""",
        (goal["id"],adopted)).fetchall()
    if set(x for row in rows for x in row["criterion_ids"])!=valid:
        raise ContractConflict('GOAL_CRITERIA_UNCOVERED','Current accepted Work does not cover every adopted criterion')

    if any(w["state"] not in ("accepted","cancelled") or w["delivery_state"] in
           ("pending","claimed","running","unknown","cancel_requested") for w in all_work):
        raise ContractConflict("GOAL_WORK_UNSETTLED","Goal retains unfinished work or uncertain execution; settle it explicitly before accepting")
