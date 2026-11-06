# Phase 10 controlled release-candidate checklist

This checklist describes unreleased source. It does not designate a production
release and does not authorize packaging.

## Identity

- Proposed version: `0.2.0-rc5` (not assigned in application metadata).
- Source base: `f32be135396e` plus the reviewed dirty working-tree changes.
- Build identity must be generated from the final approved source immediately
  before packaging; `working-tree-unreleased` is not a distributable build ID.

## Required automated gates

- Complete backend suite, including authentic North, Cherry Creek, Industrial
  Park, and Muncie tests available on the build machine.
- All frontend application and secure-development-proxy contracts.
- TypeScript and production Vite build.
- Release-manifest and bundled-runtime integrity tests.
- No whitespace errors from `git diff --check`.
- Record every fixture skip separately; a skipped authentic fixture is not a pass.

## Required manual gates

- Native Windows Browse, Save, Save As, restart, reopen, relocation, and cancel.
- Cherry Creek panel redesign/fitting and fresh review before PDF/DXF export.
- Industrial Park exact route, CUMDIST edit, panel review, PDF/DXF download.
- Muncie must list both blocking lateral identities and expose no artifact.
- Representative PDF page visual inspection for clipping/collisions/readability.
- Engineering DXF inspection and 1:1 plot comparison in AutoCAD.
- Authentic SI model with independent distance/elevation reference measurements.
- Restricted company laptop startup/shutdown and portable-runtime acceptance.

## Known release limitations

- Muncie lateral structures are preview-only; authoritative export is blocked.
- Unknown/mixed units, ambiguous topology, missing junction distances, and
  unsupported structures fail closed.
- Cherry Creek Panel 1 cannot contain its current authoritative content at the
  saved 1 in = 5 ft vertical scale on an 11x17 sheet; page, scale, or panel
  layout requires an explicit engineer decision and new review.
- CAD acceptance and authentic SI acceptance remain external blockers.
- No datum transformation, DXF import, FDT, or FIRM workflow is provided.

## Rollback and recovery

- Keep the previously accepted package unchanged and separately checksummed.
- Never overwrite an earlier release directory or ZIP.
- A failed candidate starts in a new directory; do not patch packaged files.
- Preserve user HDF and `.rasplot.json` files. Rollback replaces only the
  application directory; per-user logs/cache may be retained for diagnosis.
- Cancelled/failed exports must not be registered or offered for download.

## Approval record

- Engineering PDF reviewer: PENDING
- AutoCAD reviewer: PENDING
- Authentic SI reviewer: PENDING
- Windows deployment reviewer: PENDING
- Release owner approval to package/publish: PENDING
