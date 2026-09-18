"""One bounded lease allocator for candidate and rollback downloads.

Caller holds campaign and device row locks; returned changes commit in that transaction.
"""
from datetime import timedelta

def allocate(targets, devices, stamp, *, rollback=False, paused=False, deadline=None, capacity=5):
    pending=[]
    for target in targets:
        generation=target.rollback_generation if rollback else target.assigned_generation
        device=devices[target.device_id]
        matching=generation is not None and device.applied_generation==generation
        if rollback:
            matching=matching and device.actual_release_id==target.previous_release_id and device.actual_config_version_id==target.previous_config_version_id
        else:
            matching=matching and device.actual_release_id==device.desired_release_id and device.actual_config_version_id==target.assigned_config_version_id
        fresh=device.last_heartbeat_at is not None and (stamp-device.last_heartbeat_at).total_seconds()<=30
        if generation is None or matching or not fresh:
            target.permit_expires_at=None
            continue
        pending.append(target)
    active=[]
    for target in pending:
        if target.permit_expires_at and target.permit_expires_at>stamp:
            active.append(target)
        else:target.permit_expires_at=None
    # Repair an old release's over-allocation deterministically, including rollback leases.
    for target in active[capacity:]:target.permit_expires_at=None
    active=active[:capacity]
    for target in active:
        device=devices[target.device_id]
        if device.agent_state=='fetching' and (deadline is None or stamp<deadline):
            target.permit_expires_at=min(stamp+timedelta(minutes=10),deadline) if deadline else stamp+timedelta(minutes=10)
    if not paused and (deadline is None or stamp<deadline):
        for target in pending:
            if len(active)>=capacity:break
            if target.permit_expires_at is None:
                target.permit_expires_at=min(stamp+timedelta(minutes=10),deadline) if deadline else stamp+timedelta(minutes=10)
                active.append(target)
    return len(active)
