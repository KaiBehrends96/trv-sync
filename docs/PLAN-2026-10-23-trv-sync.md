# Plan: TRV-Sync, in-house virtual thermostat integration (BT replacement)

Status: final
Created: 2026-10-23

## Goal

Replace Better Thermostat with a small in-house Home Assistant custom integration.
One virtual climate per room. Per-TRV local calibration via the TRV's offset register
so the TRV's measured temperature matches the external room sensor. The control loop
stays entirely in the TRV firmware. Setpoints sync bidirectionally. HA or its Zigbee
stack going down must not affect room control (offsets and setpoints live in the TRV
registers).

## Requirements

Functional
- One virtual climate per room, controlling 1-2 real TRVs (config entry: climate entity list).
- Offset per TRV: `new_offset = current_offset + (room_temp - trv_displayed)`,
  quantized to 0.1, clamped to the TRVZB register range (read min/max from the ZHA number entity).
- The component only calibrates and syncs setpoints. No weather, no learning, no external services.
- Observed-setpoint-change semantics: a setpoint change seen on any TRV becomes the room
  target and propagates to the other fresh TRVs. Same effect as changing the virtual climate.
  No writer discrimination, no persisted override state. In-memory per-TRV last-observed values only.
- Fallback when the external sensor is missing, unavailable or stale: write offsets to 0
  (once), room temperature = mean of TRV internal temps, trim stays active. Single recompute
  when the external sensor returns.
- Missing/dead entities: skipped for both reads and writes; the remaining TRVs keep running.
  A returning TRV rejoins the sync on the next cycle automatically.
- Per-room manual trim offset: the integration itself creates one number entity per room
  (trv_sync.trim_<room>), steerable directly, default 0. No separate helper to create.
- Modes heat/off. off = TRV hvac off (setpoint registers untouched). heat = TRV hvac heat.
- Restart: room target = median of TRV setpoints. Known caveat (accepted): an intentionally
  off-TRV value against the room target is clobbered back to the median after a restart.
- No window-open logic, no device-side schedules fighting the setpoint sync: TRVZB must run
  in manual mode (cutover checklist item).

Robustness / anti-oscillation (all options, editable per room in the options flow)
- deadband 0.3 deg (no write below delta)
- write rate limit: 1 offset write per TRV per 10 min
- quantize to 0.1
- staleness threshold 30 min for sensor age (external and TRV internal both must be fresh)
- poll interval 60 s
- never compute offsets unless both sides are fresh; never write to an unavailable TRV
- write-failure or unavailable TRV never blocks the other TRVs
- guard log: sanity warning on long ext/trv disagreement, write counter per TRV per day

Offline
- No dependency on internet or cloud services at all.
- If HA is down mid-heat: TRVs keep their own loop with last written offset and setpoint.

## Decisions

- Vehicle: HA custom integration (config entry per room, climate platform, coordinator).
  Not AppDaemon, not pure automations, not pyscript, not a BT fork (tradeoffs from chat 23.10).
- Write path: existing ZHA number entities (local temperature offset) and climate services,
  same path BT used.
- Development: Gitea repo from the first commit, offline unit tests for pure helpers
  (offset math, clamping, quantization, median, staleness), coding-guidelines standard.
- Config values (deadband, rate limit, quantize, staleness, poll) are options, defaults above.
- Pilot room: HeizZimmer1. During pilot the BT entry HeizZimmer1 is disabled
  (both masters on the same TRVs would fight).
- Cutover per room: disable BT entry, watch pilot result, then migrate rooms one by one,
  BT entries removed afterwards, card/dashboard wiring updated.
- Naming (user decision 23.10, revised): pilot under temporary names (climate.trv_sync_<room>);
  at each room's cutover the orphaned BT virtual climate registry row is removed FIRST, then our
  entity is renamed to the freed BT entity_id (climate.heizzimmer1, climate.heizzimmer3, ...).
  Old automations, dashboard cards and template sensors referencing the climate.<room> ids
  keep working unchanged across the cutover.
- Schedules: none in v1 (user confirmation 23.10: no schedule needed; current house heating
  runs constant setpoints. The broken automation.heizung (unique_id 1706188021135) predates
  the BT era and stays an unrelated automation-repair item, not part of this plan).

## Out of scope (v1)

- Window-open handling (BT had it; TRVZB local detection stays the responsibility of the device).
- Schedules, presets, away mode, weather, EMA/learning, heating power estimation.
- UI card replacement: none needed. Cards and automations keep the climate.<room> ids via the
  rename-at-cutover strategy; during pilot the new trv_sync entities are simply not referenced.

## Acceptance criteria

- Unit tests green for pure helpers (offset math incl. double-count avoidance, clamp, quantize, median, staleness, override semantics).
- HeizZimmer1 pilot >= 24 h: no more than 1 offset write per TRV per hour in steady state
  (BT 1.9.3 wrote dozens per minute).
- Manual setpoint change on a TRV dial appears on the virtual climate within 2 polls and propagates to the other TRV.
- HA restart on the pilot room: room target survives (median restoration), no BT-style target wipe.
- External sensor pulled offline: offsets go to 0 once, fallback mean is used, no oscillation;
  sensor return: single recompute.
- Offline drill: HA stopped 10 min while heating, room holds target on TRV-local loop, control
  resumes cleanly after restart.

## Open questions

None (23.10.2026):
- Q1 answered: trim as integration-provided number entity per room.
- Q2 answered, then revised 23.10 (user): rename-at-cutover instead of permanent own names,
  so old automations, cards and template sensors keep the climate.<room> ids.
- Q3 answered: no schedules in v1 (none in current use; broken automation.heizung
  is unrelated).