# RAS-Plot supported scope and release acceptance

Authoritative scope document for 0.2.0-dev. This is unreleased source, not a
certification of every HEC-RAS model or the Thursday package. The real fixture
anchors are North (HEC-RAS 5.0.7) and Muncie (HEC-RAS 6.4.1).

VALIDATED means the stated contract has fixture/regression evidence, not that
every model of that type is accepted. IMPLEMENTED_NEEDS_VALIDATION describes
implemented behavior still requiring end-user/deployment evidence.
BLOCKED_BY_DATA requires the listed source datasets before engineering support
can expand. UNSUPPORTED has no authorized processing path.
EXTERNAL_ACCEPTANCE_REQUIRED cannot be established by automated code tests.

| Capability | Status | Exact boundary / remaining evidence |
| --- | --- | --- |
| Ordinary single-reach US Customary | VALIDATED | North/Muncie identity, source lengths and extents; result-side XS association required |
| North culvert | VALIDATED | Observed configuration only, approximately 110-ft face separation, explicit connections and deck evidence |
| Muncie lateral stationing | VALIDATED | Zero added longitudinal distance and retained source connectivity only |
| Muncie lateral authoritative full export | BLOCKED_BY_DATA | Long lateral geometry, main-channel start/end extent and receiving-area connectivity plus accepted HEC-RAS reference output |
| Connected multi-reach | BLOCKED_BY_DATA | Computed junction fixture with explicit topology, junction stationing/length rules and accepted continuous reference profile |
| Bridges | BLOCKED_BY_DATA | Real bridge HDF, exact plan-linked geometry text, deck/openings/connections, accepted profile |
| Multiple openings | BLOCKED_BY_DATA | Real multiple-opening HDF/text with group identities and verified longitudinal treatment |
| Alternative culverts / multiple barrels | BLOCKED_BY_DATA | Shape/barrel-specific HDF/text and accepted reference geometry |
| Inline/weir/gated | BLOCKED_BY_DATA | Real structures with crest/gates/operating states and verified profile geometry |
| Multiple structures in one XS interval | BLOCKED_BY_DATA | Explicit placement/connectivity evidence and accepted interval partition |
| SI | UNSUPPORTED | Explicitly rejected; no feet-based processing of SI, no speculative conversion |
| Unknown units | UNSUPPORTED | Authoritative export blocked until supported source units are established |
| Datum | VALIDATED | SOURCE / USER_ENTERED / UNKNOWN retained; required-known deliverables block UNKNOWN; no transformations |
| Steady results | VALIDATED | Observed North schema; stable IDs, duplicate/comma labels, identity/count checks |
| Unsteady saved timesteps / envelope | VALIDATED | Muncie schema; parent timestamps when matched; envelope over saved rows only, not simultaneous or source-authoritative maximum |
| Revision / panels / export snapshot | VALIDATED | Automated state transitions and isolation; unchanged manual panels, coverage checks, explicit review |
| Panel Apply/Cancel/reopen UI | IMPLEMENTED_NEEDS_VALIDATION | Browser/user acceptance pending; API/draft contract covered separately |
| PDF | VALIDATED | Serialized physical-inch scale, range clipping and missing-data gaps; not regulatory deliverable certification |
| DXF | VALIDATED | Generated DXF inch contract, geometry/layers/gaps; export only, no DXF import |
| AutoCAD / Civil 3D | EXTERNAL_ACCEPTANCE_REQUIRED | Open generated revisions in actual supported CAD versions, measure scales/units, inspect layers/text/gaps and plot |
| FDT / FIRM | UNSUPPORTED | Endpoints disabled; implementations retained solely for future regulatory validation |
| Portable Python 3.11 Windows runtime | IMPLEMENTED_NEEDS_VALIDATION | Current runtime used for backend regression; changed launcher/source packaging must be accepted on a standard restricted company laptop |
| Windows launcher lock / job / per-user storage | IMPLEMENTED_NEEDS_VALIDATION | Exercise double launch, read-only install folder, console close, normal shutdown/restart, download during cleanup |
| Other deployment environments | EXTERNAL_ACCEPTANCE_REQUIRED | Not validated; target users do not install Python/Node or require admin/internet |

## Engineering-state and asynchronous requests

Ordered identity/length/connectivity changes make CUMDIST dirty. Recalculate is
explicit and never regenerates manual panels. Explicit Revision Limits remain
unchanged even if a new extent makes them invalid; the user must review them.
Panel dependencies include hydraulics, profile selection/backwater, ground,
landmark elevations, inventory and source units. Intentional overlap is allowed;
coverage gaps list the missing station intervals. Saved panels require Apply
after dependent changes. Optional generated export panels never replace saved
definitions. Datum and units are checked on every export.

All client engineering calls carry a context generation and expected reach.
Mutations are sent in order; queued obsolete mutations are not sent. Late
successes/errors are discarded. Only profile reads use latest-read selection;
harmless independent metadata reads are not cancelled. Dialogs additionally
retain their open/close lifetime. Panel/Export requests carry the content hash
of the database they opened against and reject intervening changes. API clients
omitting this optional hash still receive current-state preflight under the lock.

API transactions are serialized. Export deep-copies the active database, records
its content version, validates settings and renders the same snapshot. Stable
profile IDs are operational; names are labels. Export options do not change live
session metadata, values or panels. Unique artifact IDs and ownership determine
downloads, not user-supplied filenames.

## Retention and local file access

Sessions expire after 24 hours without API activity; at most 32 remain open.
Opening another project closes the prior UI session. Session state is in memory;
restart/close invalidates its download URLs, so save needed downloads beforehand.
Artifacts expire after 24 hours, with 256 per session and 2,048 registered per
process. Cleanup refuses to delete an actively streaming download. Generated
artifact sidecars authorize cleanup of expired files after restart; unmarked
files and user HDFs are never recursively deleted. A crash before registration
may leave an unmarked file for manual administrator/user review, not blind cleanup.
Limits are rejection gates, not eviction of current downloads. Cleanup is lazy,
on startup/export, so expiry is not an exact-time deletion guarantee.

Normal packaged runtime writes logs/cache/temp below `%LOCALAPPDATA%\RAS-Plot`.
Logs older than 30 days are removed by exact application log pattern. The source
folder need not be writable. A per-user OS lock prevents simultaneous launch;
a Windows job terminates backend children when the launcher exits. Neither
normal runtime nor Browse requires Node, admin rights or internet.

Resolved containment protects static files, merge inputs/outputs and downloads.
Artifact registry and session ownership are required. Loopback peer, local Host,
matching Origin and Fetch Metadata are checked, independently of CORS.

## Errors and release artifacts

JSON errors retain actionable `detail` plus one of ENGINEERING_PREFLIGHT,
UNSUPPORTED_SCHEMA, INVALID_INPUT, STALE_STATE, FILE_ACCESS, EXPORT_FAILURE,
SECURITY_REJECTION or INTERNAL_ERROR. Expected engineering rejection is 4xx;
render/disk failures may be 500 with a specific category. Technical exceptions
are logged locally rather than exposed as traceback text.

Packaging source rebuilds via the existing lockfile/development toolchain,
compares frontend source identity before/after build, checks generated references
and staged asset hashes, and produces a release manifest with source/assets,
CPython version and installed dependency identities. The tested runtime is
CPython 3.11.3 Windows x64. The manifest says acceptance PENDING until the release
is separately approved. No package rebuild is implied by source changes.
