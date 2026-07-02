"""Deterministic SIMOD JSON -> SimuBridge baseline scenario builder.

The second-LLM pipeline keeps the SIMOD-discovered scenario as the
source of truth for every unchanged parameter. The LLM only produces a
:class:`~second_llm.output_schema_patch.ScenarioPatch`; this module
produces the *baseline* SimuBridge scenario that the patch is applied
on top of.

Scope
-----
Covers the SIMOD v5/v6 JSON shape produced by the integrated runner
(``resource_profiles``, ``resource_calendars``, ``task_resource_distributions``,
``arrival_time_distribution``, ``gateway_branching_probabilities``).
When fields are missing or in an unexpected shape the builder falls
back to safe defaults and records a note so the caller can see which
parts of the baseline were inferred.

Not covered
-----------
BPMN XML is carried through verbatim as an opaque string (the
``BPMN`` field of each :class:`ProcessModel`). SimuBridge expects the
LLM's scenario body to reference BPMN element IDs, which SIMOD already
produces — we do not re-derive them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from second_llm.output_schema import (
    Activity,
    DistributionParameter,
    DistributionType,
    Gateway,
    ModelParameter,
    ProcessModel,
    Resource,
    ResourceParameters,
    Role,
    SimuBridgeScenario,
    StartEvent,
    TimeDistribution,
    TimeUnit,
    Timetable,
    TimetableItem,
    Weekday,
)


# -------------------------------------------------------------------
# Result container
# -------------------------------------------------------------------

@dataclass
class BaselineBuildResult:
    """Outcome of a SIMOD -> SimuBridgeScenario build attempt."""

    scenario: SimuBridgeScenario | None = None
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.scenario is not None and not self.errors


# -------------------------------------------------------------------
# Parsing helpers
# -------------------------------------------------------------------

_SIMOD_DAY_MAP: dict[str, Weekday] = {
    "MONDAY": Weekday.MONDAY,
    "TUESDAY": Weekday.TUESDAY,
    "WEDNESDAY": Weekday.WEDNESDAY,
    "THURSDAY": Weekday.THURSDAY,
    "FRIDAY": Weekday.FRIDAY,
    "SATURDAY": Weekday.SATURDAY,
    "SUNDAY": Weekday.SUNDAY,
}


def _parse_hour_of_day(value: Any) -> int:
    """Parse SIMOD time strings (``09:00:00.000``, ``17:30``, ``9``, ``9.5``)
    into an integer hour in [0, 24]."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        h = float(value)
    elif isinstance(value, str):
        parts = value.strip().split(":")
        try:
            h = float(parts[0]) + (float(parts[1]) / 60.0 if len(parts) > 1 else 0.0)
        except (ValueError, IndexError):
            return 0
    else:
        return 0
    return max(0, min(24, int(round(h))))


def _as_list_of_dicts(value: Any, id_key: str = "id") -> list[dict[str, Any]]:
    """Normalise SIMOD fields that are sometimes dicts, sometimes lists.

    Returns ``[{id_key: k, **v}, ...]`` for dict-of-dicts and the original
    list for lists. Entries that are not dicts are dropped.
    """
    if isinstance(value, dict):
        return [
            {id_key: k, **v}
            for k, v in value.items()
            if isinstance(v, dict)
        ]
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    return []


# -------------------------------------------------------------------
# Distribution extraction
# -------------------------------------------------------------------

_SIMOD_DIST_ALIASES: dict[str, DistributionType] = {
    "exponential": DistributionType.EXPONENTIAL,
    "expon": DistributionType.EXPONENTIAL,
    "norm": DistributionType.NORMAL,
    "normal": DistributionType.NORMAL,
    "gauss": DistributionType.NORMAL,
    "uniform": DistributionType.UNIFORM,
    "uni": DistributionType.UNIFORM,
    "constant": DistributionType.CONSTANT,
    "fix": DistributionType.CONSTANT,
    "fixed": DistributionType.CONSTANT,
    "erlang": DistributionType.ERLANG,
    "gamma": DistributionType.ERLANG,
    "triangular": DistributionType.TRIANGULAR,
    "tri": DistributionType.TRIANGULAR,
}


def _parse_time_unit(raw: Any) -> TimeUnit:
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in ("sec", "secs", "second", "seconds"):
            return TimeUnit.SECONDS
        if low in ("hour", "hours", "hrs", "h"):
            return TimeUnit.HOURS
        if low in ("min", "mins", "minute", "minutes"):
            return TimeUnit.MINUTES
    # SIMOD stores all durations in seconds when no explicit unit field is present
    return TimeUnit.SECONDS


def _parse_distribution(
    raw: dict[str, Any] | None,
    default_mean_seconds: float = 60.0,
) -> TimeDistribution:
    """Convert a SIMOD distribution dict into a ``TimeDistribution``.

    SIMOD distributions commonly look like
    ``{"distribution_name": "expon", "distribution_params": [{"value": 10}, ...]}``
    or ``{"dname": "norm", "mean": 10, "sd": 2}``.
    """
    if not isinstance(raw, dict):
        return TimeDistribution(
            distributionType=DistributionType.CONSTANT,
            timeUnit=TimeUnit.SECONDS,
            values=[DistributionParameter(id="constantValue", value=default_mean_seconds)],
        )

    name_raw = (
        raw.get("distribution_name")
        or raw.get("dname")
        or raw.get("name")
        or raw.get("type")
        or "exponential"
    )
    dist_type = _SIMOD_DIST_ALIASES.get(
        str(name_raw).strip().lower(), DistributionType.EXPONENTIAL,
    )
    time_unit = _parse_time_unit(raw.get("time_unit") or raw.get("unit"))

    # SIMOD's ``distribution_params`` is a list of ``{"value": x}``
    # positional entries — their meaning depends on the distribution.
    values: list[DistributionParameter] = []
    params_list = raw.get("distribution_params")
    if isinstance(params_list, list):
        positional = [p.get("value") for p in params_list if isinstance(p, dict)]
        values = _positional_to_named(dist_type, positional)

    # Alternative: named scalar fields on the dict itself.
    if not values:
        values = _named_fields_to_params(dist_type, raw, default_mean_seconds)

    if not values:
        values = [DistributionParameter(id="mean", value=default_mean_seconds)]

    try:
        return TimeDistribution(
            distributionType=dist_type,
            timeUnit=time_unit,
            values=values,
        )
    except Exception:
        # Fall back to a safe constant if the distribution cannot be
        # validated (e.g. missing required param).
        return TimeDistribution(
            distributionType=DistributionType.CONSTANT,
            timeUnit=time_unit,
            values=[DistributionParameter(id="constantValue", value=default_mean_seconds)],
        )


def _positional_to_named(
    dist_type: DistributionType,
    positional: list[Any],
) -> list[DistributionParameter]:
    """Interpret SIMOD's positional distribution_params list."""

    def _f(i: int) -> float | None:
        if i < len(positional) and positional[i] is not None:
            try:
                return float(positional[i])
            except (TypeError, ValueError):
                return None
        return None

    if dist_type == DistributionType.EXPONENTIAL:
        m = _f(0)
        return [DistributionParameter(id="mean", value=m)] if m is not None else []
    if dist_type == DistributionType.NORMAL:
        m = _f(0)
        v = _f(1)
        out: list[DistributionParameter] = []
        if m is not None:
            out.append(DistributionParameter(id="mean", value=m))
        if v is not None:
            # SIMOD often stores standard deviation; the Pydantic model
            # normalises "std" -> "variance" via its aliases so we store
            # the raw numeric value under "variance" here.
            out.append(DistributionParameter(id="variance", value=v))
        return out
    if dist_type == DistributionType.UNIFORM:
        lo = _f(0)
        hi = _f(1)
        out = []
        if lo is not None:
            out.append(DistributionParameter(id="lower", value=lo))
        if hi is not None:
            out.append(DistributionParameter(id="upper", value=hi))
        return out
    if dist_type == DistributionType.CONSTANT:
        v = _f(0)
        return [DistributionParameter(id="constantValue", value=v)] if v is not None else []
    if dist_type == DistributionType.ERLANG:
        # SIMOD "gamma": first param mean, second variance. Pydantic
        # normalisation turns that into erlang(order, mean).
        m = _f(0)
        v = _f(1)
        out = []
        if m is not None:
            out.append(DistributionParameter(id="mean", value=m))
        if v is not None:
            out.append(DistributionParameter(id="variance", value=v))
        return out
    if dist_type == DistributionType.TRIANGULAR:
        lo = _f(0)
        pk = _f(1)
        hi = _f(2)
        out = []
        if lo is not None:
            out.append(DistributionParameter(id="lower", value=lo))
        if pk is not None:
            out.append(DistributionParameter(id="peak", value=pk))
        if hi is not None:
            out.append(DistributionParameter(id="upper", value=hi))
        return out
    return []


def _named_fields_to_params(
    dist_type: DistributionType,
    raw: dict[str, Any],
    default_mean: float,
) -> list[DistributionParameter]:
    """Fallback path: read mean/sd/min/max directly off the SIMOD dict."""
    mean = raw.get("mean")
    sd = raw.get("sd") or raw.get("std") or raw.get("stddev") or raw.get("variance")
    lo = raw.get("min") or raw.get("lower") or raw.get("lo")
    hi = raw.get("max") or raw.get("upper") or raw.get("hi")

    out: list[DistributionParameter] = []
    if dist_type in (DistributionType.EXPONENTIAL,) and mean is not None:
        out.append(DistributionParameter(id="mean", value=float(mean)))
    elif dist_type == DistributionType.NORMAL:
        if mean is not None:
            out.append(DistributionParameter(id="mean", value=float(mean)))
        if sd is not None:
            out.append(DistributionParameter(id="variance", value=float(sd)))
    elif dist_type == DistributionType.UNIFORM:
        if lo is not None:
            out.append(DistributionParameter(id="lower", value=float(lo)))
        if hi is not None:
            out.append(DistributionParameter(id="upper", value=float(hi)))
    elif dist_type == DistributionType.CONSTANT:
        val = raw.get("value") or raw.get("constantValue") or mean or default_mean
        out.append(DistributionParameter(id="constantValue", value=float(val)))
    return out


# -------------------------------------------------------------------
# Calendar / timetable extraction
# -------------------------------------------------------------------

def _build_timetable(cal: dict[str, Any]) -> Timetable | None:
    """Build a :class:`Timetable` from one SIMOD calendar dict."""
    cal_id = cal.get("id") or cal.get("name")
    if not cal_id:
        return None

    periods = cal.get("time_periods") or cal.get("timePeriods") or []
    if not isinstance(periods, list) or not periods:
        return None

    items: list[TimetableItem] = []
    for p in periods:
        if not isinstance(p, dict):
            continue
        from_day = str(p.get("from", "")).upper()
        to_day = str(p.get("to", from_day)).upper()
        start_wd = _SIMOD_DAY_MAP.get(from_day)
        end_wd = _SIMOD_DAY_MAP.get(to_day) or start_wd
        if start_wd is None or end_wd is None:
            continue
        start_h = _parse_hour_of_day(p.get("beginTime") or p.get("begin_time"))
        end_h = _parse_hour_of_day(p.get("endTime") or p.get("end_time"))
        if end_h <= start_h:
            continue
        items.append(TimetableItem(
            startWeekday=start_wd,
            endWeekday=end_wd,
            startTime=start_h,
            endTime=end_h,
        ))

    if not items:
        return None
    return Timetable(id=str(cal_id), timeTableItems=items)


# -------------------------------------------------------------------
# Resource profile extraction
# -------------------------------------------------------------------

def _build_roles_and_resources(
    profiles: list[dict[str, Any]],
    timetables: dict[str, Timetable],
    default_tt_id: str,
) -> tuple[list[Role], list[Resource], list[str]]:
    """Build :class:`Role` + :class:`Resource` lists from SIMOD resource profiles."""
    roles: list[Role] = []
    resources: list[Resource] = []
    notes: list[str] = []

    seen_role_ids: set[str] = set()
    seen_resource_ids: set[str] = set()

    for prof in profiles:
        role_id = prof.get("name") or prof.get("id")
        if not role_id:
            continue
        role_id = str(role_id)
        if role_id in seen_role_ids:
            continue
        seen_role_ids.add(role_id)

        res_list_raw = prof.get("resource_list") or prof.get("resources") or []
        res_ids: list[str] = []
        pool_cost = 0.0
        pool_calendar: str | None = None

        if isinstance(res_list_raw, list) and res_list_raw:
            for r in res_list_raw:
                if not isinstance(r, dict):
                    continue
                rid = r.get("id") or r.get("name")
                if not rid:
                    continue
                rid = str(rid)
                res_ids.append(rid)
                if rid not in seen_resource_ids:
                    resources.append(Resource(id=rid))
                    seen_resource_ids.add(rid)
                cost = r.get("cost_per_hour") or r.get("costHour")
                if cost is not None:
                    try:
                        pool_cost = max(pool_cost, float(cost))
                    except (TypeError, ValueError):
                        pass
                cal = r.get("calendar") or r.get("calendar_id")
                if cal and str(cal) in timetables:
                    pool_calendar = str(cal)

        # Profile-level overrides
        top_cost = prof.get("cost_per_hour") or prof.get("costHour")
        if top_cost is not None:
            try:
                pool_cost = max(pool_cost, float(top_cost))
            except (TypeError, ValueError):
                pass
        top_cal = prof.get("calendar") or prof.get("calendar_id")
        if top_cal and str(top_cal) in timetables:
            pool_calendar = str(top_cal)

        if not res_ids:
            default_res_id = f"{role_id}_1"
            res_ids = [default_res_id]
            if default_res_id not in seen_resource_ids:
                resources.append(Resource(id=default_res_id))
                seen_resource_ids.add(default_res_id)
            notes.append(
                f"Role '{role_id}' had no resource list in SIMOD output; "
                f"synthesised 1 resource."
            )

        if pool_calendar is None:
            pool_calendar = default_tt_id
            notes.append(
                f"Role '{role_id}' had no calendar reference; bound to "
                f"'{default_tt_id}'."
            )

        roles.append(Role(
            id=role_id,
            schedule=pool_calendar,
            costHour=pool_cost,
            resources=[Resource(id=rid) for rid in res_ids],
        ))

    return roles, resources, notes


# -------------------------------------------------------------------
# Activity / gateway / start event extraction
# -------------------------------------------------------------------

def _parse_bpmn_names(bpmn_xml: str) -> dict[str, str]:
    """Extract node_id → human-readable name from BPMN XML.

    Returns empty dict when bpmn_xml is empty or unparseable.
    The map is used to set Activity.name to the process label so the
    LLM can reference activities by human name in its modifications.
    """
    if not bpmn_xml:
        return {}
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(bpmn_xml)
        name_map: dict[str, str] = {}
        # BPMN uses namespace prefixes; strip them for robustness
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag in ("task", "userTask", "serviceTask", "manualTask",
                       "scriptTask", "sendTask", "receiveTask",
                       "businessRuleTask", "subProcess", "callActivity",
                       "startEvent", "endEvent", "exclusiveGateway",
                       "parallelGateway", "inclusiveGateway", "eventBasedGateway"):
                elem_id = elem.get("id") or ""
                elem_name = (elem.get("name") or "").strip()
                if elem_id and elem_name:
                    name_map[elem_id] = elem_name
        return name_map
    except Exception:
        return {}


def build_flow_name_map(bpmn_xml: str) -> dict[str, str]:
    """Map sequence flow ID → '→ TargetName' for human-readable warnings.

    Used by the merger to label gateway outgoing paths by their target
    activity name instead of raw BPMN node IDs.
    """
    if not bpmn_xml:
        return {}
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(bpmn_xml)
    except Exception:
        return {}

    id_to_name: dict[str, str] = {}
    for elem in root.iter():
        eid = elem.attrib.get("id")
        name = (elem.attrib.get("name") or "").strip()
        if eid and name:
            id_to_name[eid] = name

    flow_map: dict[str, str] = {}
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag == "sequenceFlow":
            fid = elem.attrib.get("id")
            tgt = elem.attrib.get("targetRef")
            if fid and tgt:
                tgt_name = id_to_name.get(tgt, tgt)
                flow_map[fid] = f"→ {tgt_name}"
    return flow_map


def _build_activities(
    task_dists: list[dict[str, Any]],
    roles_index: dict[str, Role],
    bpmn_name_map: dict[str, str] | None = None,
) -> tuple[list[Activity], list[str]]:
    """Produce :class:`Activity` entries from SIMOD task_resource_distributions."""
    out: list[Activity] = []
    notes: list[str] = []
    role_ids = set(roles_index.keys())
    default_role = next(iter(role_ids), None)

    # Build resource_id → role_id reverse map.
    # SIMOD task_resource_distributions references individual resource IDs
    # (e.g. "Alberto Duport") while role IDs carry the "_profile" suffix
    # (e.g. "Alberto Duport_profile").  The reverse map resolves both.
    resource_to_role: dict[str, str] = {}
    for role in roles_index.values():
        for res in (role.resources or []):
            res_id = getattr(res, "id", None) or getattr(res, "name", None)
            if res_id:
                resource_to_role[str(res_id).lower()] = role.id

    def _resolve_role_ref(ref: str) -> str | None:
        """Return the matching role_id for a raw SIMOD resource/role reference."""
        # 1. Direct match (role named exactly as given)
        if ref in role_ids:
            return ref
        # 2. SIMOD "_profile" suffix convention
        suffixed = ref + "_profile"
        if suffixed in role_ids:
            return suffixed
        # 3. Individual resource → parent role reverse lookup
        return resource_to_role.get(ref.lower())

    for td in task_dists:
        task_id = td.get("task_id") or td.get("id")
        if not task_id:
            continue
        task_id = str(task_id)
        # Prefer human-readable BPMN label over UUID fallback
        name = str(
            (bpmn_name_map or {}).get(task_id)
            or td.get("task_name")
            or td.get("name")
            or task_id
        )

        # SIMOD stores one entry per (task, resource). Collapse them
        # into a single Activity whose duration is taken from the first
        # resource and whose resource list is the union of eligible roles.
        resource_entries = td.get("resources") or []
        picked_duration: dict[str, Any] | None = None
        eligible_roles: list[str] = []

        if isinstance(resource_entries, list):
            for re in resource_entries:
                if not isinstance(re, dict):
                    continue
                role_ref = (
                    re.get("resource_id") or re.get("resource") or re.get("role")
                )
                if role_ref:
                    resolved = _resolve_role_ref(str(role_ref))
                    if resolved:
                        eligible_roles.append(resolved)
                if picked_duration is None:
                    dist = (
                        re.get("duration_distribution")
                        or re.get("distribution")
                    )
                    if not isinstance(dist, dict) and re.get("distribution_name"):
                        # Flat format: fields are directly on the resource entry
                        dist = {k: re[k] for k in re
                                if k not in ("resource_id", "resource", "role")}
                    if isinstance(dist, dict):
                        picked_duration = dist

        # Some SIMOD variants put the distribution directly on the
        # activity record.
        if picked_duration is None:
            for k in ("duration", "duration_distribution", "distribution"):
                if isinstance(td.get(k), dict):
                    picked_duration = td[k]
                    break

        duration = _parse_distribution(picked_duration)

        if not eligible_roles:
            if default_role is None:
                # Skip activities we cannot assign any role to.
                notes.append(
                    f"Activity '{name}' had no eligible roles and no "
                    f"fallback role — skipped."
                )
                continue
            eligible_roles = [default_role]
            notes.append(
                f"Activity '{name}' had no eligible roles; bound to "
                f"fallback role '{default_role}'."
            )

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_roles = []
        for r in eligible_roles:
            if r not in seen:
                seen.add(r)
                unique_roles.append(r)

        out.append(Activity(
            id=task_id,
            name=name,
            resources=unique_roles,
            cost=0.0,
            duration=duration,
        ))

    return out, notes


def _build_gateways(gw_probs: list[dict[str, Any]]) -> list[Gateway]:
    """Produce :class:`Gateway` entries from SIMOD gateway_branching_probabilities."""
    out: list[Gateway] = []
    for gw in gw_probs:
        gw_id = gw.get("gateway_id") or gw.get("id")
        if not gw_id:
            continue
        probs_raw = gw.get("probabilities") or gw.get("outgoing") or {}
        probs: dict[str, float] = {}
        if isinstance(probs_raw, dict):
            for k, v in probs_raw.items():
                try:
                    probs[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
        elif isinstance(probs_raw, list):
            for entry in probs_raw:
                if not isinstance(entry, dict):
                    continue
                sf = entry.get("path_id") or entry.get("flow") or entry.get("id")
                val = entry.get("value") or entry.get("probability")
                if sf is None or val is None:
                    continue
                try:
                    probs[str(sf)] = float(val)
                except (TypeError, ValueError):
                    continue

        if not probs:
            continue
        # Normalise if sum is noticeably off (common from SIMOD rounding).
        total = sum(probs.values())
        if total > 0 and abs(total - 1.0) > 0.02:
            probs = {k: v / total for k, v in probs.items()}

        try:
            out.append(Gateway(
                id=str(gw_id),
                name=str(gw.get("name") or gw_id),
                probabilities=probs,
            ))
        except Exception:
            # Skip gateways that still fail model validation after
            # normalisation — they will stay out of the baseline.
            continue
    return out


def _build_start_events(
    arrival: dict[str, Any] | None,
    start_event_id: str = "StartEvent_1",
) -> list[StartEvent]:
    """Produce a single :class:`StartEvent` from the SIMOD arrival distribution."""
    if not isinstance(arrival, dict):
        arrival = {"distribution_name": "exponential", "distribution_params": [{"value": 1.0}]}
    return [
        StartEvent(
            id=start_event_id,
            interArrivalTime=_parse_distribution(arrival, default_mean_seconds=3600.0),
        ),
    ]


# -------------------------------------------------------------------
# Top-level builder
# -------------------------------------------------------------------

_DEFAULT_TT = Timetable(
    id="default_24_7",
    timeTableItems=[
        TimetableItem(
            startWeekday=Weekday.MONDAY,
            endWeekday=Weekday.SUNDAY,
            startTime=0,
            endTime=24,
        ),
    ],
)


def build_baseline_scenario(
    simod_dict: dict[str, Any] | None,
    *,
    scenario_name: str = "Baseline (from SIMOD)",
    process_name: str = "BaselineProcess",
    number_of_instances: int = 1000,
    bpmn_xml: str = "",
) -> BaselineBuildResult:
    """Deterministically convert a SIMOD JSON dict into a SimuBridge baseline.

    Parameters
    ----------
    simod_dict:
        Parsed SIMOD simulation-parameter JSON.
    scenario_name, process_name, number_of_instances:
        Scenario-level metadata fields for the generated SimuBridgeScenario.
    bpmn_xml:
        Optional BPMN XML to carry through on the ProcessModel.

    Returns
    -------
    BaselineBuildResult
        ``scenario`` is the built :class:`SimuBridgeScenario` on success,
        otherwise ``None``. ``notes`` explains inferred defaults and
        ``errors`` is non-empty only when the SIMOD shape could not be
        interpreted at all.
    """
    result = BaselineBuildResult()

    if not isinstance(simod_dict, dict):
        result.errors.append("SIMOD input is not a JSON dict; cannot build baseline.")
        return result

    # --- Calendars ---
    cal_entries = _as_list_of_dicts(
        simod_dict.get("resource_calendars") or simod_dict.get("calendars") or [],
    )
    timetables: dict[str, Timetable] = {}
    for cal in cal_entries:
        tt = _build_timetable(cal)
        if tt is not None:
            timetables[tt.id] = tt

    if not timetables:
        timetables[_DEFAULT_TT.id] = _DEFAULT_TT
        result.notes.append(
            "No valid resource_calendars found in SIMOD output — falling back to 24/7 default timetable."
        )
    default_tt_id = next(iter(timetables.keys()))

    # --- Resource profiles ---
    profile_entries = _as_list_of_dicts(
        simod_dict.get("resource_profiles") or [],
        id_key="name",
    )
    roles, global_resources, role_notes = _build_roles_and_resources(
        profile_entries, timetables, default_tt_id,
    )
    result.notes.extend(role_notes)

    if not roles:
        # Synthesise a single default role so the scenario is minimally valid.
        default_role = Role(
            id="DefaultRole",
            schedule=default_tt_id,
            costHour=0.0,
            resources=[Resource(id="DefaultRole_1")],
        )
        roles.append(default_role)
        global_resources.append(Resource(id="DefaultRole_1"))
        result.notes.append(
            "No resource_profiles found in SIMOD output — synthesised 'DefaultRole'."
        )

    try:
        resource_parameters = ResourceParameters(
            roles=roles,
            resources=global_resources,
            timeTables=list(timetables.values()),
        )
    except Exception as exc:
        result.errors.append(f"Failed to assemble ResourceParameters: {exc}")
        return result

    # --- Activities ---
    roles_index = {r.id: r for r in roles}
    task_dists = _as_list_of_dicts(
        simod_dict.get("task_resource_distributions")
        or simod_dict.get("task_resource_distribution")
        or [],
    )
    bpmn_name_map = _parse_bpmn_names(bpmn_xml)
    activities, act_notes = _build_activities(task_dists, roles_index, bpmn_name_map)
    result.notes.extend(act_notes)

    # --- Gateways ---
    gw_entries = _as_list_of_dicts(
        simod_dict.get("gateway_branching_probabilities")
        or simod_dict.get("gateway_branching_probability")
        or [],
        id_key="gateway_id",
    )
    gateways = _build_gateways(gw_entries)

    # --- Start event ---
    events = _build_start_events(
        simod_dict.get("arrival_time_distribution")
        or simod_dict.get("case_arrival_distribution"),
    )

    model_parameter = ModelParameter(
        activities=activities,
        gateways=gateways,
        events=events,
    )
    process_model = ProcessModel(
        name=process_name,
        modelParameter=model_parameter,
        BPMN=bpmn_xml or "<carried over from SIMOD>",
    )

    try:
        scenario = SimuBridgeScenario(
            scenarioName=scenario_name,
            numberOfInstances=number_of_instances,
            resourceParameters=resource_parameters,
            models=[process_model],
        )
    except Exception as exc:
        result.errors.append(f"Failed to assemble SimuBridgeScenario: {exc}")
        return result

    result.scenario = scenario
    return result
