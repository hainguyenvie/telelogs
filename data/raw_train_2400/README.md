# TeleLogs troubleshooting — original training set

`train.json` is the exact 2,400-sample original training split from
`netop/TeleLogs`, `troubleshooting/train.json`, copied from H200 on 2026-07-24.
It is a JSON array; each item contains a `question` and an `answer` label (`C1`–`C8`).
The 864-sample test split is stored separately under `data/official_test_864/`.

| Class | Samples |
|---|---:|
| C1 | 264 |
| C2 | 320 |
| C3 | 330 |
| C4 | 283 |
| C5 | 352 |
| C6 | 225 |
| C7 | 349 |
| C8 | 277 |
| **Total** | **2,400** |

SHA-256: `5bdfe012ac05bd7fde4196ee308e2f143ea1042a4718cf3cf64bd875afc58a2a`

The dataset terms prohibit public sharing, re-uploading, and redistribution of
this benchmark data. These files may remain in this Git repository only while
the repository is private.
