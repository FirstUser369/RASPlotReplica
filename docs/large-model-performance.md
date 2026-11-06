# Large-model performance benchmark

Phase 6 uses `backend/benchmarks/large_model_benchmark.py` to create a
deterministic, disposable HDF for 1,000, 5,000, 10,000, or 25,000 cross
sections. The fixture has one connected route, five steady profiles, exact
result-side composite identities, three ground points per section, and a
missing hydraulic value every 997 sections. Source files are created in a
temporary directory and are never authoritative engineering fixtures.

Run from the repository root:

```powershell
python -m backend.benchmarks.large_model_benchmark --sizes 1000 5000 10000 25000
python -m backend.benchmarks.large_model_benchmark --sizes 1000 5000 10000 25000 --exports --output "$env:TEMP\rasplot-large-exports"
```

Each operation reports elapsed seconds and Python peak allocation MiB using
`time.perf_counter` and `tracemalloc`. The export run also reports PDF/DXF file
sizes. OS, h5py, Matplotlib, and ezdxf native allocations can exceed the Python
peak and should be measured with Windows Task Manager during manual acceptance.

The benchmark validates HDF schema selection, canonical import, working-table
construction, one channel-length edit, CUMDIST, panel generation, dependency
evidence, project serialization/reopen/restoration, PDF, and Engineering DXF.
It deliberately does not fabricate structures. Real-model structure and route
coverage remains in the North, Muncie, Cherry Creek, and Industrial Park tests.

## Baseline and optimization contract

The pre-optimization direct-model baseline used the same five-profile values
and dry-gap cadence. At 25,000 XS it measured 33.422 s for database creation,
3.3659 s for panel generation, 2.1001 s / 67.84 MiB for project serialization,
and an 11,862,970-byte project document.

Phase 6 replaces repeated full-profile panel scans with indexed finite runs,
uses composite-identity maps for structure insertion, avoids redundant deep
copies of scalar hydraulic records, omits default empty labels from new project
documents, batches PDF XS markers, and pages the frontend table at 500 rows.
None of these changes decimates vertices, fills hydraulic gaps, changes units,
or changes station/elevation values.

Timing is diagnostic, not an engineering acceptance criterion. Correctness is
enforced separately by `test_large_model_performance.py` and the complete
regression suite.

## Measured Phase 6 results

Windows 11, Python 3.11 portable runtime, five profiles. Times are seconds;
peak values are Python allocations under `tracemalloc`.

| XS | HDF import | Working DB | CUMDIST | Panels | Save JSON | Full reopen/restore | Project size |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 0.54 | 0.26 | 0.007 | 0.005 | 0.012 | 1.10 s / 12.9 MiB | 0.24 MB |
| 5,000 | 2.60 | 1.46 | 0.046 | 0.028 | 0.056 | 5.51 s / 57.5 MiB | 1.18 MB |
| 10,000 | 5.30 | 3.83 | 0.106 | 0.070 | 0.133 | 16.45 s / 112.6 MiB | 2.35 MB |
| 25,000 | 16.46 | 9.56 | 0.360 | 0.196 | 0.322 | 40.92 s / 282.5 MiB | 5.86 MB |

Export measurements retain every engineering vertex and XS marker:

| XS | PDF time / peak / size | Engineering DXF time / peak / size |
|---:|---:|---:|
| 1,000 | 6.08 s / 16 MiB / 29 KB | 4.22 s / 16 MiB / 0.76 MB |
| 5,000 | 19.43 s / 78 MiB / 31 KB | 23.59 s / 78 MiB / 3.60 MB |
| 10,000 | 34.74 s / 157 MiB / 33 KB | 35.56 s / 157 MiB / 7.16 MB |
| 25,000 | 66.79 s / 393 MiB / 39 KB | 80.27 s / 393 MiB / 17.83 MB |

The 25,000-XS case is supported but is the current practical engineering-review
ceiling: full restoration and exports should present progress/cancellation in a
future phase. No geometry simplification was introduced to improve these times.

## Phase 10 regression check

The same disposable five-profile fixtures were rerun on the Phase 10 working
tree. At 1k/5k/10k/25k XS, PDF export took 2.91/10.18/22.64/54.71 seconds and
Engineering DXF took 3.33/15.80/30.40/72.80 seconds. The 25k Python allocation
peak remained 393.14 MiB; artifacts were 39,259-byte PDF and 17,832,056-byte
DXF. Import, working-table, CUMDIST, panels, serialization, and restore remained
within the established practical envelope. Timing is machine-load dependent;
engineering identities, dependency digests, gaps, and vertex-preserving export
contracts remain the acceptance gates.
