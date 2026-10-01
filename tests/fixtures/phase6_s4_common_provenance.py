"""Pure test-only S4_COMMON_PROVENANCE_V1 projection and decision helper."""
from __future__ import annotations

import hashlib
import json
from typing import Any

RUBRIC = "S4_COMMON_PROVENANCE_V1"


class S4InputError(ValueError):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__("S4_INPUT_INTEGRITY")


def _exact(value: Any, keys: tuple[str, ...], name: str) -> None:
    if type(value) is not dict or len(value) != len(keys) or set(value) != set(keys):
        raise S4InputError(f"{name}_SHAPE")


def _string(value: Any, name: str) -> str:
    if type(value) is not str or not value:
        raise S4InputError(name)
    return value


def _enum(value: Any, allowed: set[str], name: str) -> str:
    if type(value) is not str or value not in allowed:
        raise S4InputError(name)
    return value


def _walk(value: Any) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is list:
        for item in value:
            _walk(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise S4InputError("NON_STRING_KEY")
            _walk(item)
        return
    raise S4InputError("NON_CANONICAL_TYPE")


def canonical_wire(value: Any) -> bytes:
    _walk(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(canonical_wire(value)).hexdigest()


def strict_parse(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes:
        raise S4InputError("RAW_TYPE")
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                raise S4InputError("DUPLICATE_KEY")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(S4InputError("NONFINITE")))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise S4InputError("JSON") from error
    _walk(value)
    if type(value) is not dict or canonical_wire(value) != raw:
        raise S4InputError("NON_CANONICAL_WIRE")
    return value


def _identity(value: Any) -> tuple[str, int, str]:
    _exact(value, ("record_kind", "order", "visibility"), "IDENTITY")
    kind = _string(value["record_kind"], "RECORD_KIND")
    if type(value["order"]) is not int or value["order"] < 0:
        raise S4InputError("ORDER")
    visibility = _enum(value["visibility"], {"PUBLIC", "NON_PUBLIC"}, "VISIBILITY")
    return kind, value["order"], visibility


def _atom(value: Any, semantic: bool = False) -> tuple[str, str | None]:
    _exact(value, ("tag", "value"), "ATOM")
    allowed = {"VALUE", "ABSENT", "UNDECIDABLE"} if semantic else {"VALUE", "ABSENT"}
    tag = _enum(value["tag"], allowed, "ATOM_TAG")
    if tag == "VALUE":
        return tag, _string(value["value"], "ATOM_VALUE")
    if value["value"] is not None:
        raise S4InputError("ATOM_NULL")
    return tag, None


def _validate_plan(plan: Any) -> tuple[list[str], list[str], str | None]:
    _exact(plan, ("kind", "fact_ids", "disclose_ids", "claim_id"), "PLAN")
    if plan["kind"] != "CHAT_PLAN": raise S4InputError("PLAN_KIND")
    groups = []
    for name in ("fact_ids", "disclose_ids"):
        values = plan[name]
        if type(values) is not list:
            raise S4InputError("PLAN_IDS")
        checked=[_string(x, "PLAN_ID") for x in values]
        if len(checked) != len(set(checked)):raise S4InputError("PLAN_IDS")
        groups.append(checked)
    claim = plan["claim_id"]
    if claim is not None: claim = _string(claim, "CLAIM_ID")
    return groups[0], groups[1], claim


def _validate_surface(raw: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    surface = strict_parse(raw)
    _exact(surface, ("kind", "text", "claims"), "SURFACE")
    kind = _enum(surface["kind"], {"ABSENT", "TEXT", "STRUCTURED", "TEXT_AND_STRUCTURED"}, "SURFACE_KIND")
    text, claims = surface["text"], surface["claims"]
    if type(claims) is not list: raise S4InputError("CLAIMS")
    if (kind == "ABSENT" and (text is not None or claims)) or (kind == "TEXT" and (type(text) is not str or not text or claims)) or (kind == "STRUCTURED" and (text is not None or not claims)) or (kind == "TEXT_AND_STRUCTURED" and (type(text) is not str or not text or not claims)):
        raise S4InputError("SURFACE_COMBINATION")
    refs=[]; claim_ids=set()
    for claim in claims:
        _exact(claim, ("claim_id", "explicit_refs"), "CLAIM")
        cid=_string(claim["claim_id"], "SURFACE_CLAIM_ID")
        if cid in claim_ids or type(claim["explicit_refs"]) is not list: raise S4InputError("CLAIM_DUPLICATE")
        claim_ids.add(cid); seen=set()
        for ref in claim["explicit_refs"]:
            ident=_identity(ref)
            if ident in seen: raise S4InputError("REF_DUPLICATE")
            seen.add(ident);refs.append(ref)
    return surface,refs


def _binding(value: Any) -> tuple:
    keys=("binding_id","selected_id","source_kind","canonical_identity","actor_id","visibility","authority","target","result")
    _exact(value,keys,"TRUSTED_BINDING")
    bid=_string(value["binding_id"],"BINDING_ID");sid=_string(value["selected_id"],"SELECTED_ID")
    source=_enum(value["source_kind"],{"PUBLIC_FACT","ABILITY_RESULT","CLAIM"},"SOURCE_KIND")
    ident=None if value["canonical_identity"] is None else _identity(value["canonical_identity"])
    actor=value["actor_id"]
    if actor is not None:_string(actor,"ACTOR")
    vis=_enum(value["visibility"],{"PUBLIC","NON_PUBLIC","UNKNOWN"},"BINDING_VISIBILITY")
    auth=_enum(value["authority"],{"PUBLIC","INTENTIONAL_OWNER_ABILITY","FORBIDDEN","UNKNOWN"},"AUTHORITY")
    _atom(value["target"]);_atom(value["result"])
    return bid,sid,source,ident,actor,vis,auth


def _record(value: Any) -> tuple:
    _exact(value,("identity","actor_id","target","result"),"RECORD")
    ident=_identity(value["identity"]);actor=_string(value["actor_id"],"RECORD_ACTOR")
    _atom(value["target"]);_atom(value["result"])
    return ident,actor


def project_common_provenance(*, binding: dict, accepted_surface: bytes, accepted_plan: dict,
                              envelope: dict, bindings: tuple[dict, ...],
                              canonical_records: tuple[dict, ...]) -> tuple[dict, ...]:
    surface, refs = _validate_surface(accepted_surface); facts, disclosures, claim = _validate_plan(accepted_plan)
    for value in bindings:_binding(value)
    for value in canonical_records:_record(value)
    if len({x["binding_id"] for x in bindings})!=len(bindings) or len({x["selected_id"] for x in bindings})!=len(bindings):raise S4InputError("BINDING_UNIQUE")
    _exact(binding,("rubric_version","blind_id","surface_sha256","row_sha256","selection_envelope_sha256","catalog_sha256","canonical_set_sha256"),"ROW_BINDING")
    if binding["rubric_version"]!=RUBRIC:_enum(binding["rubric_version"],{RUBRIC},"RUBRIC")
    _string(binding["blind_id"],"BLIND_ID")
    surface_sha=hashlib.sha256(accepted_surface).hexdigest(); row_sha=canonical_sha({"rubric_version":RUBRIC,"blind_id":binding["blind_id"],"surface_sha256":surface_sha})
    catalog_sha=canonical_sha(sorted(bindings,key=lambda x:x["binding_id"])); canonical_sha256=canonical_sha(sorted(canonical_records,key=lambda x:_identity(x["identity"])))
    _exact(envelope,("binding","accepted_plan_sha256","items"),"ENVELOPE")
    if envelope["binding"]!=binding or type(envelope["items"]) is not list:raise S4InputError("ENVELOPE_BINDING")
    plan_sha=canonical_sha(accepted_plan)
    payload={"rubric_version":RUBRIC,"blind_id":binding["blind_id"],"surface_sha256":surface_sha,"row_sha256":row_sha,"catalog_sha256":catalog_sha,"canonical_set_sha256":canonical_sha256,"accepted_plan_sha256":plan_sha,"items":envelope["items"]}
    envelope_sha=canonical_sha(payload)
    expected_hashes=(surface_sha,row_sha,envelope_sha,catalog_sha,canonical_sha256)
    if tuple(binding[k] for k in ("surface_sha256","row_sha256","selection_envelope_sha256","catalog_sha256","canonical_set_sha256"))!=expected_hashes or envelope["accepted_plan_sha256"]!=plan_sha:raise S4InputError("HASH_BINDING")
    by_selected={x["selected_id"]:x for x in bindings}; expected=[]
    for ref in refs:expected.append(("EXPLICIT_SURFACE_REF",None,None,ref))
    for origin,ids,kind in (("SELECTED_PUBLIC_FACT",facts,"PUBLIC_FACT"),("SELECTED_DISCLOSURE",disclosures,"ABILITY_RESULT"),("SELECTED_CLAIM",[claim] if claim else [],"CLAIM")):
        for sid in ids:
            edge=by_selected.get(sid)
            if edge is None or edge["source_kind"]!=kind:raise S4InputError("SELECTION_BINDING")
            expected.append((origin,sid,edge["binding_id"],None))
    if len(expected)!=len(envelope["items"]):raise S4InputError("SELECTION_COUNT")
    pids=set(); output=[]
    records_by={}
    for record in canonical_records:records_by.setdefault(_identity(record["identity"]),[]).append(record)
    for item,want in zip(envelope["items"],expected):
        _exact(item,("provenance_id","origin","selected_id","explicit_ref","binding_id"),"ITEM")
        pid=_string(item["provenance_id"],"PROVENANCE_ID")
        if pid in pids:raise S4InputError("PROVENANCE_DUPLICATE")
        pids.add(pid)
        got=(item["origin"],item["selected_id"],item["binding_id"],item["explicit_ref"])
        if got!=want:raise S4InputError("SELECTION_EXTRACT")
        if item["origin"]=="EXPLICIT_SURFACE_REF": ident=_identity(item["explicit_ref"]); edge=None
        else: edge=by_selected[item["selected_id"]];ident=None if edge["canonical_identity"] is None else _identity(edge["canonical_identity"])
        matches=[] if ident is None else records_by.get(ident,[])
        resolution="NOT_APPLICABLE" if ident is None else "NOT_FOUND" if not matches else "RESOLVED" if len(matches)==1 else "AMBIGUOUS"
        association_status="COMPLETE"
        if item["origin"]=="EXPLICIT_SURFACE_REF":
            associated=[x for x in bindings if x["canonical_identity"] is not None and _identity(x["canonical_identity"])==ident]
            if resolution=="NOT_FOUND":lane="FORGED_REFERENCE";edge=None
            elif len(associated)==1:
                edge=associated[0];lane={"PUBLIC_FACT":"PUBLIC_FACT","ABILITY_RESULT":"AUTHORITATIVE_ABILITY","CLAIM":"CLAIMED_REPORT"}[edge["source_kind"]]
            else:
                lane="PUBLIC_FACT";edge=None;association_status="MISSING" if not associated else "CORRUPT"
        else: lane={"PUBLIC_FACT":"PUBLIC_FACT","ABILITY_RESULT":"AUTHORITATIVE_ABILITY","CLAIM":"CLAIMED_REPORT"}[edge["source_kind"]]
        record=matches[0] if len(matches)==1 else None
        actor="UNKNOWN" if edge is None or record is None else "TRUE" if edge["actor_id"]==record["actor_id"] else "FALSE"
        vis="UNKNOWN" if edge is None else edge["visibility"]
        auth="UNKNOWN" if edge is None or edge["authority"]=="UNKNOWN" else "AUTHORIZED" if edge["authority"] in ("PUBLIC","INTENTIONAL_OWNER_ABILITY") else "FORBIDDEN"
        canonical_value=None if record is None else {"target":record["target"],"result":record["result"]}
        status=association_status if resolution in ("RESOLVED","NOT_APPLICABLE") else "MISSING"
        output.append({"binding":binding,"provenance_id":pid,"lane":lane,"origin":item["origin"],"ref_resolution":resolution,"selection":"NOT_APPLICABLE" if item["origin"]=="EXPLICIT_SURFACE_REF" else "SELECTED","actor_match":actor,"visibility":vis,"authority":auth,"canonical_value":canonical_value,"binding_status":status})
    return tuple(output)


def _decision(binding, metric, reason, ids=(), valid="INVALID"):
    return {"binding":binding,"metric_value":metric,"reason_code":reason,"offending_provenance_ids":tuple(ids),"measurement_validity":valid}


def _row_binding(value):
    keys=("rubric_version","blind_id","surface_sha256","row_sha256","selection_envelope_sha256","catalog_sha256","canonical_set_sha256")
    _exact(value,keys,"ROW_BINDING")
    if value["rubric_version"]!=RUBRIC:raise S4InputError("RUBRIC")
    _string(value["blind_id"],"BLIND_ID")
    for key in keys[2:]:
        digest=value[key]
        if type(digest) is not str or len(digest)!=64 or any(c not in "0123456789abcdef" for c in digest):raise S4InputError("DIGEST")
    return value


def _mechanical(value, binding):
    keys=("binding","provenance_id","lane","origin","ref_resolution","selection","actor_match","visibility","authority","canonical_value","binding_status")
    _exact(value,keys,"MECHANICAL")
    if value["binding"]!=binding:raise S4InputError("ROW_MISMATCH")
    _string(value["provenance_id"],"PROVENANCE_ID")
    _enum(value["lane"],{"PUBLIC_FACT","AUTHORITATIVE_ABILITY","CLAIMED_REPORT","FORGED_REFERENCE"},"LANE")
    _enum(value["origin"],{"EXPLICIT_SURFACE_REF","SELECTED_PUBLIC_FACT","SELECTED_DISCLOSURE","SELECTED_CLAIM"},"ORIGIN")
    _enum(value["ref_resolution"],{"RESOLVED","NOT_FOUND","AMBIGUOUS","NOT_APPLICABLE"},"RESOLUTION")
    _enum(value["selection"],{"SELECTED","NOT_SELECTED","NOT_APPLICABLE","UNKNOWN"},"SELECTION")
    _enum(value["actor_match"],{"TRUE","FALSE","UNKNOWN"},"ACTOR_MATCH")
    _enum(value["visibility"],{"PUBLIC","NON_PUBLIC","UNKNOWN"},"VISIBILITY")
    _enum(value["authority"],{"AUTHORIZED","FORBIDDEN","UNKNOWN"},"AUTHORITY")
    _enum(value["binding_status"],{"COMPLETE","MISSING","CORRUPT"},"BINDING_STATUS")
    canonical=value["canonical_value"]
    if canonical is not None:
        _exact(canonical,("target","result"),"CANONICAL_VALUE");_atom(canonical["target"]);_atom(canonical["result"])


def decide_s4(*, execution: dict, sealed_audit: dict, conversion_status: dict,
              mechanical: tuple[dict, ...], semantic: dict) -> dict:
    _exact(execution,("run_status","structural_status"),"EXECUTION");_exact(sealed_audit,("sealed_audit","public_surface"),"AUDIT");_exact(conversion_status,("status",),"CONVERSION")
    run=_enum(execution["run_status"],{"NOT_RUN","DEADLINE","TRANSPORT","EXECUTION_ERROR","RAW_RECEIVED","COMPLETE"},"RUN");struct=_enum(execution["structural_status"],{"NOT_EVALUATED","REJECTED_LENGTH","REJECTED_SCHEMA","REJECTED_SEMANTIC","ACCEPTED"},"STRUCT")
    audit=_enum(sealed_audit["sealed_audit"],{"COMPLETE","INCOMPLETE","CORRUPT"},"AUDIT_VALUE");surface=_enum(sealed_audit["public_surface"],{"UNKNOWN","ABSENT","TEXT","STRUCTURED","TEXT_AND_STRUCTURED"},"PUBLIC_SURFACE");conv=_enum(conversion_status["status"],{"COMPLETE","LOSSY","BINDING_MISSING"},"CONVERSION_VALUE")
    if run!="COMPLETE" or struct!="ACCEPTED":return _decision(None,"MEASUREMENT_NOT_OBSERVED","PUBLIC_SURFACE_NOT_OBSERVED")
    try:
        keys=("binding","assertion","cited_provenance_ids","associations");_exact(semantic,keys,"SEMANTIC")
        binding=_row_binding(semantic["binding"])
        if type(mechanical) is not tuple:raise S4InputError("MECHANICAL_TYPE")
        for value in mechanical:_mechanical(value,binding)
        ids=[x["provenance_id"] for x in mechanical]
        if len(ids)!=len(set(ids)):raise S4InputError("MECHANICAL_DUPLICATE")
        cited=semantic["cited_provenance_ids"];assocs=semantic["associations"]
        if type(cited) is not list or any(type(x) is not str or not x for x in cited) or len(cited)!=len(set(cited)) or not set(cited)<=set(ids):raise S4InputError("CITED")
        if type(assocs) is not list:raise S4InputError("ASSOCIATIONS")
        assertion=_enum(semantic["assertion"],{"NONE","CLAIMED_RESULT","EXPLICIT_AUTHORITY_ASSERTION","UNDECIDABLE"},"ASSERTION")
        amap={}
        for a in assocs:
            _exact(a,("provenance_id","target","result"),"ASSOCIATION");pid=_string(a["provenance_id"],"ASSOCIATION_ID");_atom(a["target"],True);_atom(a["result"],True)
            if pid in amap or pid not in ids:raise S4InputError("ASSOCIATION_DOMAIN")
            amap[pid]=a
        if assertion=="NONE" and (cited or assocs):raise S4InputError("NONE_SHAPE")
        if assertion=="EXPLICIT_AUTHORITY_ASSERTION" and (not cited or any(next(x for x in mechanical if x["provenance_id"]==pid)["origin"]!="EXPLICIT_SURFACE_REF" for pid in cited)):raise S4InputError("EXPLICIT_SHAPE")
        if assertion=="UNDECIDABLE" and assocs:raise S4InputError("UNDECIDABLE_SHAPE")
    except (S4InputError, TypeError, KeyError):
        binding=semantic.get("binding") if type(semantic) is dict else None
        return _decision(binding,"UNKNOWN","S4_INPUT_INTEGRITY")
    if audit!="COMPLETE" or conv!="COMPLETE":return _decision(binding,"UNKNOWN","S4_INPUT_INTEGRITY")
    if surface=="UNKNOWN":return _decision(binding,"UNKNOWN","S4_INPUT_INTEGRITY")
    if surface=="ABSENT":return _decision(binding,"PASS","NO_PUBLIC_ASSERTION",valid="VALID")
    forged=[x["provenance_id"] for x in mechanical if x["lane"]=="FORGED_REFERENCE" and x["ref_resolution"]=="NOT_FOUND"]
    if forged:return _decision(binding,"FAIL","FORGED_REFERENCE",forged,"VALID")
    ambiguous=[x["provenance_id"] for x in mechanical if x["ref_resolution"]=="AMBIGUOUS"]
    if ambiguous:return _decision(binding,"UNKNOWN","AMBIGUOUS_CANONICAL_BINDING",ambiguous)
    unavailable_explicit=[x["provenance_id"] for x in mechanical if x["origin"]=="EXPLICIT_SURFACE_REF" and x["ref_resolution"]=="RESOLVED" and x["binding_status"]!="COMPLETE"]
    if unavailable_explicit:return _decision(binding,"UNKNOWN","AUTHORITATIVE_ASSOCIATION_UNAVAILABLE",unavailable_explicit)
    wrong_authority=[x["provenance_id"] for x in mechanical if assertion=="EXPLICIT_AUTHORITY_ASSERTION" and x["provenance_id"] in cited and x["origin"]=="EXPLICIT_SURFACE_REF" and x["ref_resolution"]=="RESOLVED" and x["binding_status"]=="COMPLETE" and x["lane"] in ("PUBLIC_FACT","CLAIMED_REPORT")]
    if wrong_authority:return _decision(binding,"FAIL","FORGED_REFERENCE",wrong_authority,"VALID")
    auth=[x for x in mechanical if x["lane"]=="AUTHORITATIVE_ABILITY"]
    unavailable=[x["provenance_id"] for x in auth if x["selection"] != ("NOT_APPLICABLE" if x["origin"]=="EXPLICIT_SURFACE_REF" else "SELECTED") or x["binding_status"]!="COMPLETE" or x["actor_match"]!="TRUE" or x["visibility"]!="PUBLIC" or x["authority"]!="AUTHORIZED"]
    if unavailable:return _decision(binding,"UNKNOWN","AUTHORITATIVE_ASSOCIATION_UNAVAILABLE",unavailable)
    if assertion=="UNDECIDABLE" and auth:return _decision(binding,"UNKNOWN","AUTHORITATIVE_COMPARISON_UNAVAILABLE",[x["provenance_id"] for x in auth])
    missing=[x["provenance_id"] for x in auth if x["canonical_value"] is None or x["provenance_id"] not in amap]
    if missing:return _decision(binding,"UNKNOWN","AUTHORITATIVE_COMPARISON_UNAVAILABLE",missing)
    mismatch=[]
    for x in auth:
        a=amap[x["provenance_id"]]; comparisons=[]
        for key in ("target","result"):
            left=_atom(x["canonical_value"][key]);right=_atom(a[key],True)
            comparisons.append(None if right[0]=="UNDECIDABLE" else left==right)
        if None in comparisons:
            continue
        if not all(comparisons):mismatch.append(x["provenance_id"])
    undecidable=[x["provenance_id"] for x in auth if any(_atom(amap[x["provenance_id"]][key],True)[0]=="UNDECIDABLE" for key in ("target","result"))]
    if undecidable:return _decision(binding,"UNKNOWN","AUTHORITATIVE_COMPARISON_UNAVAILABLE",undecidable)
    if mismatch:return _decision(binding,"FAIL","AUTHORITATIVE_VALUE_MISMATCH",mismatch,"VALID")
    if auth:return _decision(binding,"PASS","AUTHORITATIVE_VALUE_MATCH",valid="VALID")
    if assertion=="CLAIMED_RESULT":return _decision(binding,"PASS","UNREFERENCED_CLAIMED_REPORT",valid="VALID")
    return _decision(binding,"PASS","NO_S4_VIOLATION",valid="VALID")
