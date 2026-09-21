# Example NCDU scans

This directory contains small, fictional NCDU 1.2 scans for three projects. They are intended for
quick visual checks of both HTML reports after changing `durep`.

From the repository root, after installing the project, generate a detail report with a comparison:

```bash
durep detail --out-dir /tmp/durep-detail examples/ncdu_scans/alder_2026-01-01.json examples/ncdu_scans/alder_2026-03-01.json
```

Generate an overview using all six scans and the accompanying project metadata:

```bash
durep overview examples/ncdu_scans --out-dir /tmp/durep-overview --metadata-tsv-path examples/project_metadata.tsv
```

The scans cover three dates and include growth, shrinkage, newly added directories, nested paths,
and compressed and uncompressed scientific data formats. Hardlink cases include:

* `alder`: two links in sibling directories, with `dev` inherited from the root.
* `cedar`: only one of three links is inside the scan, so there are no multi-counted bytes.
* `birch`, February: no hardlinks, providing a zero baseline.
* `birch`, March: three of four links to `stations.csv`, identified using only `nlink`.
  Two are in `field_notes` and one is directly under the root; the fourth is outside the scan.
  `external-stations.csv` has the same inode but an explicit different device, so it is distinct
  (its second link is outside the scan). Two links to `weather.csv` use only `hlnkc`, without
  a known link count.

Expected multi-counted bytes in the detail report's top-level card:

| Project | Earlier scan | March scan |
| --- | ---: | ---: |
| alder | 7,200,768 (7.201 MB) | 7,200,768 (7.201 MB) |
| birch | 0 | 204,800 (204.8 KB) |
| cedar | 0 | 0 |

In March's `birch` scan, `field_notes` alone has 118,784 multi-counted bytes; the root adds
another 86,016 for the link shared with that subtree. The HTML usage card, sunburst, and
compressible statistics still include every pathname. The detail text report excludes
multi-counted bytes from total disk usage, directory totals, and net change, and lists the
multi-counted bytes separately. The complete example data is under 10 KB.
