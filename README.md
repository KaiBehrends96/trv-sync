# TRV Sync

In-house Home Assistant custom integration: one virtual room thermostat per
room that calibrates the real thermostatic radiator valves (TRVZB) with their
local temperature offset register and syncs setpoints. It replaces Better
Thermostat, which oscillated and lost state on restarts in this HA 2026.10.x
environment.

## Design (why it is small on purpose)

- The control loop stays **in the TRV firmware**. trv_sync only calibrates
  sensors and syncs setpoints, so HA being offline never stops room control:
  the TRVs keep running with the last written offset and setpoint registers.
- No weather, no window logic, no learning, no external services. All decision
  math is in one pure module (`logic.py`), covered by offline unit tests.
- Restart restoration: the room target is re-derived as the **median of the
  TRV setpoints**; per-TRV write bookkeeping is RAM only. Known tradeoff: a
  TRV deliberately holding a setpoint against the room target gets clobbered
  back to the median after a restart.

## Calibration principle

A TRV's *displayed* internal temperature already includes its calibration
offset (displayed = raw sensor + offset). trv_sync corrects as a delta:

    new_offset = current_offset + (room_temp + trim - displayed)

Working against the displayed value is what keeps the loop from
double-counting (recomputing from raw re-applies the same correction twice;
that is the failing pattern BT 1.9.3 showed here, swinging ±12 K and writing
dozens of offsets per minute).

## Anti-oscillation guards (per room, defaults shown)

| Guard | Default | Effect |
| --- | --- | --- |
| deadband | 0.3 K | no offset write below this delta |
| write rate limit | 10 min | min time between offset writes per TRV |
| quantize | 0.1 | offsets land on the register grid |
| staleness | 30 min | temperature older than this is not a control input |
| poll interval | 60 s (pilot: 5 s) | decision cadence |
| push mute | 3 fails | a TRV that fails setpoint pushes stops getting them until its next observed change |

## Fallback when the external sensor is missing/stale

Offsets drift back to the neutral `trim` value (0 when untouched, once),
room temperature = mean of fresh TRV raw readings (displayed minus offset).
When the sensor returns, calibration resumes on the next writes under the
normal guards. Trim lives on an integration-owned number entity
(`trv_sync trim <room>`).

## Setpoint semantics

A setpoint change observed on any TRV (dial/physical) becomes the room target
and propagates to the other fresh TRVs. The same applies to changes on the
virtual climate. Devices running `auto` (device-side schedule) are forced
back to `heat`, so no schedule can fight the sync (TRVZB must run manual
mode; check at cutover).

## Naming / cutover

Pilot rooms register as `climate.trv_sync_<room>`. At each room's cutover the
old Better Thermostat virtual climate's registry row is removed first, then
this entity is renamed to the freed BT entity id (`climate.heizzimmer1`, ...).
Automations, dashboards and template sensors keep the old ids and keep
working without edits.

## Install (HACS custom integration or manual copy)

Copy `custom_components/trv_sync` into `/config/custom_components/`, restart
HA, then add one config entry per room (name, TRV climate entities, optional
external sensor, guards). Development repo:
`git.rudolf-beeer.duckdns.org/HomeLab/ha-trv-sync`.

## Status

1.0.3, 2026-10-23 (1.0.0 first release, 1.0.1/1.0.2 load-path fixes
caught at first bring-up, 1.0.3 deferred startup restore). Pilot room:
HeizZimmer1; its registry entities run as climate.heizzimer1 and
number.trv_sync_heizzimer1_trim, the Better Thermostat room entry stays
disabled as fallback (climate.heizzimer1_bt).

The working contract is versioned in the repo at
`docs/REQUIREMENTS.md`: numbered requirements R1-R20 with a per-requirement
implementation status, plus acceptance criteria AC1-AC6 (which double as the
pilot/release checklist). Check future changes against the R-numbers before
they land. The full planning record with the decisions and their reasons is
kept verbatim next to it: `docs/PLAN-2026-10-23-trv-sync.md`; on a conflict
the requirements document wins.