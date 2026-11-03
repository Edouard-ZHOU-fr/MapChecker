"""Check 1: turn_direction (turn signal) of curves / turns"""

from .features import turn_direction_features
from .geometry import arc_length, centerline, fmt_xy, max_heading_change
from .lanelet_map import by_id

VALID_TURN_DIRECTIONS = {"left", "right", "straight"}


def check_turn_direction(lanelets, intersecting, opts, out, ctx, autoware=None, step=0.5):
    """
    intersecting: set of ids of lanelets intersecting other lanes (crossing / merge, diverge does not count)
      - curve: turn_direction should be set; if ignore_isolated_turns is True, isolated curves are ignored
      - intersecting straight lanelet: see straight_as_turn_light
    straight_as_turn_light: whether straight counts as a turn signal being set
      - True:  only check that turn_direction is set (left/right/straight all OK), not that the direction matches geometry;
               both curves and intersecting straight lanelets must have it set
      - False: check that the direction matches the geometry; a curve tagged straight counts as not set (warning);
               intersecting straight lanelets are not required to be tagged straight
    ctx: result of utils.features.lanelet_context(), used to compute the features of warnings
    autoware: turn_direction results of the Autoware check {lanelet id: conflicting lanelet / area ids}, None means disabled;
              lanelets already reported by the geometry check above are not output again, only those missed by the
              geometry check are added (e.g. diverge, very small overlap area)
    """
    turn_angle, window = opts["turn_angle"], opts["turn_window"]
    ignore_isolated, straight_ok = opts["ignore_isolated_turns"], opts["straight_as_turn_light"]

    out.header(f"Check 1: turn_direction on curves / turns (angle threshold {turn_angle:.0f}°, window {window:.0f} m)")
    n_turns = n_ignored = n_straight = n_autoware = n_autoware_only = 0
    out.hold()  # with a model, output in descending order of p
    for ll in sorted(lanelets.values(), key=by_id):
        td = ll.turn_direction
        if td is not None and td not in VALID_TURN_DIRECTIONS:
            out.warn("turn_direction", f"lanelet {ll.id}: turn_direction=\"{td}\" is not a valid value (left/right/straight)", (ll.id,))
            continue

        center = centerline(ll, step)
        angle = max_heading_change(center, step, window)
        msg = None

        if abs(angle) < turn_angle:
            # straight: only check those intersecting other lanes
            if ll.id in intersecting:
                n_straight += 1
                if td is None and straight_ok:
                    msg = "intersects other lanes without turn_direction (geometry straight, suggest turn_direction=straight)"
                elif td in ("left", "right") and not straight_ok:
                    msg = f"intersects other lanes, turn_direction={td} but geometry is straight"
        else:
            n_turns += 1
            geo = "left" if angle > 0 else "right"
            if ignore_isolated and ll.id not in intersecting:
                n_ignored += 1
            elif td is None:
                msg = f"curve without turn_direction (geometry turns {geo}, suggest turn_direction={geo})"
            elif straight_ok:
                pass  # only check whether it is set, not the direction
            elif td == "straight":
                msg = f"curve tagged turn_direction=straight, no turn signal (geometry turns {geo})"
            elif td != geo:
                msg = f"turn_direction={td} is opposite to geometry (turns {geo})"

        if autoware and ll.id in autoware:
            n_autoware += 1
            if msg is None:
                n_autoware_only += 1
                msg = f"conflicts with {', '.join(autoware[ll.id])} in Autoware routing graph without turn_direction"

        if msg:
            middle = center[len(center) // 2]
            where = f"middle {fmt_xy(middle)}, angle {angle:+.0f}°, length {arc_length(center)[-1]:.1f} m"
            out.warn("turn_direction", f"lanelet {ll.id}: {msg} | {where}", (ll.id,),
                     turn_direction_features(ll, center, angle, opts, ctx), middle)
    out.flush()

    if ignore_isolated:
        out.info(f"{n_turns} curved lanelets detected, {n_ignored} ignored (no crossing / merging with other lanes)")
    else:
        out.info(f"{n_turns} curved lanelets detected")
    out.info(f"{n_straight} straight lanelets intersect other lanes")
    if autoware is not None:
        out.info(f"{n_autoware} lanelets without turn_direction conflict with others in Autoware routing graph, "
                 f"{n_autoware_only} of them not detected by the geometry check above")
    if out.count["turn_direction"] == 0:
        out.ok("All checked lanelets have a correct turn_direction")
