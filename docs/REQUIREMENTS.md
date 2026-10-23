# TRV Sync requirements

The numbered requirements are the working contract. Every change to this
integration gets checked against them before it lands; the acceptance
criteria double as the release checklist. The full planning record with the
decisions and their reasons is kept verbatim next to this file:
`docs/PLAN-2026-10-23-trv-sync.md`. When code, README and this document
disagree, this document wins and the fix goes to the loser.

Status marks refer to v1.0.1 on the pilot room HeizZimmer1.

## Functional

- **R1** One virtual climate per room, controlling 1-2 real TRVs. Room config
  entry: name, list of TRV climate entities, optional external room sensor.
  `Status: implemented.`
- **R2** Offset per TRV:
  `new_offset = current_offset + (room_temp + trim - trv_displayed)`,
  quantized to the 0.1 grid, clamped to the TRVZB register range read from
  the ZHA number entity's min/max. (The plan's original formula shows the
  no-trim case; trim is the same formula with trim = 0.) `Status: implemented.`
- **R3** The component only calibrates and syncs setpoints. No weather, no
  learning, no window logic, no external services. `Status: implemented.`
- **R4** Observed-setpoint-change semantics: a setpoint change seen on any TRV
  becomes the room target and propagates to the other fresh TRVs, with the
  same effect as changing the virtual climate. No writer discrimination, no
  persisted override state; in-memory per-TRV last-observed values only, so
  the first poll after a restart adopts nothing. `Status: implemented.`
- **R5** Fallback when the external sensor is missing, unavailable or stale:
  offsets write to the neutral value once (the trim, 0 when untouched),
  room temperature = mean of the TRV internal temps + trim, trim stays
  active. When the sensor returns: a single recompute under the normal
  guards, no oscillation. `Status: implemented.`
- **R6** Missing or dead entities are skipped for both reads and writes; the
  remaining TRVs keep running. A returning TRV rejoins the sync on the next
  cycle automatically. `Status: implemented.`
- **R7** Per-room manual trim offset: the integration itself creates one
  number entity per room (`trv_sync.trim_<room>`, range +-5 K, default 0,
  restore across restarts), steerable directly. No separate helper to
  create. `Status: implemented.`
- **R8** Modes heat/off. off = TRV hvac off, setpoint registers untouched.
  heat = TRV hvac heat. `Status: implemented.`
- **R9** Restart: room target = median of the TRV setpoints. Accepted caveat
  (plan): an intentionally off-value TRV against the room target is
  clobbered back to the median after a restart. `Status: implemented.`
- **R10** No window-open logic, no device-side schedules fighting the
  setpoint sync: the coordinator forces foreign hvac modes (auto, cool, ...)
  back to heat every poll; the TRVZB must still be switched to manual mode
  as a cutover checklist item. `Status: implemented (runtime enforce).`

## Guards (per room, all options editable in the options flow)

- **R11** Deadband 0.3 K: no offset write below the delta. `Status: implemented.`
- **R12** Write rate limit: max 1 offset write per TRV per 10 min.
  `Status: implemented.`
- **R13** Quantize: offsets land on the 0.1 register grid.
  `Status: implemented.`
- **R14** Staleness threshold 30 min for the sensor age of measurement values
  (external sensor and TRV internal temps both must be fresh; setpoints and
  offset registers may sit unchanged for hours without counting as stale).
  `Status: implemented.`
- **R15** Poll interval 60 s. `Status: implemented.`
- **R16** Offsets are never computed unless both sides of the comparison are
  fresh, and nothing is ever written to an unavailable TRV.
  `Status: implemented.`
- **R17** A write failure or an unavailable TRV never blocks the other TRVs.
  `Status: implemented.`

- **R18** Guard log: sanity warning on long ext/TRV disagreement, and a write
  counter per TRV per day. `Status: OPEN, not implemented in v1.0.1.`
  Decide at the pilot review: implement in 1.0.2 or retire from this list.

## Offline

- **R19** No dependency on internet or cloud services at all (manifest
  requirements list is empty). `Status: implemented.`
- **R20** If HA is down mid-heat: the TRVs keep running their own loop with
  the last written offset and setpoint registers; nothing on the path to
  that state depends on HA state that a restart could lose.
  `Status: implemented (design property).`

## Acceptance criteria (pilot check list)

- **AC1** Unit tests green for the pure helpers: offset math including
  double-count avoidance, clamp, quantize, median, staleness, override
  semantics. `Status: done, 31 tests green as of v1.0.1.`
- **AC2** HeizZimmer1 pilot >= 24 h with no more than 1 offset write per TRV
  per hour in steady state (Better Thermostat 1.9.3 wrote dozens per
  minute). `Status: pending pilot.`
- **AC3** A manual setpoint change on a TRV dial appears on the virtual
  climate within 2 polls and propagates to the other TRV.
  `Status: pending pilot.`
- **AC4** HA restart on the pilot room: the room target survives (median
  restoration), no BT-style target wipe. `Status: pending pilot.`
- **AC5** External sensor pulled offline: offsets go to the neutral value
  once, the fallback mean is used, no oscillation; sensor return triggers a
  single recompute. `Status: pending pilot.`
- **AC6** Offline drill: HA stopped 10 min while heating; the room holds its
  target on the TRV-local loop, control resumes cleanly after restart.
  `Status: pending pilot.`